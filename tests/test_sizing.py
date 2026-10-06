from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance, Signal, SignalValidity
from bot_obrero.sizing import (
    FixedQuantitySizer,
    SizingCalculationConfig,
    SizingError,
    SizingResult,
    SIZING_IDENTITY,
    SIZING_TYPE,
    SIZING_VERSION,
)


DECISION = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)


def make_signal(
    *,
    validity: SignalValidity = SignalValidity.VALID,
    generated_at: datetime | None = None,
    expires_at: datetime | None = None,
    symbol: str = "BTC/USDT",
    direction: str = "LONG",
) -> Signal:
    generated = DECISION if generated_at is None else generated_at
    return Signal(
        symbol=symbol,
        hypothesis_id="hypothesis-1",
        direction=direction,
        generated_at=generated,
        decision_timestamp=DECISION,
        provenance=Provenance(
            source="strategy_runtime",
            nature=ArtifactNature.DERIVED,
            reference="strategic-artifact-1",
            metadata={
                "strategy_identity": "strategy.test",
                "strategy_version": "1.0.0",
            },
        ),
        evidence={
            "strategy_identity": "strategy.test",
            "strategy_version": "1.0.0",
        },
        expires_at=expires_at,
        validity=validity,
        signal_id="signal-1",
    )


def make_config(
    *,
    quantity=Decimal("0.25"),
    sizing_type: str = SIZING_TYPE,
    sizing_identity: str = SIZING_IDENTITY,
    sizing_version: str = SIZING_VERSION,
    effective_configuration=None,
    effective_parameters=None,
) -> SizingCalculationConfig:
    return SizingCalculationConfig(
        sizing_type=sizing_type,
        sizing_identity=sizing_identity,
        sizing_version=sizing_version,
        effective_configuration=(
            {"rounding": "none"}
            if effective_configuration is None
            else effective_configuration
        ),
        effective_parameters=(
            {"quantity": quantity}
            if effective_parameters is None
            else effective_parameters
        ),
    )


def logical_fields(result: SizingResult) -> tuple[object, ...]:
    return (
        result.signal_id,
        result.symbol,
        result.direction,
        result.requested_quantity,
        result.sizing_type,
        result.sizing_identity,
        result.sizing_version,
        result.effective_configuration,
        result.effective_parameters,
        result.decision_timestamp,
        result.signal_provenance,
    )


def test_happy_path_returns_positive_decimal_quantity() -> None:
    result = FixedQuantitySizer().compute(
        make_signal(),
        make_config(quantity=Decimal("1.2500")),
    )

    assert result.requested_quantity == Decimal("1.2500")
    assert result.requested_quantity > 0
    assert isinstance(result.requested_quantity, Decimal)


def test_result_inherits_signal_identity_direction_symbol_and_decision_timestamp() -> None:
    signal = make_signal(direction="SHORT")
    result = FixedQuantitySizer().compute(signal, make_config())

    assert result.signal_id == signal.signal_id
    assert result.symbol == signal.symbol
    assert result.direction == signal.direction
    assert result.decision_timestamp == signal.decision_timestamp
    assert result.signal_provenance == signal.provenance


def test_signal_valid_is_accepted() -> None:
    result = FixedQuantitySizer().compute(
        make_signal(validity=SignalValidity.VALID),
        make_config(),
    )

    assert result.requested_quantity == Decimal("0.25")


@pytest.mark.parametrize(
    "validity",
    [SignalValidity.EXPIRED, SignalValidity.UNKNOWN],
)
def test_expired_and_unknown_signals_fail_closed(validity: SignalValidity) -> None:
    with pytest.raises(SizingError, match="validity"):
        FixedQuantitySizer().compute(
            make_signal(validity=validity),
            make_config(),
        )


def test_valid_signal_that_is_expired_at_its_evaluation_timestamp_is_rejected() -> None:
    signal = make_signal(
        validity=SignalValidity.VALID,
        generated_at=DECISION + timedelta(minutes=10),
        expires_at=DECISION + timedelta(minutes=5),
    )

    with pytest.raises(SizingError, match="evaluation timestamp"):
        FixedQuantitySizer().compute(signal, make_config())


def test_non_signal_input_is_rejected() -> None:
    with pytest.raises(SizingError, match="Signal"):
        FixedQuantitySizer().compute(object(), make_config())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sizing_type", "other_type"),
        ("sizing_identity", "sizing.other"),
        ("sizing_version", "9.9.9"),
    ],
)
def test_configuration_identity_fields_must_match(field: str, value: str) -> None:
    kwargs = {
        "sizing_type": SIZING_TYPE,
        "sizing_identity": SIZING_IDENTITY,
        "sizing_version": SIZING_VERSION,
    }
    kwargs[field] = value

    with pytest.raises(SizingError):
        make_config(**kwargs)


@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        1.0,
        1,
        True,
        "1.0",
        None,
    ],
)
def test_invalid_financial_quantity_is_rejected(quantity) -> None:
    with pytest.raises(SizingError):
        make_config(quantity=quantity)


def test_unsupported_price_or_capital_parameters_are_rejected() -> None:
    with pytest.raises(SizingError, match="exactly 'quantity'"):
        make_config(
            effective_parameters={
                "quantity": Decimal("1"),
                "price": Decimal("100"),
                "capital": Decimal("1000"),
            }
        )


def test_configuration_rejects_nested_float_values() -> None:
    with pytest.raises(SizingError, match="float"):
        make_config(
            effective_configuration={
                "nested": {"unexpected": 0.5},
            }
        )


def test_configuration_is_immutable_and_deeply_frozen() -> None:
    config = make_config(
        effective_configuration={
            "nested": {"mode": "fixed"},
        }
    )

    assert not hasattr(config.effective_configuration, "update")
    assert not hasattr(config.effective_parameters, "update")
    with pytest.raises(TypeError):
        config.effective_parameters["quantity"] = Decimal("2")  # type: ignore[index]

    nested = config.effective_configuration["nested"]
    assert not hasattr(nested, "update")


def test_result_is_immutable_and_contains_only_sizing_evidence() -> None:
    result = FixedQuantitySizer().compute(
        make_signal(),
        make_config(),
    )

    assert "risk_decision" not in {field.name for field in fields(result)}
    assert "reservation" not in {field.name for field in fields(result)}
    assert "order_intent" not in {field.name for field in fields(result)}
    assert "execution_result" not in {field.name for field in fields(result)}

    with pytest.raises((AttributeError, TypeError)):
        result.requested_quantity = Decimal("2")  # type: ignore[misc]


def test_result_provenance_and_effective_parameters_are_preserved() -> None:
    signal = make_signal()
    config = make_config(
        effective_configuration={"policy": "explicit"},
        effective_parameters={"quantity": Decimal("2.5")},
    )

    result = FixedQuantitySizer().compute(signal, config)

    assert result.signal_id == signal.signal_id
    assert result.signal_provenance == signal.provenance
    assert result.sizing_identity == SIZING_IDENTITY
    assert result.sizing_version == SIZING_VERSION
    assert result.effective_parameters["quantity"] == Decimal("2.5")
    assert result.effective_configuration["policy"] == "explicit"


def test_result_timestamp_is_inherited_not_replaced_by_current_time() -> None:
    signal = make_signal(
        generated_at=DECISION + timedelta(minutes=3),
    )

    result = FixedQuantitySizer().compute(signal, make_config())

    assert result.decision_timestamp is signal.decision_timestamp
    assert result.decision_timestamp == DECISION


def test_determinism_of_logical_sizing_fields() -> None:
    signal = make_signal()
    config = make_config(
        quantity=Decimal("3.125"),
        effective_configuration={"policy": "explicit"},
    )

    first = FixedQuantitySizer().compute(signal, config)
    second = FixedQuantitySizer().compute(signal, config)

    assert logical_fields(first) == logical_fields(second)
    assert first.sizing_result_id != second.sizing_result_id


def test_sizing_does_not_mutate_signal_or_configuration() -> None:
    signal = make_signal()
    config = make_config(
        effective_configuration={"nested": {"mode": "fixed"}},
    )

    before_signal = (
        signal.signal_id,
        signal.symbol,
        signal.direction,
        signal.generated_at,
        signal.decision_timestamp,
        dict(signal.evidence),
        signal.provenance,
        signal.validity,
        signal.expires_at,
    )
    before_config = (
        config.sizing_type,
        config.sizing_identity,
        config.sizing_version,
        dict(config.effective_configuration),
        dict(config.effective_parameters),
    )

    FixedQuantitySizer().compute(signal, config)

    after_signal = (
        signal.signal_id,
        signal.symbol,
        signal.direction,
        signal.generated_at,
        signal.decision_timestamp,
        dict(signal.evidence),
        signal.provenance,
        signal.validity,
        signal.expires_at,
    )
    after_config = (
        config.sizing_type,
        config.sizing_identity,
        config.sizing_version,
        dict(config.effective_configuration),
        dict(config.effective_parameters),
    )

    assert after_signal == before_signal
    assert after_config == before_config


def test_fixed_quantity_sizing_does_not_create_financial_authority_objects() -> None:
    source = Path("bot_obrero/sizing.py").read_text(encoding="utf-8")

    forbidden = (
        "RiskDecision",
        "RiskAuthorization",
        "Reservation",
        "OrderIntent",
        "ExecutionResult",
        "risk_evaluation",
        "reservation",
        "execution",
        "trade_proposal",
    )

    for fragment in forbidden:
        assert fragment not in source


def test_provider_access_is_absent() -> None:
    tree = ast.parse(
        Path("bot_obrero/sizing.py").read_text(encoding="utf-8"),
        filename="bot_obrero/sizing.py",
    )

    imported_modules: list[str] = []
    float_calls: list[int] = []
    datetime_now_calls: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                float_calls.append(node.lineno)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "now"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
            ):
                datetime_now_calls.append(node.lineno)

    forbidden_import_fragments = (
        "binance",
        "websocket",
        "execution",
        "account",
        "requests",
        "httpx",
        "urllib",
        "socket",
    )

    for module in imported_modules:
        assert not any(
            fragment in module
            for fragment in forbidden_import_fragments
        )

    assert float_calls == []
    assert datetime_now_calls == []


def test_result_rejects_invalid_timestamp_and_quantity() -> None:
    provenance = make_signal().provenance

    with pytest.raises(SizingError, match="timezone-aware"):
        SizingResult(
            sizing_result_id="sizing-result-1",
            signal_id="signal-1",
            symbol="BTC/USDT",
            direction="LONG",
            requested_quantity=Decimal("1"),
            sizing_type=SIZING_TYPE,
            sizing_identity=SIZING_IDENTITY,
            sizing_version=SIZING_VERSION,
            effective_configuration={},
            effective_parameters={"quantity": Decimal("1")},
            decision_timestamp=datetime(2026, 10, 6, 18, 0),
            signal_provenance=provenance,
        )


def test_result_never_contains_strategy_or_market_inputs() -> None:
    result = FixedQuantitySizer().compute(make_signal(), make_config())

    result_field_names = {field.name for field in fields(result)}

    assert "analysis_snapshot" not in result_field_names
    assert "market_data" not in result_field_names
    assert "balance" not in result_field_names
    assert "position" not in result_field_names
    assert "provider" not in result_field_names


def test_module_is_provider_neutral_and_minimal() -> None:
    tree = ast.parse(
        Path("bot_obrero/sizing.py").read_text(encoding="utf-8"),
        filename="bot_obrero/sizing.py",
    )

    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }

    assert "FixedQuantitySizer" in names
    assert "Signal" in names
    assert "SizingCalculationConfig" in names
    assert "RiskDecision" not in names
    assert "Reservation" not in names
    assert "ExecutionResult" not in names
