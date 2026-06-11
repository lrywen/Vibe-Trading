"""Gate.io trading connector via the ``ccxt`` unified exchange client."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from src.config.paths import get_runtime_root

CONFIG_FILENAME = "gate.json"

PROFILE_ENVIRONMENTS = {
    "live-readonly": "live",
    "live": "live",
}

DEFAULT_HOST = "https://api.gateio.ws"

_QUOTE_ASSETS = ("USDT", "USDC", "BTC", "ETH")


class GateDependencyError(RuntimeError):
    """Raised when the optional ``ccxt`` package is not installed."""


class GateConfigError(RuntimeError):
    """Raised when the connector configuration is missing or invalid."""


def normalize_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper().replace("-", "/")
    if "/" in clean:
        return clean
    for quote in _QUOTE_ASSETS:
        if clean.endswith(quote) and len(clean) > len(quote):
            return f"{clean[: -len(quote)]}/{quote}"
    return clean


@dataclass(frozen=True)
class GateConfig:
    api_key: str = ""
    api_secret: str = ""
    profile: str = "live-readonly"
    host: str = DEFAULT_HOST
    timeout: float = 15.0
    readonly: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> "GateConfig":
        import os
        payload = dict(data or {})
        profile = str(payload.get("profile") or "live-readonly").strip().lower()
        if profile not in PROFILE_ENVIRONMENTS:
            raise GateConfigError("profile must be 'live-readonly' or 'live'")
        return cls(
            api_key=str(payload.get("api_key") or os.environ.get("GATE_API_KEY", "")).strip(),
            api_secret=str(payload.get("api_secret") or os.environ.get("GATE_API_SECRET", "")).strip(),
            profile=profile,
            host=str(payload.get("host") or DEFAULT_HOST).strip(),
            timeout=float(payload.get("timeout") or 15.0),
            readonly=bool(payload.get("readonly", True)),
        )

    def with_overrides(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        profile: str | None = None,
        host: str | None = None,
    ) -> "GateConfig":
        payload = asdict(self)
        if api_key is not None:
            payload["api_key"] = api_key
        if api_secret is not None:
            payload["api_secret"] = api_secret
        if profile is not None:
            payload["profile"] = profile
        if host is not None:
            payload["host"] = host
        return GateConfig.from_mapping(payload)

    @property
    def environment(self) -> str:
        return PROFILE_ENVIRONMENTS.get(self.profile, "live")


import os
_OVERRIDE_KEYS = ("api_key", "api_secret", "profile", "host")


def build_config(profile_config: Mapping[str, Any] | None = None, overrides: Mapping[str, Any] | None = None) -> "GateConfig":
    base = asdict(load_config())
    for key, value in dict(profile_config or {}).items():
        if value is not None:
            base[key] = value
    cfg = GateConfig.from_mapping(base)
    clean = {k: v for k, v in dict(overrides or {}).items() if k in _OVERRIDE_KEYS and v not in (None, "")}
    return cfg.with_overrides(**clean) if clean else cfg


def config_path() -> Path:
    return get_runtime_root() / CONFIG_FILENAME


def load_config() -> GateConfig:
    path = config_path()
    if path.exists():
        try:
            with open(path) as f:
                return GateConfig.from_mapping(json.load(f))
        except (json.JSONDecodeError, GateConfigError):
            pass
    return GateConfig()


def save_config(config: GateConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(asdict(config), f, indent=2)


def get_ccxt_module() -> ModuleType:
    try:
        import ccxt
        return ccxt
    except ImportError:
        raise GateDependencyError("ccxt is required for Gate.io connector")


def _get_client(config: GateConfig):
    ccxt = get_ccxt_module()
    timeout_ms = int(os.environ.get("GATE_WS_TIMEOUT_MS", int(config.timeout * 1000)))
    proxy_urls = [p.strip() for p in os.environ.get("GATE_WS_PROXY_URLS", os.environ.get("SOCKS5_PROXY", "")).split(",") if p.strip()]
    selected_proxy = proxy_urls[0] if proxy_urls else ""
    ccxt_config = {
        "apiKey": config.api_key,
        "secret": config.api_secret,
        "enableRateLimit": True,
        "timeout": timeout_ms,
    }
    if selected_proxy:
        ccxt_config["proxies"] = {"http": selected_proxy, "https": selected_proxy}
    exchange = ccxt.gate(ccxt_config)
    return exchange


def check_status(config: GateConfig) -> dict[str, Any]:
    try:
        client = _get_client(config)
        accounts = client.private_spot_get_accounts()
        return {"status": "ok", "message": "Connected to Gate.io", "account_count": len(accounts)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_account_snapshot(config: GateConfig) -> dict[str, Any]:
    try:
        client = _get_client(config)
        accounts = client.private_spot_get_accounts()
        balance = {
            "free": {},
            "used": {},
            "total": {},
            "raw": accounts,
        }
        for item in accounts:
            currency = str(item.get("currency") or "").upper()
            if not currency:
                continue
            available = float(item.get("available") or 0)
            locked = float(item.get("locked") or 0)
            balance["free"][currency] = available
            balance["used"][currency] = locked
            balance["total"][currency] = available + locked
        return {"status": "ok", "balance": balance}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_positions(config: GateConfig) -> dict[str, Any]:
    try:
        account = get_account_snapshot(config)
        if account.get("status") != "ok":
            return account
        total = account.get("balance", {}).get("total", {})
        positions = []
        for symbol, quantity in total.items():
            if quantity > 0:
                positions.append({
                    "symbol": symbol,
                    "quantity": quantity,
                })
        return {"status": "ok", "positions": positions}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_open_orders(config: GateConfig, include_executions: bool = False) -> dict[str, Any]:
    try:
        client = _get_client(config)
        orders = client.private_spot_get_open_orders()
        return {"status": "ok", "orders": orders}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_quote(symbol: str, config: GateConfig) -> dict[str, Any]:
    try:
        # First try to use cache
        try:
            from src.gate_ws_client import get_cached_ticker
            normalized = normalize_symbol(symbol)
            ticker = get_cached_ticker(normalized.replace("/", "-"))
            if ticker:
                return {
                    "status": "ok",
                    "quote": {
                        "symbol": normalized,
                        "last": ticker.get("last"),
                        "bid": ticker.get("bid"),
                        "ask": ticker.get("ask"),
                        "high": ticker.get("high24h"),
                        "low": ticker.get("low24h"),
                        "volume": ticker.get("quoteVolume"),
                    }
                }
        except Exception:
            pass

        # Fallback to direct CCXT
        client = _get_client(config)
        normalized = normalize_symbol(symbol)
        ticker = client.fetch_ticker(normalized)
        return {
            "status": "ok",
            "quote": {
                "symbol": normalized,
                "last": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "high": ticker.get("high"),
                "low": ticker.get("low"),
                "volume": ticker.get("volume"),
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_historical_bars(
    symbol: str,
    config: GateConfig,
    *,
    period: str = "1d",
    limit: int = 100,
) -> dict[str, Any]:
    try:
        # First try to use cache
        try:
            from src.gate_ws_client import get_cached_ohlcv
            normalized = normalize_symbol(symbol)
            ohlcv = get_cached_ohlcv(normalized.replace("/", "-"), timeframe=period, limit=limit)
            if ohlcv:
                return {"status": "ok", "bars": ohlcv}
        except Exception:
            pass

        # Fallback to direct CCXT
        client = _get_client(config)
        normalized = normalize_symbol(symbol)
        ohlcv = client.fetch_ohlcv(normalized, period, limit=limit)
        return {"status": "ok", "bars": ohlcv}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def place_order(
    config: GateConfig,
    *,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "limit",
    limit_price: float | None = None,
    time_in_force: str = "GTC",
) -> dict[str, Any]:
    try:
        client = _get_client(config)
        normalized = normalize_symbol(symbol)
        params = {}

        if order_type.lower() == "market":
            order = client.create_order(normalized, "market", side.lower(), quantity)
        else:
            if limit_price is None:
                return {"status": "error", "error": "limit_price required for limit orders"}
            order = client.create_order(normalized, "limit", side.lower(), quantity, limit_price)

        return {"status": "ok", "order": order}
    except Exception as e:
        return {"status": "error", "error": str(e)}
