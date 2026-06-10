from __future__ import annotations


def dollars_for_target_weight(equity: float, target_weight: float) -> float:
    if equity < 0:
        raise ValueError("equity cannot be negative")
    if not 0 <= target_weight <= 1:
        raise ValueError("target_weight must be between 0 and 1")
    return equity * target_weight


def shares_for_dollars(dollars: float, estimated_price: float) -> float:
    if dollars < 0:
        raise ValueError("dollars cannot be negative")
    if estimated_price <= 0:
        raise ValueError("estimated_price must be greater than zero")
    return dollars / estimated_price


def estimate_trade_value(quantity: float, estimated_price: float) -> float:
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    if estimated_price <= 0:
        raise ValueError("estimated_price must be greater than zero")
    return quantity * estimated_price
