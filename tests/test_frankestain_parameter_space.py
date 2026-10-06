from decimal import Decimal

import pytest

from frankestain.candidates.parameter_space import (
    ChoiceParameter,
    DecimalParameter,
    IntegerParameter,
    ParameterError,
    build_grid,
)


def test_integer_parameter_is_deterministic_and_inclusive():
    parameter = IntegerParameter(name="period", minimum=5, maximum=9, step=2)

    assert parameter.values() == (5, 7, 9)


def test_decimal_parameter_is_deterministic_and_inclusive():
    parameter = DecimalParameter(
        name="threshold",
        minimum=Decimal("0.1"),
        maximum=Decimal("0.3"),
        step=Decimal("0.1"),
    )

    assert parameter.values() == (
        Decimal("0.1"),
        Decimal("0.2"),
        Decimal("0.3"),
    )


def test_choice_parameter_normalizes_to_tuple():
    parameter = ChoiceParameter(name="mode", options=["a", "b"])

    assert parameter.values() == ("a", "b")


def test_build_grid_is_deterministic():
    grid = build_grid(
        (
            IntegerParameter("period", 5, 7, 2),
            ChoiceParameter("mode", ("fast", "slow")),
        )
    )

    assert grid == (
        {"period": 5, "mode": "fast"},
        {"period": 5, "mode": "slow"},
        {"period": 7, "mode": "fast"},
        {"period": 7, "mode": "slow"},
    )


def test_duplicate_parameter_names_are_rejected():
    with pytest.raises(ParameterError, match="unique"):
        build_grid(
            (
                IntegerParameter("period", 5, 6),
                IntegerParameter("period", 7, 8),
            )
        )


def test_grid_size_guard_is_fail_closed():
    with pytest.raises(ParameterError, match="max_combinations"):
        build_grid(
            (
                IntegerParameter("a", 1, 10),
                IntegerParameter("b", 1, 10),
            ),
            max_combinations=50,
        )
