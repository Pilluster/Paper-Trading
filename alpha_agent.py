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


# ─── DAILY REPORT ──────────────────────────────────────────────────────────────

def build_report(metrics: dict, signals: list[Signal],
                 macro: MacroState, portfolio: Portfolio,
                 prices: dict) -> str:

    buys  = sorted([s for s in signals if s.action=="buy"],
                   key=lambda x: x.score, reverse=True)[:6]
    watch = sorted([s for s in signals if s.action=="watch"],
                   key=lambda x: x.score, reverse=True)[:5]

    mode_tag = "PAPER" if PAPER_MODE else "LIVE"
    data_tag = "yfinance" if not USE_ANGEL_ONE else "AngelOne"
    alpha    = round(metrics["total"] - Config.VIRTUAL_CAPITAL, 2)
    alpha_pct= round((metrics["total"] / Config.VIRTUAL_CAPITAL - 1) * 100, 2)

    lines = [
        f"<b>AlphaAgent Daily Report</b> [{mode_tag} | {data_tag}]",
        f"{datetime.now().strftime('%d %b %Y, %I:%M %p IST')}",
        "",
        f"<b>Macro</b>: Regime {macro.regime} | VIX {macro.vix} | FII ₹{macro.fii_flow:+.0f}Cr",
        f"Nifty vs 50DMA: {macro.nifty_vs_50:+.1f}% | vs 200DMA: {macro.nifty_vs_200:+.1f}%",
        "",
        f"<b>Portfolio</b>",
        f"  Value    : ₹{metrics['total']:>10,.0f}",
        f"  Cash     : ₹{metrics['cash']:>10,.0f}",
        f"  P&L      : ₹{alpha:>+10,.0f} ({alpha_pct:+.2f}%)",
        f"  Drawdown : {metrics['drawdown']:+.2f}%",
        f"  Positions: {metrics['positions']}/{Config.MAX_POSITIONS}",
    ]

    if metrics["paused"]:
        lines += ["", "⚠️ CIRCUIT BREAKER: Drawdown > 10%. No new entries."]

    if portfolio.positions:
        lines += ["", "<b>Open Positions</b>"]
        for p in portfolio.positions:
            ltp = prices.get(p.symbol, p.entry)
            pnl_pct = (ltp - p.entry) / p.entry * 100
            lines.append(f"  {p.symbol:<14} ₹{ltp:.2f} | Entry ₹{p.entry:.2f} | "
                         f"{pnl_pct:+.1f}% | Stop ₹{p.live_stop:.2f}")

    if buys:
        lines += ["", "<b>New Buy Signals</b>"]
        for s in buys:
            cost = s.entry * s.qty
            lines.append(f"  {s.symbol:<14} Score {s.score:.0f}/100 | "
                         f"₹{s.entry:.2f} | Qty {s.qty} | "
                         f"Cost ₹{cost:,.0f} | Stop ₹{s.stop:.2f}")

    if watch:
        lines += ["", "<b>Watchlist</b> (score 55–71)"]
        for s in watch:
            lines.append(f"  {s.symbol:<14} Score {s.score:.0f}")

    return "\n".join(lines)


# ─── MAIN AGENT LOOP ───────────────────────────────────────────────────────────

def run():
    log.info(f"\n{'='*55}\nALPHAGENT RUN — {datetime.now().strftime('%d %b %Y %H:%M')}\n{'='*55}")

    client    = DataClient()
    portfolio = Portfolio()
    journal   = Journal()

    # ── OBSERVE ──────────────────────────────────────────────────────────────
    log.info("OBSERVE: Fetching macro + prices...")
    macro  = get_macro(client)
    prices = {}
    all_syms = Config.NIFTY50 + Config.MIDCAP
    for sym in all_syms:
        ltp = client.get_ltp(sym)
        if ltp: prices[sym] = ltp
        time.sleep(0.05)

    # Update position prices
    for pos in portfolio.positions:
        if pos.symbol not in prices:
            ltp = client.get_ltp(pos.symbol)
            if ltp: prices[pos.symbol] = ltp

    metrics = portfolio.metrics(prices)
    log.info(f"Portfolio: ₹{metrics['total']:,.0f} | "
             f"DD: {metrics['drawdown']:.2f}% | "
             f"Positions: {metrics['positions']}")

    # ── MANAGE EXISTING POSITIONS ─────────────────────────────────────────────
    exits = check_exits(portfolio.positions, prices)
    for ex in exits:
        oid = client.place_order(ex["sym"], ex["qty"], ex["price"], "SELL")
        if oid:
            portfolio.remove(ex["sym"], ex["qty"], ex["price"])
            journal.log({
                "datetime": datetime.now().isoformat(),
                "symbol": ex["sym"], "action": "SELL",
                "qty": ex["qty"], "price": ex["price"],
                "score": 0, "reason": ex["reason"],
                "kind": ex["kind"], "regime": macro.regime,
                "mode": "PAPER" if PAPER_MODE else "LIVE"
            })

    # ── REASON: SCORE ALL CANDIDATES ──────────────────────────────────────────
    log.info("REASON: Scoring candidates...")
    signals: list[Signal] = []
    if macro.regime != "C":
        syms_to_score = [s for s in all_syms if not portfolio.has(s)]
        for sym in syms_to_score:
            df = client.get_historical(sym, 265)
            if df.empty: continue
            sig = score_symbol(sym, df, macro, portfolio.cash)
            signals.append(sig)
            time.sleep(0.1)

    signals.sort(key=lambda x: x.score, reverse=True)
    buy_count = len([s for s in signals if s.action=="buy"])
    log.info(f"Scored {len(signals)} symbols | {buy_count} buy signals")

    # ── ACT: PLACE NEW ENTRIES ────────────────────────────────────────────────
    if not metrics["paused"]:
        open_count = len(portfolio.positions)
        for sig in signals:
            if sig.action != "buy": continue
            if open_count >= Config.MAX_POSITIONS: break
            if portfolio.has(sig.symbol): continue
            if sig.qty <= 0: continue
            cost = sig.entry * sig.qty
            if cost > portfolio.cash * 0.95:
                log.info(f"Skip {sig.symbol}: need ₹{cost:,.0f}, have ₹{portfolio.cash:,.0f}")
                continue

            oid = client.place_order(sig.symbol, sig.qty, sig.entry, "BUY")
            if oid:
                pos = Position(
                    symbol=sig.symbol, entry=sig.entry, qty=sig.qty,
                    stop=sig.stop,
                    target1=round(sig.entry * (1 + Config.PARTIAL_EXIT_1_PCT), 2),
                    target2=round(sig.entry * (1 + Config.PARTIAL_EXIT_2_PCT), 2),
                    entry_date=datetime.now().strftime("%Y-%m-%d"),
                    score=sig.score
                )
                portfolio.add(pos)
                client.place_gtt_stop(sig.symbol, sig.entry, sig.stop, sig.qty)
                journal.log({
                    "datetime": datetime.now().isoformat(),
                    "symbol": sig.symbol, "action": "BUY",
                    "qty": sig.qty, "price": sig.entry,
                    "score": sig.score, "reason": sig.reason,
                    "kind": "entry", "regime": macro.regime,
                    "mode": "PAPER" if PAPER_MODE else "LIVE"
                })
                log.info(f"ENTRY: {sig.symbol} | {sig.qty}x @ ₹{sig.entry:.2f} | "
                         f"Score {sig.score:.0f} | Stop ₹{sig.stop:.2f}")
                open_count += 1
    else:
        log.warning("Circuit breaker active — no new entries")

    # ── LEARN: REPORT + NOTIFY ────────────────────────────────────────────────
    metrics = portfolio.metrics(prices)
    report  = build_report(metrics, signals, macro, portfolio, prices)

    today   = datetime.now().strftime("%Y%m%d")
    rpath   = f"{Config.REPORT_DIR}/report_{today}.txt"
    with open(rpath, "w") as f:
        # Strip HTML tags for plain text file
        plain = report.replace("<b>","").replace("</b>","")
        f.write(plain)
    log.info(f"Report saved: {rpath}")

    send_telegram(report)
    log.info("Run complete.")
    return report


if __name__ == "__main__":
    run()
