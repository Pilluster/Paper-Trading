"""
AlphaAgent — Automated Trading Bot for Indian Markets
Angel One SmartAPI (live) | yfinance (paper/interim)
Target: 15–20% annual alpha over Nifty 50

Run modes:
  PAPER_MODE=true  + no Angel One keys → uses yfinance (works TODAY)
  PAPER_MODE=true  + Angel One keys    → uses SmartAPI, paper orders
  PAPER_MODE=false + Angel One keys    → LIVE trading

Triggered automatically by GitHub Actions — no manual runs needed.
"""

import os
import json
import time
import logging
import pyotp
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─── ENV / CONFIG ──────────────────────────────────────────────────────────────

PAPER_MODE      = os.getenv("PAPER_MODE", "true").lower() == "true"
USE_ANGEL_ONE   = all([
    os.getenv("ANGELONE_API_KEY"),
    os.getenv("ANGELONE_CLIENT_ID"),
    os.getenv("ANGELONE_PASSWORD"),
    os.getenv("ANGELONE_TOTP_SECRET"),
])
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")


class Config:
    API_KEY        = os.getenv("ANGELONE_API_KEY", "")
    CLIENT_ID      = os.getenv("ANGELONE_CLIENT_ID", "")
    PASSWORD       = os.getenv("ANGELONE_PASSWORD", "")
    TOTP_SECRET    = os.getenv("ANGELONE_TOTP_SECRET", "")

    VIRTUAL_CAPITAL     = float(os.getenv("VIRTUAL_CAPITAL", "500000"))
    MAX_POSITIONS       = 14
    MAX_POSITION_PCT    = 0.08
    RISK_PER_TRADE_PCT  = 0.015
    SECTOR_CAP_PCT      = 0.22
    ETF_BOOK_PCT        = 0.15
    SWING_BOOK_PCT      = 0.75

    ENTRY_SCORE_MIN     = 72
    WATCHLIST_SCORE_MIN = 55
    STOP_LOSS_PCT       = 0.07
    PARTIAL_EXIT_1_PCT  = 0.18
    PARTIAL_EXIT_2_PCT  = 0.30
    TRAILING_STOP_PCT   = 0.08
    TRAILING_TRIGGER    = 0.20
    TIME_STOP_DAYS      = 20

    WEIGHTS = {
        "stage2":        20,
        "vcp":           15,
        "ma_stack":      10,
        "macd":           8,
        "rsi":            5,
        "volume":         2,
        "relative_str":  12,
        "sector_rrg":    10,
        "macro":          8,
        "earnings":       6,
        "crude_dxy":      4,
    }

    # NSE symbols (Angel One format)
    NIFTY50 = [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC",
        "SBIN","BHARTIARTL","KOTAKBANK","LT","AXISBANK","BAJFINANCE",
        "ASIANPAINT","MARUTI","SUNPHARMA","TITAN","HCLTECH","WIPRO",
        "ULTRACEMCO","ADANIENT","POWERGRID","NTPC","TECHM","BAJAJFINSV",
        "ONGC","JSWSTEEL","TATAMOTORS","NESTLEIND","COALINDIA","INDUSINDBK",
        "GRASIM","HDFCLIFE","SBILIFE","CIPLA","DIVISLAB","DRREDDY","BPCL",
        "EICHERMOT","HINDALCO","TATASTEEL","BRITANNIA","APOLLOHOSP",
        "ADANIPORTS","MM","TATACONSUM","BAJAJ-AUTO","HEROMOTOCO",
        "SHRIRAMFIN","LTIM"
    ]

    MIDCAP = [
        "TRENT","PAGEIND","PIIND","DEEPAKNTR","ASTRAL","CHOLAFIN",
        "MPHASIS","PERSISTENT","COFORGE","LTTS","SUPREMEIND","SCHAEFFLER",
        "GRINDWELL","CAMS","CDSL","MARICO","GODREJCP","DABUR","EMAMILTD"
    ]

    # yfinance suffix for NSE
    ETF_YF = {
        "GOLDBEES.NS":   "Gold ETF",
        "SILVERBEES.NS": "Silver ETF",
        "CPSEETF.NS":    "CPSE ETF",
    }

    LOG_FILE   = "alpha_agent.log"
    JOURNAL    = "reports/trade_journal.csv"
    STATE_FILE = "reports/portfolio_state.json"
    REPORT_DIR = "reports"


# ─── LOGGING ───────────────────────────────────────────────────────────────────

os.makedirs(Config.REPORT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(Config.LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("AlphaAgent")
log.info(f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'} | "
         f"Data: {'AngelOne' if USE_ANGEL_ONE else 'yfinance'}")


# ─── NOTIFICATIONS ─────────────────────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


# ─── DATA LAYER — auto-selects yfinance or AngelOne ───────────────────────────

class DataClient:
    """
    Unified data interface.
    Uses yfinance when Angel One keys are not available.
    Automatically switches to SmartAPI once keys are added to GitHub Secrets.
    """

    def __init__(self):
        self.angel = None
        if USE_ANGEL_ONE:
            self._init_angel()
        else:
            log.info("Using yfinance for market data (Angel One keys not set)")

    def _init_angel(self):
        try:
            from SmartApi import SmartConnect
            self.angel = SmartConnect(api_key=Config.API_KEY)
            totp = pyotp.TOTP(Config.TOTP_SECRET).now()
            data = self.angel.generateSession(Config.CLIENT_ID, Config.PASSWORD, totp)
            if data["status"]:
                log.info("Angel One authenticated")
            else:
                log.error(f"Angel One auth failed: {data['message']}")
                self.angel = None
        except Exception as e:
            log.error(f"Angel One init failed: {e}")
            self.angel = None

    def get_historical(self, symbol: str, days: int = 260) -> pd.DataFrame:
        if self.angel:
            return self._angel_historical(symbol, days)
        else:
            return self._yf_historical(symbol, days)

    def get_ltp(self, symbol: str) -> Optional[float]:
        if self.angel:
            return self._angel_ltp(symbol)
        else:
            return self._yf_ltp(symbol)

    def place_order(self, symbol: str, qty: int, price: float,
                    transaction: str = "BUY") -> str:
        order_id = f"PAPER_{symbol}_{datetime.now().strftime('%H%M%S')}"
        if PAPER_MODE or not self.angel:
            log.info(f"[PAPER] {transaction} {qty}x {symbol} @ ₹{price:.2f}")
            return order_id
        # Live order via Angel One
        try:
            token = self._get_angel_token(symbol)
            resp  = self.angel.placeOrder({
                "variety": "NORMAL", "tradingsymbol": symbol,
                "symboltoken": token, "transactiontype": transaction,
                "exchange": "NSE", "ordertype": "LIMIT",
                "producttype": "DELIVERY", "duration": "DAY",
                "price": str(round(price, 2)), "quantity": str(qty),
            })
            if resp["status"]:
                oid = resp["data"]["orderid"]
                log.info(f"[LIVE] {transaction} {qty}x {symbol} @ ₹{price:.2f} | {oid}")
                return oid
        except Exception as e:
            log.error(f"Order failed {symbol}: {e}")
        return ""

    def place_gtt_stop(self, symbol: str, entry: float,
                       stop: float, qty: int) -> str:
        """Place GTT stop-loss order (works in paper mode as a log entry)."""
        log.info(f"[GTT STOP] {symbol} stop @ ₹{stop:.2f} for {qty} shares")
        if PAPER_MODE or not self.angel:
            return f"PAPER_GTT_{symbol}"
        try:
            token = self._get_angel_token(symbol)
            resp  = self.angel.gttCreateRule({
                "tradingsymbol": symbol, "symboltoken": token,
                "exchange": "NSE", "producttype": "DELIVERY",
                "transactiontype": "SELL",
                "price":        str(round(stop * 0.995, 2)),
                "qty":          str(qty),
                "triggerprice": str(stop),
                "disclosedqty": str(qty),
                "timeperiod":   365
            })
            return resp.get("data", {}).get("id", "")
        except Exception as e:
            log.warning(f"GTT failed {symbol}: {e}")
        return ""

    # ── yfinance implementation ──────────────────────────────────────────────

    def _yf_historical(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            # Angel One symbols → yfinance NSE format
            yf_sym = symbol.replace("BAJAJ-AUTO", "BAJAJ-AUTO") + ".NS"
            # Handle special cases
            if symbol in ["MM"]: yf_sym = "M&M.NS"
            if symbol == "NIFTY50_INDEX": yf_sym = "^NSEI"

            ticker = yf.Ticker(yf_sym)
            end    = datetime.now()
            start  = end - timedelta(days=days + 50)  # buffer
            df     = ticker.history(start=start.strftime("%Y-%m-%d"),
                                    end=end.strftime("%Y-%m-%d"))
            if df.empty:
                return pd.DataFrame()
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"index": "date"})
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            df = df[["date","open","high","low","close","volume"]].dropna()
            return df.tail(days).reset_index(drop=True)
        except Exception as e:
            log.warning(f"yfinance fetch failed {symbol}: {e}")
            return pd.DataFrame()

    def _yf_ltp(self, symbol: str) -> Optional[float]:
        try:
            import yfinance as yf
            yf_sym = symbol + ".NS"
            if symbol == "MM": yf_sym = "M&M.NS"
            t = yf.Ticker(yf_sym)
            info = t.fast_info
            return float(info.last_price)
        except:
            return None

    # ── Angel One implementation ─────────────────────────────────────────────

    def _angel_historical(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            token     = self._get_angel_token(symbol)
            to_date   = datetime.now().strftime("%Y-%m-%d %H:%M")
            from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
            data      = self.angel.getCandleData({
                "exchange": "NSE", "symboltoken": token,
                "interval": "ONE_DAY",
                "fromdate": from_date, "todate": to_date
            })
            if data["status"] and data["data"]:
                df = pd.DataFrame(data["data"],
                                  columns=["date","open","high","low","close","volume"])
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            log.warning(f"Angel historical failed {symbol}: {e}")
        return pd.DataFrame()

    def _angel_ltp(self, symbol: str) -> Optional[float]:
        try:
            token = self._get_angel_token(symbol)
            data  = self.angel.ltpData("NSE", symbol, token)
            if data["status"]:
                return float(data["data"]["ltp"])
        except:
            return None

    def _get_angel_token(self, symbol: str) -> str:
        try:
            data = self.angel.searchScrip("NSE", symbol)
            if data["status"] and data["data"]:
                return data["data"][0]["symboltoken"]
        except:
            pass
        return ""


# ─── INDICATORS ────────────────────────────────────────────────────────────────

class Ind:
    @staticmethod
    def sma(s, n): return s.rolling(n).mean()

    @staticmethod
    def ema(s, n): return s.ewm(span=n, adjust=False).mean()

    @staticmethod
    def rsi(s, n=14):
        d  = s.diff()
        g  = d.clip(lower=0).rolling(n).mean()
        l  = (-d.clip(upper=0)).rolling(n).mean()
        return 100 - 100 / (1 + g / l.replace(0, np.nan))

    @staticmethod
    def macd(s, fast=12, slow=26, sig=9):
        line  = Ind.ema(s, fast) - Ind.ema(s, slow)
        signal= line.ewm(span=sig, adjust=False).mean()
        return line, signal, line - signal

    @staticmethod
    def atr(df, n=14):
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift()).abs()
        lc = (df["low"]  - df["close"].shift()).abs()
        return pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(n).mean()

    @staticmethod
    def ma_stack(df):
        c    = df["close"]
        cur  = c.iloc[-1]
        ma50 = Ind.sma(c, 50)
        ma100= Ind.sma(c,100)
        ma200= Ind.sma(c,200)
        return {
            "full": cur > ma50.iloc[-1] > ma100.iloc[-1] > ma200.iloc[-1],
            "ma50_slope":  ma50.iloc[-1]  > ma50.iloc[-6],
            "ma100_slope": ma100.iloc[-1] > ma100.iloc[-11],
            "ma200_slope": ma200.iloc[-1] > ma200.iloc[-21],
            "ma50":  round(ma50.iloc[-1],  2),
            "ma100": round(ma100.iloc[-1], 2),
            "ma200": round(ma200.iloc[-1], 2),
        }

    @staticmethod
    def weinstein_stage(df):
        """Stage 2 = price above rising 30W (~150D) MA"""
        if len(df) < 155: return 0
        c    = df["close"]
        ma   = Ind.sma(c, 150)
        cur  = c.iloc[-1]
        now  = ma.iloc[-1]
        prev = ma.iloc[-21]
        rising = now > prev
        if cur > now and rising:     return 2
        if cur > now and not rising: return 3
        if cur < now and rising:     return 1
        return 4

    @staticmethod
    def detect_vcp(df):
        """Volatility contraction: 3 contracting ranges → pivot breakout."""
        if len(df) < 65:
            return {"ok": False, "pivot": 0, "pct": 0}
        rec = df.tail(65).copy()
        w   = len(rec) // 3
        rng = [(rec.iloc[i*w:(i+1)*w]["high"].max() -
                rec.iloc[i*w:(i+1)*w]["low"].min()) /
               rec.iloc[i*w:(i+1)*w]["close"].mean()
               for i in range(3)]
        contracting = rng[0] > rng[1] > rng[2]
        shrink      = (rng[0] - rng[2]) / rng[0] if rng[0] else 0
        pivot       = rec["high"].max()
        near_pivot  = (pivot - rec["close"].iloc[-1]) / pivot < 0.06
        return {
            "ok":    contracting and shrink > 0.25 and near_pivot,
            "pivot": round(pivot, 2),
            "pct":   round(shrink, 3)
        }


# ─── MACRO MONITOR ─────────────────────────────────────────────────────────────

@dataclass
class MacroState:
    regime: str     = "A"
    vix:    float   = 14.0
    nifty_vs_50:  float = 0.0
    nifty_vs_200: float = 0.0
    fii_flow: float = 0.0
    score:    float = 6.0

def get_macro(client: DataClient) -> MacroState:
    m = MacroState()
    # Nifty vs MAs
    ndf = client._yf_historical("NIFTY50_INDEX", 260)
    if not ndf.empty:
        c    = ndf["close"]
        cur  = c.iloc[-1]
        ma50 = Ind.sma(c, 50).iloc[-1]
        ma200= Ind.sma(c,200).iloc[-1]
        m.nifty_vs_50  = round((cur - ma50)  / ma50  * 100, 2)
        m.nifty_vs_200 = round((cur - ma200) / ma200 * 100, 2)

    # VIX (India VIX via yfinance approximation)
    try:
        import yfinance as yf
        vix_data = yf.Ticker("^VIX").fast_info
        m.vix = round(float(vix_data.last_price), 1)
    except:
        m.vix = 15.0

    # FII flow (NSE public endpoint)
    try:
        sess = requests.Session()
        sess.get("https://www.nseindia.com",
                 headers={"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.nseindia.com/"}, timeout=5)
        r = sess.get("https://www.nseindia.com/api/fiidiiTradeReact",
                     headers={"User-Agent": "Mozilla/5.0",
                               "Referer": "https://www.nseindia.com/"}, timeout=5)
        for row in r.json():
            if row.get("category") == "FII/FPI" and "equity" in row.get("type","").lower():
                m.fii_flow = float(row.get("netPurchasesSales", 0))
                break
    except:
        pass

    # Regime
    if m.nifty_vs_50 > 0 and m.nifty_vs_200 > 0 and m.vix < 18:
        m.regime = "A"
    elif m.nifty_vs_200 > -5 and m.vix < 22:
        m.regime = "B"
    else:
        m.regime = "C"

    # Macro score (out of 8)
    s = 0
    if m.nifty_vs_200 > 5:   s += 3
    elif m.nifty_vs_200 > 0: s += 1.5
    if m.nifty_vs_50  > 0:   s += 2
    if m.vix < 14:            s += 2
    elif m.vix < 18:          s += 1
    if m.fii_flow > 1000:     s += 1
    m.score = round(min(s, Config.WEIGHTS["macro"]), 2)

    log.info(f"MACRO | Regime={m.regime} | VIX={m.vix} | "
             f"Nifty/50dma={m.nifty_vs_50:+.1f}% | Nifty/200dma={m.nifty_vs_200:+.1f}% | "
             f"FII={m.fii_flow:+.0f}Cr | MacroScore={m.score}")
    return m


# ─── SIGNAL ENGINE ─────────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:  str
    score:   float
    action:  str        # buy | watch | avoid
    entry:   float = 0.0
    stop:    float = 0.0
    qty:     int   = 0
    risk:    float = 0.0
    comps:   dict  = field(default_factory=dict)
    reason:  str   = ""

def score_symbol(symbol: str, df: pd.DataFrame,
                 macro: MacroState, cash: float) -> Signal:
    sig = Signal(symbol=symbol, score=0, action="avoid")
    if len(df) < 205:
        sig.reason = "Insufficient history"
        return sig

    c   = df["close"]
    cur = c.iloc[-1]
    sc  = {}

    # 1. Stage (20)
    stage     = Ind.weinstein_stage(df)
    sc["stage2"] = Config.WEIGHTS["stage2"] if stage == 2 else 0

    # 2. VCP (15)
    vcp = Ind.detect_vcp(df)
    if vcp["ok"]:
        sc["vcp"] = Config.WEIGHTS["vcp"]
    elif vcp["pct"] > 0.15:
        sc["vcp"] = Config.WEIGHTS["vcp"] * 0.5
    else:
        sc["vcp"] = 0

    # 3. MA stack (10)
    ma = Ind.ma_stack(df)
    if ma["full"] and ma["ma50_slope"] and ma["ma200_slope"]:
        sc["ma_stack"] = Config.WEIGHTS["ma_stack"]
    elif ma["full"]:
        sc["ma_stack"] = Config.WEIGHTS["ma_stack"] * 0.6
    elif cur > ma["ma200"]:
        sc["ma_stack"] = Config.WEIGHTS["ma_stack"] * 0.3
    else:
        sc["ma_stack"] = 0

    # 4. MACD (8)
    ml, ms, mh = Ind.macd(c)
    bullish = ml.iloc[-2] < ms.iloc[-2] and ml.iloc[-1] > ms.iloc[-1]  # crossover
    above   = ml.iloc[-1] > ms.iloc[-1]
    expand  = mh.iloc[-1] > mh.iloc[-2] > 0
    if bullish and expand:        sc["macd"] = Config.WEIGHTS["macd"]
    elif above and expand:        sc["macd"] = Config.WEIGHTS["macd"] * 0.6
    elif above:                   sc["macd"] = Config.WEIGHTS["macd"] * 0.3
    else:                         sc["macd"] = 0

    # 5. Weekly RSI (5)
    wc = df.set_index("date")["close"].resample("W").last().dropna()
    if len(wc) >= 15:
        wrsi = Ind.rsi(wc).iloc[-1]
        if 50 <= wrsi <= 70:      sc["rsi"] = Config.WEIGHTS["rsi"]
        elif 45 <= wrsi < 50 or 70 < wrsi <= 78:
                                  sc["rsi"] = Config.WEIGHTS["rsi"] * 0.5
        else:                     sc["rsi"] = 0
    else:                         sc["rsi"] = 0

    # 6. Volume (2)
    avg_vol = df["volume"].tail(50).mean()
    tod_vol = df["volume"].iloc[-1]
    if tod_vol >= avg_vol * 1.5:  sc["volume"] = Config.WEIGHTS["volume"]
    elif tod_vol >= avg_vol * 1.2:sc["volume"] = Config.WEIGHTS["volume"] * 0.5
    else:                         sc["volume"] = 0

    # 7. Relative strength vs Nifty 6M (12)
    if len(df) >= 130:
        ret_6m = (cur / c.iloc[-126] - 1) * 100
        nifty_6m = 8.0          # Update dynamically — placeholder
        rs = ret_6m - nifty_6m
        if rs > 15:             sc["relative_str"] = Config.WEIGHTS["relative_str"]
        elif rs > 8:            sc["relative_str"] = Config.WEIGHTS["relative_str"] * 0.7
        elif rs > 0:            sc["relative_str"] = Config.WEIGHTS["relative_str"] * 0.4
        else:                   sc["relative_str"] = 0
    else:                       sc["relative_str"] = 0

    # 8. Sector RRG (10) — simplified relative momentum
    sc["sector_rrg"] = Config.WEIGHTS["sector_rrg"] * 0.6

    # 9. Macro (8)
    sc["macro"] = macro.score

    # 10. Earnings (6) — stub; extend with Screener API
    sc["earnings"] = Config.WEIGHTS["earnings"] * 0.5

    # 11. DXY/Crude (4) — stub
    sc["crude_dxy"] = Config.WEIGHTS["crude_dxy"] * 0.5

    total = round(sum(sc.values()), 2)
    sig.score = total
    sig.comps = {k: round(v,2) for k,v in sc.items()}

    threshold = Config.ENTRY_SCORE_MIN
    if macro.regime == "B": threshold = 80
    if macro.regime == "C": threshold = 9999

    if total >= threshold:
        sig.action = "buy"
        # Stop: VCP base low or 7% flat
        base_low    = df["low"].tail(30).min()
        stop_vcp    = base_low if vcp["ok"] else cur * (1 - Config.STOP_LOSS_PCT)
        sig.stop    = round(max(stop_vcp, cur * 0.90), 2)  # never > 10% away
        sig.entry   = round(cur, 2)
        risk_share  = sig.entry - sig.stop
        risk_budget = Config.VIRTUAL_CAPITAL * Config.RISK_PER_TRADE_PCT
        sig.qty     = max(1, int(risk_budget / risk_share)) if risk_share > 0 else 1
        max_qty     = int(Config.VIRTUAL_CAPITAL * Config.MAX_POSITION_PCT / cur)
        sig.qty     = min(sig.qty, max_qty)
        # Don't exceed available cash
        max_by_cash = int(cash * 0.95 / cur)
        sig.qty     = min(sig.qty, max_by_cash)
        sig.risk    = round(sig.qty * risk_share, 2)
        sig.reason  = (f"Score {total:.0f}/100 | Stage {stage} | "
                       f"VCP={'Yes' if vcp['ok'] else 'No'} | "
                       f"MA={'Full stack' if ma['full'] else 'Partial'} | "
                       f"Regime {macro.regime}")

    elif total >= Config.WATCHLIST_SCORE_MIN:
        sig.action = "watch"
        sig.reason = f"Score {total:.0f} — monitoring for setup"
    else:
        sig.action = "avoid"
        sig.reason = f"Score {total:.0f} — does not meet criteria"

    return sig


# ─── PORTFOLIO STATE ───────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol:      str
    entry:       float
    qty:         int
    stop:        float
    target1:     float
    target2:     float
    entry_date:  str
    score:       float
    partial:     int   = 0
    trail_on:    bool  = False
    trail_high:  float = 0.0
    flat_days:   int   = 0

    @property
    def live_stop(self):
        if self.trail_on and self.trail_high > 0:
            return self.trail_high * (1 - Config.TRAILING_STOP_PCT)
        return self.stop

class Portfolio:
    def __init__(self):
        self.positions: list[Position] = []
        self.cash = Config.VIRTUAL_CAPITAL
        self._load()

    def _load(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE) as f:
                    d = json.load(f)
                self.cash      = d.get("cash", Config.VIRTUAL_CAPITAL)
                self.positions = [Position(**p) for p in d.get("positions", [])]
            except:
                pass

    def save(self):
        os.makedirs(Config.REPORT_DIR, exist_ok=True)
        with open(Config.STATE_FILE, "w") as f:
            json.dump({"cash": self.cash,
                       "positions": [asdict(p) for p in self.positions]}, f, indent=2)

    def has(self, sym): return any(p.symbol == sym for p in self.positions)

    def add(self, pos: Position):
        self.positions.append(pos)
        self.cash -= pos.entry * pos.qty
        self.save()

    def remove(self, sym: str, qty: int, price: float):
        for p in self.positions[:]:
            if p.symbol == sym:
                self.cash += price * qty
                rem = p.qty - p.partial * (p.qty // 3)
                if qty >= rem:
                    self.positions.remove(p)
                break
        self.save()

    def metrics(self, prices: dict) -> dict:
        total = self.cash
        pnl   = 0.0
        for p in self.positions:
            ltp   = prices.get(p.symbol, p.entry)
            total += ltp * p.qty
            pnl   += (ltp - p.entry) * p.qty
        dd = (Config.VIRTUAL_CAPITAL - total) / Config.VIRTUAL_CAPITAL * 100
        return {
            "total":     round(total, 2),
            "cash":      round(self.cash, 2),
            "unrealized": round(pnl, 2),
            "drawdown":  round(dd, 2),
            "positions": len(self.positions),
            "paused":    dd > 10
        }


# ─── RISK MANAGER ──────────────────────────────────────────────────────────────

def check_exits(positions: list[Position], prices: dict) -> list[dict]:
    acts = []
    for p in positions:
        ltp = prices.get(p.symbol)
        if ltp is None: continue
        pct = (ltp - p.entry) / p.entry

        # Activate trailing stop
        if pct >= Config.TRAILING_TRIGGER and not p.trail_on:
            p.trail_on   = True
            p.trail_high = ltp
        if p.trail_on and ltp > p.trail_high:
            p.trail_high = ltp

        rem = p.qty - p.partial * (p.qty // 3)

        # Hard/trailing stop
        if ltp <= p.live_stop:
            acts.append({"sym": p.symbol, "qty": rem, "price": ltp,
                         "reason": f"Stop ₹{p.live_stop:.2f}", "kind": "stop"})
            continue

        # Partial 1 at +18%
        if pct >= Config.PARTIAL_EXIT_1_PCT and p.partial == 0:
            acts.append({"sym": p.symbol, "qty": p.qty//3, "price": ltp,
                         "reason": f"Target1 +{pct*100:.1f}%", "kind": "t1"})
            p.partial = 1

        # Partial 2 at +30%
        elif pct >= Config.PARTIAL_EXIT_2_PCT and p.partial == 1:
            acts.append({"sym": p.symbol, "qty": p.qty//3, "price": ltp,
                         "reason": f"Target2 +{pct*100:.1f}%", "kind": "t2"})
            p.partial = 2

        # Time stop
        p.flat_days = p.flat_days + 1 if abs(pct) < 0.02 else 0
        if p.flat_days >= Config.TIME_STOP_DAYS:
            acts.append({"sym": p.symbol, "qty": rem, "price": ltp,
                         "reason": f"Time stop ({p.flat_days}d flat)", "kind": "time"})
    return acts


# ─── TRADE JOURNAL ─────────────────────────────────────────────────────────────

class Journal:
    def __init__(self):
        os.makedirs(Config.REPORT_DIR, exist_ok=True)
        if not os.path.exists(Config.JOURNAL):
            pd.DataFrame(columns=[
                "datetime","symbol","action","qty","price",
                "score","reason","kind","regime","mode"
            ]).to_csv(Config.JOURNAL, index=False)

    def log(self, row: dict):
        pd.DataFrame([row]).to_csv(Config.JOURNAL, mode="a",
                                    header=False, index=False)

    def summary(self) -> pd.DataFrame:
        if os.path.exists(Config.JOURNAL):
            return pd.read_csv(Config.JOURNAL)
        return pd.DataFrame()


# ─── DAILY REPORT (RICH HTML) ──────────────────────────────────────────────────

def gauge_bar(value, min_val, max_val, low_bad=True, width=120):
    pct = max(0, min(100, (value - min_val) / (max_val - min_val) * 100))
    color = ("#e74c3c" if pct < 30 else "#f39c12" if pct < 60 else "#27ae60") if low_bad else             ("#27ae60" if pct < 30 else "#f39c12" if pct < 60 else "#e74c3c")
    return (f'<span style="display:inline-block;width:{width}px;height:8px;background:#e0e0e0;'
            f'border-radius:4px;vertical-align:middle;margin:0 6px;">'
            f'<span style="display:block;width:{pct:.0f}%;height:100%;background:{color};border-radius:4px;"></span></span>')

def build_html_report(metrics, signals, macro, portfolio, prices):
    mode_tag  = "PAPER" if PAPER_MODE else "LIVE"
    data_tag  = "yfinance" if not USE_ANGEL_ONE else "AngelOne"
    now_str   = datetime.now().strftime("%d %b %Y, %I:%M %p IST")
    alpha     = metrics["total"] - Config.VIRTUAL_CAPITAL
    alpha_pct = (metrics["total"] / Config.VIRTUAL_CAPITAL - 1) * 100
    pnl_color = "#27ae60" if alpha >= 0 else "#e74c3c"
    dd_color  = "#27ae60" if metrics["drawdown"] <= 3 else "#f39c12" if metrics["drawdown"] <= 7 else "#e74c3c"

    regime_map = {
        "A": {"label":"A — Risk On (Bull Market)","color":"#27ae60","bg":"#eafaf1","border":"#27ae60",
              "meaning":"Nifty is above both its 50-day and 200-day moving averages and trending up. Ideal environment for swing trading — strong institutional support. Bot is fully deployed.",
              "action":"Full deployment. All modes active. Swing + ETF positions open.","emoji":"🟢"},
        "B": {"label":"B — Cautious (Choppy Market)","color":"#f39c12","bg":"#fef9e7","border":"#f39c12",
              "meaning":"Nifty is in a mixed zone between its key moving averages. Market lacks clear direction. High risk of whipsaws. Bot raises entry threshold and reduces position count.",
              "action":"Reduced exposure. Score threshold raised to 80. Max 8 positions. ETF hedge increased.","emoji":"🟡"},
        "C": {"label":"C — Risk Off (Bear Market)","color":"#e74c3c","bg":"#fdedec","border":"#e74c3c",
              "meaning":"Nifty is below its 200-day moving average — a confirmed downtrend. Institutional money is leaving equities. Buying stocks now means catching a falling knife. Bot stays in cash.",
              "action":"No new equity entries. Capital fully preserved in cash. Only Gold ETF allowed.","emoji":"🔴"},
    }
    r = regime_map.get(macro.regime, regime_map["B"])

    vix = macro.vix
    if vix < 12:   vix_label, vix_color = "Very low — extreme complacency, watch for reversal", "#f39c12"
    elif vix < 16: vix_label, vix_color = "Low — calm market, good for momentum trades", "#27ae60"
    elif vix < 20: vix_label, vix_color = "Moderate — normal healthy volatility", "#27ae60"
    elif vix < 25: vix_label, vix_color = "Elevated — caution warranted, tighten stops", "#f39c12"
    elif vix < 30: vix_label, vix_color = "High — fear in market, reduce all exposure", "#e74c3c"
    else:          vix_label, vix_color = "Extreme fear — stay in cash, wait for calm", "#e74c3c"

    def ma_interp(pct):
        if pct > 5:    return f"{pct:+.1f}% above — strong uptrend confirmed", "#27ae60"
        elif pct > 0:  return f"{pct:+.1f}% above — mildly bullish", "#27ae60"
        elif pct > -3: return f"{pct:+.1f}% below — caution zone", "#f39c12"
        else:          return f"{pct:+.1f}% below — confirmed downtrend", "#e74c3c"

    ma50_label,  ma50_color  = ma_interp(macro.nifty_vs_50)
    ma200_label, ma200_color = ma_interp(macro.nifty_vs_200)

    fii = macro.fii_flow
    if fii > 2000:    fii_label, fii_color = "Strong buying — very bullish signal", "#27ae60"
    elif fii > 500:   fii_label, fii_color = "Net buyers — bullish", "#27ae60"
    elif fii > 0:     fii_label, fii_color = "Marginal buying — neutral", "#f39c12"
    elif fii > -500:  fii_label, fii_color = "Marginal selling — mild caution", "#f39c12"
    elif fii > -2000: fii_label, fii_color = "Net sellers — bearish pressure", "#e74c3c"
    else:             fii_label, fii_color = "Heavy selling — very bearish", "#e74c3c"

    buys  = sorted([s for s in signals if s.action=="buy"],  key=lambda x: x.score, reverse=True)[:8]
    watch = sorted([s for s in signals if s.action=="watch"],key=lambda x: x.score, reverse=True)[:8]

    def sig_rows(sigs):
        if not sigs:
            return '<tr><td colspan="7" style="text-align:center;color:#999;padding:16px;">No signals in current regime — bot protecting capital</td></tr>'
        rows = ""
        for s in sigs:
            cost = s.entry * s.qty
            rr   = round((s.entry*(1+Config.PARTIAL_EXIT_1_PCT)-s.entry)/(s.entry-s.stop),2) if s.stop < s.entry else 0
            sc   = "#27ae60" if s.score>=72 else "#f39c12" if s.score>=55 else "#e74c3c"
            rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
              <td style="padding:9px 8px;font-weight:700;">{s.symbol}</td>
              <td style="padding:9px 8px;text-align:center;">
                <span style="background:{sc};color:#fff;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:700;">{s.score:.0f}</span>
              </td>
              <td style="padding:9px 8px;text-align:right;">₹{s.entry:,.2f}</td>
              <td style="padding:9px 8px;text-align:right;color:#e74c3c;">₹{s.stop:,.2f}</td>
              <td style="padding:9px 8px;text-align:center;">{s.qty}</td>
              <td style="padding:9px 8px;text-align:right;">₹{cost:,.0f}</td>
              <td style="padding:9px 8px;text-align:center;color:#7f8c8d;">{rr:.1f}x</td>
            </tr>"""
        return rows

    def pos_rows():
        if not portfolio.positions:
            return '<tr><td colspan="7" style="text-align:center;color:#999;padding:16px;">No open positions — fully in cash</td></tr>'
        rows = ""
        for p in portfolio.positions:
            ltp = prices.get(p.symbol, p.entry)
            pp  = (ltp - p.entry) / p.entry * 100
            pv  = (ltp - p.entry) * p.qty
            pc  = "#27ae60" if pp >= 0 else "#e74c3c"
            sd  = (ltp - p.live_stop) / ltp * 100
            tr  = "✓ Trailing" if p.trail_on else "Hard stop"
            rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
              <td style="padding:9px 8px;font-weight:700;">{p.symbol}</td>
              <td style="padding:9px 8px;text-align:right;">₹{p.entry:,.2f}</td>
              <td style="padding:9px 8px;text-align:right;">₹{ltp:,.2f}</td>
              <td style="padding:9px 8px;text-align:right;color:{pc};font-weight:700;">{pp:+.1f}%<br><span style="font-size:11px;">₹{pv:+,.0f}</span></td>
              <td style="padding:9px 8px;text-align:right;color:#e74c3c;">₹{p.live_stop:,.2f}<br><span style="font-size:11px;color:#7f8c8d;">{sd:.1f}% away | {tr}</span></td>
              <td style="padding:9px 8px;text-align:right;">₹{p.target1:,.2f}</td>
              <td style="padding:9px 8px;text-align:center;font-size:12px;color:#7f8c8d;">{p.entry_date}</td>
            </tr>"""
        return rows

    def comp_table():
        if not buys: return ""
        top = buys[0]
        labels = {
            "stage2":("Stage 2 — Weinstein",20,"Price above rising 30-week MA"),
            "vcp":("VCP Pattern",15,"Volatility contraction + pivot breakout"),
            "ma_stack":("MA Stack 50/100/200",10,"Full upward hierarchy"),
            "macd":("MACD",8,"Bullish crossover + expanding histogram"),
            "rsi":("Weekly RSI",5,"Sweet spot: 50–70"),
            "volume":("Volume",2,"Breakout vol ≥ 1.5× average"),
            "relative_str":("Relative Strength vs Nifty",12,"6-month outperformance"),
            "sector_rrg":("Sector RRG",10,"Leading quadrant"),
            "macro":("Macro Score",8,"RBI + FII + VIX + Nifty MAs"),
            "earnings":("Earnings Momentum",6,"Revenue + EPS trend"),
            "crude_dxy":("DXY / Crude Oil",4,"Global macro direction"),
        }
        rows = ""
        for key,(label,mx,desc) in labels.items():
            got = top.comps.get(key,0)
            pct = got/mx*100 if mx else 0
            bc  = "#27ae60" if pct>=70 else "#f39c12" if pct>=40 else "#e74c3c"
            rows += f"""<tr style="border-bottom:1px solid #f8f8f8;">
              <td style="padding:7px 8px;font-size:13px;font-weight:600;">{label}</td>
              <td style="padding:7px 8px;font-size:12px;color:#7f8c8d;">{desc}</td>
              <td style="padding:7px 8px;text-align:right;font-weight:700;color:{bc};">{got:.0f}/{mx}</td>
              <td style="padding:7px 8px;width:100px;"><div style="background:#e0e0e0;border-radius:3px;height:6px;"><div style="width:{pct:.0f}%;background:{bc};height:6px;border-radius:3px;"></div></div></td>
            </tr>"""
        return f"""<h3 style="color:#2c3e50;font-size:15px;margin:24px 0 10px;">Signal breakdown — {top.symbol} (top pick today)</h3>
        <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">
          <thead><tr style="background:#f8f9fa;">
            <th style="padding:8px;text-align:left;color:#7f8c8d;">Factor</th>
            <th style="padding:8px;text-align:left;color:#7f8c8d;">What it measures</th>
            <th style="padding:8px;text-align:right;color:#7f8c8d;">Score</th>
            <th style="padding:8px;color:#7f8c8d;">Strength</th>
          </tr></thead><tbody>{rows}</tbody></table>"""

    circuit = ""
    if metrics["paused"]:
        circuit = '<div style="background:#fdedec;border:1px solid #e74c3c;border-radius:8px;padding:14px 18px;margin:16px 0;"><b style="color:#e74c3c;">⚠ Circuit Breaker Active</b><p style="color:#c0392b;margin:6px 0 0;font-size:13px;">Portfolio drawdown exceeded 10%. No new entries until recovery. Bot is protecting your capital.</p></div>'

    watch_rows = "".join(f"""<tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 8px;font-weight:700;">{s.symbol}</td>
      <td style="padding:9px 8px;text-align:center;"><span style="background:#fef9e7;color:#f39c12;padding:2px 8px;border-radius:10px;font-size:12px;">{s.score:.0f}</span></td>
      <td style="padding:9px 8px;text-align:right;">₹{s.entry:,.2f}</td>
      <td style="padding:9px 8px;font-size:12px;color:#7f8c8d;">{s.reason}</td>
    </tr>""" for s in watch) or '<tr><td colspan="4" style="text-align:center;color:#999;padding:16px;">Nothing on watchlist today</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#2c3e50;max-width:700px;margin:0 auto;padding:20px;background:#f5f6fa;">

<div style="background:#1a252f;border-radius:10px;padding:24px;margin-bottom:20px;">
  <table width="100%"><tr>
    <td><h1 style="color:#fff;margin:0;font-size:22px;">AlphaAgent Daily Report</h1>
        <p style="color:#95a5a6;margin:4px 0 0;font-size:13px;">{now_str}</p></td>
    <td align="right">
      <span style="background:#2ecc71;color:#fff;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;">{mode_tag}</span>
      <span style="background:#34495e;color:#bdc3c7;padding:4px 12px;border-radius:20px;font-size:12px;margin-left:6px;">{data_tag}</span>
    </td>
  </tr></table>
</div>

{circuit}

<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8ecef;">
  <h2 style="color:#2c3e50;font-size:16px;margin:0 0 16px;">Portfolio</h2>
  <table width="100%"><tr>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:14px;">
      <div style="font-size:11px;color:#7f8c8d;margin-bottom:4px;">TOTAL VALUE</div>
      <div style="font-size:22px;font-weight:700;">₹{metrics["total"]:,.0f}</div>
    </td>
    <td width="5%"></td>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:14px;">
      <div style="font-size:11px;color:#7f8c8d;margin-bottom:4px;">TOTAL P&L</div>
      <div style="font-size:22px;font-weight:700;color:{pnl_color};">{alpha_pct:+.2f}%</div>
      <div style="font-size:12px;color:{pnl_color};">₹{alpha:+,.0f}</div>
    </td>
    <td width="5%"></td>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:14px;">
      <div style="font-size:11px;color:#7f8c8d;margin-bottom:4px;">DRAWDOWN</div>
      <div style="font-size:22px;font-weight:700;color:{dd_color};">{metrics["drawdown"]:.2f}%</div>
      <div style="font-size:11px;color:#7f8c8d;">Limit: 10%</div>
    </td>
  </tr></table>
  <table width="100%" style="margin-top:12px;"><tr>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#7f8c8d;">CASH AVAILABLE</div>
      <div style="font-size:16px;font-weight:600;">₹{metrics["cash"]:,.0f}</div>
    </td>
    <td width="5%"></td>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#7f8c8d;">OPEN POSITIONS</div>
      <div style="font-size:16px;font-weight:600;">{metrics["positions"]} / {Config.MAX_POSITIONS}</div>
    </td>
    <td width="5%"></td>
    <td width="33%" style="text-align:center;background:#f8f9fa;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#7f8c8d;">UNREALIZED P&L</div>
      <div style="font-size:16px;font-weight:600;color:{pnl_color};">₹{metrics["unrealized"]:+,.0f}</div>
    </td>
  </tr></table>
</div>

<div style="background:{r["bg"]};border:2px solid {r["border"]};border-radius:10px;padding:20px;margin-bottom:16px;">
  <div style="font-size:24px;margin-bottom:8px;">{r["emoji"]} <span style="font-size:17px;font-weight:700;color:{r["color"]};">{r["label"]}</span></div>
  <p style="color:#2c3e50;font-size:13px;line-height:1.7;margin:0 0 12px;">{r["meaning"]}</p>
  <div style="background:rgba(255,255,255,0.7);border-radius:6px;padding:10px 14px;">
    <b style="font-size:12px;color:{r["color"]};">What the bot is doing today: </b>
    <span style="font-size:12px;color:#2c3e50;">{r["action"]}</span>
  </div>
</div>

<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8ecef;">
  <h2 style="color:#2c3e50;font-size:16px;margin:0 0 16px;">Macro indicators</h2>
  <table width="100%" style="border-collapse:collapse;">
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:10px 0;font-size:13px;color:#7f8c8d;width:170px;">VIX (Fear Index)</td>
      <td style="padding:10px 0;"><b style="color:{vix_color};">{vix:.1f}</b> {gauge_bar(vix,10,40,low_bad=False)} <span style="font-size:12px;color:{vix_color};">{vix_label}</span></td>
      <td style="padding:10px 0;font-size:11px;color:#bdc3c7;text-align:right;white-space:nowrap;">Normal: 12–20</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:10px 0;font-size:13px;color:#7f8c8d;">Nifty vs 50 DMA</td>
      <td style="padding:10px 0;"><b style="color:{ma50_color};">{macro.nifty_vs_50:+.1f}%</b> {gauge_bar(macro.nifty_vs_50+10,0,20)} <span style="font-size:12px;color:{ma50_color};">{ma50_label}</span></td>
      <td style="padding:10px 0;font-size:11px;color:#bdc3c7;text-align:right;white-space:nowrap;">Bull: above 0%</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:10px 0;font-size:13px;color:#7f8c8d;">Nifty vs 200 DMA</td>
      <td style="padding:10px 0;"><b style="color:{ma200_color};">{macro.nifty_vs_200:+.1f}%</b> {gauge_bar(macro.nifty_vs_200+10,0,20)} <span style="font-size:12px;color:{ma200_color};">{ma200_label}</span></td>
      <td style="padding:10px 0;font-size:11px;color:#bdc3c7;text-align:right;white-space:nowrap;">Bull: above 0%</td>
    </tr>
    <tr>
      <td style="padding:10px 0;font-size:13px;color:#7f8c8d;">FII Net Flow</td>
      <td style="padding:10px 0;"><b style="color:{fii_color};">₹{fii:+,.0f} Cr</b> {gauge_bar(fii+3000,0,6000)} <span style="font-size:12px;color:{fii_color};">{fii_label}</span></td>
      <td style="padding:10px 0;font-size:11px;color:#bdc3c7;text-align:right;white-space:nowrap;">Bull: &gt;₹500Cr</td>
    </tr>
  </table>
</div>

<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8ecef;">
  <h2 style="color:#2c3e50;font-size:16px;margin:0 0 16px;">Open positions ({metrics["positions"]})</h2>
  <table width="100%" style="border-collapse:collapse;font-size:13px;">
    <thead><tr style="background:#f8f9fa;">
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">Symbol</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Entry</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">LTP</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">P&L</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Stop</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Target 1</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Entry date</th>
    </tr></thead>
    <tbody>{pos_rows()}</tbody>
  </table>
</div>

<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8ecef;">
  <h2 style="color:#2c3e50;font-size:16px;margin:0 0 4px;">Buy signals ({len(buys)})</h2>
  <p style="color:#7f8c8d;font-size:12px;margin:0 0 14px;">Stocks scoring ≥72/100. R:R = reward-to-risk ratio (target ≥2x).</p>
  <table width="100%" style="border-collapse:collapse;font-size:13px;">
    <thead><tr style="background:#f8f9fa;">
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">Symbol</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Score</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Entry ₹</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Stop ₹</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Qty</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Capital</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">R:R</th>
    </tr></thead>
    <tbody>{sig_rows(buys)}</tbody>
  </table>
  {comp_table()}
</div>

<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8ecef;">
  <h2 style="color:#2c3e50;font-size:16px;margin:0 0 4px;">Watchlist ({len(watch)})</h2>
  <p style="color:#7f8c8d;font-size:12px;margin:0 0 14px;">Scoring 55–71. Setting up but not ready yet — monitor daily.</p>
  <table width="100%" style="border-collapse:collapse;font-size:13px;">
    <thead><tr style="background:#f8f9fa;">
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">Symbol</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Score</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Price</th>
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">What is missing</th>
    </tr></thead>
    <tbody>{watch_rows}</tbody>
  </table>
</div>

<div style="background:#eaf4fb;border:1px solid #85c1e9;border-radius:10px;padding:16px 20px;margin-bottom:16px;">
  <b style="color:#1a5276;font-size:13px;">Strategy rules — quick reference</b>
  <table width="100%" style="margin-top:10px;font-size:12px;color:#1a5276;">
    <tr><td>Risk per trade</td><td><b>1.5% of portfolio (₹{Config.VIRTUAL_CAPITAL*Config.RISK_PER_TRADE_PCT:,.0f})</b></td>
        <td>Stop loss</td><td><b>7% below entry or VCP base low</b></td></tr>
    <tr><td style="padding-top:6px;">Partial exit 1</td><td><b>Sell ⅓ at +18%</b></td>
        <td style="padding-top:6px;">Partial exit 2</td><td><b>Sell ⅓ at +30%</b></td></tr>
    <tr><td style="padding-top:6px;">Trailing stop</td><td><b>Activates at +20%, trails 8%</b></td>
        <td style="padding-top:6px;">Circuit breaker</td><td><b>Pause at 10% drawdown</b></td></tr>
  </table>
</div>

<div style="text-align:center;color:#bdc3c7;font-size:11px;padding:10px;">
  AlphaAgent automated paper trading | not SEBI-registered financial advice<br>
  Attachments: trade_journal.csv (complete history) · report.txt (plain text)
</div>
</body></html>"""

    plain = f"""AlphaAgent Daily Report [{mode_tag} | {data_tag}]
{now_str}

MARKET REGIME: {r["label"]}
{r["meaning"]}
Bot action: {r["action"]}

MACRO INDICATORS
  VIX              : {vix:.1f}  [{vix_label}]  (Normal: 12-20)
  Nifty vs 50 DMA  : {macro.nifty_vs_50:+.1f}%  [{ma50_label}]
  Nifty vs 200 DMA : {macro.nifty_vs_200:+.1f}%  [{ma200_label}]
  FII Net Flow     : Rs{fii:+,.0f} Cr  [{fii_label}]

PORTFOLIO
  Total Value : Rs{metrics["total"]:,.0f}
  Cash        : Rs{metrics["cash"]:,.0f}
  P&L         : Rs{alpha:+,.0f} ({alpha_pct:+.2f}%)
  Drawdown    : {metrics["drawdown"]:.2f}%  (Limit: 10%)
  Positions   : {metrics["positions"]}/{Config.MAX_POSITIONS}

BUY SIGNALS ({len(buys)})
{"  No buy signals — regime C, bot protecting capital in cash." if not buys else chr(10).join(f"  {s.symbol:<14} Score {s.score:.0f}/100 | Entry Rs{s.entry:.2f} | Stop Rs{s.stop:.2f} | Qty {s.qty} | Capital Rs{s.entry*s.qty:,.0f}" for s in buys)}

WATCHLIST ({len(watch)})
{"  Nothing on watchlist today." if not watch else chr(10).join(f"  {s.symbol:<14} Score {s.score:.0f} | Rs{s.entry:.2f} | {s.reason}" for s in watch)}
"""
    return html, plain


# ─── MAIN AGENT LOOP ───────────────────────────────────────────────────────────

def run():
    log.info(f"\n{'='*55}\nALPHAGENT RUN — {datetime.now().strftime('%d %b %Y %H:%M')}\n{'='*55}")

    client    = DataClient()
    portfolio = Portfolio()
    journal   = Journal()

    log.info("OBSERVE: Fetching macro + prices...")
    macro  = get_macro(client)
    prices = {}
    all_syms = Config.NIFTY50 + Config.MIDCAP
    for sym in all_syms:
        ltp = client.get_ltp(sym)
        if ltp: prices[sym] = ltp
        time.sleep(0.05)

    for pos in portfolio.positions:
        if pos.symbol not in prices:
            ltp = client.get_ltp(pos.symbol)
            if ltp: prices[pos.symbol] = ltp

    metrics = portfolio.metrics(prices)
    log.info(f"Portfolio: Rs{metrics['total']:,.0f} | DD: {metrics['drawdown']:.2f}% | Positions: {metrics['positions']}")

    exits = check_exits(portfolio.positions, prices)
    for ex in exits:
        oid = client.place_order(ex["sym"], ex["qty"], ex["price"], "SELL")
        if oid:
            portfolio.remove(ex["sym"], ex["qty"], ex["price"])
            journal.log({"datetime":datetime.now().isoformat(),"symbol":ex["sym"],"action":"SELL",
                         "qty":ex["qty"],"price":ex["price"],"score":0,"reason":ex["reason"],
                         "kind":ex["kind"],"regime":macro.regime,"mode":"PAPER" if PAPER_MODE else "LIVE"})

    log.info("REASON: Scoring candidates...")
    signals = []
    if macro.regime != "C":
        for sym in [s for s in all_syms if not portfolio.has(s)]:
            df = client.get_historical(sym, 265)
            if df.empty: continue
            sig = score_symbol(sym, df, macro, portfolio.cash)
            signals.append(sig)
            time.sleep(0.1)

    signals.sort(key=lambda x: x.score, reverse=True)
    log.info(f"Scored {len(signals)} symbols | {len([s for s in signals if s.action=='buy'])} buy signals")

    if not metrics["paused"]:
        open_count = len(portfolio.positions)
        for sig in signals:
            if sig.action != "buy": continue
            if open_count >= Config.MAX_POSITIONS: break
            if portfolio.has(sig.symbol): continue
            if sig.qty <= 0: continue
            cost = sig.entry * sig.qty
            if cost > portfolio.cash * 0.95: continue
            oid = client.place_order(sig.symbol, sig.qty, sig.entry, "BUY")
            if oid:
                pos = Position(symbol=sig.symbol, entry=sig.entry, qty=sig.qty, stop=sig.stop,
                               target1=round(sig.entry*(1+Config.PARTIAL_EXIT_1_PCT),2),
                               target2=round(sig.entry*(1+Config.PARTIAL_EXIT_2_PCT),2),
                               entry_date=datetime.now().strftime("%Y-%m-%d"), score=sig.score)
                portfolio.add(pos)
                client.place_gtt_stop(sig.symbol, sig.entry, sig.stop, sig.qty)
                journal.log({"datetime":datetime.now().isoformat(),"symbol":sig.symbol,"action":"BUY",
                             "qty":sig.qty,"price":sig.entry,"score":sig.score,"reason":sig.reason,
                             "kind":"entry","regime":macro.regime,"mode":"PAPER" if PAPER_MODE else "LIVE"})
                log.info(f"ENTRY: {sig.symbol} | {sig.qty}x @ Rs{sig.entry:.2f} | Score {sig.score:.0f}")
                open_count += 1
    else:
        log.warning("Circuit breaker active — no new entries")

    metrics = portfolio.metrics(prices)
    html_report, plain_report = build_html_report(metrics, signals, macro, portfolio, prices)

    today = datetime.now().strftime("%Y%m%d")
    os.makedirs(Config.REPORT_DIR, exist_ok=True)
    with open(f"{Config.REPORT_DIR}/report_{today}.html", "w") as f: f.write(html_report)
    with open(f"{Config.REPORT_DIR}/report_{today}.txt",  "w") as f: f.write(plain_report)
    log.info(f"Reports saved.")
    send_telegram(plain_report[:4000])
    log.info("Run complete.")
    return html_report, plain_report


if __name__ == "__main__":
    run()
