# Polymarket Hub — Portfolio Tracker + Opportunity Scanner

A live browser dashboard for tracking Polymarket positions and scanning for scalp & asymmetric betting opportunities across BTC, ETH, and SOL daily/weekly markets.

## Features

### 📊 Portfolio Tab
- Track your Polymarket positions with live price updates
- Real-time P&L showing entry vs current price
- Auto-refresh with color-coded gains/losses

### 🔍 Opportunity Scanner
- **Scalp Plays** — Buy "No" on near-certain markets (0.1–2% return, near-zero risk, resolves in 1–2 days)
- **Asymmetric Plays** — Buy "Yes" on undervalued markets (5–20x upside if you're right)
- Live BTC/ETH/SOL spot prices from CoinGecko
- Safety analysis: calculates the price move needed to invalidate your bet
- Orderbook depth check (CLI only)

### 🤖 Daily Cron (Hermes Agent)
- Auto-scans Polymarket every morning at 7:00 AM PT
- Delivers a ranked briefing of the day's best plays

## Live Demo

👉 **https://nwfella.github.io/hermes-polymarket**

Open directly in any browser. No install, no wallet required.

## Quick Start (Local)

```bash
# Open the dashboard
open dashboard.html

# Or run the CLI scanner
python3 scanner.py --brief
```

### CLI Scanner Options

```bash
python3 scanner.py                    # Full scan with details
python3 scanner.py --brief            # Compact table format
python3 scanner.py --top 3            # Show top 3 plays
```

## How It Works

### Scalping Strategy
1. Scanner identifies daily BTC/ETH/SOL price markets resolving within 1–2 days
2. Cross-references current spot price against market threshold
3. Filters for near-certain "No" outcomes (>95% probability)
4. Checks real orderbook depth (not just surface price)
5. Ranks by safety × return × fillable size

### Asymmetric Strategy
1. Finds undervalued "Yes" outcomes (<10% probability)
2. Calculates upside multiplier (1/price)
3. Checks safety margin against current spot price

## Data Sources

- **Polymarket Gamma API** — market discovery and search
- **Polymarket CLOB API** — real-time prices and orderbooks
- **CoinGecko API** — BTC/ETH/SOL spot prices

All read-only, no authentication required.

## Repo Structure

```
├── index.html          # Full browser dashboard (Portfolio + Scanner)
├── scanner.py          # CLI scanner for terminal / cron jobs
└── README.md           # This file
```

## Your Positions

The dashboard comes pre-configured with example positions (Verstappen F1, Piastri F1, BTC limit order). Edit the `POSITIONS` array in `index.html` to track your own bets.

## License

MIT — do whatever you want with it.
