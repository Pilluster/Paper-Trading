# AlphaAgent — India Markets Automated Trading Bot

## What this does
Runs automatically on GitHub's servers every weekday at **8:45 AM IST** and **3:35 PM IST**.
Zero manual intervention. Results committed to this repo after every run.

---

## Setup — do this once

### Step 1: Create a GitHub account
Go to [github.com](https://github.com) → Sign up (free account is enough).

---

### Step 2: Create a private repository
1. Click **+** (top right) → **New repository**
2. Name it: `alphaagent-india`
3. Set visibility to **Private**
4. Click **Create repository** (do NOT add README)

---

### Step 3: Install Git and Python on your computer
- **Windows**: Download [Git for Windows](https://git-scm.com/download/win) and [Python 3.11](https://www.python.org/downloads/)
- **Mac**: Open Terminal → `xcode-select --install` (Git) + [Python 3.11](https://www.python.org/downloads/)
- Verify: open Terminal/Command Prompt → `git --version` and `python --version`

---

### Step 4: Push these files to GitHub
Open Terminal / Command Prompt and run:

```bash
# 1. Go to the folder where you downloaded the bot files
cd ~/Desktop/alphaagent-india

# 2. Initialize git
git init

# 3. Add all files
git add .

# 4. First commit
git commit -m "AlphaAgent initial setup"

# 5. Connect to your GitHub repo (replace YOUR_USERNAME)
git remote add origin https://github.com/YOUR_USERNAME/alphaagent-india.git

# 6. Push
git branch -M main
git push -u origin main
```

---

### Step 5: Add secrets to GitHub (credentials never go in code)
Go to your repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets one by one:

| Secret name | Value | Required? |
|-------------|-------|-----------|
| `PAPER_MODE` | `true` | Yes — keep `true` until you're ready for live |
| `VIRTUAL_CAPITAL` | `500000` | Yes |
| `ANGELONE_API_KEY` | your key | No (bot uses yfinance until you add this) |
| `ANGELONE_CLIENT_ID` | your ID | No |
| `ANGELONE_PASSWORD` | your MPIN | No |
| `ANGELONE_TOTP_SECRET` | your TOTP | No |
| `TELEGRAM_TOKEN` | bot token | Optional — for notifications |
| `TELEGRAM_CHAT_ID` | your chat ID | Optional |

---

### Step 6: Enable GitHub Actions
Go to your repo → **Actions** tab → Click **"I understand my workflows, go ahead and enable them"**

That's it. The bot will now run automatically.

---

## How to get a Telegram notification (optional but recommended)

1. Open Telegram → search **@BotFather** → `/newbot`
2. Name it anything (e.g. AlphaAgent) → copy the **token**
3. Search **@userinfobot** → `/start` → copy your **Chat ID**
4. Add both as GitHub Secrets (`TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`)

You'll get a message like this after each run:
```
AlphaAgent Daily Report [PAPER | yfinance]
10 Apr 2026, 08:45 AM IST

Macro: Regime A | VIX 13.2 | FII +₹1,240Cr
Nifty vs 50DMA: +2.1% | vs 200DMA: +8.4%

Portfolio
  Value    : ₹5,02,340
  P&L      : +₹2,340 (+0.47%)
  Drawdown :  -0.00%
  Positions: 4/14

New Buy Signals
  TRENT         Score 78/100 | ₹5,812 | Qty 4 | Stop ₹5,405
  PERSISTENT    Score 75/100 | ₹4,920 | Qty 5 | Stop ₹4,576
```

---

## How to see results
After each automated run:
1. Go to your GitHub repo
2. Check `reports/` folder — daily report txt and trade journal CSV
3. Click **Actions** tab to see run logs
4. Telegram message (if set up)

---

## When Angel One API is approved
1. Go to [myaccount.angelbroking.com](https://myaccount.angelbroking.com) → SmartAPI → Create App
2. Copy **API Key**, **Client ID**, **MPIN**, **TOTP secret**
3. Add these as GitHub Secrets (Step 5 above)
4. The bot automatically switches from yfinance to SmartAPI on the next run

---

## When you're ready for live trading
1. Change `PAPER_MODE` secret from `true` to `false`
2. The next scheduled run places real orders on Angel One
3. Recommend: run paper for minimum 4 weeks first

---

## File structure
```
alphaagent-india/
├── alpha_agent.py              # Full trading bot
├── requirements.txt            # Python dependencies
├── .gitignore                  # Protects secrets
├── README.md                   # This file
├── .github/
│   └── workflows/
│       └── trade.yml           # GitHub Actions schedule
└── reports/                    # Auto-generated after each run
    ├── portfolio_state.json    # Current portfolio
    ├── trade_journal.csv       # All trades
    └── report_YYYYMMDD.txt     # Daily reports
```

---

## Tax note
Short-term capital gains (STCG) on equity: **20%** (post Budget 2024).
Long-term (held > 1 year): **12.5%** above ₹1.25L/year.
Keep the `trade_journal.csv` — your CA will need it.

---

*Not SEBI-registered investment advice. Educational paper trading system.*
