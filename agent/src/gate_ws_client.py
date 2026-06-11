"""Gate.io Real-time Market Data Client.

Provides real-time cryptocurrency ticker data and OHLCV history via CCXT.
Falls back to REST polling when WebSocket is unavailable.

Environment variables:
    CCXT_EXCHANGE: Set to "gate" to enable Gate.io (default: "gate")
    GATE_API_KEY: Gate.io API key (optional, read-only works without)
    GATE_API_SECRET: Gate.io API secret (optional, read-only works without)
    CCXT_API_KEY: Alternative name for GATE_API_KEY
    CCXT_SECRET: Alternative name for GATE_API_SECRET
    GATE_WS_SYMBOLS: Comma-separated trading pairs (default: BTC/USDT,ETH/USDT,...)
    GATE_WS_POLL_INTERVAL: Seconds between polls (default: 5.0)
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Module-level logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# Load environment variables from .env
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# Global cache
_ticker_cache: Dict[str, Dict[str, Any]] = {}
_ohlcv_cache: Dict[str, Dict[str, Any]] = {}  # {symbol: {timeframe: {data, timestamp}}}
_cache_lock = threading.Lock()
_poll_thread: Optional[threading.Thread] = None
_poll_running = False

# File cache path for cross-process sharing
_CACHE_DIR = Path(os.environ.get("VIBE_TRADING_RUNTIME_ROOT", "/tmp/.vibe-trading")) / "gate_ws_cache"
_CACHE_FILE = _CACHE_DIR / "tickers.json"
_OHLCV_CACHE_FILE = _CACHE_DIR / "ohlcv.json"


def _ensure_cache_dir():
    """Ensure the cache directory exists."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Failed to create cache directory: %s", e)


def _save_cache_to_file():
    """Save the ticker cache to a file for cross-process sharing."""
    try:
        _ensure_cache_dir()
        with open(_CACHE_FILE, "w") as f:
            data = {
                "timestamp": time.time(),
                "tickers": _ticker_cache
            }
            json.dump(data, f)
        logger.debug("Cache saved to file: %s", _CACHE_FILE)
    except Exception as e:
        logger.warning("Failed to save cache to file: %s", e)


def _load_cache_from_file():
    """Load the ticker cache from file (for cross-process access)."""
    global _ticker_cache
    try:
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, "r") as f:
                data = json.load(f)
                age = time.time() - data.get("timestamp", 0)
                if age <= 60.0:  # Valid if less than 60 seconds old
                    _ticker_cache = data.get("tickers", {})
                    logger.debug("Loaded cache from file: %d symbols, age=%.1fs", 
                                len(_ticker_cache), age)
                    return True
                else:
                    logger.debug("Cache file expired (age=%.1fs)", age)
        return False
    except Exception as e:
        logger.warning("Failed to load cache from file: %s", e)
        return False


def _save_ohlcv_cache_to_file():
    """Save the OHLCV cache to a file for cross-process sharing."""
    try:
        _ensure_cache_dir()
        with open(_OHLCV_CACHE_FILE, "w") as f:
            data = {
                "timestamp": time.time(),
                "ohlcv": _ohlcv_cache
            }
            json.dump(data, f)
        logger.debug("OHLCV cache saved to file: %s", _OHLCV_CACHE_FILE)
    except Exception as e:
        logger.warning("Failed to save OHLCV cache to file: %s", e)


def _load_ohlcv_cache_from_file():
    """Load the OHLCV cache from file (for cross-process access)."""
    global _ohlcv_cache
    try:
        if _OHLCV_CACHE_FILE.exists():
            with open(_OHLCV_CACHE_FILE, "r") as f:
                data = json.load(f)
                age = time.time() - data.get("timestamp", 0)
                if age <= 3600.0:  # Valid if less than 1 hour old
                    _ohlcv_cache = data.get("ohlcv", {})
                    logger.debug("Loaded OHLCV cache from file: %d symbols, age=%.1fs", 
                                len(_ohlcv_cache), age)
                    return True
                else:
                    logger.debug("OHLCV cache file expired (age=%.1fs)", age)
        return False
    except Exception as e:
        logger.warning("Failed to load OHLCV cache from file: %s", e)
        return False


def _get_exchange():
    """Initialize and return CCXT Gate.io exchange instance."""
    logger.info("Initializing CCXT Gate.io exchange instance...")
    try:
        import ccxt

        api_key = os.environ.get("GATE_API_KEY", "") or os.environ.get("CCXT_API_KEY", "")
        api_secret = os.environ.get("GATE_API_SECRET", "") or os.environ.get("CCXT_SECRET", "")

        has_key = bool(api_key)
        has_secret = bool(api_secret)
        logger.debug("API credentials: key=%s, secret=%s", has_key, has_secret)
        if has_key and has_secret:
            logger.info("Using GATE_API_KEY for authenticated access")
        else:
            logger.info("No API credentials provided - using public (read-only) access")

        timeout_ms = int(os.environ.get("GATE_WS_TIMEOUT_MS", os.environ.get("CCXT_TIMEOUT_MS", "8000")))
        proxy_urls = [p.strip() for p in os.environ.get("GATE_WS_PROXY_URLS", os.environ.get("SOCKS5_PROXY", "")).split(",") if p.strip()]
        if os.environ.get("GATE_WS_DIRECT_FALLBACK", "false").lower() in ("1", "true", "yes"):
            proxy_urls.append("")
        selected_proxy = proxy_urls[0] if proxy_urls else ""

        config = {"enableRateLimit": True, "timeout": timeout_ms}
        if selected_proxy:
            config["proxies"] = {"http": selected_proxy, "https": selected_proxy}
        if api_key:
            config["apiKey"] = api_key
        if api_secret:
            config["secret"] = api_secret

        logger.info("CCXT Gate route: proxy=%s, timeout_ms=%d, candidates=%d",
                    selected_proxy or "DIRECT", timeout_ms, len(proxy_urls))
        logger.debug("CCXT config: enableRateLimit=%s, has_api_key=%s",
                     config.get("enableRateLimit"), bool(config.get("apiKey")))
        exchange = ccxt.gate(config)
        exchange._vibe_proxy_candidates = proxy_urls or [""]
        exchange._vibe_proxy_index = 0
        exchange._vibe_timeout_ms = timeout_ms
        logger.info("Gate.io exchange instance created: id=%s, version=%s, authenticated=%s",
                    exchange.id, exchange.version, has_key and has_secret)
        return exchange
    except ImportError:
        logger.error("ccxt not installed. Install with: pip install ccxt")
        return None
    except Exception as e:
        logger.exception("Failed to initialize exchange: %s", e)
        return None


def get_cached_ticker(symbol: str) -> Optional[Dict[str, Any]]:
    """Get cached ticker data for a symbol.

    First checks the in-memory cache, then falls back to file cache
    for cross-process access.

    Args:
        symbol: Trading pair like "BTC/USDT" or "BTC-USDT"

    Returns:
        Dict with keys: last, bid, ask, high, low, volume, timestamp
        None if no cached data
    """
    import time as _time
    query_start = _time.time()
    normalized = symbol.replace("-", "/").upper().strip()
    logger.info("[CACHE-QUERY] Querying ticker: symbol=%s → normalized=%s", symbol, normalized)

    with _cache_lock:
        ticker = _ticker_cache.get(normalized)
        if ticker:
            age = _time.time() - ticker.get("timestamp", 0)
            logger.info("[CACHE-QUERY] Memory cache hit: %s, age=%.1fs, price=%.2f",
                         normalized, age, float(ticker.get("last", 0) or 0))
            if age <= 30.0:
                query_elapsed = _time.time() - query_start
                logger.info("[CACHE-QUERY] ✔ Returning fresh memory cache for %s (price=%.2f, query_time=%.3fs)",
                            normalized, float(ticker.get("last", 0) or 0), query_elapsed)
                return ticker
            else:
                logger.info("[CACHE-QUERY] Memory cache expired for %s (age=%.1fs > 30s threshold)",
                               normalized, age)
    
    # Try file cache for cross-process access
    logger.info("[CACHE-QUERY] Memory cache miss, trying file cache for %s", normalized)
    file_load_start = _time.time()
    if _load_cache_from_file():
        file_load_elapsed = _time.time() - file_load_start
        logger.info("[CACHE-QUERY] File cache loaded in %.3fs", file_load_elapsed)
        with _cache_lock:
            ticker = _ticker_cache.get(normalized)
            if ticker:
                age = _time.time() - ticker.get("timestamp", 0)
                if age <= 60.0:
                    query_elapsed = _time.time() - query_start
                    logger.info("[CACHE-QUERY] ✔ Returning file cache for %s (price=%.2f, age=%.1fs, total_query_time=%.3fs)",
                                normalized, float(ticker.get("last", 0) or 0), age, query_elapsed)
                    return ticker
                else:
                    logger.info("[CACHE-QUERY] File cache expired for %s (age=%.1fs > 60s threshold)", normalized, age)
    
    query_elapsed = _time.time() - query_start
    logger.warning("[CACHE-QUERY] ✘ Cache miss for %s, returning None (query_time=%.3fs)", normalized, query_elapsed)
    return None


def get_cached_ohlcv(symbol: str, timeframe: str = "1d", limit: int = 100) -> Optional[List[List[float]]]:
    """Get cached OHLCV historical data for a symbol.

    First checks the in-memory cache, then falls back to file cache,
    and finally fetches directly from CCXT if needed.

    Args:
        symbol: Trading pair like "BTC/USDT" or "BTC-USDT"
        timeframe: Timeframe (1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w)
        limit: Number of candles to fetch (default: 100)

    Returns:
        List of [timestamp, open, high, low, close, volume] or None
    """
    import time as _time
    query_start = _time.time()
    normalized = symbol.replace("-", "/").upper().strip()
    timeframe = timeframe.lower()
    
    logger.info("[OHLCV-QUERY] ═══════════════════════════════════════════════════════════")
    logger.info("[OHLCV-QUERY] 📊 Historical data request: %s-%s (limit=%d)", 
                normalized, timeframe, limit)
    logger.info("[OHLCV-QUERY] ═══════════════════════════════════════════════════════════")

    # Data source attempt chain
    attempt_chain = []
    
    # First check memory cache
    logger.info("[OHLCV-QUERY] [1/3] Checking MEMORY CACHE...")
    with _cache_lock:
        if normalized in _ohlcv_cache:
            if timeframe in _ohlcv_cache[normalized]:
                cached_data = _ohlcv_cache[normalized][timeframe]
                age = _time.time() - cached_data.get("timestamp", 0)
                logger.info("[OHLCV-QUERY]       Found %d bars, age=%.1fs", 
                           len(cached_data.get("data", [])), age)
                if age <= 3600.0:  # Valid if less than 1 hour old
                    data = cached_data.get("data", [])
                    query_elapsed = _time.time() - query_start
                    logger.info("[OHLCV-QUERY] ✅ [MEMORY CACHE HIT] %s-%s (%d bars, time=%.3fs)", 
                                normalized, timeframe, len(data), query_elapsed)
                    return data[:limit]
                else:
                    logger.warning("[OHLCV-QUERY]       Memory cache EXPIRED (age=%.1fs > 3600s)", age)
            else:
                logger.warning("[OHLCV-QUERY]       Symbol found but timeframe '%s' not cached", timeframe)
        else:
            logger.warning("[OHLCV-QUERY]       Symbol '%s' not in memory cache", normalized)
    attempt_chain.append(("MEMORY CACHE", False, "cache miss or expired"))
    
    # Try file cache
    logger.info("[OHLCV-QUERY] [2/3] Checking FILE CACHE (path=%s)...", _OHLCV_CACHE_FILE)
    if _load_ohlcv_cache_from_file():
        with _cache_lock:
            if normalized in _ohlcv_cache:
                if timeframe in _ohlcv_cache[normalized]:
                    cached_data = _ohlcv_cache[normalized][timeframe]
                    age = _time.time() - cached_data.get("timestamp", 0)
                    logger.info("[OHLCV-QUERY]       Found %d bars, age=%.1fs", 
                               len(cached_data.get("data", [])), age)
                    if age <= 7200.0:  # Valid if less than 2 hours old
                        data = cached_data.get("data", [])
                        query_elapsed = _time.time() - query_start
                        logger.info("[OHLCV-QUERY] ✅ [FILE CACHE HIT] %s-%s (%d bars, age=%.1fs, time=%.3fs)", 
                                    normalized, timeframe, len(data), age, query_elapsed)
                        return data[:limit]
                    else:
                        logger.warning("[OHLCV-QUERY]       File cache EXPIRED (age=%.1fs > 7200s)", age)
                else:
                    logger.warning("[OHLCV-QUERY]       Symbol found but timeframe '%s' not cached", timeframe)
            else:
                logger.warning("[OHLCV-QUERY]       Symbol '%s' not in file cache", normalized)
    else:
        logger.warning("[OHLCV-QUERY]       File cache not accessible or empty")
    attempt_chain.append(("FILE CACHE", False, "cache miss or expired"))
    
    # Fetch directly from CCXT
    logger.info("[OHLCV-QUERY] [3/3] FETCHING FROM CCXT (Gate.io)...")
    exchange = _get_exchange()
    if exchange is None:
        logger.error("[OHLCV-QUERY] ❌ [CCXT FAILED] Exchange initialization failed")
        attempt_chain.append(("CCXT", False, "exchange initialization failed"))
        _log_attempt_chain(query_start, attempt_chain)
        return None
    
    try:
        fetch_start = _time.time()
        logger.info("[OHLCV-QUERY]       Calling exchange.fetch_ohlcv('%s', '%s', limit=%d)...", 
                    normalized, timeframe, limit)
        ohlcv = exchange.fetch_ohlcv(normalized, timeframe, limit=limit)
        fetch_elapsed = _time.time() - fetch_start
        
        if ohlcv and len(ohlcv) > 0:
            logger.info("[OHLCV-QUERY] ✅ [CCXT SUCCESS] %s-%s: %d bars fetched in %.2fs",
                       normalized, timeframe, len(ohlcv), fetch_elapsed)
            attempt_chain.append(("CCXT", True, f"{len(ohlcv)} bars"))
            
            # Cache the result
            with _cache_lock:
                if normalized not in _ohlcv_cache:
                    _ohlcv_cache[normalized] = {}
                _ohlcv_cache[normalized][timeframe] = {
                    "data": ohlcv,
                    "timestamp": _time.time()
                }
            _save_ohlcv_cache_to_file()
            logger.info("[OHLCV-QUERY]       Result cached for future use")
            
            query_elapsed = _time.time() - query_start
            logger.info("[OHLCV-QUERY] ✅ TOTAL: %s-%s (%d bars, total_time=%.3fs)", 
                        normalized, timeframe, len(ohlcv), query_elapsed)
            _log_attempt_chain(query_start, attempt_chain)
            return ohlcv
        else:
            logger.warning("[OHLCV-QUERY] ⚠️ [CCXT WARNING] Empty result returned")
            attempt_chain.append(("CCXT", False, "empty result"))
            
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)[:200]
        logger.error("[OHLCV-QUERY] ❌ [CCXT FAILED] %s: %s", error_type, error_msg)
        attempt_chain.append(("CCXT", False, f"{error_type}: {error_msg}"))
    
    _log_attempt_chain(query_start, attempt_chain)
    logger.warning("[OHLCV-QUERY] ❌ ALL DATA SOURCES FAILED - returning None")
    logger.info("[OHLCV-QUERY] 💡 HINT: Check network connectivity to Gate.io API")
    logger.info("[OHLCV-QUERY] 💡 HINT: Alternative: Use 'from src.gate_ws_client import get_cached_ticker' for real-time data")
    return None


def _log_attempt_chain(query_start, attempt_chain):
    """Log the complete data source attempt chain."""
    import time as _time
    elapsed = _time.time() - query_start
    logger.info("[OHLCV-QUERY] ─────────────────────────────────────────────────────────")
    logger.info("[OHLCV-QUERY] 📋 Attempt Chain Summary (total_time=%.3fs):", elapsed)
    for i, (source, success, reason) in enumerate(attempt_chain, 1):
        status = "✅" if success else "❌"
        logger.info("[OHLCV-QUERY]    %d. %s %s: %s", i, status, source, reason)
    logger.info("[OHLCV-QUERY] ─────────────────────────────────────────────────────────")


def _apply_gate_proxy(exchange, proxy: str):
    if proxy:
        exchange.proxies = {"http": proxy, "https": proxy}
    else:
        exchange.proxies = {}


def _fetch_ticker_with_retry(exchange, symbol: str, thread_id: str):
    import requests

    retries = max(0, int(os.environ.get("GATE_WS_RETRIES", "2")))
    retry_delay = max(0.0, float(os.environ.get("GATE_WS_RETRY_DELAY", "1.5")))
    timeout = max(1.0, int(os.environ.get("GATE_WS_TIMEOUT_MS", "8000")) / 1000)
    candidates = getattr(exchange, "_vibe_proxy_candidates", [""]) or [""]
    last_error = None
    currency_pair = symbol.replace("/", "_")
    url = "https://api.gateio.ws/api/v4/spot/tickers"

    for attempt in range(retries + 1):
        proxy_index = getattr(exchange, "_vibe_proxy_index", 0) % len(candidates)
        proxy = candidates[proxy_index]
        proxies = {"http": proxy, "https": proxy} if proxy else None
        started = time.time()
        try:
            logger.debug("[%s] Fetching %s attempt=%d/%d proxy=%s timeout=%.1fs",
                         thread_id, symbol, attempt + 1, retries + 1, proxy or "DIRECT", timeout)
            response = requests.get(url, params={"currency_pair": currency_pair}, proxies=proxies, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            raw = data[0] if isinstance(data, list) and data else data
            ticker = {
                "last": float(raw.get("last") or 0),
                "bid": float(raw.get("highest_bid") or raw.get("bid") or 0),
                "ask": float(raw.get("lowest_ask") or raw.get("ask") or 0),
                "high": float(raw.get("high_24h") or 0),
                "low": float(raw.get("low_24h") or 0),
                "baseVolume": float(raw.get("base_volume") or 0),
                "quoteVolume": float(raw.get("quote_volume") or 0),
                "percentage": float(raw.get("change_percentage") or 0),
            }
            elapsed = time.time() - started
            if attempt > 0 or elapsed > 2.0:
                logger.info("[%s] Gate fetch recovered: symbol=%s attempt=%d elapsed=%.2fs proxy=%s",
                            thread_id, symbol, attempt + 1, elapsed, proxy or "DIRECT")
            return ticker, elapsed
        except Exception as e:
            elapsed = time.time() - started
            last_error = e
            logger.warning("[%s] Gate fetch failed: symbol=%s attempt=%d/%d elapsed=%.2fs proxy=%s error=%s: %s",
                           thread_id, symbol, attempt + 1, retries + 1, elapsed, proxy or "DIRECT",
                           type(e).__name__, str(e)[:240])
            if len(candidates) > 1:
                exchange._vibe_proxy_index = (proxy_index + 1) % len(candidates)
                logger.info("[%s] Switching Gate proxy route to candidate #%d: %s",
                            thread_id, exchange._vibe_proxy_index + 1,
                            candidates[exchange._vibe_proxy_index] or "DIRECT")
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))

    raise last_error


def _poll_tickers():
    """Background thread that polls ticker data."""
    global _poll_running
    thread_id = threading.current_thread().name
    logger.info("[%s] Poll thread started", thread_id)

    exchange = _get_exchange()
    if exchange is None:
        logger.error("[%s] Abort: exchange initialization failed", thread_id)
        _poll_running = False
        return

    symbols = os.environ.get(
        "GATE_WS_SYMBOLS",
        "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
    ).split(",")
    symbols = [s.strip().upper() for s in symbols]

    poll_interval = float(os.environ.get("GATE_WS_POLL_INTERVAL", "5.0"))

    logger.info("[%s] Will poll %d symbols: %s, interval=%.1fs",
                thread_id, len(symbols), symbols, poll_interval)

    poll_count = 0
    error_count = 0

    while _poll_running:
        poll_count += 1
        cycle_start = time.time()
        logger.debug("[%s] Poll cycle #%d starting...", thread_id, poll_count)

        updated = 0
        failed = 0

        try:
            for symbol in symbols:
                try:
                    ticker, fetch_time = _fetch_ticker_with_retry(exchange, symbol, thread_id)
                    logger.info("[%s] Fetched %s in %.2fs — last=%.2f, vol=%.2f, change=%.2f%%",
                                thread_id, symbol, fetch_time,
                                float(ticker.get("last", 0) or 0),
                                float(ticker.get("baseVolume", 0) or 0),
                                float(ticker.get("percentage", 0) or 0))

                    ticker_data = {
                        "last": float(ticker.get("last", 0) or 0),
                        "bid": float(ticker.get("bid", 0) or 0),
                        "ask": float(ticker.get("ask", 0) or 0),
                        "high": float(ticker.get("high", 0) or 0),
                        "low": float(ticker.get("low", 0) or 0),
                        "volume": float(ticker.get("baseVolume", 0) or 0),
                        "quoteVolume": float(ticker.get("quoteVolume", 0) or 0),
                        "changePercent": float(ticker.get("percentage", 0) or 0),
                        "timestamp": time.time()
                    }

                    with _cache_lock:
                        _ticker_cache[symbol] = ticker_data
                    updated += 1

                except Exception as e:
                    failed += 1
                    error_count += 1
                    logger.warning("[%s] Failed to fetch %s: %s (type=%s)",
                                   thread_id, symbol, e, type(e).__name__)

            cycle_time = time.time() - cycle_start
            logger.info("[DATA-POLL] Cycle #%d completed: updated=%d, failed=%d, elapsed=%.2fs",
                        poll_count, updated, failed, cycle_time)

            if updated > 0:
                with _cache_lock:
                    logger.info("[DATA-POLL] Current cache entries: %d symbols", len(_ticker_cache))
                # Save to file cache for cross-process access
                save_start = time.time()
                _save_cache_to_file()
                save_elapsed = time.time() - save_start
                logger.info("[DATA-POLL] File cache saved in %.3fs", save_elapsed)

            time.sleep(poll_interval)

        except Exception as e:
            logger.exception("[%s] Poll cycle error: %s", thread_id, e)
            error_count += 1
            time.sleep(poll_interval)

    logger.info("[%s] Poll thread stopped after %d cycles, %d errors",
                thread_id, poll_count, error_count)


def start():
    """Start the Gate.io market data client background thread."""
    global _poll_running, _poll_thread
    logger.info("start() called — checking config")

    exchange_name = os.environ.get("CCXT_EXCHANGE", "gate").lower()
    logger.debug("CCXT_EXCHANGE=%s", exchange_name)

    if exchange_name != "gate":
        logger.warning("Skipping: CCXT_EXCHANGE=%s (not 'gate')", exchange_name)
        return

    if _poll_running:
        logger.warning("Client is already running — skipping duplicate start")
        return

    _poll_running = True
    _poll_thread = threading.Thread(target=_poll_tickers, daemon=True, name="GateWSPoller")
    _poll_thread.start()
    logger.info("✅ Gate.io market data client started (thread=%s)", _poll_thread.name)


def stop():
    """Stop the Gate.io market data client."""
    global _poll_running
    logger.info("stop() called — stopping poll thread")
    _poll_running = False
    if _poll_thread:
        logger.info("Thread name: %s, is_alive=%s", _poll_thread.name, _poll_thread.is_alive())
    logger.info("✅ Gate.io client stopped")


def is_running() -> bool:
    """Check if client is running."""
    running = _poll_running
    logger.debug("is_running() → %s", running)
    return running


def get_all_tickers() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the current ticker cache (for diagnostics/tests)."""
    with _cache_lock:
        copy = {k: dict(v) for k, v in _ticker_cache.items()}
    logger.debug("get_all_tickers() → %d symbols", len(copy))
    return copy


# Auto-initialize on module import (if explicitly enabled)
if os.environ.get("GATE_WS_AUTO_START", "").lower() in ("true", "1", "yes"):
    logger.info("GATE_WS_AUTO_START detected — auto-initializing")
    start()
