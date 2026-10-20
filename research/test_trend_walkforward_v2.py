from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from trend_walkforward_v2 import (
    COSTS, GRID, GRID_COUNT, WalkForwardError, aggregate_4h, indicators,
    make_grid, make_signal_cache, simulate_slice, adverse_price, stop_reference_fill,
    EXPECTED_DATASET_SHA256, EXPECTED_CONFIG_SHA256, load_dataset,
)

def synthetic_hourly(groups: int = 420, drop_last_hours: int = 0):
    ix = pd.date_range("2025-01-01T00:00:00Z", periods=groups*4, freq="h")
    i = np.arange(groups, dtype=float)
    prices = {
        "BTCUSDT": 100*np.exp(.0006*i + .06*np.sin(i/7.0) + .012*np.sin(i/2.2)),
        "ETHUSDT": 50*np.exp(.0004*i + .07*np.sin(i/8.0+.4) + .014*np.sin(i/2.6)),
    }
    frames = {}
    for sym, closes in prices.items():
        rows=[]; prev=closes[0]*.998
        for k, c in enumerate(closes):
            hourly=np.linspace(prev,float(c),4)
            for j, close in enumerate(hourly):
                op=prev if j==0 else float(hourly[j-1])
                rows.append({"timestamp":ix[k*4+j],"open":op,"high":max(op,float(close))*1.001,
                             "low":min(op,float(close))*.999,"close":float(close),"volume":10.0})
            prev=float(c)
        df=pd.DataFrame(rows).set_index("timestamp",drop=False)
        if drop_last_hours: df=df.iloc[:-drop_last_hours]
        frames[sym]=df
    return frames

def test_parameter_grid_has_2880_unique_predeclared_configurations():
    assert GRID_COUNT == 2880
    grid=make_grid()
    assert len(grid)==len(set(grid))==2880
    assert set(GRID)=={"entry","exit","ema","slope","atr_stop","adx_min"}

def test_aggregate_only_keeps_complete_contiguous_four_hour_bars():
    frames=synthetic_hourly(groups=30,drop_last_hours=2)
    got=aggregate_4h(frames["BTCUSDT"])
    assert len(got)==29
    assert (got.index.to_series().diff().dropna()==pd.Timedelta(hours=4)).all()

def test_donchian_uses_prior_bars_and_ignores_current_bar_high_low():
    frame=aggregate_4h(synthetic_hourly()["BTCUSDT"])
    a=indicators(frame); changed=frame.copy(); t=frame.index[300]
    changed.loc[t,"high"]*=5; changed.loc[t,"low"]*=.2
    b=indicators(changed)
    for n in GRID["entry"]:
        assert a["entry_high"][n][300] == pytest.approx(b["entry_high"][n][300])
    for n in GRID["exit"]:
        assert a["exit_low"][n][300] == pytest.approx(b["exit_low"][n][300])

def test_future_suffix_does_not_change_prior_ema_or_donchian_values():
    f=aggregate_4h(synthetic_hourly(groups=420)["ETHUSDT"])
    a=indicators(f); cutoff=330; prefix=indicators(f.iloc[:cutoff].copy())
    for span in GRID["ema"]:
        np.testing.assert_allclose(a["ema"][span][:cutoff],prefix["ema"][span],equal_nan=True)
    for window in GRID["entry"]:
        np.testing.assert_allclose(a["entry_high"][window][:cutoff],prefix["entry_high"][window],equal_nan=True)

def test_stop_gap_fill_is_conservative():
    assert stop_reference_fill(100,94,95)==95
    assert stop_reference_fill(92,90,95)==92
    assert stop_reference_fill(100,96,95) is None
    with pytest.raises(WalkForwardError,match="INVALID_STOP_INPUT"):
        stop_reference_fill(100,94,0)

def test_stress_costs_are_monotonically_more_adverse():
    ret={}
    for name,cost in COSTS.items():
        b,fee=adverse_price(100,cost,"BUY");s,fee2=adverse_price(102,cost,"SELL")
        ret[name]=s*(1-fee2)/(b*(1+fee))-1
    assert ret["BASE"]>ret["STRESS_1"]>ret["STRESS_2"]

def test_candidate_replay_uses_next_open_and_has_no_overlapping_positions():
    frames=synthetic_hourly()
    four={s:aggregate_4h(f) for s,f in frames.items()}
    idx=four["BTCUSDT"].index.intersection(four["ETHUSDT"].index)
    feat={s:indicators(four[s].loc[idx]) for s in four};cache=make_signal_cache(feat)
    metrics,trades=simulate_slice(feat,cache,(20,10,100,3,2.0,0.0),260,410,scenarios=("BASE","STRESS_1"))
    assert set(metrics)=={"BASE","STRESS_1"}
    assert trades
    for prev,cur in zip(trades,trades[1:]):
        assert pd.Timestamp(cur["entry_time"]) >= pd.Timestamp(prev["exit_time"])
    assert metrics["STRESS_1"]["return_pct"] <= metrics["BASE"]["return_pct"]

def test_bad_archived_input_fails_closed_before_replay(tmp_path):
    p=tmp_path/"bad.json";p.write_text(json.dumps({"dataset_sha256":EXPECTED_DATASET_SHA256,"configuration_sha256":EXPECTED_CONFIG_SHA256}))
    with pytest.raises(WalkForwardError,match="PINNED_RAW_DATASET_SHA256_MISMATCH"):
        load_dataset(p)

@pytest.mark.parametrize("symbol",["BTCUSDT","ETHUSDT"])
def test_real_archive_pin_and_unknown_point_in_time_flags(symbol):
    p=Path(__file__).parent.parent/"input"/"btc-eth-follow-365d-dataset.json"
    if not p.is_file(): pytest.skip("CI downloads the pinned archive after unit tests; CI verifies its hashes in the replay step")
    bundle,frames,raw_hash,missing,unknown=load_dataset(p)
    assert bundle["dataset_sha256"]==EXPECTED_DATASET_SHA256
    assert bundle["configuration_sha256"]==EXPECTED_CONFIG_SHA256
    assert raw_hash=="3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36"
    assert len(frames[symbol])==15360
    assert missing[symbol]==15360 and unknown[symbol]==15360
