---
name: ccxt
category: data-source
description: CCXT unified crypto exchange library (Gate.io via CCXT). Free public market data. Primary crypto data source. Gate.io is the default and preferred exchange.
---

## Overview

CCXT is a unified cryptocurrency exchange trading library supporting 100+ exchanges including Gate.io, Binance, Bybit, OKX, Coinbase, Kraken, and more. Public market data (OHLCV, tickers, order books) requires no API key.

## Real-time Cache Usage (CRITICAL)

The system maintains a real-time ticker cache that updates every 5 seconds via Gate.io API. **ALWAYS use the cached data first** for real-time prices. This is the **fastest and most reliable** method.

```python
# Step 1: Always try cached data first (recommended, fast, no network)
import sys
sys.path.insert(0, '/tmp/Vibe-Trading/agent')  # Ensure module can be imported
from src.gate_ws_client import get_cached_ticker, get_cached_ohlcv, get_all_tickers

# Get real-time BTC price from cache
ticker = get_cached_ticker("BTC-USDT")
if ticker:
    print(f"BTC Price: {ticker['last']} USDT")
    print(f"24h Change: {ticker.get('changePercent', 0):.2f}%")

# Get OHLCV historical data from cache (or CCXT fallback)
ohlcv = get_cached_ohlcv("ETH-USDT", timeframe="1d", limit=100)
if ohlcv:
    print(f"Fetched {len(ohlcv)} daily candles for ETH")
    print(f"Latest close: {ohlcv[-1][4]}")
else:
    # Step 2: Fallback to direct CCXT if cache is empty
    import ccxt
    exchange = ccxt.gate({"enableRateLimit": True})
    ticker = exchange.fetch_ticker("BTC/USDT")
    print(f"BTC Price: {ticker['last']} USDT")
```

**IMPORTANT**: 
- `get_cached_ticker("BTC-USDT")` returns cached data with ~5 second update frequency
- `get_cached_ohlcv("BTC-USDT", "1d", 100)` returns historical candles (cached for 1 hour)
- The cache file is at `/tmp/.vibe-trading/gate_ws_cache/tickers.json` and `ohlcv.json`
- Use `get_all_tickers()` to get all cached tickers at once
- Always check if cache is empty first, then fall back to direct CCXT API

- GitHub: https://github.com/ccxt/ccxt (35k+ stars)
- Install: `pip install ccxt`

## Quick Start

```python
import ccxt

# Default exchange is Gate.io (configurable via CCXT_EXCHANGE env var)
exchange = ccxt.gate({"enableRateLimit": True})

# Fetch daily OHLCV
ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1d", limit=100)
# Returns: [[timestamp, open, high, low, close, volume], ...]

# Fetch ticker
ticker = exchange.fetch_ticker("ETH/USDT")
print(f"ETH price: {ticker['last']}")
```

## Key Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `fetch_ohlcv(symbol, timeframe, since, limit)` | Historical candles | `[[ts, o, h, l, c, v], ...]` |
| `fetch_ticker(symbol)` | Latest quote | `{last, bid, ask, volume, ...}` |
| `fetch_tickers(symbols)` | Batch quotes | `{symbol: ticker}` |
| `fetch_order_book(symbol, limit)` | Order book | `{bids, asks, timestamp}` |
| `fetch_trades(symbol, since, limit)` | Recent trades | `[{price, amount, side, timestamp}, ...]` |

## Timeframes

`1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `12h`, `1d`, `1w`, `1M`

Note: not all exchanges support all timeframes. Use `exchange.timeframes` to check.

## Symbol Format

CCXT uses slash format: `BTC/USDT`, `ETH/BTC`, `SOL/USDT`

The project's DataLoader automatically converts `BTC-USDT` (hyphen) to `BTC/USDT` (slash).

## Exchange Selection

Set via environment variable: `CCXT_EXCHANGE=gate` (default: Gate.io)

Popular exchanges: `gate`, `binance`, `bybit`, `okx`, `coinbase`, `kraken`, `bitget`

## Gate.io Configuration

For authenticated access (optional, for private data):
```bash
export GATE_API_KEY=your_api_key
export GATE_API_SECRET=your_api_secret
```

## Built-in Loader

The project has a built-in CCXT DataLoader at `backtest/loaders/ccxt_loader.py`. It is the **primary** crypto data source, with OKX as fallback.

## Pagination

For long history, CCXT paginates via the `since` parameter (millisecond timestamp). The built-in loader handles this automatically (up to 200 pages).
