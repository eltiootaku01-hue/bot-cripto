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
