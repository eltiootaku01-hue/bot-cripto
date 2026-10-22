from datetime import date
from decimal import Decimal
import pandas as pd
import pytest
from research import historical_cost_aware_ml_v1 as h

def test_archive_plan_uses_monthlies_and_daily_edge():
    plan=h.archive_plan(date(2026,8,1),date(2026,10,4))
    periods=[p for p,_ in plan]
    assert "2026-08" in periods and "2026-09" in periods and "2026-10-01" in periods
    assert all("monthly/klines" in u for p,u in plan if len(p)==7)
    assert all("daily/klines" in u for p,u in plan if len(p)==10)

def test_fixed_spread_is_in_round_trip_gate():
    assert h.COSTS[2].friction>h.COSTS[0].friction
    assert float(h.COSTS[2].friction*10000)==pytest.approx(120)

def test_walkforward_cutoffs_are_chronological():
    assert h.FOLDS[0][2]==h.FOLDS[1][1] and h.FOLDS[1][2]==h.FOLDS[2][1]
    assert h.FOLDS[2][2]==pd.Timestamp("2026-10-22T00:00:00Z")

def test_quantity_is_rounded_and_stays_within_cash():
    q,px,fee=h.size_for(h.CAP,Decimal("90000"),h.COSTS[0])
    assert q>0 and q%h.STEP==0 and q*px+fee<=h.CAP


def test_rest_supplement_parser_requires_allowlisted_timestamp_and_records_raw_hash():
    stamp=pd.Timestamp("2023-03-24T13:00:00Z")
    open_ms=int(stamp.timestamp()*1000)
    payload=(f'[[{open_ms},"28000.0","28010.0","27990.0","28005.0","12.5",{open_ms+3599999},"350000.0",123,"5.0","140000.0","0"]]').encode()
    frame,evidence=h.parse_rest_supplement(stamp,payload)
    assert frame.iloc[0]["timestamp"]==stamp
    assert evidence["raw_response_sha256"]==h.sha(payload)
    assert evidence["source_type"]=="supplemental_public_rest_kline"
    with pytest.raises(RuntimeError,match="NOT_ALLOWLISTED"):
        h.parse_rest_supplement(pd.Timestamp("2023-03-24T14:00:00Z"),payload)
