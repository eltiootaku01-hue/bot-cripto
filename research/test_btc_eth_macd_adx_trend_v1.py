from __future__ import annotations
from decimal import Decimal
import math
import numpy as np
import pandas as pd
from research import btc_eth_macd_adx_trend_v1 as study

def synthetic_frames(days: int = 1050):
    idx=pd.date_range("2024-11-12T00:00:00Z",periods=days*6,freq="4h")
    t=np.arange(len(idx),dtype=float); out={}
    for symbol,base,drift,phase in (
        ("BTCUSDT",1000.0,0.000025,0.0),
        ("ETHUSDT",500.0,0.000022,0.9),
    ):
        close=base*np.exp(drift*t+0.045*np.sin(t/53.0+phase)+0.018*np.sin(t/11.0+phase))
        open_=np.r_[close[0]*(1-0.001),close[:-1]]
        high=np.maximum(open_,close)*1.003
        low=np.minimum(open_,close)*0.997
        frame=pd.DataFrame({"timestamp":idx,"open":open_,"high":high,"low":low,"close":close,
                            "volume":np.full(len(idx),1000.0)}).set_index("timestamp",drop=False)
        out[symbol]=frame
    return out

def test_macd_adx_and_signals_are_invariant_to_future_suffix():
    frames=synthetic_frames(420)
    full={s:study._indicators(d) for s,d in frames.items()}
    cutoff=270*6
    prefix={s:study._indicators(d.iloc[:cutoff].copy()) for s,d in frames.items()}
    columns=("ema20","ema50","ema200","macd","macd_signal","macd_hist","atr14","adx14",
             "cross_up","cross_down","buy_signal","exit_signal","weight")
    check=240*6
    for s in study.ASSETS:
        for col in columns:
            pd.testing.assert_series_equal(full[s][col].iloc[:check].reset_index(drop=True),
                prefix[s][col].iloc[:check].reset_index(drop=True),check_names=False,check_dtype=False)

def test_wilder_adx_and_macd_warmup_contracts():
    frames=synthetic_frames(380)
    for symbol in study.ASSETS:
        d=study._indicators(frames[symbol])
        assert d["macd"].first_valid_index() is not None
        assert d["macd_signal"].first_valid_index() is not None
        assert d["adx14"].first_valid_index() is not None
        adx=d["adx14"].dropna()
        assert (adx>=0).all() and (adx<=100).all()
        assert d["ema200"].first_valid_index() is not None
        assert (d["weight"].dropna()>0).all()
        assert (d["weight"].dropna()<=float(study.MAX_WEIGHT)).all()

def test_fee_spread_slippage_and_lot_rounding_are_conservative():
    results={}
    for name,cost in study.COSTS.items():
        buy,fee=study._price(Decimal("100"),cost,"BUY")
        sell,_=study._price(Decimal("102"),cost,"SELL")
        results[name]=sell*(1-fee)/(buy*(1+fee))-1
    assert results["BASE"]>results["STRESS_1"]>results["STRESS_2"]
    assert study._floor(Decimal("0.123459"),Decimal("0.00001"))==Decimal("0.12345")
    assert study._floor(Decimal("0.000009"),Decimal("0.00001"))==Decimal("0.00000")

def test_virtual_replay_financial_invariants_and_nonoverlapping_positions():
    frames=synthetic_frames()
    indicators={s:study._indicators(d) for s,d in frames.items()}
    filters={
        "BTCUSDT":{"step":Decimal("0.00001"),"min_qty":Decimal("0.00001"),"min_notional":Decimal("5")},
        "ETHUSDT":{"step":Decimal("0.0001"),"min_qty":Decimal("0.0001"),"min_notional":Decimal("5")},
    }
    result=study.simulate(indicators,filters,"2025",Decimal("60"),"STRESS_2",study.COSTS["STRESS_2"])
    assert result["status"]=="COMPLETED"
    assert math.isfinite(float(result["net_pnl"]))
    assert math.isfinite(result["max_drawdown_pct"])
    assert float(result["net_pnl"])>=-60
    assert all(float(x["equity"])>=0 for x in result["equity"])
    trades=result["trades"]
    for trade in trades:
        assert pd.Timestamp(trade["entry_time"])==pd.Timestamp(trade["signal_time"])
        assert pd.Timestamp(trade["exit_time"])>=pd.Timestamp(trade["entry_time"])
        assert Decimal(trade["entry_debit"])<=Decimal("60")+Decimal("1e-8")
        assert Decimal(trade["qty"])>0
    for prev,curr in zip(trades,trades[1:]):
        assert pd.Timestamp(curr["entry_time"])>=pd.Timestamp(prev["exit_time"])
