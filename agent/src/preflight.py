"""Startup preflight checks for data sources and LLM provider.

Runs connectivity checks at startup and prints a status table.
Non-critical failures are warnings (degraded functionality),
LLM provider failure is critical (blocks startup).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec
from typing import List, Optional

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: str  # "ready", "error", "not_configured", "skipped"
    message: str
    impact: str  # what breaks if this fails
    critical: bool = False


def _check_llm_provider() -> CheckResult:
    """Verify LLM provider connectivity."""
    from src.providers.llm import _ensure_dotenv, _sync_provider_env

    _ensure_dotenv()
    provider = os.getenv("LANGCHAIN_PROVIDER", "").strip()
    model = os.getenv("LANGCHAIN_MODEL_NAME", "").strip()

    if not provider:
        return CheckResult(
            name="LLM Provider",
            status="not_configured",
            message="LANGCHAIN_PROVIDER not set in .env",
            impact="agent cannot function",
            critical=True,
        )
    if not model:
        return CheckResult(
            name=f"LLM ({provider})",
            status="not_configured",
            message="LANGCHAIN_MODEL_NAME not set in .env",
            impact="agent cannot function",
            critical=True,
        )

    _sync_provider_env()
    base_url = os.getenv("OPENAI_BASE_URL", "") or os.getenv("OPENAI_API_BASE", "")

    if provider.lower() in {"openai-codex", "openai_codex"}:
        try:
            from src.providers.openai_codex import get_openai_codex_login_status

            token = get_openai_codex_login_status()
        except Exception as exc:
            return CheckResult(
                name=f"LLM ({provider})",
                status="error",
                message=f"OAuth status unavailable: {exc}",
                impact="run `vibe-trading provider login openai-codex`",
                critical=True,
            )
        if not token:
            return CheckResult(
                name=f"LLM ({provider})",
                status="not_configured",
                message="ChatGPT OAuth login not found",
                impact="run `vibe-trading provider login openai-codex`",
                critical=True,
            )
        account = getattr(token, "account_id", None) or "authenticated account"
        return CheckResult(
            name=f"LLM ({provider})",
            status="ready",
            message=f"{model} via ChatGPT OAuth ({account})",
            impact="",
        )

    if not base_url:
        return CheckResult(
            name=f"LLM ({provider})",
            status="not_configured",
            message=f"base URL not set for {provider}",
            impact="agent cannot function",
            critical=True,
        )

    # Ping the base URL
    try:
        import requests

        # Strip /v1 suffix for health check, just test TCP+SSL
        ping_url = base_url.rstrip("/")
        if ping_url.endswith("/v1"):
            ping_url = ping_url[:-3]
        resp = requests.get(ping_url, timeout=10)
        return CheckResult(
            name=f"LLM ({provider})",
            status="ready",
            message=f"{model} via {base_url}",
            impact="",
        )
    except Exception as exc:
        return CheckResult(
            name=f"LLM ({provider})",
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            impact="agent cannot function",
            critical=True,
        )


def _check_okx() -> CheckResult:
    """Check OKX public API reachability (fallback for crypto)."""
    exchange_id = os.environ.get("CCXT_EXCHANGE", "gate").lower()
    
    if exchange_id:
        return CheckResult(
            name="OKX API",
            status="hidden",
            message=f"using CCXT ({exchange_id}) as primary",
            impact="OKX is fallback only",
        )
    
    try:
        import requests

        resp = requests.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": "BTC-USDT", "bar": "1D", "limit": "1"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == "0":
            return CheckResult(name="OKX API", status="ready", message="reachable", impact="")
        return CheckResult(
            name="OKX API",
            status="error",
            message=f"API returned code={data.get('code')}: {data.get('msg', '')}",
            impact="crypto backtest unavailable",
        )
    except Exception as exc:
        return CheckResult(
            name="OKX API",
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            impact="crypto backtest unavailable",
        )


def _check_yfinance() -> CheckResult:
    """Check yfinance availability."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return CheckResult(
            name="yfinance",
            status="skipped",
            message="package not installed",
            impact="US/HK equity backtest unavailable",
        )

    try:
        import yfinance as yf

        ticker = yf.Ticker("AAPL")
        info = ticker.fast_info
        if hasattr(info, "last_price") and info.last_price:
            return CheckResult(name="yfinance", status="ready", message="reachable", impact="")
        return CheckResult(name="yfinance", status="ready", message="reachable (no price data)", impact="")
    except Exception as exc:
        return CheckResult(
            name="yfinance",
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            impact="US/HK equity backtest unavailable",
        )


def _check_tushare() -> CheckResult:
    """Check Tushare token configuration."""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return CheckResult(
            name="Tushare",
            status="not_configured",
            message="TUSHARE_TOKEN not set (optional)",
            impact="A-share data unavailable",
        )

    try:
        import tushare  # noqa: F401
    except ImportError:
        return CheckResult(
            name="Tushare",
            status="skipped",
            message="package not installed",
            impact="A-share data unavailable",
        )

    return CheckResult(name="Tushare", status="ready", message="token configured", impact="")


def _check_akshare() -> CheckResult:
    """Check akshare availability."""
    if find_spec("akshare") is None:
        return CheckResult(
            name="akshare",
            status="skipped",
            message="package not installed",
            impact="A-share/forex fallback unavailable",
        )
    return CheckResult(name="akshare", status="ready", message="installed", impact="")


def _check_ccxt() -> CheckResult:
    """Check ccxt availability and configured exchange connectivity."""
    try:
        import ccxt
    except ImportError:
        return CheckResult(
            name="ccxt",
            status="skipped",
            message="package not installed",
            impact="crypto data unavailable",
        )

    exchange_id = os.environ.get("CCXT_EXCHANGE", "gate").lower()
    
    if exchange_id == "gate":
        api_key = os.environ.get("GATE_API_KEY", "") or os.environ.get("CCXT_API_KEY", "")
        api_secret = os.environ.get("GATE_API_SECRET", "") or os.environ.get("CCXT_SECRET", "")
        has_credentials = bool(api_key) and bool(api_secret)
        
        try:
            exchange = ccxt.gate({
                "enableRateLimit": True,
                "apiKey": api_key,
                "secret": api_secret,
            })
            
            ticker = exchange.fetch_ticker("BTC/USDT")
            if ticker and ticker.get("last"):
                msg = f"Ready (authenticated)" if has_credentials else f"Ready (public)"
                return CheckResult(
                    name=f"CCXT (Gate.io)",
                    status="ready",
                    message=msg,
                    impact="",
                )
            return CheckResult(
                name=f"CCXT (Gate.io)",
                status="error",
                message="No ticker data received",
                impact="crypto data unavailable",
            )
        except Exception as exc:
            error_msg = str(exc)
            if any(keyword in error_msg.lower() for keyword in ["connection", "timeout", "network", "proxy"]):
                return CheckResult(
                    name=f"CCXT (Gate.io)",
                    status="error",
                    message=f"Network error: {type(exc).__name__}",
                    impact="Check network connectivity or proxy configuration",
                )
            elif "authentication" in error_msg.lower() or "invalid" in error_msg.lower():
                return CheckResult(
                    name=f"CCXT (Gate.io)",
                    status="error",
                    message=f"Authentication failed",
                    impact="Check API key and secret",
                )
            else:
                return CheckResult(
                    name=f"CCXT (Gate.io)",
                    status="error",
                    message=f"{type(exc).__name__}: {exc}",
                    impact="crypto data unavailable",
                )
    
    elif exchange_id:
        try:
            exchange_class = getattr(ccxt, exchange_id, None)
            if not exchange_class:
                return CheckResult(
                    name=f"CCXT ({exchange_id})",
                    status="error",
                    message=f"Exchange '{exchange_id}' not supported",
                    impact="crypto data unavailable",
                )
            
            exchange = exchange_class({"enableRateLimit": True})
            ticker = exchange.fetch_ticker("BTC/USDT")
            if ticker and ticker.get("last"):
                return CheckResult(
                    name=f"CCXT ({exchange_id})",
                    status="ready",
                    message="Ready (public)",
                    impact="",
                )
            return CheckResult(
                name=f"CCXT ({exchange_id})",
                status="error",
                message="No ticker data received",
                impact="crypto data unavailable",
            )
        except Exception as exc:
            error_msg = str(exc)
            if any(keyword in error_msg.lower() for keyword in ["connection", "timeout", "network", "proxy"]):
                return CheckResult(
                    name=f"CCXT ({exchange_id})",
                    status="error",
                    message=f"Network error: {type(exc).__name__}",
                    impact="Check network connectivity or proxy configuration",
                )
            else:
                return CheckResult(
                    name=f"CCXT ({exchange_id})",
                    status="error",
                    message=f"{type(exc).__name__}: {exc}",
                    impact="crypto data unavailable",
                )
    
    return CheckResult(
        name="ccxt",
        status="ready",
        message="installed (no exchange configured)",
        impact="",
    )


# -- Status icons and colors --------------------------------------------------

_STATUS_DISPLAY = {
    "ready": ("[green]OK[/green]", "green"),
    "error": ("[red]FAIL[/red]", "red"),
    "not_configured": ("[yellow]N/A[/yellow]", "yellow"),
    "skipped": ("[dim]SKIP[/dim]", "dim"),
    "hidden": ("", ""),
}


def run_preflight(console: Optional[Console] = None) -> List[CheckResult]:
    """Run all preflight checks and print results.

    Args:
        console: Rich console for output. Creates one if not provided.

    Returns:
        List of check results.
    """
    if console is None:
        console = Console()

    checks = [
        _check_llm_provider,
        _check_ccxt,
        _check_okx,
        _check_yfinance,
        _check_tushare,
        _check_akshare,
    ]

    results: List[CheckResult] = []
    for check_fn in checks:
        results.append(check_fn())

    # Build display table (filter out hidden entries)
    display_results = [r for r in results if r.status != "hidden"]
    table = Table(show_header=False, show_edge=False, padding=(0, 1), expand=False)
    table.add_column(width=4)   # icon
    table.add_column(width=18)  # name
    table.add_column()          # message

    for r in display_results:
        icon, color = _STATUS_DISPLAY[r.status]
        detail = r.message
        if r.status in ("error", "not_configured") and r.impact:
            detail = f"{r.message} ({r.impact})"
        table.add_row(icon, f"[{color}]{r.name}[/{color}]", f"[{color}]{detail}[/{color}]")

    console.print()
    console.print("[bold]Preflight Check[/bold]")
    console.print(table)

    # 显示当前加密货币数据源配置
    exchange_id = os.environ.get("CCXT_EXCHANGE", "gate").lower()
    console.print(f"\n[bold]Crypto Data Source Configuration[/bold]")
    console.print(f"  Primary: [green]CCXT ({exchange_id})[/green]")
    console.print(f"  Fallback: [yellow]OKX[/yellow]")
    console.print(f"  Routing: [blue]ccxt > okx[/blue]")
    
    # 检查 CCXT 状态并给出降级提示
    ccxt_result = next((r for r in results if r.name.startswith("CCXT")), None)
    okx_result = next((r for r in results if r.name == "OKX API"), None)
    
    if ccxt_result and ccxt_result.status != "ready":
        console.print(f"\n[bold yellow]⚠️  Primary data source {ccxt_result.name} unavailable[/bold yellow]")
        if okx_result and okx_result.status == "ready":
            console.print(f"   Will fall back to OKX API")
        else:
            console.print(f"   OKX API also unavailable - crypto data may be limited")

    has_critical = any(r.critical and r.status != "ready" for r in results)
    if has_critical:
        console.print("\n[bold red]Critical check failed - agent cannot start without a working LLM provider.[/bold red]")
        console.print("[dim]  See: agent/.env.example for configuration reference[/dim]")
    else:
        ready_count = sum(1 for r in display_results if r.status == "ready")
        console.print(f"\n[dim]{ready_count}/{len(display_results)} services ready[/dim]")

    console.print()
    return results
