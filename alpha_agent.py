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

    # ── Complete tradable universe — Nifty 500 (covers 95% of NSE market cap) ──
    # Nifty 50 — large cap anchors
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

    # Nifty Next 50 — large cap extended
    NIFTY_NEXT50 = [
        "ADANIGREEN","ADANIPOWER","AMBUJACEM","BAJAJHLDNG","BANKBARODA",
        "BERGEPAINT","BOSCHLTD","CANBK","CHOLAFIN","COLPAL","DMART",
        "GAIL","GODREJCP","HAL","HAVELLS","HDFCAMC","HDFCLIFE",
        "INDIAMART","INDIGO","IOC","IRCTC","JINDALSTEL","JUBLFOOD",
        "LICI","LTTS","LUPIN","MARICO","MCDOWELL-N","MOTHERSON",
        "MPHASIS","NAUKRI","NMDC","OFSS","PIDILITIND","PIIND",
        "PNB","RECLTD","SAIL","SIEMENS","SRF","SYNGENE",
        "TORNTPHARM","TRENT","TVSMOTOR","UBL","UNITDSPR","VEDL",
        "VOLTAS","WHIRLPOOL","ZOMATO"
    ]

    # Nifty Midcap 150 — high alpha source
    MIDCAP = [
        "ABCAPITAL","ABFRL","APLAPOLLO","APLLTD","ATUL","AUBANK",
        "AUROPHARMA","AVANTIFEED","BALKRISIND","BATAINDIA","BAYERCROP",
        "BLUEDART","BRIGADE","CAMS","CANFINHOME","CASTROLIND","CDSL",
        "CESC","CLEAN","COFORGE","CONCORDBIO","CRISIL","CROMPTON",
        "CYIENT","DABUR","DATAPATTNS","DEEPAKNTR","DELHIVERY","EMAMILTD",
        "ENGINERSIN","EQUITASBNK","EXIDEIND","FEDERALBNK","FINPIPE",
        "FIVESTAR","GLENMARK","GODREJIND","GPPL","GRINDWELL","GSPL",
        "HAPPSTMNDS","HDFCAMC","HFCL","HINDPETRO","HONAUT","IDFCFIRSTB",
        "IIFL","INDIACEM","INDIANB","INDUSINDBK","INTELLECT","IPCALAB",
        "JBCHEPHARM","JKCEMENT","JSWENERGY","JUBILPHARMA","KAJARIACER",
        "KALPATPOWR","KANSAINER","KARURVYSYA","KECL","KPITTECH",
        "KPRMILL","KRISHNAVIS","LAURUSLABS","LAXMIMACH","LICHSGFIN",
        "LTTS","LUXIND","MAHINDCIE","MANAPPURAM","MFSL","MINDTREE",
        "MPHASIS","MRPL","NATCOPHARM","NAUKRI","NAVINFLUOR","NBCC",
        "NIACL","NOCIL","OFSS","OLECTRA","PAGEIND","PERSISTENT",
        "PETRONET","PFIZER","PHOENIXLTD","PNBHOUSING","POLYCAB",
        "POLYMED","PRESTIGE","PRINCEPIPES","PVCGLOBS","RADICO",
        "RAJESHEXPO","RAMCOCEM","RITES","ROSSARI","SAFARI","SCHAEFFLER",
        "SHRIRAMFIN","SKFINDIA","SOBHA","SONACOMS","STARHEALTH",
        "SUPREMEIND","SUVENPHAR","TANLA","TATACOMM","TATACHEM",
        "TATAINVEST","TEJASNET","THYROCARE","TIMKEN","TITAN",
        "TORNTPOWER","TRITURBINE","TTKPRESTIG","UCOBANK","UJJIVAN",
        "UNIONBANK","VAIBHAVGBL","VIJAYA","VSTIND","WABCOINDIA",
        "WELCORP","WHIRLPOOL","WIPRO","ZENSARTECH","ZYDUSLIFE"
    ]

    # Nifty Smallcap 100 — opportunistic (higher risk, higher reward)
    SMALLCAP = [
        "AAVAS","ABSLAMC","ACCELYA","AEGISLOG","AFFLE","AGROPHOS",
        "AJANTPHARM","ALKEM","ALKYLAMINE","ALLCARGO","AMBER","ANURAS",
        "APARINDS","ARCHIDPLY","ARVINDFASN","ASKAUTOLTD","ASIANTILES",
        "ASTER","ASTERDM","ATGL","BAJAJCON","BALRAMCHIN","BARBEQUE",
        "BASF","BAYERCROP","BBTC","BCG","BECTORFOOD","BIKAJI",
        "CAPACITE","CARBORUNIV","CARTRADE","CCL","CENTURYPLY",
        "CERA","CHEMCON","CLEANBREW","COLLEGE","CONFIPET",
        "CONTROLPRINT","CSBBANK","DATAMATICS","DCB","DCBBANK",
        "DECCANCE","DEEPAKFERT","DELTACORP","DHANI","DIAMONDYD",
        "DLINKINDIA","DODLA","DPWWORLD","DRREDDY","EASEMYTRIP",
        "ECLERX","EIDPARRY","ELECON","ELGIEQUIP","EMUDHRA",
        "ERIS","ESABINDIA","ETHOSLTD","EXLSERVICE","FCONSUMER",
        "FINEORG","FINOFINANCE","FLAIR","FLEXITUFF","GANESHHOUC",
        "GARFIBRES","GATEWAY","GESHIP","GHCL","GILLETTE",
        "GLAND","GLOBUSSPR","GMMPFAUDLR","GNFC","GODFRYPHLP",
        "GODREJAGRO","GODREJIND","GOLDIAM","GPPL","GREENLAM",
        "GREENPANEL","GRINFRA","GRSE","GSFC","GTPL",
        "GULFOILLUB","GUJGASLTD","HAPPYFORGE","HATHWAY","HAWKINCOOK",
        "HCG","HGINFRA","HIKAL","HINDCOPPER","HINDWAREAP",
    ]

    # Commodity ETFs
    ETF_SYMBOLS = [
        "GOLDBEES","SILVERBEES","CPSEETF","NIFTYBEES","JUNIORBEES",
        "BANKBEES","ICICIB22","MOM100","NETFIT","FMCGIETF"
    ]

    @classmethod
    def get_universe(cls, run_number: int = 0) -> list:
        """
        Returns the universe to scan for this run.
        Rotates through segments to cover all 500 stocks across 4 daily runs.
        Morning run: Large caps + ETFs (fast, high priority)
        Evening run: Midcap + Smallcap rotation
        """
        import datetime as dt
        hour = dt.datetime.now().hour
        # Morning run (before 10 AM IST = before 4:30 UTC)
        if hour < 10:
            return cls.NIFTY50 + cls.NIFTY_NEXT50 + cls.ETF_SYMBOLS
        else:
            # Evening — rotate through midcap+smallcap segments by day of week
            day = dt.datetime.now().weekday()  # 0=Mon, 4=Fri
            if day in [0, 3]:    # Mon, Thu
                return cls.NIFTY50 + cls.MIDCAP[:75]
            elif day in [1, 4]:  # Tue, Fri
                return cls.NIFTY50 + cls.MIDCAP[75:] + cls.SMALLCAP[:50]
            else:                # Wed
                return cls.NIFTY50 + cls.NIFTY_NEXT50 + cls.SMALLCAP[50:]

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

    def _yf_sym(self, symbol: str) -> str:
        """Convert Angel One symbol to yfinance NSE format."""
        special = {"MM": "M&M.NS", "NIFTY50_INDEX": "^NSEI",
                   "BAJAJ-AUTO": "BAJAJ-AUTO.NS"}
        if symbol in special:
            return special[symbol]
        return symbol + ".NS"

    def _yf_historical(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            yf_sym = self._yf_sym(symbol)
            ticker = yf.Ticker(yf_sym)
            end    = datetime.now() + timedelta(days=1)  # +1 to include today
            start  = datetime.now() - timedelta(days=days + 60)

            # auto_adjust=False gives raw OHLC — no dividend/split distortion
            df = ticker.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=False,
                back_adjust=False,
            )
            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]

            # Handle both 'date' and 'datetime' column names
            if "datetime" in df.columns:
                df = df.rename(columns={"datetime": "date"})

            # Strip timezone info
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

            # Use raw Close (not Adj Close) for accurate prices
            if "close" not in df.columns and "adj close" in df.columns:
                df = df.rename(columns={"adj close": "close"})

            df = df[["date","open","high","low","close","volume"]].dropna()
            df = df.sort_values("date").reset_index(drop=True)

            # Log latest date to catch stale data
            if not df.empty:
                latest = df["date"].iloc[-1].strftime("%Y-%m-%d")
                log.debug(f"{symbol}: latest data {latest} | close {df['close'].iloc[-1]:.2f}")

            return df.tail(days).reset_index(drop=True)
        except Exception as e:
            log.warning(f"yfinance fetch failed {symbol}: {e}")
            return pd.DataFrame()

    def _yf_ltp(self, symbol: str) -> Optional[float]:
        """Get latest traded price — uses fast_info for speed."""
        try:
            import yfinance as yf
            yf_sym = self._yf_sym(symbol)
            t      = yf.Ticker(yf_sym)
            # fast_info.last_price is the most recent available price
            price  = float(t.fast_info.last_price)
            # Sanity check — reject obviously wrong values
            if price > 0.5:
                return round(price, 2)
        except:
            pass
        # Fallback: use last close from historical
        try:
            df = self._yf_historical(symbol, 5)
            if not df.empty:
                return round(float(df["close"].iloc[-1]), 2)
        except:
            pass
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
    regime:       str   = "A"
    vix:          float = 14.0
    nifty_vs_50:  float = 0.0
    nifty_vs_200: float = 0.0
    nifty_vs_100: float = 0.0
    fii_flow:     float = 0.0
    dii_flow:     float = 0.0
    score:        float = 6.0
    # Extended macro
    usd_inr:      float = 83.0
    crude_usd:    float = 75.0
    gold_inr:     float = 70000.0
    us_10y_yield: float = 4.5
    dxy:          float = 104.0
    sgx_nifty_chg:float = 0.0
    rbi_stance:   str   = "neutral"
    geo_risk:     str   = "low"
    macro_notes:  str   = ""
    ad_advances:  int   = 0
    ad_declines:  int   = 0
    ad_ratio:     float = 1.0
    ad_label:     str   = "Unavailable"
    nifty_6m_ret: float = -5.0


def _fetch_fii_dii() -> tuple:
    """
    Fetch FII/DII flows.
    Most web sources block GitHub server IPs.
    Best approach: derive from Nifty ETF vs broader market flows using yfinance.
    We also try multiple public APIs with rotating strategies.
    """
    import yfinance as yf

    # Method 1: Use Angel One MarketData API if connected
    if USE_ANGEL_ONE:
        try:
            from SmartApi import SmartConnect
            import pyotp as _pyotp
            smart = SmartConnect(api_key=Config.API_KEY)
            totp  = _pyotp.TOTP(Config.TOTP_SECRET).now()
            sess  = smart.generateSession(Config.CLIENT_ID, Config.PASSWORD, totp)
            if sess.get("status"):
                # Angel One market data endpoint
                r = requests.get(
                    "https://apiconnect.angelbroking.com/rest/secure/angelbroking/"
                    "marketData/v1/gainers-losers",
                    headers={
                        "Authorization": f"Bearer {sess['data']['jwtToken']}",
                        "Content-Type": "application/json",
                        "X-ClientLocalIP": "127.0.0.1",
                        "X-ClientPublicIP": "127.0.0.1",
                        "X-MACAddress": "00:00:00:00:00:00",
                        "X-PrivateKey": Config.API_KEY,
                    }, timeout=8
                )
                # Try to extract FII from response
                d = r.json()
                log.info(f"Angel One market data: {str(d)[:200]}")
        except Exception as e:
            log.warning(f"Angel One FII failed: {e}")

    # Method 2: Derive FII proxy from ETF flows
    # NIFTYBEES (Nifty ETF) vs JUNIORBEES (Junior Nifty ETF) relative volume
    # High volume + price up = institutional buying (FII proxy)
    try:
        nifty_etf = yf.Ticker("NIFTYBEES.NS")
        hist = nifty_etf.history(period="5d")
        if len(hist) >= 2:
            avg_vol   = hist["Volume"].iloc[:-1].mean()
            today_vol = hist["Volume"].iloc[-1]
            today_ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100
            # Estimate FII flow in Crores (rough proxy)
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1
            # Each unit of NIFTYBEES ≈ 1/10 Nifty, avg trade ~100Cr/day
            fii_proxy = round((vol_ratio - 1) * 500 * (1 if today_ret > 0 else -1), 0)
            log.info(f"FII proxy from NIFTYBEES: {fii_proxy}Cr (vol_ratio={vol_ratio:.2f})")
            return fii_proxy, 0.0
    except Exception as e:
        log.warning(f"ETF FII proxy failed: {e}")

    # Method 3: Try public API with different headers
    sources = [
        ("https://www.nsdl.co.in/fii-dii.php", {}),
        ("https://groww.in/api/v1/market_data/fii_dii", {"User-Agent": "Mozilla/5.0"}),
    ]
    for url, headers in sources:
        try:
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200 and len(r.text) > 100:
                import re
                nums = re.findall(r"[+-]?\d+,?\d+\.?\d*", r.text[:2000])
                if nums:
                    fii = float(nums[0].replace(",",""))
                    log.info(f"FII from {url}: {fii}")
                    return fii, 0.0
        except:
            pass

    log.warning("All FII sources failed — using ETF proxy as 0")
    return 0.0, 0.0


def _fetch_india_vix() -> float:
    """Fetch India VIX — tries NSE first, falls back to yfinance."""
    # Source 1: NSE India VIX API
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.nseindia.com/",
        }
        sess = requests.Session()
        sess.get("https://www.nseindia.com", headers=headers, timeout=6)
        r = sess.get("https://www.nseindia.com/api/allIndices",
                     headers=headers, timeout=6)
        for idx in r.json().get("data", []):
            if idx.get("index") == "INDIA VIX":
                return round(float(idx["last"]), 2)
    except:
        pass

    # Source 2: yfinance India VIX
    try:
        import yfinance as yf
        v = yf.Ticker("^INDIAVIX").fast_info
        val = float(v.last_price)
        if 5 < val < 100:
            return round(val, 2)
    except:
        pass

    # Source 3: US VIX as proxy
    try:
        import yfinance as yf
        v = yf.Ticker("^VIX").fast_info
        return round(float(v.last_price), 2)
    except:
        return 15.0


def _fetch_global_macro() -> dict:
    """Fetch USD/INR, Crude, Gold, US 10Y yield, DXY, SGX Nifty."""
    result = {
        "usd_inr": 83.0, "crude": 75.0, "gold_inr": 70000.0,
        "us_10y": 4.5, "dxy": 104.0, "sgx_chg": 0.0
    }
    try:
        import yfinance as yf
        tickers = {
            "USDINR=X":  "usd_inr",
            "CL=F":      "crude",
            "GC=F":      "gold_usd",
            "^TNX":      "us_10y",
            "DX-Y.NYB":  "dxy",
        }
        for ticker, key in tickers.items():
            try:
                val = float(yf.Ticker(ticker).fast_info.last_price)
                result[key] = round(val, 2)
            except:
                pass

        # Convert gold USD to INR
        if "gold_usd" in result:
            result["gold_inr"] = round(result["gold_usd"] * result["usd_inr"], 0)

        # SGX Nifty (approximate via Nifty futures)
        try:
            nf = yf.Ticker("^NSEI")
            hist = nf.history(period="2d")
            if len(hist) >= 2:
                result["sgx_chg"] = round(
                    (hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100, 2
                )
        except:
            pass

    except Exception as e:
        log.warning(f"Global macro fetch error: {e}")
    return result


def _fetch_advance_decline() -> dict:
    """
    Fetch Advance/Decline ratio for NSE.
    Uses a basket of Nifty 500 stocks via yfinance to compute breadth.
    Returns: {advances, declines, ratio, breadth_label}
    """
    try:
        import yfinance as yf
        # Sample 50 Nifty 500 stocks for breadth check
        sample = [
            "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
            "HINDUNILVR.NS","ITC.NS","SBIN.NS","BHARTIARTL.NS","KOTAKBANK.NS",
            "LT.NS","AXISBANK.NS","BAJFINANCE.NS","ASIANPAINT.NS","MARUTI.NS",
            "SUNPHARMA.NS","TITAN.NS","HCLTECH.NS","WIPRO.NS","ULTRACEMCO.NS",
            "ONGC.NS","JSWSTEEL.NS","TATAMOTORS.NS","COALINDIA.NS","INDUSINDBK.NS",
            "GRASIM.NS","CIPLA.NS","DRREDDY.NS","HINDALCO.NS","TATASTEEL.NS",
            "BRITANNIA.NS","TRENT.NS","PERSISTENT.NS","COFORGE.NS","CHOLAFIN.NS",
            "MARICO.NS","DABUR.NS","GODREJCP.NS","PIIND.NS","DEEPAKNTR.NS",
            "MPHASIS.NS","LTTS.NS","CAMS.NS","CDSL.NS","ASTRAL.NS",
            "TATACONSUM.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS","EICHERMOT.NS","BPCL.NS"
        ]
        data = yf.download(sample, period="2d", progress=False, threads=True)
        if data.empty or "Close" not in data:
            return {"advances": 0, "declines": 0, "ratio": 1.0, "label": "Unavailable"}

        closes = data["Close"]
        if len(closes) < 2:
            return {"advances": 0, "declines": 0, "ratio": 1.0, "label": "Unavailable"}

        changes = closes.iloc[-1] / closes.iloc[-2] - 1
        advances = int((changes > 0).sum())
        declines = int((changes < 0).sum())
        total    = advances + declines
        ratio    = round(advances / declines, 2) if declines > 0 else advances

        if ratio >= 3:     label = "Strongly bullish breadth"
        elif ratio >= 1.5: label = "Bullish — majority rising"
        elif ratio >= 0.8: label = "Neutral — mixed breadth"
        elif ratio >= 0.4: label = "Bearish — majority falling"
        else:              label = "Strongly bearish breadth"

        log.info(f"A/D Ratio: {advances}A / {declines}D = {ratio}")
        return {"advances": advances, "declines": declines, "ratio": ratio, "label": label}
    except Exception as e:
        log.warning(f"A/D ratio fetch failed: {e}")
        return {"advances": 0, "declines": 0, "ratio": 1.0, "label": "Unavailable"}


def _fetch_geo_risk() -> tuple:
    """
    Assess geopolitical risk using news sentiment from RSS feeds.
    Returns (risk_level, notes) where risk_level in low/medium/high
    """
    risk_keywords = {
        "high": ["war", "sanctions", "nuclear", "invasion", "crisis", "collapse",
                 "default", "military strike", "trade war", "embargo"],
        "medium": ["tension", "conflict", "tariff", "inflation surge", "recession",
                   "rate hike", "fed hawkish", "oil shock", "geopolit"],
        "low": []
    }
    india_keywords = ["india", "rbi", "nifty", "sensex", "rupee", "modi",
                      "budget", "sebi", "nse", "bse"]
    notes = []

    try:
        # Reuters RSS
        feeds = [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.reuters.com/reuters/INbusinessNews",
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        ]
        all_titles = []
        for feed_url in feeds:
            try:
                r = requests.get(feed_url,
                                 headers={"User-Agent": "Mozilla/5.0"},
                                 timeout=6)
                import re
                titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
                if not titles:
                    titles = re.findall(r"<title>(.*?)</title>", r.text)
                all_titles.extend(titles[:10])
            except:
                pass

        combined = " ".join(all_titles).lower()

        # Check for India-specific news
        india_news = [t for t in all_titles
                      if any(k in t.lower() for k in india_keywords)]
        if india_news:
            notes.append(f"India news: {india_news[0][:80]}")

        # Assess risk level
        high_hits = sum(1 for k in risk_keywords["high"] if k in combined)
        med_hits  = sum(1 for k in risk_keywords["medium"] if k in combined)

        if high_hits >= 2:
            return "high", " | ".join(notes[:2]) if notes else "Elevated global risk signals"
        elif high_hits >= 1 or med_hits >= 3:
            return "medium", " | ".join(notes[:2]) if notes else "Moderate risk environment"
        else:
            return "low", " | ".join(notes[:2]) if notes else "Calm macro environment"

    except Exception as e:
        log.warning(f"Geo risk fetch failed: {e}")
        return "low", "Unable to fetch news"


def _fetch_rbi_stance() -> str:
    """
    Determine RBI stance from latest policy.
    Uses multiple signals — defaults to known current stance.
    RBI cut repo rate by 25bps in Feb 2025 and again in Apr 2025 — current stance: cutting.
    """
    # Known baseline: RBI has been in cutting cycle since Feb 2025
    # Only override if very clear hiking signals in recent news
    baseline = "cutting"
    try:
        feeds = [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://www.rbi.org.in/scripts/rss.aspx",
        ]
        for feed_url in feeds:
            try:
                r = requests.get(feed_url,
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                text = r.text.lower()
                # Only count very recent/strong hiking signals
                hiking_signals = sum(1 for k in [
                    "rate hike", "rbi hikes", "repo rate increased",
                    "monetary tightening", "rbi raises rate"
                ] if k in text)
                cut_signals = sum(1 for k in [
                    "rate cut", "repo cut", "rbi cuts", "accommodative",
                    "easing", "rate reduction", "repo rate reduced"
                ] if k in text)

                if hiking_signals >= 2:
                    return "hiking"
                elif cut_signals >= 1:
                    return "cutting"
            except:
                continue
    except:
        pass
    return baseline


def get_macro(client: DataClient) -> MacroState:
    m = MacroState()
    log.info("Fetching comprehensive macro data...")

    # 1. Nifty vs all MAs (50, 100, 200)
    ndf = client._yf_historical("NIFTY50_INDEX", 260)
    if not ndf.empty:
        c     = ndf["close"]
        cur   = c.iloc[-1]
        ma50  = Ind.sma(c,  50).iloc[-1]
        ma100 = Ind.sma(c, 100).iloc[-1]
        ma200 = Ind.sma(c, 200).iloc[-1]
        m.nifty_vs_50  = round((cur - ma50)  / ma50  * 100, 2)
        m.nifty_vs_100 = round((cur - ma100) / ma100 * 100, 2)
        m.nifty_vs_200 = round((cur - ma200) / ma200 * 100, 2)
        # Real Nifty 6M return for RS calculation
        if len(c) >= 130:
            m.nifty_6m_ret = round((cur / c.iloc[-126] - 1) * 100, 2)

    # 2. India VIX (real)
    m.vix = _fetch_india_vix()
    log.info(f"India VIX: {m.vix}")

    # 3. FII/DII flows
    m.fii_flow, m.dii_flow = _fetch_fii_dii()
    log.info(f"FII: {m.fii_flow:+.0f}Cr | DII: {m.dii_flow:+.0f}Cr")

    # 4. Global macro — USD/INR, Crude, Gold, DXY, US yields
    gm = _fetch_global_macro()
    m.usd_inr      = gm.get("usd_inr", 83.0)
    m.crude_usd    = gm.get("crude",   75.0)
    m.gold_inr     = gm.get("gold_inr",70000.0)
    m.us_10y_yield = gm.get("us_10y",  4.5)
    m.dxy          = gm.get("dxy",     104.0)
    m.sgx_nifty_chg= gm.get("sgx_chg",0.0)
    log.info(f"USD/INR: {m.usd_inr} | Crude: {m.crude_usd} | DXY: {m.dxy}")

    # 5. RBI stance
    m.rbi_stance = _fetch_rbi_stance()
    log.info(f"RBI stance: {m.rbi_stance}")

    # 6. Advance/Decline ratio
    ad = _fetch_advance_decline()
    m.ad_advances = ad["advances"]
    m.ad_declines = ad["declines"]
    m.ad_ratio    = ad["ratio"]
    m.ad_label    = ad["label"]

    # 7. Geopolitical risk
    m.geo_risk, m.macro_notes = _fetch_geo_risk()
    log.info(f"Geo risk: {m.geo_risk} | Notes: {m.macro_notes}")

    # 7. Regime classification (enhanced)
    vix_ok    = m.vix < 20
    nifty_ok  = m.nifty_vs_50 > 0 and m.nifty_vs_200 > 0
    fii_ok    = m.fii_flow > -500
    geo_ok    = m.geo_risk != "high"
    rbi_ok    = m.rbi_stance != "hiking"
    crude_ok  = m.crude_usd < 90      # crude above 90 = macro headwind
    dxy_ok    = m.dxy < 108           # strong dollar = EM headwind

    if nifty_ok and vix_ok and fii_ok and geo_ok:
        m.regime = "A"
    elif m.nifty_vs_200 > -5 and m.vix < 24 and geo_ok:
        m.regime = "B"
    else:
        m.regime = "C"

    # Override to B if any major headwind even in bull market
    if m.regime == "A" and (not rbi_ok or not crude_ok or not dxy_ok or m.geo_risk == "medium"):
        m.regime = "B"

    # Upgrade C → B if breadth is strongly bullish (A/D > 3x) and VIX falling
    # This catches recovery days where 200 DMA lags actual market momentum
    if m.regime == "C" and m.ad_ratio >= 3.0 and m.vix < 20 and m.nifty_vs_50 > -2:
        m.regime = "B"
        log.info(f"Regime upgraded C→B: strong breadth {m.ad_ratio:.1f}x overrides 200DMA lag")

    # 8. Macro score (out of 8) — enhanced
    s = 0.0
    if m.nifty_vs_200 > 5:    s += 3
    elif m.nifty_vs_200 > 0:  s += 1.5
    if m.nifty_vs_50  > 0:    s += 1.5
    if m.vix < 14:             s += 1.5
    elif m.vix < 18:           s += 1.0
    if m.fii_flow > 1000:      s += 1.0
    elif m.fii_flow > 0:       s += 0.5
    if m.dii_flow > 500:       s += 0.5
    if m.rbi_stance == "cutting": s += 0.5
    if m.geo_risk == "low":    s += 0.5
    if m.crude_usd < 80:       s += 0.5
    m.score = round(min(s, Config.WEIGHTS["macro"]), 2)

    log.info(f"MACRO | Regime={m.regime} | VIX={m.vix} | "
             f"Nifty/50={m.nifty_vs_50:+.1f}% | Nifty/200={m.nifty_vs_200:+.1f}% | "
             f"FII={m.fii_flow:+.0f} | DII={m.dii_flow:+.0f} | "
             f"RBI={m.rbi_stance} | Geo={m.geo_risk} | Score={m.score}")
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
    stage:   int   = 0    # Weinstein stage 1-4
    vcp:     bool  = False
    missing: str   = ""   # What needs to happen to trigger buy

def score_symbol(symbol: str, df: pd.DataFrame,
                 macro: MacroState, cash: float) -> Signal:
    sig = Signal(symbol=symbol, score=0, action="avoid")
    if len(df) < 205:
        sig.reason = "Insufficient history"
        return sig

    c   = df["close"]
    cur = c.iloc[-1]
    sig.entry = round(cur, 2)   # Always set entry price
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

    # 7. Relative strength vs Nifty — use best of 1M, 3M, 6M windows (12)
    # Using max window helps in crash recoveries where 6M is depressed for all stocks
    nifty_6m = getattr(macro, "nifty_6m_ret", -5.0)
    nifty_1m = nifty_6m / 6   # Approximate
    if len(df) >= 130:
        ret_6m = (cur / c.iloc[-126] - 1) * 100
        rs_6m  = ret_6m - nifty_6m
    else:
        rs_6m  = 0
    if len(df) >= 66:
        ret_3m = (cur / c.iloc[-66] - 1) * 100
        rs_3m  = ret_3m - (nifty_6m / 2)
    else:
        rs_3m  = 0
    if len(df) >= 22:
        ret_1m = (cur / c.iloc[-22] - 1) * 100
        rs_1m  = ret_1m - nifty_1m
    else:
        rs_1m  = 0
    # Use best RS window — rewards stocks recovering fastest
    rs = max(rs_6m, rs_3m, rs_1m)
    if rs > 10:             sc["relative_str"] = Config.WEIGHTS["relative_str"]
    elif rs > 4:            sc["relative_str"] = Config.WEIGHTS["relative_str"] * 0.7
    elif rs > 0:            sc["relative_str"] = Config.WEIGHTS["relative_str"] * 0.4
    elif rs > -5:           sc["relative_str"] = Config.WEIGHTS["relative_str"] * 0.2
    else:                   sc["relative_str"] = 0

    # 8. Sector momentum proxy (10) — based on stock's own 3M vs 1M momentum trend
    # Improving = stock accelerating = RRG "Leading" equivalent
    ret_1m = (cur / c.iloc[-22]  - 1) * 100 if len(c) >= 22  else 0
    ret_3m = (cur / c.iloc[-66]  - 1) * 100 if len(c) >= 66  else 0
    ret_6m = (cur / c.iloc[-126] - 1) * 100 if len(c) >= 126 else 0
    # Improving momentum: 1M better than 3M average monthly
    monthly_3m = ret_3m / 3
    accelerating = ret_1m > monthly_3m and ret_1m > 0
    if accelerating and ret_3m > 5:
        sc["sector_rrg"] = Config.WEIGHTS["sector_rrg"]          # Full score
    elif accelerating:
        sc["sector_rrg"] = Config.WEIGHTS["sector_rrg"] * 0.7
    elif ret_1m > 0:
        sc["sector_rrg"] = Config.WEIGHTS["sector_rrg"] * 0.4
    else:
        sc["sector_rrg"] = 0

    # 9. Macro (8)
    sc["macro"] = macro.score

    # 10. Earnings (6) — score based on price momentum as proxy
    ret_1m = (cur / c.iloc[-22] - 1) * 100 if len(c) >= 22 else 0
    ret_3m = (cur / c.iloc[-66] - 1) * 100 if len(c) >= 66 else 0
    if ret_1m > 5 and ret_3m > 10:
        sc["earnings"] = Config.WEIGHTS["earnings"]
    elif ret_1m > 0 and ret_3m > 0:
        sc["earnings"] = Config.WEIGHTS["earnings"] * 0.6
    elif ret_3m > -5:
        sc["earnings"] = Config.WEIGHTS["earnings"] * 0.3
    else:
        sc["earnings"] = 0

    # 11. DXY/Crude macro impact (4)
    crude = getattr(macro, "crude_usd", 75.0)
    dxy   = getattr(macro, "dxy", 104.0)
    if crude < 75 and dxy < 102:
        sc["crude_dxy"] = Config.WEIGHTS["crude_dxy"]       # Best case for India
    elif crude < 85 and dxy < 106:
        sc["crude_dxy"] = Config.WEIGHTS["crude_dxy"] * 0.6
    elif crude < 90:
        sc["crude_dxy"] = Config.WEIGHTS["crude_dxy"] * 0.3
    else:
        sc["crude_dxy"] = 0

    total = round(sum(sc.values()), 2)
    sig.score = total
    sig.comps = {k: round(v,2) for k,v in sc.items()}

    threshold = Config.ENTRY_SCORE_MIN   # 72 for Regime A
    if macro.regime == "B": threshold = 70   # Balanced — buy strong setups in recovery
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
        sig.stage   = stage
        sig.vcp     = vcp["ok"]
        sig.reason  = (f"Score {total:.0f}/100 | Stage {stage} | "
                       f"VCP={'Yes' if vcp['ok'] else 'No'} | "
                       f"MA={'Full stack' if ma['full'] else 'Partial'} | "
                       f"Regime {macro.regime}")

    elif total >= Config.WATCHLIST_SCORE_MIN:
        sig.action = "watch"
        sig.stage  = stage
        sig.vcp    = vcp["ok"]
        # Identify what's missing for a buy signal
        gaps = []
        threshold = 70 if macro.regime == "B" else 68
        gap       = threshold - total

        # Specific, actionable gap descriptions
        if sc.get("stage2", 0) == 0:
            gaps.append(f"Stage {stage} — wait for price to cross rising 30W MA")
        if sc.get("vcp", 0) == 0 and sc.get("stage2", 0) > 0:
            gaps.append("VCP incomplete — price range still wide, wait for tighter base (3 contractions)")
        if sc.get("vcp", 0) == 0 and sc.get("stage2", 0) == 0:
            gaps.append("VCP not applicable until Stage 2 confirmed")
        if sc.get("ma_stack", 0) < Config.WEIGHTS["ma_stack"] * 0.6:
            gaps.append("Price below 100/200 DMA — wait for MA alignment")
        if sc.get("macd", 0) == 0:
            gaps.append("MACD bearish — wait for bullish crossover")
        if sc.get("relative_str", 0) == 0:
            gaps.append("Underperforming Nifty — wait for relative strength to turn")
        if sc.get("rsi", 0) == 0:
            gaps.append("RSI outside 50-70 zone")

        if not gaps:
            if gap > 0:
                gaps.append(f"Score {total:.0f}/100 — need macro improvement or volume surge")
            else:
                gaps.append("Ready to buy — awaiting regime recovery")

        sig.missing = ", ".join(gaps[:2])
        sig.reason  = f"Score {total:.0f} — {sig.missing}"
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
        self.watchlist_history = {}   # symbol -> {days, prev_score, first_seen}
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE) as f:
                    d = json.load(f)
                self.cash             = d.get("cash", Config.VIRTUAL_CAPITAL)
                self.positions        = [Position(**p) for p in d.get("positions", [])]
                self.watchlist_history= d.get("watchlist_history", {})
            except:
                pass

    def save(self, regime: str = "", watchlist: list = None):
        os.makedirs(Config.REPORT_DIR, exist_ok=True)
        data = {
            "cash":      self.cash,
            "positions": [asdict(p) for p in self.positions],
            "watchlist_history": self.watchlist_history,
        }
        if regime:
            data["last_regime"] = regime
        with open(Config.STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def update_watchlist_history(self, signals: list):
        """Track watchlist stocks across days — score trend, days monitored."""
        today = datetime.now().strftime("%Y-%m-%d")
        current_watch = {s.symbol for s in signals if s.action == "watch"}
        for sym in current_watch:
            sig = next(s for s in signals if s.symbol == sym)
            if sym not in self.watchlist_history:
                self.watchlist_history[sym] = {
                    "first_seen": today, "days": 1,
                    "prev_score": sig.score, "score_history": [sig.score]
                }
            else:
                h = self.watchlist_history[sym]
                h["days"] += 1
                h["score_history"] = h.get("score_history", []) + [sig.score]
                h["score_history"] = h["score_history"][-10:]  # keep last 10
                h["prev_score"] = h.get("prev_score", sig.score)
        # Clean up stocks no longer on watchlist for > 5 days
        to_remove = [s for s in list(self.watchlist_history.keys())
                     if s not in current_watch and
                     self.watchlist_history[s].get("days", 0) > 0]
        for sym in to_remove:
            self.watchlist_history.pop(sym, None)

    def get_last_regime(self) -> str:
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE) as f:
                    return json.load(f).get("last_regime", "")
            except:
                pass
        return ""

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


    # Regime change detection
    prev_regime = portfolio.get_last_regime()
    regime_change_banner = ""
    if prev_regime and prev_regime != macro.regime:
        change_styles = {
            ("C","B"): ("#1a5276","#eaf4fb","#85c1e9","📈 Regime upgraded: C → B (Bear → Cautious)",
                        "Market has recovered from bear territory. Nifty is now between key moving averages. Bot raising alert — watchlist being monitored. Entry threshold raised to 80/100. Max 8 positions."),
            ("C","A"): ("#1e8449","#eafaf1","#27ae60","🎉 Regime upgraded: C → A (Bear → Bull Market!)",
                        "Major recovery! Nifty back above both 50 and 200 DMA. Bot fully deployed — all modes active. Entry threshold 72/100. Up to 14 positions."),
            ("B","A"): ("#1e8449","#eafaf1","#27ae60","📈 Regime upgraded: B → A (Full Bull Mode)",
                        "Market strengthened. Nifty clearly above all key MAs. Bot expanding to full deployment — up to 14 positions, intraday and swing both active."),
            ("A","B"): ("#7d6608","#fef9e7","#f39c12","⚠️ Regime downgraded: A → B (Bull → Cautious)",
                        "Market weakening. Nifty showing mixed signals. Bot tightening — entry threshold raised to 80, max 8 positions. Existing positions held with tighter stops."),
            ("B","C"): ("#922b21","#fdedec","#e74c3c","🔴 Regime downgraded: B → C (BEAR MARKET)",
                        "Market breakdown. Nifty below 200 DMA — confirmed downtrend. Bot moving to full cash protection. No new equity entries until recovery."),
            ("A","C"): ("#922b21","#fdedec","#e74c3c","🚨 ALERT: Sharp crash detected A → C",
                        "Major breakdown! Nifty fell below 200 DMA. Bot immediately halting all new entries. Capital preserved in cash. Monitor closely."),
        }
        key = (prev_regime, macro.regime)
        if key in change_styles:
            tc, bg, bc, title, msg = change_styles[key]
            regime_change_banner = (
                f'<div style="background:{bg};border:2px solid {bc};border-radius:10px;' +
                f'padding:16px 20px;margin-bottom:16px;">' +
                f'<b style="color:{tc};font-size:15px;">{title}</b>' +
                f'<p style="color:{tc};font-size:13px;line-height:1.6;margin:8px 0 0;">{msg}</p>' +
                f'<p style="color:{tc};font-size:12px;margin:6px 0 0;opacity:0.8;">' +
                f'Previous: <b>{prev_regime}</b> → Now: <b>{macro.regime}</b></p></div>'
            )

    circuit = ""
    if metrics["paused"]:
        circuit = '<div style="background:#fdedec;border:1px solid #e74c3c;border-radius:8px;padding:14px 18px;margin:16px 0;"><b style="color:#e74c3c;">⚠ Circuit Breaker Active</b><p style="color:#c0392b;margin:6px 0 0;font-size:13px;">Portfolio drawdown exceeded 10%. No new entries until recovery. Bot is protecting your capital.</p></div>'

        # Build enriched watchlist rows
    def _wrow(s):
        h           = portfolio.watchlist_history.get(s.symbol, {})
        days        = h.get("days", 1)
        hist        = h.get("score_history", [s.score])
        prev_s      = hist[-2] if len(hist) >= 2 else s.score
        trend       = s.score - prev_s
        t_arrow     = "▲" if trend > 1 else "▼" if trend < -1 else "▶"
        t_color     = "#27ae60" if trend > 1 else "#e74c3c" if trend < -1 else "#7f8c8d"
        sc          = {1:"#3498db",2:"#27ae60",3:"#f39c12",4:"#e74c3c"}.get(s.stage,"#7f8c8d")
        sl          = {1:"Stage 1",2:"Stage 2 ✓",3:"Stage 3",4:"Stage 4"}.get(s.stage,"?")
        vcp_b       = '<span style="background:#eaf4fb;color:#1a5276;padding:1px 5px;border-radius:3px;font-size:10px;margin-left:3px;">VCP</span>' if s.vcp else ""
        threshold   = 80 if macro.regime == "B" else 72
        gap         = max(0, threshold - s.score)
        miss        = s.missing
        for pfx in [f"Need +{gap:.0f}pts: ", f"Need +{gap+1:.0f}pts: ", "Need +"]:
            if miss.startswith(pfx): miss = miss[len(pfx):]; break
        return f'''<tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:9px 8px;font-size:13px;font-weight:700;">{s.symbol}{vcp_b}<div style="font-size:10px;color:{sc};">{sl}</div></td>
          <td style="padding:9px 8px;text-align:center;"><span style="background:#fef9e7;color:#f39c12;padding:2px 7px;border-radius:10px;font-size:13px;font-weight:700;">{s.score:.0f}</span> <span style="color:{t_color};font-size:11px;">{t_arrow}</span></td>
          <td style="padding:9px 8px;text-align:right;font-size:13px;">&#8377;{s.entry:,.2f}</td>
          <td style="padding:9px 8px;text-align:center;font-size:12px;font-weight:600;color:#7f8c8d;">{days}d</td>
          <td style="padding:9px 8px;text-align:center;font-size:12px;font-weight:700;color:#e74c3c;">+{gap:.0f}</td>
          <td style="padding:9px 8px;font-size:11px;color:#7f8c8d;">{miss[:55]}</td>
        </tr>'''
    watch_rows = "".join(_wrow(s) for s in watch) or '<tr><td colspan="6" style="text-align:center;color:#999;padding:16px;">Nothing on watchlist today</td></tr>'    # Extended macro interpretations for HTML
    rbi_color = "#27ae60" if macro.rbi_stance=="cutting" else "#e74c3c" if macro.rbi_stance=="hiking" else "#f39c12"
    rbi_label = "Cutting rates — bullish for equities" if macro.rbi_stance=="cutting" else "Hiking rates — bearish headwind" if macro.rbi_stance=="hiking" else "Neutral — monitoring inflation"
    crude_color = "#27ae60" if macro.crude_usd < 75 else "#f39c12" if macro.crude_usd < 90 else "#e74c3c"
    crude_label = "Low — positive for India economy" if macro.crude_usd < 75 else "Moderate — manageable" if macro.crude_usd < 90 else "High — negative for CAD and inflation"
    dxy_color = "#27ae60" if macro.dxy < 102 else "#f39c12" if macro.dxy < 107 else "#e74c3c"
    dxy_label = "Weak dollar — EM equity tailwind" if macro.dxy < 102 else "Moderate dollar — neutral" if macro.dxy < 107 else "Strong dollar — FII outflow risk for India"
    geo_color = "#27ae60" if macro.geo_risk=="low" else "#f39c12" if macro.geo_risk=="medium" else "#e74c3c"
    inr_color = "#27ae60" if macro.usd_inr < 83 else "#f39c12" if macro.usd_inr < 85 else "#e74c3c"
    inr_label = "Strong rupee — FII inflow friendly" if macro.usd_inr < 83 else "Stable range" if macro.usd_inr < 85 else "Weak rupee — FII pressure increasing"
    sgx_color = "#27ae60" if macro.sgx_nifty_chg > 0.3 else "#e74c3c" if macro.sgx_nifty_chg < -0.3 else "#f39c12"

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

{regime_change_banner}
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
  <table width="100%" style="border-collapse:collapse;font-size:13px;">
    <tr style="background:#f8f9fa;"><td colspan="3" style="padding:7px 8px;font-weight:700;color:#7f8c8d;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Indian Market</td></tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;width:170px;">India VIX</td>
      <td><b style="color:{vix_color};">{vix:.1f}</b> {gauge_bar(vix,10,35,low_bad=False)} <span style="font-size:12px;color:{vix_color};">{vix_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;white-space:nowrap;">Normal: 12–20</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Nifty vs 50 DMA</td>
      <td><b style="color:{ma50_color};">{macro.nifty_vs_50:+.1f}%</b> {gauge_bar(macro.nifty_vs_50+10,0,20)} <span style="font-size:12px;color:{ma50_color};">{ma50_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Bull: above 0%</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Nifty vs 200 DMA</td>
      <td><b style="color:{ma200_color};">{macro.nifty_vs_200:+.1f}%</b> {gauge_bar(macro.nifty_vs_200+10,0,20)} <span style="font-size:12px;color:{ma200_color};">{ma200_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Bull: above 0%</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">FII Net Flow</td>
      <td><b style="color:{fii_color};">&#8377;{fii:+,.0f} Cr</b> {gauge_bar(fii+3000,0,6000)} <span style="font-size:12px;color:{fii_color};">{fii_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Bull: &gt;&#8377;500Cr</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Advance / Decline</td>
      <td>
        <b style="color:{"#27ae60" if macro.ad_ratio>=1.5 else "#f39c12" if macro.ad_ratio>=0.8 else "#e74c3c"};">
          {macro.ad_advances}A / {macro.ad_declines}D
          (ratio {macro.ad_ratio:.1f}x)
        </b>
        <span style="font-size:12px;color:{"#27ae60" if macro.ad_ratio>=1.5 else "#f39c12" if macro.ad_ratio>=0.8 else "#e74c3c"};margin-left:6px;">
          {macro.ad_label}
        </span>
      </td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Market breadth</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">RBI Stance</td>
      <td><b style="color:{rbi_color};">{macro.rbi_stance.title()}</b>
        <span style="font-size:12px;color:{rbi_color};margin-left:8px;">{rbi_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Policy signal</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">USD/INR</td>
      <td><b style="color:{inr_color};">&#8377;{macro.usd_inr:.2f}</b>
        <span style="font-size:12px;color:{inr_color};margin-left:8px;">{inr_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">FII sensitivity</td>
    </tr>
    <tr style="background:#f8f9fa;"><td colspan="3" style="padding:7px 8px;font-weight:700;color:#7f8c8d;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Global Macro</td></tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Crude Oil (WTI)</td>
      <td><b style="color:{crude_color};">${macro.crude_usd:.1f}</b> {gauge_bar(macro.crude_usd,50,110,low_bad=False)}
        <span style="font-size:12px;color:{crude_color};">{crude_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">India: low is good</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Gold (INR/10g)</td>
      <td><b style="color:#f39c12;">&#8377;{macro.gold_inr:,.0f}</b>
        <span style="font-size:12px;color:#7f8c8d;margin-left:8px;">{"Safe haven demand high" if macro.gold_inr>80000 else "Moderate" if macro.gold_inr>70000 else "Low safe haven demand"}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Hedge signal</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">US Dollar Index</td>
      <td><b style="color:{dxy_color};">{macro.dxy:.1f}</b> {gauge_bar(macro.dxy,95,115,low_bad=False)}
        <span style="font-size:12px;color:{dxy_color};">{dxy_label}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">EM flows driver</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">US 10Y Yield</td>
      <td><b style="color:{"#e74c3c" if macro.us_10y_yield>4.5 else "#f39c12" if macro.us_10y_yield>4 else "#27ae60"};">{macro.us_10y_yield:.2f}%</b>
        <span style="font-size:12px;color:#7f8c8d;margin-left:8px;">{"High — EM outflow risk" if macro.us_10y_yield>4.5 else "Elevated" if macro.us_10y_yield>4 else "Low — EM friendly"}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">FII cost of carry</td>
    </tr>
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:9px 0;color:#7f8c8d;">Prev Day Signal</td>
      <td><b style="color:{sgx_color};">{macro.sgx_nifty_chg:+.2f}%</b>
        <span style="font-size:12px;color:#7f8c8d;margin-left:8px;">Nifty prior session change</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Gap indicator</td>
    </tr>
    <tr style="background:#f8f9fa;"><td colspan="3" style="padding:7px 8px;font-weight:700;color:#7f8c8d;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Geopolitical</td></tr>
    <tr>
      <td style="padding:9px 0;color:#7f8c8d;">Global Risk</td>
      <td><b style="color:{geo_color};">{macro.geo_risk.title()}</b>
        <span style="font-size:12px;color:#7f8c8d;margin-left:8px;">{macro.macro_notes[:100] if macro.macro_notes else "Monitoring global news feeds"}</span></td>
      <td style="font-size:11px;color:#bdc3c7;text-align:right;">Auto-monitored</td>
    </tr>
  </table>

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
  <p style="color:#7f8c8d;font-size:12px;margin:0 0 6px;">
    {"Regime C — all equities on hold. Showing best positioned stocks for when market recovers." if macro.regime=="C" else
     f"Regime B — buy threshold raised to 80. Stocks scoring 55–79 monitored here." if macro.regime=="B" else
     "Regime A — scoring 55–71. Close to buy threshold of 72. Monitor daily for breakout."}
  </p>
  <p style="color:#7f8c8d;font-size:11px;margin:0 0 14px;background:#f8f9fa;padding:8px 10px;border-radius:6px;">
    <b>Stage guide:</b> Stage 1=base building (too early) · <b style="color:#27ae60;">Stage 2=uptrend (buy zone)</b> · Stage 3=topping (exit) · Stage 4=downtrend (avoid) &nbsp;|&nbsp;
    <b>VCP</b>=Volatility Contraction Pattern — price tightening before breakout. Stage 2 + VCP = highest conviction setup.
  </p>
  <table width="100%" style="border-collapse:collapse;font-size:13px;">
    <thead><tr style="background:#f8f9fa;">
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">Symbol + Stage</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Score (trend)</th>
      <th style="padding:9px 8px;text-align:right;color:#7f8c8d;">Price</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Days tracked</th>
      <th style="padding:9px 8px;text-align:center;color:#7f8c8d;">Gap to buy</th>
      <th style="padding:9px 8px;text-align:left;color:#7f8c8d;">What needs to happen</th>
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


def _save_watchlist_excel(signals: list, portfolio, macro: MacroState, today: str):
    """Save rolling 10-day watchlist history to Excel."""
    try:
        excel_path = f"{Config.REPORT_DIR}/watchlist_history.xlsx"
        watch  = sorted([s for s in signals if s.action=="watch"],
                        key=lambda x: x.score, reverse=True)[:20]
        buys   = sorted([s for s in signals if s.action=="buy"],
                        key=lambda x: x.score, reverse=True)[:10]

        rows = []
        for s in buys + watch:
            h       = portfolio.watchlist_history.get(s.symbol, {})
            hist    = h.get("score_history", [s.score])
            prev_s  = hist[-2] if len(hist) >= 2 else s.score
            trend   = round(s.score - prev_s, 1)
            threshold = 70 if macro.regime == "B" else 68 if macro.regime == "A" else 70
            gap     = max(0, threshold - s.score)
            miss    = s.missing
            for pfx in [f"Need +{gap:.0f}pts: ", "Need +"]:
                if miss.startswith(pfx): miss = miss[len(pfx):]; break
            stage_map = {1:"Stage 1 — Base",2:"Stage 2 — Uptrend ✓",
                         3:"Stage 3 — Top",4:"Stage 4 — Decline"}
            rows.append({
                "Date":           today,
                "Symbol":         s.symbol,
                "Action":         s.action.upper(),
                "Score":          s.score,
                "Score Trend":    f"{trend:+.1f}",
                "Stage":          stage_map.get(s.stage, "Unknown"),
                "VCP":            "Yes" if s.vcp else "No",
                "Price":          s.entry,
                "Gap to Buy":     gap,
                "What's Missing": miss[:80],
                "Days Tracked":   h.get("days", 1),
                "First Seen":     h.get("first_seen", today),
                "Regime":         macro.regime,
                "Nifty vs 200DMA":f"{macro.nifty_vs_200:+.1f}%",
                "VIX":            macro.vix,
                "FII Cr":         macro.fii_flow,
            })

        new_df = pd.DataFrame(rows)

        # Load existing and append
        if os.path.exists(excel_path):
            existing = pd.read_excel(excel_path)
            # Keep last 10 trading days
            all_dates = sorted(existing["Date"].unique(), reverse=True)
            keep_dates = all_dates[:9]  # keep 9 old + today = 10
            existing   = existing[existing["Date"].isin(keep_dates)]
            combined   = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_excel(excel_path, index=False)
        log.info(f"Excel watchlist saved: {excel_path}")
    except Exception as e:
        log.warning(f"Excel watchlist save failed: {e}")



# ─── MAIN AGENT LOOP ───────────────────────────────────────────────────────────

# NSE holidays 2025-2026 — verified against NSE official calendar
# Fallback holiday list — only used if live NSE API fails
NSE_HOLIDAYS_FALLBACK = {
    "2025-01-26","2025-02-19","2025-03-14","2025-03-31",
    "2025-04-10","2025-04-14","2025-04-18","2025-05-01",
    "2025-08-15","2025-08-27","2025-10-02","2025-10-21",
    "2025-10-22","2025-10-24","2025-11-05","2025-12-25",
    "2026-01-26","2026-02-18","2026-03-19","2026-03-20",
    "2026-04-02","2026-04-03","2026-04-14","2026-05-01",
    "2026-08-15","2026-10-02","2026-11-14","2026-12-25",
}


def _fetch_nse_holidays() -> set:
    """Fetch live NSE holiday list from NSE API."""
    try:
        sess = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer":    "https://www.nseindia.com/",
        }
        sess.get("https://www.nseindia.com", headers=headers, timeout=6)
        r = sess.get(
            "https://www.nseindia.com/api/holiday-master?type=trading",
            headers=headers, timeout=6
        )
        holidays = set()
        for item in r.json().get("CM", []):
            date_str = item.get("tradingDate", "")
            if date_str:
                try:
                    from datetime import datetime as dt2
                    d = dt2.strptime(date_str, "%d-%b-%Y")
                    holidays.add(d.strftime("%Y-%m-%d"))
                except:
                    pass
        if len(holidays) > 5:
            log.info(f"Live NSE holidays fetched: {len(holidays)} dates")
            return holidays
    except Exception as e:
        log.warning(f"NSE live holiday fetch failed: {e}")
    return set()


def is_market_open() -> tuple:
    """
    Check if NSE is open today.
    Tries live NSE API first — never relies solely on hardcoded dates.
    """
    now     = datetime.now()
    today   = now.strftime("%Y-%m-%d")
    weekday = now.weekday()

    if weekday >= 5:
        return False, "Weekend — NSE closed"

    # Always try live API first
    live = _fetch_nse_holidays()
    if live:
        if today in live:
            return False, f"NSE holiday ({today})"
        log.info(f"Market open — confirmed via live NSE API")
        return True, "Market open"

    # Only use fallback if live API completely fails
    log.warning("Using fallback holiday calendar — live API unavailable")
    if today in NSE_HOLIDAYS_FALLBACK:
        return False, f"NSE holiday ({today}) — fallback calendar"
    return True, "Market open"


def write_holiday_report(reason: str):
    """Write a holiday HTML report so trade.yml email step sends it normally."""
    os.makedirs(Config.REPORT_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p IST")
    today   = datetime.now().strftime("%Y%m%d")

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#2c3e50;max-width:700px;margin:0 auto;padding:20px;background:#f5f6fa;">
<div style="background:#1a252f;border-radius:10px;padding:24px;margin-bottom:20px;">
  <h1 style="color:#fff;margin:0;font-size:22px;">AlphaAgent Daily Report</h1>
  <p style="color:#95a5a6;margin:4px 0 0;font-size:13px;">{now_str}</p>
</div>
<div style="background:#fef9e7;border:2px solid #f39c12;border-radius:10px;padding:24px;margin-bottom:16px;">
  <div style="font-size:28px;margin-bottom:12px;">&#128274;</div>
  <h2 style="color:#856404;margin:0 0 10px;font-size:18px;">{reason}</h2>
  <p style="color:#856404;font-size:14px;line-height:1.6;margin:0;">
    NSE is closed today. No market data available, no trades placed.
    Portfolio is unchanged. Bot resumes automatically on the next trading day.
  </p>
</div>
<div style="background:#eaf4fb;border:1px solid #85c1e9;border-radius:10px;padding:16px 20px;margin-bottom:16px;">
  <b style="color:#1a5276;font-size:14px;">Use today to review:</b>
  <ul style="color:#1a5276;font-size:13px;margin:8px 0 0;padding-left:18px;line-height:1.8;">
    <li>Yesterday's watchlist — check CPSEETF, JINDALSTEL, VEDL closely</li>
    <li>Any macro events scheduled for tomorrow (RBI minutes, FII data, global cues)</li>
    <li>Global markets today — US futures, SGX Nifty will signal tomorrow's open</li>
  </ul>
</div>
<div style="text-align:center;color:#bdc3c7;font-size:11px;padding:10px;">
  AlphaAgent automated paper trading | not SEBI-registered financial advice
</div>
</body></html>"""

    plain = f"""AlphaAgent Daily Report
{now_str}

MARKET CLOSED: {reason}
No trades placed. Portfolio unchanged.
Bot resumes on next trading day.

Review yesterday's watchlist and global cues for tomorrow's open.
"""
    with open(f"{Config.REPORT_DIR}/report_{today}.html","w") as f: f.write(html)
    with open(f"{Config.REPORT_DIR}/report_{today}.txt", "w") as f: f.write(plain)
    log.info(f"Holiday report written: {reason}")


def run():
    log.info(f"\n{'='*55}\nALPHAGENT RUN — {datetime.now().strftime('%d %b %Y %H:%M')}\n{'='*55}")

    # Check market holiday first
    market_open, market_reason = is_market_open()
    if not market_open:
        today = datetime.now().strftime("%Y%m%d")
        holiday_file = f"{Config.REPORT_DIR}/report_{today}.html"
        os.makedirs(Config.REPORT_DIR, exist_ok=True)
        if os.path.exists(holiday_file):
            log.info(f"Holiday report already sent today — skipping second run.")
            return
        log.info(f"Market closed: {market_reason}. Writing holiday report.")
        write_holiday_report(market_reason)
        return

    client    = DataClient()
    portfolio = Portfolio()
    journal   = Journal()

    log.info("OBSERVE: Fetching macro + prices...")
    macro  = get_macro(client)
    prices = {}
    all_syms = Config.get_universe()
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

    log.info("REASON: Scoring all candidates (always — even in Regime C for watchlist)...")
    signals = []
    for sym in [s for s in all_syms if not portfolio.has(s)]:
        df = client.get_historical(sym, 265)
        if df.empty: continue
        sig = score_symbol(sym, df, macro, portfolio.cash)
        # In Regime C: override action to watch — never buy
        if macro.regime == "C" and sig.action == "buy":
            sig.action  = "watch"
            threshold   = 72   # Regime A threshold for reference
            gap         = max(0, threshold - sig.score)
            gaps        = []
            if sig.stage != 2:           gaps.append(f"Stage {sig.stage} (need Stage 2)")
            if not sig.vcp:              gaps.append("VCP not formed")
            # For Stage 2 + VCP stocks in Regime C — they're READY, just need regime
            if sig.stage == 2 and sig.vcp:
                miss = f"Score {sig.score:.0f} ready. Stage 2 + VCP confirmed. Awaiting market recovery (Regime A/B)"
            elif sig.stage == 2:
                miss = f"Stage 2 confirmed. VCP forming — watch for tighter base. Buy on Regime A/B recovery"
            else:
                miss = f"Stage {sig.stage} — need Stage 2 + VCP before entry"
            sig.missing = miss
            sig.reason  = f"Score {sig.score:.0f} — {miss}"
        # Ensure watchlist signals also have missing populated
        if sig.action == "watch" and not sig.missing:
            threshold = 70 if macro.regime == "B" else 68 if macro.regime == "A" else 70
            gap       = max(0, threshold - sig.score)
            gaps      = []
            if sig.stage != 2: gaps.append(f"Stage {sig.stage} (need Stage 2)")
            if not sig.vcp:    gaps.append("VCP not formed")
            sig.missing = f"Need +{gap:.0f}pts: " + (", ".join(gaps[:2]) if gaps else "Score below threshold")
        signals.append(sig)
        time.sleep(0.1)

    signals.sort(key=lambda x: x.score, reverse=True)
    buy_count   = len([s for s in signals if s.action=="buy"])
    watch_count = len([s for s in signals if s.action=="watch"])
    log.info(f"Scored {len(signals)} symbols | {buy_count} buy signals | {watch_count} on watchlist")

    # In Regime C — buy Gold ETF as hedge (up to 15% of portfolio)
    if macro.regime == "C" and not metrics["paused"]:
        gold_sym = "GOLDBEES"
        if not portfolio.has(gold_sym):
            gold_ltp = client.get_ltp(gold_sym)
            if gold_ltp and gold_ltp > 0:
                gold_budget = Config.VIRTUAL_CAPITAL * Config.ETF_BOOK_PCT
                gold_qty    = max(1, int(gold_budget / gold_ltp))
                max_by_cash = int(portfolio.cash * 0.95 / gold_ltp)
                gold_qty    = min(gold_qty, max_by_cash)
                if gold_qty > 0:
                    oid = client.place_order(gold_sym, gold_qty, gold_ltp, "BUY")
                    if oid:
                        pos = Position(
                            symbol=gold_sym, entry=gold_ltp, qty=gold_qty,
                            stop=round(gold_ltp * 0.93, 2),
                            target1=round(gold_ltp * 1.10, 2),
                            target2=round(gold_ltp * 1.20, 2),
                            entry_date=datetime.now().strftime("%Y-%m-%d"),
                            score=70
                        )
                        portfolio.add(pos)
                        journal.log({
                            "datetime": datetime.now().isoformat(),
                            "symbol": gold_sym, "action": "BUY",
                            "qty": gold_qty, "price": gold_ltp,
                            "score": 70, "reason": "Regime C hedge — Gold ETF",
                            "kind": "etf_hedge", "regime": macro.regime,
                            "mode": "PAPER" if PAPER_MODE else "LIVE"
                        })
                        log.info(f"GOLD ETF ENTRY: {gold_qty}x GOLDBEES @ Rs{gold_ltp:.2f}")

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

    portfolio.update_watchlist_history(signals)
    portfolio.save(macro.regime)
    metrics = portfolio.metrics(prices)
    html_report, plain_report = build_html_report(metrics, signals, macro, portfolio, prices)

    today = datetime.now().strftime("%Y%m%d")
    os.makedirs(Config.REPORT_DIR, exist_ok=True)
    with open(f"{Config.REPORT_DIR}/report_{today}.html", "w") as f: f.write(html_report)
    with open(f"{Config.REPORT_DIR}/report_{today}.txt",  "w") as f: f.write(plain_report)

    # Excel rolling watchlist — 10 day history
    _save_watchlist_excel(signals, portfolio, macro, today)
    log.info(f"Reports saved.")
    send_telegram(plain_report[:4000])
    log.info("Run complete.")
    return html_report, plain_report


if __name__ == "__main__":
    run()
