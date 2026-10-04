from __future__ import annotations
import ast
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from bot_obrero.analysis_contracts import ArtifactNature, Provenance
from bot_obrero.effective_capacity import EffectiveCapacityError, EffectiveCapacityStatus, calculate_effective_capacity
from bot_obrero.reservation import Reservation, ReservationReadSet, ReservationResourceKind, ReservationState
from bot_obrero.risk_contracts import BalanceSnapshot, CanonicalAccountState, Completeness

BASE=datetime(2026,10,4,13,0,tzinfo=timezone.utc)
P=Provenance('hueso-02-f1-test',ArtifactNature.OBSERVED)

def bal(asset, value):
    value=Decimal(value)
    return BalanceSnapshot(asset=asset,total=value,available=value,locked=Decimal('0'))

def can(*,account='A',balances=(bal('USDT','100'),),complete=Completeness.COMPLETE):
    return CanonicalAccountState(account_state_id='state-'+account,account_id=account,as_of=BASE,balances=balances,completeness=complete,provenance=P)

def res(rid,*,account='A',amount='30',asset='USDT',kind=ReservationResourceKind.QUOTE,state=ReservationState.ACTIVE,consumed='0'):
    r=Decimal(amount); c=Decimal(consumed)
    return Reservation(reservation_id=rid,account_id=account,resource_kind=kind,asset=asset,reserved_amount=r,consumed_amount=c,remaining_amount=r-c,state=state,proposal_id='p-'+rid,risk_decision_id='r-'+rid,correlation_id='c-'+rid,client_order_id=None,exchange_order_id=None,created_at=BASE,updated_at=BASE)

def rs(items=(),*,account='A',complete=Completeness.COMPLETE):
    return ReservationReadSet(account_id=account,reservations=tuple(items),read_at=BASE,completeness=complete)

def calc(**kw):
    account=kw.pop('account','A'); asset=kw.pop('asset','USDT'); kind=kw.pop('kind',ReservationResourceKind.QUOTE); balances=kw.pop('balances',(bal(asset,kw.pop('available','100')),)); items=kw.pop('items',()); cc=kw.pop('cc',Completeness.COMPLETE); rc=kw.pop('rc',Completeness.COMPLETE)
    assert not kw
    return calculate_effective_capacity(can(account=account,balances=balances,complete=cc),rs(items,account=account,complete=rc),resource_kind=kind,asset=asset)

def test_empty_and_active():
    assert calc().effective_available==Decimal('100')
    assert calc(items=(res('x',amount='30'),)).effective_available==Decimal('70')

def test_partial_uses_remaining():
    x=calc(items=(res('x',amount='100',state=ReservationState.PARTIALLY_CONSUMED,consumed='40'),))
    assert x.protected_active_reserved==Decimal('60') and x.effective_available==Decimal('40')

def test_unknown_protects_and_completeness_survives():
    x=calc(items=(res('x',amount='70',state=ReservationState.UNKNOWN),),rc=Completeness.UNKNOWN)
    assert x.protected_active_reserved==Decimal('70') and x.effective_available==Decimal('30') and x.completeness is Completeness.UNKNOWN

@pytest.mark.parametrize('state,consumed',[(ReservationState.CONSUMED,'70'),(ReservationState.RELEASED,'0')])
def test_terminal_zero(state,consumed):
    assert calc(items=(res('x',amount='70',state=state,consumed=consumed),)).protected_active_reserved==Decimal('0')

def test_overcommit_not_clamped():
    x=calc(items=(res('x',amount='120'),))
    assert x.effective_available==Decimal('-20') and x.status is EffectiveCapacityStatus.OVERCOMMITTED

def test_asset_and_kind_isolation():
    items=(res('u',amount='30',asset='USDT'),res('b',amount='40',asset='USDT',kind=ReservationResourceKind.BASE),res('btc',amount='2',asset='BTC'))
    x=calc(items=items,balances=(bal('USDT','100'),bal('BTC','3')))
    assert x.protected_active_reserved==Decimal('30') and x.effective_available==Decimal('70')

def test_account_mismatch_fails_closed():
    with pytest.raises(EffectiveCapacityError,match='account_id'):
        calculate_effective_capacity(can(account='A'),rs((res('x',account='B'),),account='B'),resource_kind=ReservationResourceKind.QUOTE,asset='USDT')

def test_completeness_join():
    assert calc(cc=Completeness.PARTIAL).completeness is Completeness.PARTIAL
    assert calc(rc=Completeness.PARTIAL).completeness is Completeness.PARTIAL
    assert calc(rc=Completeness.UNKNOWN).completeness is Completeness.UNKNOWN

def test_deterministic_decimal_and_no_mutation():
    items=(res('a',amount='10'),res('b',amount='20')); c=can(); s=rs(items); before=(c,s)
    a=calculate_effective_capacity(c,s,resource_kind=ReservationResourceKind.QUOTE,asset='USDT'); b=calculate_effective_capacity(c,s,resource_kind=ReservationResourceKind.QUOTE,asset='USDT')
    assert a==b and type(a.effective_available) is Decimal and a.effective_available==Decimal('70') and (c,s)==before

def test_missing_balance_not_zero():
    with pytest.raises(EffectiveCapacityError,match='balance'):
        calculate_effective_capacity(can(balances=()),rs(),resource_kind=ReservationResourceKind.QUOTE,asset='USDT')

def test_provider_neutral_no_persistence_or_time():
    source=open('bot_obrero/effective_capacity.py',encoding='utf-8').read(); tree=ast.parse(source); mods=[]; floats=[]; literals=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): mods.extend(a.name.lower() for a in n.names)
        elif isinstance(n,ast.ImportFrom): mods.append((n.module or '').lower())
        elif isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='float': floats.append(n.lineno)
        elif isinstance(n,ast.Constant) and isinstance(n.value,float): literals.append(n.lineno)
    forbidden=('binance','websocket','execution','strategy','availability','temporal')
    assert all(not any(f in m for f in forbidden) for m in mods) and floats==[] and literals==[] and 'sqlite' not in source.lower() and 'datetime.now' not in source
