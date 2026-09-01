"""Deterministic pricing rules used by the acceptance tasks."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DEFAULT_TAX_RATES = {
    "US": 0.08,
    "DE": 0.19,
    "JP": 0.10,
}

REMOTE_ZIP_PREFIXES = ("999", "969")


def round_money(value: float) -> float:
    """Round to cents using stable half-up behavior."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_discount(amount: float, discount_pct: float) -> float:
    """Return ``amount`` minus ``discount_pct`` percent, rounded to cents."""
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if discount_pct < 0 or discount_pct > 100:
        raise ValueError("discount_pct must be between 0 and 100")
    return round_money(amount * (1 - discount_pct / 100))


def tax_rate(country_code: str) -> float:
    """Return the configured VAT/sales tax rate for a two-letter country code."""
    normalized = (country_code or "").strip().upper()
    if normalized not in DEFAULT_TAX_RATES:
        raise KeyError(f"no tax rate configured for {country_code!r}")
    return DEFAULT_TAX_RATES[normalized]


def shipping_cost(weight_g: float, remote_zip: bool = False) -> float:
    """Base shipping cost by weight plus a remote-area surcharge."""
    if weight_g < 0:
        raise ValueError("weight must be non-negative")
    if weight_g <= 1000:
        base = 4.0
    elif weight_g <= 5000:
        base = 8.0
    else:
        base = 12.0
    return round_money(base + (6.0 if remote_zip else 0.0))


def line_total(unit_price: float, quantity: int, discount_pct: float = 0.0) -> float:
    """Subtotal for one line item: price * quantity minus discount."""
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return apply_discount(unit_price * quantity, discount_pct)
