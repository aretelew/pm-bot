# PM-Bot: Kalshi Prediction Market Trading Bot

A Python trading bot for the [Kalshi](https://kalshi.com) prediction market exchange. Supports multiple strategies, paper trading via Kalshi's demo environment, backtesting, and real-time monitoring.

## Quick Start

### 1. Setup

```bash
# Create and activate virtual environment
py -m venv .venv
.venv\Scripts\Activate.ps1   # Windows PowerShell
# source .venv/bin/activate  # macOS/Linux

# Install
pip install -e ".[dev]"
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your Kalshi API credentials:

```bash
cp .env.example .env
```

You need a Kalshi account and API key. Generate one at:
- **Demo**: https://demo.kalshi.co (recommended for testing)
- **Production**: https://kalshi.com

### 3. Verify Connectivity

```bash
pm-bot check
```

### 4. Initialize Database

```bash
pm-bot init-db
```

## CLI Commands

| Command | Description |
|---|---|
| `pm-bot check` | Verify API connectivity |
| `pm-bot init-db` | Create database tables |
| `pm-bot scan` | Run market scanner continuously |
| `pm-bot top-markets` | Show markets stored in DB |
| `pm-bot orderbook <TICKER>` | Show orderbook for a market |
| `pm-bot price-history <TICKER>` | Show stored price history |
| `pm-bot balance` | Show portfolio balance |
| `pm-bot positions` | Show open positions |
| `pm-bot stream [TICKERS...]` | Stream real-time WebSocket data |
| `pm-bot run -s <strategy>` | Run the trading bot |
| `pm-bot backtest -s <strategy>` | Backtest a strategy against stored data |

## Strategies

| Strategy | Name | Description |
|---|---|---|
| Naive Value | `naive_value` | Trade when price diverges from orderbook mid |
| Market Maker | `market_maker` | Quote bid/ask around mid, earn spread |
| Cross-Market Arb | `arbitrage` | Exploit price inconsistencies across related markets |
| Signal-Based | `signal_based` | Trade on external data signals |

### Running Multiple Strategies

```bash
pm-bot run -s naive_value -s market_maker
```

## Backtesting

First collect data by running the scanner, then backtest:

```bash
pm-bot scan              # Let it run for a while to collect data
pm-bot backtest -s naive_value --html   # Generate HTML report
```

## Dashboard

```bash
pip install streamlit
streamlit run src/pm_bot/dashboard.py
```

## Architecture

```
src/pm_bot/
  api/            Kalshi REST + WebSocket clients
  strategies/     Trading strategy implementations
  engine/         Bot orchestrator, scanner, order/risk management
  data/           Database models and storage layer
  backtest/       Backtesting engine and reporting
  utils/          Logging, alerts
  cli.py          CLI entry point
  dashboard.py    Streamlit monitoring dashboard
```

## Safety

- **Demo-first**: all development targets `demo-api.kalshi.co` by default
- **Kill switch**: bot halts and cancels orders if daily loss exceeds limit
- **Rate limiting**: built-in token-bucket limiter with 429 retry handling
- **Graceful shutdown**: cancels all open orders on exit (Ctrl+C / SIGTERM)
- **Credentials**: API keys stored in `.env`, never committed to git
