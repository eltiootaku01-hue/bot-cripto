"""Deterministic strategy parameter-space candidates for FRANKESTAIN.

Architectural inspiration:
- Freqtrade HyperOpt/strategy parameter declarations
- Jesse hyperparameter/backtest workflow

Original implementation. It is a sandbox candidate and does not execute a
strategy, access providers, or persist optimization state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class ParameterError(ValueError):
    """Invalid optimization parameter definition."""


@dataclass(frozen=True)
class IntegerParameter:
    name: str
    minimum: int
    maximum: int
    step: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ParameterError("name must not be empty")
        if self.minimum > self.maximum:
            raise ParameterError("minimum cannot exceed maximum")
        if self.step <= 0:
            raise ParameterError("step must be greater than zero")

    def values(self) -> tuple[int, ...]:
        return tuple(range(self.minimum, self.maximum + 1, self.step))


@dataclass(frozen=True)
class DecimalParameter:
    name: str
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ParameterError("name must not be empty")
        for field_name in ("minimum", "maximum", "step"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ParameterError(f"{field_name} must be finite Decimal")
        if self.minimum > self.maximum:
            raise ParameterError("minimum cannot exceed maximum")
        if self.step <= 0:
            raise ParameterError("step must be greater than zero")

    def values(self) -> tuple[Decimal, ...]:
        values: list[Decimal] = []
        current = self.minimum
        while current <= self.maximum:
            values.append(current)
            current += self.step
        return tuple(values)


@dataclass(frozen=True)
class ChoiceParameter:
    name: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ParameterError("name must not be empty")
        options = tuple(self.options)
        if not options or any(not item.strip() for item in options):
            raise ParameterError("options must contain non-empty strings")
        if len(set(options)) != len(options):
            raise ParameterError("options must be unique")
        object.__setattr__(self, "options", options)

    def values(self) -> tuple[str, ...]:
        return self.options


Parameter = IntegerParameter | DecimalParameter | ChoiceParameter


def build_grid(
    parameters: tuple[Parameter, ...],
    *,
    max_combinations: int = 100_000,
) -> tuple[dict[str, object], ...]:
    """Build a deterministic Cartesian parameter grid."""

    if max_combinations <= 0:
        raise ParameterError("max_combinations must be greater than zero")

    definitions = tuple(parameters)
    names = [item.name for item in definitions]

    if len(names) != len(set(names)):
        raise ParameterError("parameter names must be unique")

    combinations = 1
    value_sets: list[tuple[object, ...]] = []

    for item in definitions:
        values = item.values()
        if not values:
            raise ParameterError(f"parameter {item.name!r} has no values")
        combinations *= len(values)
        if combinations > max_combinations:
            raise ParameterError("parameter grid exceeds max_combinations")
        value_sets.append(values)

    if not value_sets:
        return ({},)

    result: list[dict[str, object]] = [{}]

    for name, values in zip(names, value_sets):
        result = [
            {**partial, name: value}
            for partial in result
            for value in values
        ]

    return tuple(result)


__all__ = [
    "ChoiceParameter",
    "DecimalParameter",
    "IntegerParameter",
    "ParameterError",
    "build_grid",
]
