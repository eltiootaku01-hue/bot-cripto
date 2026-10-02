"""Provider-neutral deterministic Exponential Moving Average analysis algorithm.

Canonical EMA convention used here:
- alpha = 2 / (period + 1)
- the initial EMA seed is the SMA of the first period selected observations
- each subsequent observation updates the EMA recursively
- the returned EMA corresponds to the last observation received by the algorithm

The AnalysisEngine selects the observation window and enforces temporal validity.
This algorithm never reselects or reorders observations and never accesses providers.
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

EMA_ALGORITHM_IDENTITY = "indicator.ema"
EMA_ALGORITHM_VERSION = "1.0.0"

_MISSING = object()


def _require_ema_configuration(
    configuration: AnalysisCalculationConfig,
    observation_count: int,
) -> int:
    if not isinstance(configuration, AnalysisCalculationConfig):
        raise AlgorithmConfigurationError(
            "configuration must be AnalysisCalculationConfig"
        )
    if configuration.algorithm_identity != EMA_ALGORITHM_IDENTITY:
        raise AlgorithmConfigurationError(
            "EMA configuration has an incompatible algorithm_identity"
        )
    if configuration.algorithm_version != EMA_ALGORITHM_VERSION:
        raise AlgorithmConfigurationError(
            "EMA configuration has an incompatible algorithm_version"
        )

    parameters = configuration.effective_parameters
    period = parameters.get("period", _MISSING)

    if period is _MISSING:
        raise AlgorithmConfigurationError(
            "EMA requires effective_parameters['period']"
        )
    if isinstance(period, bool) or not isinstance(period, int):
        raise AlgorithmConfigurationError("EMA period must be an integer")
    if period <= 0:
        raise AlgorithmConfigurationError("EMA period must be greater than zero")
    if period > observation_count:
        raise AlgorithmConfigurationError(
            "EMA period cannot exceed the selected observation count"
        )
    return period


def _decimal_close(observation: MarketObservation) -> Decimal:
    if "close" not in observation.values:
        raise AlgorithmConfigurationError(
            "EMA requires MarketObservation.values['close']"
        )

    value = observation.values["close"]

    if isinstance(value, (bool, float, int)):
        raise AlgorithmConfigurationError(
            "EMA close must be represented as Decimal or an exact decimal string"
        )
    if isinstance(value, Decimal):
        close = value
    elif isinstance(value, str):
        try:
            close = Decimal(value)
        except InvalidOperation as exc:
            raise AlgorithmConfigurationError(
                "EMA close must be a valid Decimal value"
            ) from exc
    else:
        raise AlgorithmConfigurationError(
            "EMA close must be represented as Decimal or an exact decimal string"
        )

    if not close.is_finite():
        raise AlgorithmConfigurationError("EMA close must be finite")
    return close


class EMAAlgorithm:
    """Deterministic EMA over exactly the observations selected by AnalysisEngine."""

    algorithm_identity = EMA_ALGORITHM_IDENTITY
    algorithm_version = EMA_ALGORITHM_VERSION

    def compute(
        self,
        observations: Sequence[MarketObservation],
        configuration: AnalysisCalculationConfig,
    ) -> Mapping[str, Any]:
        if not isinstance(observations, Sequence) or isinstance(
            observations, (str, bytes)
        ):
            raise AlgorithmConfigurationError(
                "EMA observations must be a Sequence[MarketObservation]"
            )
        if not observations:
            raise AlgorithmConfigurationError("EMA observations must not be empty")
        if not all(isinstance(item, MarketObservation) for item in observations):
            raise AlgorithmConfigurationError(
                "EMA observations must contain only MarketObservation instances"
            )

        period = _require_ema_configuration(configuration, len(observations))
        closes = tuple(_decimal_close(observation) for observation in observations)

        alpha = Decimal("2") / Decimal(period + 1)
        one_minus_alpha = Decimal("1") - alpha

        seed_total = Decimal("0")
        ema: Decimal | None = None

        for index, close in enumerate(closes):
            if index < period:
                seed_total += close
                if index == period - 1:
                    ema = seed_total / Decimal(period)
                continue

            assert ema is not None
            ema = alpha * close + one_minus_alpha * ema

        assert ema is not None
        return {
            "ema": ema,
            "period": period,
        }


__all__ = [
    "EMA_ALGORITHM_IDENTITY",
    "EMA_ALGORITHM_VERSION",
    "EMAAlgorithm",
]
