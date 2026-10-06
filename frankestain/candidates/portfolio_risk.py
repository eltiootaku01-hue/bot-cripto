"""Provider-neutral portfolio-risk candidates for FRANKESTAIN.

Original implementation based on architectural patterns observed in:
- QuantConnect LEAN RiskManagementModel
- Passivbot risk/backtest documentation

This module is intentionally independent from RiskDecision, Reservation,
Financial Admission and Execution. It returns descriptive risk signals only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class RiskTrigger(str, Enum):
    NONE = "NONE"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    HALT_NEW_ENTRIES = "HALT_NEW_ENTRIES"


@dataclass(frozen=True)
class DrawdownPolicy:
    maximum_drawdown: Decimal
    trailing: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.maximum_drawdown, Decimal):
            raise TypeError("maximum_drawdown must be Decimal")
        if not self.maximum_drawdown.is_finite():
            raise ValueError("maximum_drawdown must be finite")
        if self.maximum_drawdown <= 0 or self.maximum_drawdown >= 1:
            raise ValueError("maximum_drawdown must be between 0 and 1")


@dataclass(frozen=True)
class DrawdownObservation:
    reference_equity: Decimal
    current_equity: Decimal
    drawdown: Decimal
    trigger: RiskTrigger

    def __post_init__(self) -> None:
        for name in ("reference_equity", "current_equity", "drawdown"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be finite Decimal")


def evaluate_drawdown(
    *,
    current_equity: Decimal,
    reference_equity: Decimal | None,
    policy: DrawdownPolicy,
) -> DrawdownObservation:
    """Evaluate drawdown without authorizing any trade or touching state."""

    if not isinstance(current_equity, Decimal) or not current_equity.is_finite():
        raise ValueError("current_equity must be finite Decimal")
    if current_equity <= 0:
        raise ValueError("current_equity must be greater than zero")

    if reference_equity is None:
        reference = current_equity
    else:
        if not isinstance(reference_equity, Decimal) or not reference_equity.is_finite():
            raise ValueError("reference_equity must be finite Decimal")
        if reference_equity <= 0:
            raise ValueError("reference_equity must be greater than zero")
        if policy.trailing and current_equity > reference_equity:
            reference = current_equity
        else:
            reference = reference_equity

    drawdown = (current_equity / reference) - Decimal("1")

    trigger = (
        RiskTrigger.HALT_NEW_ENTRIES
        if drawdown <= -policy.maximum_drawdown
        else RiskTrigger.NONE
    )

    return DrawdownObservation(
        reference_equity=reference,
        current_equity=current_equity,
        drawdown=drawdown,
        trigger=trigger,
    )


@dataclass(frozen=True)
class GrossExposurePolicy:
    maximum_fraction: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.maximum_fraction, Decimal):
            raise TypeError("maximum_fraction must be Decimal")
        if not self.maximum_fraction.is_finite():
            raise ValueError("maximum_fraction must be finite")
        if self.maximum_fraction <= 0:
            raise ValueError("maximum_fraction must be greater than zero")


@dataclass(frozen=True)
class GrossExposureObservation:
    portfolio_value: Decimal
    gross_exposure: Decimal
    exposure_fraction: Decimal
    trigger: RiskTrigger

    def __post_init__(self) -> None:
        for name in (
            "portfolio_value",
            "gross_exposure",
            "exposure_fraction",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be finite Decimal")


def evaluate_gross_exposure(
    *,
    portfolio_value: Decimal,
    gross_exposure: Decimal,
    policy: GrossExposurePolicy,
) -> GrossExposureObservation:
    """Describe gross exposure relative to portfolio value."""

    if not isinstance(portfolio_value, Decimal) or not portfolio_value.is_finite():
        raise ValueError("portfolio_value must be finite Decimal")
    if not isinstance(gross_exposure, Decimal) or not gross_exposure.is_finite():
        raise ValueError("gross_exposure must be finite Decimal")
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be greater than zero")
    if gross_exposure < 0:
        raise ValueError("gross_exposure cannot be negative")

    fraction = gross_exposure / portfolio_value
    trigger = (
        RiskTrigger.REDUCE_EXPOSURE
        if fraction > policy.maximum_fraction
        else RiskTrigger.NONE
    )

    return GrossExposureObservation(
        portfolio_value=portfolio_value,
        gross_exposure=gross_exposure,
        exposure_fraction=fraction,
        trigger=trigger,
    )


__all__ = [
    "DrawdownObservation",
    "DrawdownPolicy",
    "GrossExposureObservation",
    "GrossExposurePolicy",
    "RiskTrigger",
    "evaluate_drawdown",
    "evaluate_gross_exposure",
]
