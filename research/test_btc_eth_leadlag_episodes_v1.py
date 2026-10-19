from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
from research import btc_eth_leadlag_episodes_v1 as study

def synthetic_frames(n=220):
 idx=pd.date_range("2025-01-01T00:00:00Z",periods=n,freq="h")
 btc_close=100*np.power(1.004,np.arange(n,dtype=float))
 eth_close=100*np.power(1.001,np.arange(n,dtype=float))
 out={}
 for sym,close in (("BTCUSDT",btc_close),("ETHUSDT",eth_close)):
  out[sym]=pd.DataFrame({"timestamp":idx,"open":close,"high":close*1.002,"low":close*.998,
    "close":close,"volume":np.full(n,10.0),"available_at":[None]*n,"finality":["UNKNOWN"]*n},index=idx)
 return out

def test_frozen_rule_and_cost_scenarios_are_single_fixed_hypothesis():
 assert tuple(study.COSTS)==("BASE","STRESS_1","STRESS_2")
 assert study.REPLICATES==10000 and study.SEED==20261019
 assert study.MONTH_BLOCK==3 and study.DAY_BLOCK==90

def test_episode_logic_returns_sequential_nonoverlap_and_accounts_for_every_signal():
 events,counts=study.episodes_for(synthetic_frames())
 assert counts["raw_signal_count"]>0
 assert counts["accepted_episode_count"]>0
 assert counts["overlapping_signals_skipped"]>0
 assert counts["raw_signal_count"]==counts["accepted_episode_count"]+counts["overlapping_signals_skipped"]+counts["signals_rejected_incomplete_horizon"]
 for a,b in zip(events,events[1:]):
  assert pd.Timestamp(b["entry_time"])>=pd.Timestamp(a["exit_time"])
  assert pd.Timestamp(a["exit_time"])-pd.Timestamp(a["entry_time"])==pd.Timedelta(hours=24)

def test_entry_is_next_hour_open_and_exit_is_24h_after_entry():
 events,_=study.episodes_for(synthetic_frames())
 assert events
 for e in events:
  assert pd.Timestamp(e["entry_time"])==pd.Timestamp(e["signal_close_time"])
  assert pd.Timestamp(e["exit_time"])-pd.Timestamp(e["entry_time"])==pd.Timedelta(hours=24)

def test_roundtrip_costs_make_flat_market_negative_and_stresses_are_worse():
 r={k:study.net_return(100,100,v) for k,v in study.COSTS.items()}
 assert r["BASE"]<0
 assert r["STRESS_1"]<r["BASE"]<0
 assert r["STRESS_2"]<r["STRESS_1"]

def test_circular_block_bootstrap_is_repeatable_and_rejects_nonfinite():
 x=np.array([.01,-.02,.03,.005,-.01,.02,0,.015,-.005])
 a=study.bootstrap(x,3,500,123); b=study.bootstrap(x,3,500,123)
 assert a==b and a["n_observations"]==9 and a["block_length"]==3
 assert 0<a["one_sided_null_centered_p_value_mean_greater_zero"]<=1
 with pytest.raises(ValueError,match="INVALID_BOOTSTRAP"):
  study.bootstrap(np.array([.1,np.nan,.2]),2,100,1)

def test_pinned_dataset_check_refuses_bad_hash(tmp_path):
 p=tmp_path/"bad.json"; p.write_text(json.dumps({"dataset_sha256":"bad","configuration_sha256":"bad"}))
 with pytest.raises(ValueError,match="PINNED_DATASET_SHA256_MISMATCH"):
  study.load_data(p)

def test_monthly_rows_include_cash_and_compound_realized_returns_by_exit_month():
 idx=pd.date_range("2025-01-01T00:00:00Z",periods=24*90,freq="h")
 frames={}
 for symbol,base in (("BTCUSDT",40000.0),("ETHUSDT",2000.0)):
  close=base+np.arange(len(idx),dtype=float)*.001
  frames[symbol]=pd.DataFrame({"timestamp":idx,"open":close,"high":close*1.01,"low":close*.99,
   "close":close,"volume":10.0},index=idx)
 events=[
  {"exit_month":"2025-01","net_return_base_pct":1.0,"net_return_stress_1_pct":.5,"net_return_stress_2_pct":-.1},
  {"exit_month":"2025-01","net_return_base_pct":2.0,"net_return_stress_1_pct":1.0,"net_return_stress_2_pct":0.0},
 ]
 rows=study.month_rows(events,frames)
 jan=next(r for r in rows if r["month"]=="2025-01" and r["cost_scenario"]=="BASE")
 feb=next(r for r in rows if r["month"]=="2025-02" and r["cost_scenario"]=="BASE")
 assert jan["strategy_net_return_pct"]==pytest.approx(3.02)
 assert jan["cash_return_pct"]==0 and jan["is_complete_calendar_month"]
 assert feb["strategy_net_return_pct"]==0

def test_log_return_refuses_total_loss_before_log_transform():
 with pytest.raises(ValueError,match="INVALID_RETURN_FOR_LOG"):
  study.logret(-100.0)
