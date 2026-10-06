"""Provider-neutral deterministic fixed-quantity sizing boundary.

This module implements only:

    Signal -> SizingResult

The sizing boundary validates one immutable Signal, validates one immutable
effective sizing configuration, and returns a deterministic requested quantity.
It does not evaluate risk, reserve capital, access balances or positions,
call providers, create orders, or execute anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .analysis_contracts import Provenance, Signal, SignalValidity


SIZING_TYPE = "fixed_quantity"
SIZING_IDENTITY = "sizing.fixed_quantity"
SIZING_VERSION = "1.0.0"
_QUANTITY_PARAMETER = "quantity"
_ALLOWED_SCALARS = (str, int, bool, Decimal, datetime, date, type(None))


class SizingError(ValueError):
    """Raised when the sizing boundary cannot safely produce a result."""


def _require_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SizingError(f"{field_name} must be a non-empty string")
    return value


def _freeze_value(value: Any, field_name: str) -> Any:
    """Recursively convert supported values into immutable representations."""
    if isinstance(value, Enum):
        value = value.value

    if type(value) is float:
        raise SizingError(f"{field_name} must not use float")

    if isinstance(value, _ALLOWED_SCALARS):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise SizingError(
                    f"{field_name} datetime values must be timezone-aware"
                )
        if isinstance(value, Decimal) and not value.is_finite():
            raise SizingError(f"{field_name} Decimal values must be finite")
        return value

    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise SizingError(
                    f"{field_name} mapping keys must be non-empty strings"
                )
            frozen[key] = _freeze_value(item, f"{field_name}[{key!r}]")
        return MappingProxyType(frozen)

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )

    raise SizingError(
        f"{field_name} contains unsupported or mutable value type: "
        f"{type(value).__name__}"
    )


def _freeze_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SizingError(f"{field_name} must be a mapping")
    frozen = _freeze_value(value, field_name)
    if not isinstance(frozen, Mapping):
        raise SizingError(f"{field_name} must be a mapping")
    return frozen


def _require_positive_decimal(value: Any, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise SizingError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise SizingError(f"{field_name} must be finite")
    if value <= 0:
        raise SizingError(f"{field_name} must be greater than zero")
    return value


@dataclass(frozen=True)
class SizingCalculationConfig:
    """Immutable, reproducible effective configuration for fixed-quantity sizing."""

    sizing_type: str
    sizing_identity: str
    sizing_version: str
    effective_configuration: Mapping[str, Any]
    effective_parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.sizing_type, "sizing_type")
        _require_nonempty_string(self.sizing_identity, "sizing_identity")
        _require_nonempty_string(self.sizing_version, "sizing_version")

        if self.sizing_type != SIZING_TYPE:
            raise SizingError(
                f"sizing_type must be exactly {SIZING_TYPE!r}"
            )
        if self.sizing_identity != SIZING_IDENTITY:
            raise SizingError(
                f"sizing_identity must be exactly {SIZING_IDENTITY!r}"
            )
        if self.sizing_version != SIZING_VERSION:
            raise SizingError(
                f"sizing_version must be exactly {SIZING_VERSION!r}"
            )

        frozen_configuration = _freeze_mapping(
            self.effective_configuration,
            "effective_configuration",
        )
        frozen_parameters = _freeze_mapping(
            self.effective_parameters,
            "effective_parameters",
        )

        if set(frozen_parameters) != {_QUANTITY_PARAMETER}:
            raise SizingError(
                "effective_parameters must contain exactly 'quantity'"
            )

        _require_positive_decimal(
            frozen_parameters[_QUANTITY_PARAMETER],
            "effective_parameters['quantity']",
        )

        object.__setattr__(
            self,
            "effective_configuration",
            frozen_configuration,
        )
        object.__setattr__(
            self,
            "effective_parameters",
            frozen_parameters,
        )


@dataclass(frozen=True)
class SizingResult:
    """Immutable logical sizing result. It carries no financial authority."""

    sizing_result_id: str
    signal_id: str
    symbol: str
    direction: str
    requested_quantity: Decimal
    sizing_type: str
    sizing_identity: str
    sizing_version: str
    effective_configuration: Mapping[str, Any]
    effective_parameters: Mapping[str, Any]
    decision_timestamp: datetime
    signal_provenance: Provenance

    def __post_init__(self) -> None:
        _require_nonempty_string(self.sizing_result_id, "sizing_result_id")
        _require_nonempty_string(self.signal_id, "signal_id")
        _require_nonempty_string(self.symbol, "symbol")
        _require_nonempty_string(self.direction, "direction")
        _require_nonempty_string(self.sizing_type, "sizing_type")
        _require_nonempty_string(self.sizing_identity, "sizing_identity")
        _require_nonempty_string(self.sizing_version, "sizing_version")

        if not isinstance(self.requested_quantity, Decimal):
            raise SizingError("requested_quantity must be Decimal")
        if not self.requested_quantity.is_finite():
            raise SizingError("requested_quantity must be finite")
        if self.requested_quantity <= 0:
            raise SizingError("requested_quantity must be greater than zero")

        if not isinstance(self.decision_timestamp, datetime):
            raise SizingError("decision_timestamp must be datetime")
        if (
            self.decision_timestamp.tzinfo is None
            or self.decision_timestamp.utcoffset() is None
        ):
            raise SizingError("decision_timestamp must be timezone-aware")

        if not isinstance(self.signal_provenance, Provenance):
            raise SizingError("signal_provenance must be Provenance")
        if not isinstance(self.effective_configuration, Mapping):
            raise SizingError("effective_configuration must be a mapping")
        if not isinstance(self.effective_parameters, Mapping):
            raise SizingError("effective_parameters must be a mapping")

        object.__setattr__(
            self,
            "effective_configuration",
            _freeze_mapping(
                self.effective_configuration,
                "effective_configuration",
            ),
        )
        object.__setattr__(
            self,
            "effective_parameters",
            _freeze_mapping(
                self.effective_parameters,
                "effective_parameters",
            ),
        )


class FixedQuantitySizer:
    """Deterministic fixed-quantity sizing with no provider or financial-state access."""

    sizing_type = SIZING_TYPE
    sizing_identity = SIZING_IDENTITY
    sizing_version = SIZING_VERSION

    def compute(
        self,
        signal: Signal,
        configuration: SizingCalculationConfig,
    ) -> SizingResult:
        self._validate_signal(signal)
        self._validate_configuration(configuration)

        quantity = _require_positive_decimal(
            configuration.effective_parameters[_QUANTITY_PARAMETER],
            "effective_parameters['quantity']",
        )

        return SizingResult(
            sizing_result_id=uuid4().hex,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            requested_quantity=quantity,
            sizing_type=configuration.sizing_type,
            sizing_identity=configuration.sizing_identity,
            sizing_version=configuration.sizing_version,
            effective_configuration=configuration.effective_configuration,
            effective_parameters=configuration.effective_parameters,
            decision_timestamp=signal.decision_timestamp,
            signal_provenance=signal.provenance,
        )

    @staticmethod
    def _validate_signal(signal: Signal) -> None:
        if not isinstance(signal, Signal):
            raise SizingError("signal must be a Signal")

        _require_nonempty_string(signal.symbol, "signal.symbol")
        _require_nonempty_string(signal.signal_id, "signal.signal_id")
        _require_nonempty_string(signal.direction, "signal.direction")

        if signal.validity is not SignalValidity.VALID:
            raise SizingError(
                f"signal validity {signal.validity.value!r} cannot enter sizing"
            )

        try:
            valid_at_generated = signal.is_valid_at(signal.generated_at)
        except (TypeError, ValueError) as exc:
            raise SizingError(
                f"signal validity timestamp is invalid: {exc}"
            ) from exc

        if not valid_at_generated:
            raise SizingError(
                "signal is not VALID at its evaluation timestamp"
            )

    @staticmethod
    def _validate_configuration(
        configuration: SizingCalculationConfig,
    ) -> None:
        if not isinstance(configuration, SizingCalculationConfig):
            raise SizingError(
                "configuration must be a SizingCalculationConfig"
            )

        if configuration.sizing_type != SIZING_TYPE:
            raise SizingError("configuration sizing_type mismatch")
        if configuration.sizing_identity != SIZING_IDENTITY:
            raise SizingError("configuration sizing_identity mismatch")
        if configuration.sizing_version != SIZING_VERSION:
            raise SizingError("configuration sizing_version mismatch")


__all__ = [
    "FixedQuantitySizer",
    "SizingCalculationConfig",
    "SizingError",
    "SizingResult",
    "SIZING_IDENTITY",
    "SIZING_TYPE",
    "SIZING_VERSION",
]
