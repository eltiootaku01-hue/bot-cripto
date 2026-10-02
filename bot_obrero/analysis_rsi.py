"""Provider-neutral deterministic Wilder Relative Strength Index analysis algorithm.

Canonical RSI convention used here:
- changes are consecutive close differences
- gain = max(change, 0) and loss = max(-change, 0)
- the initial averages are the arithmetic means of the first period gains/losses
- subsequent averages use Wilder's smoothing recurrence, not conventional EMA
- RSI = 100 - (100 / (1 + RS)) when average_loss > 0
- division-by-zero policy is explicit: 100 for gains-only, 0 for losses-only,
  and 50 when both averages are zero

The AnalysisEngine selects the observation window and enforces temporal validity.
This algorithm consumes exactly the sequence received and never reselects,
reorders, interpolates, pads, or accesses providers.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .analysis_contracts import MarketObservation
from .analysis_engine import (
    AlgorithmConfigurationError,
    AnalysisCalculationConfig,
)

RSI_ALGORITHM_IDENTITY = "indicator.rsi"
RSI_ALGORITHM_VERSION = "1.0.0"

_MISSING = object()


def _require_rsi_configuration(
    configuration: AnalysisCalculationConfig,
    observation_count: int,
) -> int:
    if not isinstance(configuration, AnalysisCalculationConfig):
        raise AlgorithmConfigurationError(
            "configuration must be AnalysisCalculationConfig"
        )
    if configuration.algorithm_identity != RSI_ALGORITHM_IDENTITY:
        raise AlgorithmConfigurationError(
            "RSI configuration has an incompatible algorithm_identity"
        )
    if configuration.algorithm_version != RSI_ALGORITHM_VERSION:
        raise AlgorithmConfigurationError(
            "RSI configuration has an incompatible algorithm_version"
        )

    parameters = configuration.effective_parameters
    period = parameters.get("period", _MISSING)

    if period is _MISSING:
        raise AlgorithmConfigurationError(
            "RSI requires effective_parameters['period']"
        )
    if isinstance(period, bool) or not isinstance(period, int):
        raise AlgorithmConfigurationError("RSI period must be an integer")
    if period <= 0:
        raise AlgorithmConfigurationError(
            "RSI period must be greater than zero"
        )
    if period >= observation_count:
        raise AlgorithmConfigurationError(
            "RSI period must be less than the selected observation count"
        )
    return period


def _decimal_close(observation: MarketObservation) -> Decimal:
    if "close" not in observation.values:
        raise AlgorithmConfigurationError(
            "RSI requires MarketObservation.values['close']"
        )

    value = observation.values["close"]

    if isinstance(value, (bool, float, int)):
        raise AlgorithmConfigurationError(
            "RSI close must be represented as Decimal or an exact decimal string"
        )
    if isinstance(value, Decimal):
        close = value
    elif isinstance(value, str):
        try:
            close = Decimal(value)
        except InvalidOperation as exc:
            raise AlgorithmConfigurationError(
                "RSI close must be a valid Decimal value"
            ) from exc
    else:
        raise AlgorithmConfigurationError(
            "RSI close must be represented as Decimal or an exact decimal string"
        )

    if not close.is_finite():
        raise AlgorithmConfigurationError("RSI close must be finite")
    return close


def _rsi_from_averages(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_loss == Decimal("0"):
        if average_gain > Decimal("0"):
            return Decimal("100")
        if average_gain == Decimal("0"):
            return Decimal("50")

    rs = average_gain / average_loss
    return Decimal("100") - (
        Decimal("100") / (Decimal("1") + rs)
    )


class RSIAlgorithm:
    """Deterministic Wilder RSI over exactly the observations selected by AnalysisEngine."""

    algorithm_identity = RSI_ALGORITHM_IDENTITY
    algorithm_version = RSI_ALGORITHM_VERSION

    def compute(
        self,
        observations: Sequence[MarketObservation],
        configuration: AnalysisCalculationConfig,
    ) -> Mapping[str, Any]:
        if not isinstance(observations, Sequence) or isinstance(
            observations, (str, bytes)
        ):
            raise AlgorithmConfigurationError(
                "RSI observations must be a Sequence[MarketObservation]"
            )
        if not observations:
            raise AlgorithmConfigurationError(
                "RSI observations must not be empty"
            )
        if not all(isinstance(item, MarketObservation) for item in observations):
            raise AlgorithmConfigurationError(
                "RSI observations must contain only MarketObservation instances"
            )

        period = _require_rsi_configuration(configuration, len(observations))
        closes = tuple(_decimal_close(observation) for observation in observations)

        previous_close = closes[0]
        gain_total = Decimal("0")
        loss_total = Decimal("0")
        average_gain: Decimal | None = None
        average_loss: Decimal | None = None
        rsi: Decimal | None = None

        for index in range(1, len(closes)):
            close = closes[index]
            change = close - previous_close

            gain = change if change > Decimal("0") else Decimal("0")
            loss = -change if change < Decimal("0") else Decimal("0")

            if index <= period:
                gain_total += gain
                loss_total += loss

                if index == period:
                    average_gain = gain_total / Decimal(period)
                    average_loss = loss_total / Decimal(period)
                    rsi = _rsi_from_averages(average_gain, average_loss)
            else:
                assert average_gain is not None
                assert average_loss is not None

                average_gain = (
                    (average_gain * Decimal(period - 1)) + gain
                ) / Decimal(period)
                average_loss = (
                    (average_loss * Decimal(period - 1)) + loss
                ) / Decimal(period)
                rsi = _rsi_from_averages(average_gain, average_loss)

            previous_close = close

        assert rsi is not None
        return {
            "rsi": rsi,
            "period": period,
        }


__all__ = [
    "RSI_ALGORITHM_IDENTITY",
    "RSI_ALGORITHM_VERSION",
    "RSIAlgorithm",
]
