from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolution,
    RiskLimitResolutionStatus,
    derive_reservation_read_set_id,
)
from bot_obrero.reservation import (
    Reservation,
    ReservationReadSet,
    ReservationResourceKind,
    ReservationState,
)
from bot_obrero.risk_contracts import Completeness


BASE = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
EVALUATION = BASE + timedelta(minutes=10)


def make_reservation(
    *,
    reservation_id: str = "reservation-1",
    account_id: str = "account-1",
    resource_kind: ReservationResourceKind = ReservationResourceKind.QUOTE,
    asset: str = "USDT",
    reserved_amount: str = "100",
    consumed_amount: str = "0",
    remaining_amount: str = "100",
    state: ReservationState = ReservationState.ACTIVE,
    proposal_id: str = "proposal-1",
    risk_decision_id: str = "risk-decision-1",
    correlation_id: str = "correlation-1",
    client_order_id: str | None = None,
    exchange_order_id: str | None = None,
    created_at: datetime = BASE,
    updated_at: datetime = BASE,
) -> Reservation:
    return Reservation(
        reservation_id=reservation_id,
        account_id=account_id,
        resource_kind=resource_kind,
        asset=asset,
        reserved_amount=Decimal(reserved_amount),
        consumed_amount=Decimal(consumed_amount),
        remaining_amount=Decimal(remaining_amount),
        state=state,
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        correlation_id=correlation_id,
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        created_at=created_at,
        updated_at=updated_at,
    )


def make_read_set(
    *,
    reservations: tuple[Reservation, ...] | None = None,
    account_id: str = "account-1",
    read_at: datetime = EVALUATION,
    completeness: Completeness = Completeness.COMPLETE,
) -> ReservationReadSet:
    return ReservationReadSet(
        account_id=account_id,
        reservations=(
            make_reservation(),
            ) if reservations is None else reservations,
        read_at=read_at,
        completeness=completeness,
    )


def make_binding(
    *,
    subject_kind: AvailabilitySubjectKind = AvailabilitySubjectKind.RESERVATION_READ_SET,
    subject_id: str = "read-set-1",
    available_at: datetime = BASE,
    source: str = "canonical-test",
    evidence_reference: str = "evidence-1",
) -> AvailabilityBinding:
    return AvailabilityBinding(
        subject_kind=subject_kind,
        subject_id=subject_id,
        available_at=available_at,
        source=source,
        evidence_reference=evidence_reference,
    )


def test_availability_binding_validates_timezone_and_required_fields():
    binding = make_binding()

    assert binding.subject_kind is AvailabilitySubjectKind.RESERVATION_READ_SET
    assert binding.subject_id == "read-set-1"
    assert binding.available_at == BASE
    assert binding.source == "canonical-test"
    assert binding.evidence_reference == "evidence-1"
    assert binding.binding_id

    with pytest.raises(ValueError, match="subject_kind"):
        AvailabilityBinding(
            subject_kind="RESERVATION_READ_SET",
            subject_id="read-set-1",
            available_at=BASE,
            source="source",
            evidence_reference="ref",
        )

    with pytest.raises(ValueError, match="subject_id"):
        make_binding(subject_id=" ")

    with pytest.raises(ValueError, match="source"):
        make_binding(source="")

    with pytest.raises(ValueError, match="evidence_reference"):
        make_binding(evidence_reference=" ")

    with pytest.raises(ValueError, match="timezone-aware"):
        make_binding(available_at=datetime(2026, 10, 5, 20, 0))


@pytest.mark.parametrize(
    "subject_kind",
    list(AvailabilitySubjectKind),
)
def test_subject_kind_is_closed_and_provider_neutral(subject_kind):
    binding = make_binding(subject_kind=subject_kind)
    assert binding.subject_kind is subject_kind


def test_availability_binding_accepts_equal_or_earlier_evaluation_time():
    binding = make_binding(available_at=BASE)

    binding.validate_for(BASE)
    binding.validate_for(EVALUATION)


def test_availability_binding_rejects_future_availability_and_naive_evaluation():
    binding = make_binding(available_at=EVALUATION + timedelta(seconds=1))

    with pytest.raises(ValueError, match="not valid"):
        binding.validate_for(EVALUATION)

    with pytest.raises(ValueError, match="timezone-aware"):
        binding.validate_for(datetime(2026, 10, 5, 20, 10))


def test_availability_binding_id_is_deterministic():
    first = make_binding()
    second = make_binding()

    assert first.binding_id == second.binding_id
    assert len(first.binding_id) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"subject_id": "read-set-2"},
        {"source": "source-2"},
        {"evidence_reference": "evidence-2"},
        {"available_at": BASE + timedelta(seconds=1)},
        {"subject_kind": AvailabilitySubjectKind.ACCOUNT_STATE},
    ],
)
def test_availability_binding_id_changes_with_content(changes):
    first = make_binding()
    second = make_binding(**changes)

    assert first.binding_id != second.binding_id


def test_availability_binding_is_immutable():
    binding = make_binding()

    with pytest.raises(FrozenInstanceError):
        binding.subject_id = "changed"

    with pytest.raises(FrozenInstanceError):
        binding.binding_id = "changed"


def test_reservation_read_set_identity_is_deterministic():
    first = make_read_set()
    second = make_read_set()

    assert derive_reservation_read_set_id(first) == derive_reservation_read_set_id(second)


def test_read_at_is_not_part_of_reservation_read_set_identity():
    first = make_read_set(read_at=EVALUATION)
    second = make_read_set(read_at=EVALUATION + timedelta(seconds=30))

    assert derive_reservation_read_set_id(first) == derive_reservation_read_set_id(second)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda reservation: replace(
                reservation,
                reservation_id="reservation-2",
            ),
            "different reservation",
        ),
        (
            lambda reservation: replace(
                reservation,
                state=ReservationState.RELEASED,
            ),
            "different state",
        ),
        (
            lambda reservation: replace(
                reservation,
                reserved_amount=Decimal("120"),
                remaining_amount=Decimal("120"),
            ),
            "different amount",
        ),
        (
            lambda reservation: replace(
                reservation,
                proposal_id="proposal-2",
            ),
            "different proposal",
        ),
        (
            lambda reservation: replace(
                reservation,
                risk_decision_id="risk-decision-2",
            ),
            "different risk identity",
        ),
        (
            lambda reservation: replace(
                reservation,
                correlation_id="correlation-2",
            ),
            "different correlation identity",
        ),
        (
            lambda reservation: replace(
                reservation,
                client_order_id="client-order-1",
            ),
            "different client order identity",
        ),
        (
            lambda reservation: replace(
                reservation,
                exchange_order_id="exchange-order-1",
            ),
            "different exchange order identity",
        ),
        (
            lambda reservation: replace(
                reservation,
                created_at=BASE + timedelta(seconds=1),
                updated_at=BASE + timedelta(seconds=1),
            ),
            "different timestamps",
        ),
    ],
)
def test_reservation_content_change_changes_identity(mutator, expected):
    original_reservation = make_reservation()
    changed_reservation = mutator(original_reservation)

    first = make_read_set(reservations=(original_reservation,))
    second = make_read_set(reservations=(changed_reservation,))

    assert derive_reservation_read_set_id(first) != derive_reservation_read_set_id(second), expected


def test_read_set_completeness_changes_identity():
    complete = make_read_set(completeness=Completeness.COMPLETE)
    partial = make_read_set(completeness=Completeness.PARTIAL)

    assert derive_reservation_read_set_id(complete) != derive_reservation_read_set_id(partial)


def test_read_set_account_changes_identity():
    first = make_read_set(account_id="account-1")
    second = make_read_set(
        account_id="account-2",
        reservations=(
            replace(make_reservation(), account_id="account-2"),
        ),
    )

    assert derive_reservation_read_set_id(first) != derive_reservation_read_set_id(second)


def test_reservation_order_is_part_of_identity():
    first_reservation = make_reservation(
        reservation_id="reservation-1",
        proposal_id="proposal-1",
        risk_decision_id="risk-decision-1",
        correlation_id="correlation-1",
    )
    second_reservation = make_reservation(
        reservation_id="reservation-2",
        proposal_id="proposal-2",
        risk_decision_id="risk-decision-2",
        correlation_id="correlation-2",
    )

    first = make_read_set(reservations=(first_reservation, second_reservation))
    second = make_read_set(reservations=(second_reservation, first_reservation))

    assert derive_reservation_read_set_id(first) != derive_reservation_read_set_id(second)


def test_derive_identity_accepts_partial_and_unknown_without_reinterpreting_them():
    partial = make_read_set(completeness=Completeness.PARTIAL)
    unknown = make_read_set(completeness=Completeness.UNKNOWN)

    partial_id = derive_reservation_read_set_id(partial)
    unknown_id = derive_reservation_read_set_id(unknown)

    assert partial.completeness is Completeness.PARTIAL
    assert unknown.completeness is Completeness.UNKNOWN
    assert partial_id != unknown_id


def test_derive_identity_does_not_mutate_read_set():
    reservation = make_reservation()
    read_set = make_read_set(reservations=(reservation,))

    before = (
        read_set.account_id,
        read_set.reservations,
        read_set.read_at,
        read_set.completeness,
    )

    derive_reservation_read_set_id(read_set)

    after = (
        read_set.account_id,
        read_set.reservations,
        read_set.read_at,
        read_set.completeness,
    )

    assert after == before


def test_derive_identity_accepts_only_reservation_read_set():
    with pytest.raises(TypeError, match="ReservationReadSet"):
        derive_reservation_read_set_id(object())


def test_risk_limit_resolution_status_is_closed():
    assert set(RiskLimitResolutionStatus) == {
        RiskLimitResolutionStatus.AVAILABLE,
        RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
        RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
        RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
    }


def test_available_requires_a_limit_snapshot_identity_and_can_have_no_selected_ids():
    result = RiskLimitResolution(
        status=RiskLimitResolutionStatus.AVAILABLE,
        risk_limit_set_id="limit-set-1",
        applicable_limit_ids=(),
    )

    assert result.status is RiskLimitResolutionStatus.AVAILABLE
    assert result.risk_limit_set_id == "limit-set-1"
    assert result.applicable_limit_ids == ()


def test_no_applicable_limit_is_known_and_distinct_from_unavailable():
    result = RiskLimitResolution(
        status=RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
        risk_limit_set_id="limit-set-1",
        applicable_limit_ids=(),
    )

    assert result.status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT
    assert result.status is not RiskLimitResolutionStatus.LIMITS_UNAVAILABLE


def test_no_applicable_limit_rejects_applicability_claims():
    with pytest.raises(ValueError, match="applicable_limit_ids"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
            risk_limit_set_id="limit-set-1",
            applicable_limit_ids=("limit-1",),
        )


def test_limits_unavailable_is_fail_closed_for_applicability():
    result = RiskLimitResolution(
        status=RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
        risk_limit_set_id=None,
        applicable_limit_ids=(),
    )

    assert result.status is RiskLimitResolutionStatus.LIMITS_UNAVAILABLE

    with pytest.raises(ValueError, match="cannot claim a risk_limit_set_id"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
            risk_limit_set_id="limit-set-1",
            applicable_limit_ids=(),
        )

    with pytest.raises(ValueError, match="cannot claim applicable limits"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
            risk_limit_set_id=None,
            applicable_limit_ids=("limit-1",),
        )


def test_limits_incomplete_never_claims_applicability():
    result = RiskLimitResolution(
        status=RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
        risk_limit_set_id="limit-set-1",
        applicable_limit_ids=(),
    )

    assert result.status is RiskLimitResolutionStatus.LIMITS_INCOMPLETE

    with pytest.raises(ValueError, match="cannot claim applicable limits"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
            risk_limit_set_id="limit-set-1",
            applicable_limit_ids=("limit-1",),
        )


def test_resolution_rejects_missing_snapshot_identity_for_known_snapshot_statuses():
    for status in (
        RiskLimitResolutionStatus.AVAILABLE,
        RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
        RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
    ):
        with pytest.raises(ValueError, match="risk_limit_set_id"):
            RiskLimitResolution(
                status=status,
                risk_limit_set_id=None,
                applicable_limit_ids=(),
            )


def test_resolution_rejects_duplicate_or_empty_applicable_limit_ids():
    with pytest.raises(ValueError, match="non-empty"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.AVAILABLE,
            risk_limit_set_id="limit-set-1",
            applicable_limit_ids=("",),
        )

    with pytest.raises(ValueError, match="duplicates"):
        RiskLimitResolution(
            status=RiskLimitResolutionStatus.AVAILABLE,
            risk_limit_set_id="limit-set-1",
            applicable_limit_ids=("limit-1", "limit-1"),
        )


def test_resolution_is_immutable_and_normalizes_ids_to_tuple():
    result = RiskLimitResolution(
        status=RiskLimitResolutionStatus.AVAILABLE,
        risk_limit_set_id="limit-set-1",
        applicable_limit_ids=["limit-1"],
    )

    assert isinstance(result.applicable_limit_ids, tuple)

    with pytest.raises(FrozenInstanceError):
        result.status = RiskLimitResolutionStatus.LIMITS_UNAVAILABLE


def test_provider_neutrality_and_no_persistence_static_guard():
    path = Path("bot_obrero/evidence_binding.py")
    source = path.read_text(encoding="utf-8").lower()

    forbidden_fragments = (
        "binance",
        "websocket",
        "http",
        "sqlite",
        "requests",
        "uuid4",
        "provider client",
        "execute(",
    )
    assert all(fragment not in source for fragment in forbidden_fragments)

    assert "riskengine" not in source
    assert "riskevaluationcontext" not in source
    assert "select" not in source


def test_module_exports_only_phase_i3_primitives():
    module = __import__("bot_obrero.evidence_binding", fromlist=["*"])

    assert set(module.__all__) == {
        "AvailabilityBinding",
        "AvailabilitySubjectKind",
        "RiskLimitResolution",
        "RiskLimitResolutionStatus",
        "derive_reservation_read_set_id",
    }
