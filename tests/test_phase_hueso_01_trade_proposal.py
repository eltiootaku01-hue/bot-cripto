from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, ContractError, Provenance, Signal
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
    build_trade_proposal,
)


BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
DERIVED = Provenance(source="strategy-test", nature=ArtifactNature.DERIVED)


def make_signal(*, symbol: str = "BTC/USDT", direction: str = "BUY") -> Signal:
    return Signal(
        symbol=symbol,
        hypothesis_id="hypothesis-01",
        direction=direction,
        generated_at=BASE,
        decision_timestamp=BASE,
        provenance=DERIVED,
    )


def make_market_proposal(
    *,
    signal: Signal | None = None,
    side: TradeSide = TradeSide.BUY,
    max_quote_spend: Decimal | None,
):
    source_signal = make_signal() if signal is None else signal
    return build_trade_proposal(
        signal=source_signal,
        side=side,
        requested_quantity=Decimal("0.001"),
        requested_price=None,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )


def make_limit_proposal(
    *,
    signal: Signal | None = None,
    side: TradeSide = TradeSide.SELL,
    requested_price: Decimal | None = Decimal("100.25"),
):
    source_signal = make_signal() if signal is None else signal
    return build_trade_proposal(
        signal=source_signal,
        side=side,
        requested_quantity=Decimal("0.001"),
        requested_price=requested_price,
        price_policy=PricePolicy.FIXED,
        order_type=TradeOrderType.LIMIT,
        max_quote_spend=None,
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )


def test_trade_proposal_field_contract_is_exact():
    assert [field.name for field in fields(TradeProposal)] == [
        "proposal_id",
        "signal_id",
        "symbol",
        "side",
        "requested_quantity",
        "requested_price",
        "max_quote_spend",
        "price_policy",
        "order_type",
        "strategy_identity",
        "strategy_version",
        "decision_timestamp",
        "correlation_id",
    ]


def test_identity_fields_are_generated_and_distinct():
    signal = make_signal()
    first = make_market_proposal(signal=signal, max_quote_spend=Decimal("1"))
    second = make_market_proposal(signal=signal)

    assert isinstance(first.proposal_id, str)
    assert first.proposal_id
    assert isinstance(first.correlation_id, str)
    assert first.correlation_id
    assert first.proposal_id != first.signal_id
    assert first.correlation_id != first.proposal_id
    assert first.correlation_id != first.signal_id

    assert first.signal_id == second.signal_id
    assert first.proposal_id != second.proposal_id
    assert first.correlation_id != second.correlation_id


def test_immutability_rejects_mutation():
    proposal = make_market_proposal(max_quote_spend=Decimal("1"))

    with pytest.raises(FrozenInstanceError):
        proposal.side = TradeSide.SELL

    with pytest.raises(FrozenInstanceError):
        proposal.requested_quantity = Decimal("0.002")

    with pytest.raises(FrozenInstanceError):
        proposal.requested_price = Decimal("100")

    with pytest.raises(FrozenInstanceError):
        proposal.max_quote_spend = Decimal("2")

    with pytest.raises(FrozenInstanceError):
        proposal.correlation_id = "new-correlation"


def test_signal_relationship_inherits_exact_signal_id_and_symbol():
    signal = make_signal(symbol="BTC/USDT")
    proposal = make_market_proposal(signal=signal)

    assert proposal.signal_id == signal.signal_id
    assert proposal.symbol == signal.symbol


def test_helper_rejects_non_signal_inputs():
    with pytest.raises(ContractError, match="signal must be Signal"):
        build_trade_proposal(
            signal=object(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


@pytest.mark.parametrize("value", [1, 1.0, True, False, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("0"), Decimal("-0.001")])
def test_invalid_quantity_values_are_rejected(value):
    with pytest.raises(ContractError, match="requested_quantity"):
        make_market_proposal_with_quantity(value)


def make_market_proposal_with_quantity(value):
    return build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=value,
        requested_price=None,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )


@pytest.mark.parametrize("value", [Decimal("1"), Decimal("0.001")])
def test_valid_decimal_quantities_are_accepted(value):
    proposal = build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=value,
        requested_price=None,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )
    assert type(proposal.requested_quantity) is Decimal
    assert proposal.requested_quantity == value


@pytest.mark.parametrize(
    "value",
    [None, Decimal("100.25")],
)
def test_market_price_policy_values_are_supported(value):
    if value is None:
        proposal = make_market_proposal()
        assert proposal.requested_price is None
    else:
        proposal = make_limit_proposal(requested_price=value)
        assert proposal.requested_price == value
        assert type(proposal.requested_price) is Decimal


@pytest.mark.parametrize(
    "value",
    [0, -1, 1.0, True, False, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_invalid_present_prices_are_rejected(value):
    with pytest.raises(ContractError, match="requested_price"):
        make_limit_proposal(requested_price=value)


@pytest.mark.parametrize(
    "order_type,price_policy,price",
    [
        (TradeOrderType.MARKET, PricePolicy.MARKET_REFERENCE, None),
        (TradeOrderType.LIMIT, PricePolicy.FIXED, Decimal("100.25")),
    ],
)
def test_valid_order_price_combinations(order_type, price_policy, price):
    proposal = build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=Decimal("0.001"),
        requested_price=price,
        price_policy=price_policy,
        order_type=order_type,
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )
    assert proposal.order_type is order_type
    assert proposal.price_policy is price_policy


@pytest.mark.parametrize(
    "order_type,price_policy,price",
    [
        (TradeOrderType.MARKET, PricePolicy.FIXED, None),
        (TradeOrderType.MARKET, PricePolicy.FIXED, Decimal("100.25")),
        (TradeOrderType.MARKET, PricePolicy.MARKET_REFERENCE, Decimal("100.25")),
        (TradeOrderType.LIMIT, PricePolicy.FIXED, None),
        (TradeOrderType.LIMIT, PricePolicy.MARKET_REFERENCE, Decimal("100.25")),
        (TradeOrderType.LIMIT, PricePolicy.MARKET_REFERENCE, None),
    ],
)
def test_invalid_order_price_combinations_are_rejected(
    order_type, price_policy, price
):
    with pytest.raises(ContractError):
        build_trade_proposal(
            signal=make_signal(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=price,
            price_policy=price_policy,
            order_type=order_type,
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


@pytest.mark.parametrize(
    "decision_timestamp",
    [
        BASE,
        BASE + timedelta(seconds=1),
    ],
)
def test_decision_timestamp_must_not_precede_signal(decision_timestamp):
    proposal = build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=Decimal("0.001"),
        requested_price=None,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
        decision_timestamp=decision_timestamp,
    )
    assert proposal.decision_timestamp == decision_timestamp

    with pytest.raises(ContractError):
        build_trade_proposal(
            signal=make_signal(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
            decision_timestamp=BASE - timedelta(microseconds=1),
        )


def test_naive_decision_timestamp_is_rejected():
    with pytest.raises(ContractError, match="timezone-aware"):
        build_trade_proposal(
            signal=make_signal(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
            decision_timestamp=datetime(2026, 10, 3, 12, 0),
        )


@pytest.mark.parametrize("field_name,value", [("strategy_identity", ""), ("strategy_version", "")])
def test_strategy_metadata_must_be_non_empty(field_name, value):
    kwargs = {
        "signal": make_signal(),
        "side": TradeSide.BUY,
        "requested_quantity": Decimal("0.001"),
        "requested_price": None,
        "max_quote_spend": Decimal("1"),
        "price_policy": PricePolicy.MARKET_REFERENCE,
        "order_type": TradeOrderType.MARKET,
        "strategy_identity": "strategy.example",
        "strategy_version": "1.0.0",
    }
    kwargs[field_name] = value

    with pytest.raises(ContractError, match=field_name):
        build_trade_proposal(**kwargs)


@pytest.mark.parametrize("value", ["BUY", "SELL", "", None])
def test_side_must_be_trade_side_enum(value):
    with pytest.raises(ContractError, match="side must be TradeSide"):
        build_trade_proposal(
            signal=make_signal(),
            side=value,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
        max_quote_spend=Decimal("1"),
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


def test_signal_direction_is_not_reinterpreted():
    signal = make_signal(direction="BUY")
    proposal = make_market_proposal(
        signal=signal,
        side=TradeSide.SELL,
        max_quote_spend=None,
    )

    assert signal.direction == "BUY"
    assert proposal.side is TradeSide.SELL


def test_two_proposals_from_one_signal_have_independent_correlation_ids():
    signal = make_signal()
    first = make_market_proposal(signal=signal)
    second = make_market_proposal(signal=signal)

    assert first.signal_id == second.signal_id
    assert first.correlation_id != second.correlation_id
    assert first.proposal_id != second.proposal_id


def test_default_decision_timestamp_is_signal_timestamp_without_clock_use():
    signal = make_signal()
    proposal = make_market_proposal(signal=signal)

    assert proposal.decision_timestamp == signal.decision_timestamp


def test_signal_is_not_modified_by_builder():
    signal = make_signal()
    before = (
        signal.symbol,
        signal.hypothesis_id,
        signal.direction,
        signal.generated_at,
        signal.decision_timestamp,
        signal.provenance,
        signal.evidence,
        signal.expires_at,
        signal.validity,
        signal.signal_id,
    )

    make_market_proposal(signal=signal)

    after = (
        signal.symbol,
        signal.hypothesis_id,
        signal.direction,
        signal.generated_at,
        signal.decision_timestamp,
        signal.provenance,
        signal.evidence,
        signal.expires_at,
        signal.validity,
        signal.signal_id,
    )

    assert after == before


def test_enums_are_provider_neutral_string_enums():
    assert issubclass(TradeSide, Enum)
    assert issubclass(TradeOrderType, Enum)
    assert issubclass(PricePolicy, Enum)
    assert {item.value for item in TradeSide} == {"BUY", "SELL"}
    assert {item.value for item in TradeOrderType} == {"MARKET", "LIMIT"}
    assert {item.value for item in PricePolicy} == {"FIXED", "MARKET_REFERENCE"}


def test_direct_constructor_remains_non_executable_and_has_no_provider_fields():
    proposal = make_market_proposal()
    assert not hasattr(proposal, "provider")
    assert not hasattr(proposal, "venue")
    assert not hasattr(proposal, "account_id")
    assert not hasattr(proposal, "risk_decision_id")
    assert not hasattr(proposal, "order_intent_id")
    assert not hasattr(proposal, "execution_result")


def test_module_does_not_reference_execution_or_risk_layers():
    import ast
    from pathlib import Path

    source = Path("bot_obrero/trade_proposal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: list[str] = []
    referenced_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append((node.module or "").lower())
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.Name):
            referenced_names.append(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced_names.append(node.attr.lower())

    forbidden_modules = (
        "binance",
        "execution",
        "account",
        "risk",
        "reservation",
        "financialevidence",
    )
    forbidden_names = (
        "executionboundary",
        "orderintent",
        "riskengine",
        "riskdecision",
        "reservationstate",
        "financialevidence",
    )

    for fragment in forbidden_modules:
        assert all(fragment not in module for module in imported_modules)
    for fragment in forbidden_names:
        assert fragment not in referenced_names


def test_helper_requires_explicit_economic_fields():
    proposal = make_limit_proposal()
    assert proposal.requested_quantity == Decimal("0.001")
    assert proposal.requested_price == Decimal("100.25")
    assert proposal.max_quote_spend is None
    assert proposal.price_policy is PricePolicy.FIXED
    assert proposal.order_type is TradeOrderType.LIMIT



@pytest.mark.parametrize("value", [Decimal("1"), Decimal("0.001")])
def test_buy_market_valid_max_quote_spend_values_are_accepted(value):
    proposal = build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=Decimal("0.001"),
        requested_price=None,
        max_quote_spend=value,
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        strategy_identity="strategy.example",
        strategy_version="1.0.0",
    )

    assert type(proposal.max_quote_spend) is Decimal
    assert proposal.max_quote_spend == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        Decimal("0"),
        Decimal("-0.001"),
        1,
        1.0,
        True,
        False,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_buy_market_invalid_max_quote_spend_values_are_rejected(value):
    with pytest.raises(ContractError, match="max_quote_spend"):
        build_trade_proposal(
            signal=make_signal(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            max_quote_spend=value,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


@pytest.mark.parametrize(
    ("side", "order_type", "price_policy", "requested_price", "value"),
    [
        (
            TradeSide.BUY,
            TradeOrderType.LIMIT,
            PricePolicy.FIXED,
            Decimal("100.25"),
            Decimal("10"),
        ),
        (
            TradeSide.SELL,
            TradeOrderType.MARKET,
            PricePolicy.MARKET_REFERENCE,
            None,
            Decimal("10"),
        ),
        (
            TradeSide.SELL,
            TradeOrderType.LIMIT,
            PricePolicy.FIXED,
            Decimal("100.25"),
            Decimal("10"),
        ),
    ],
)
def test_max_quote_spend_is_forbidden_outside_buy_market(
    side, order_type, price_policy, requested_price, value
):
    with pytest.raises(ContractError, match="max_quote_spend"):
        build_trade_proposal(
            signal=make_signal(),
            side=side,
            requested_quantity=Decimal("0.001"),
            requested_price=requested_price,
            max_quote_spend=value,
            price_policy=price_policy,
            order_type=order_type,
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


def test_buy_market_max_quote_spend_is_required_and_not_inferred():
    with pytest.raises(ContractError, match="max_quote_spend"):
        build_trade_proposal(
            signal=make_signal(),
            side=TradeSide.BUY,
            requested_quantity=Decimal("0.001"),
            requested_price=None,
            max_quote_spend=None,
            price_policy=PricePolicy.MARKET_REFERENCE,
            order_type=TradeOrderType.MARKET,
            strategy_identity="strategy.example",
            strategy_version="1.0.0",
        )


def test_trade_proposal_economic_fields_remain_distinct():
    proposal = make_market_proposal(max_quote_spend=Decimal("25"))

    assert proposal.requested_quantity == Decimal("0.001")
    assert proposal.requested_price is None
    assert proposal.max_quote_spend == Decimal("25")
    assert proposal.requested_quantity != proposal.max_quote_spend
