"""Provider-neutral Simple Moving Average analysis algorithm."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .analysis_contracts import MarketObservation
from .analysis_engine import (
    AlgorithmConfigurationError,
    AnalysisCalculationConfig,
)

SMA_ALGORITHM_IDENTITY = "indicator.sma"
SMA_ALGORITHM_VERSION = "1.0.0"

_MISSING = object()


def _require_sma_configuration(
    configuration: AnalysisCalculationConfig,
    observation_count: int,
) -> int:
    if configuration.algorithm_identity != SMA_ALGORITHM_IDENTITY:
        raise AlgorithmConfigurationError(
            "SMA configuration has an incompatible algorithm_identity"
        )
    if configuration.algorithm_version != SMA_ALGORITHM_VERSION:
        raise AlgorithmConfigurationError(
            "SMA configuration has an incompatible algorithm_version"
        )

    parameters = configuration.effective_parameters
    period = parameters.get("period", _MISSING)

    if period is _MISSING:
        raise AlgorithmConfigurationError(
            "SMA requires effective_parameters['period']"
        )
    if isinstance(period, bool) or not isinstance(period, int):
        raise AlgorithmConfigurationError("SMA period must be an integer")
    if period <= 0:
        raise AlgorithmConfigurationError("SMA period must be greater than zero")
    if period > observation_count:
        raise AlgorithmConfigurationError(
            "SMA period cannot exceed the selected observation count"
        )
    if period != observation_count:
        raise AlgorithmConfigurationError(
            "SMA period must equal the selected observation count"
        )
    return period


def _decimal_close(observation: MarketObservation) -> Decimal:
    if "close" not in observation.values:
        raise AlgorithmConfigurationError(
            "SMA requires MarketObservation.values['close']"
        )

    value = observation.values["close"]

    if isinstance(value, (bool, float, int)):
        raise AlgorithmConfigurationError(
            "SMA close must be represented as Decimal or an exact decimal string"
        )
    if isinstance(value, Decimal):
        close = value
    elif isinstance(value, str):
        try:
            close = Decimal(value)
        except InvalidOperation as exc:
            raise AlgorithmConfigurationError(
                "SMA close must be a valid Decimal value"
            ) from exc
    else:
        raise AlgorithmConfigurationError(
            "SMA close must be represented as Decimal or an exact decimal string"
        )

    if not close.is_finite():
        raise AlgorithmConfigurationError("SMA close must be finite")
    return close


class SMAAlgorithm:
    """Deterministic Simple Moving Average over the Engine-selected observations."""

    algorithm_identity = SMA_ALGORITHM_IDENTITY
    algorithm_version = SMA_ALGORITHM_VERSION

    def compute(
        self,
        observations: Sequence[MarketObservation],
        configuration: AnalysisCalculationConfig,
    ) -> Mapping[str, Any]:
        if not isinstance(observations, Sequence) or isinstance(
            observations, (str, bytes)
        ):
            raise AlgorithmConfigurationError(
                "SMA observations must be a Sequence[MarketObservation]"
            )
        if not observations:
            raise AlgorithmConfigurationError("SMA observations must not be empty")
        if not all(isinstance(item, MarketObservation) for item in observations):
            raise AlgorithmConfigurationError(
                "SMA observations must contain only MarketObservation instances"
            )

        period = _require_sma_configuration(configuration, len(observations))
        closes = tuple(_decimal_close(observation) for observation in observations)
        total = sum(closes, Decimal("0"))
        sma = total / Decimal(period)

        return {
            "sma": sma,
            "period": period,
        }


__all__ = [
    "SMA_ALGORITHM_IDENTITY",
    "SMA_ALGORITHM_VERSION",
    "SMAAlgorithm",
]
