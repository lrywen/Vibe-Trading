"""Built-in Gate.io connector profiles.

Read-only paper and live profiles plus order-placing profiles.
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

GATE_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="gate-live-sdk-readonly",
        connector="gate",
        label="Gate.io Live · ccxt Read-Only",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "Reads a Gate.io live account via ccxt. "
            "Order placement is not exposed in this profile."
        ),
    ),
    TradingProfile(
        id="gate-live-trade",
        connector="gate",
        label="Gate.io Live · ccxt Trading",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place.requires_mandate",),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "Places orders on a Gate.io live account via ccxt. "
            "Live order placement must be gated by the user's mandate; the "
            "orders.place.requires_mandate capability signals that requirement upstream."
        ),
    ),
)
