"""Frozen BTC volatility-squeeze breakout replay; public data only, no orders."""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence
from research import btc_volume_shock_reversal_v1 as core

STRATEGY="VOLATILITY_SQUEEZE_BREAKOUT"
PROTOCOL="BTC_VOLATILITY_SQUEEZE_BREAKOUT_V1"
ENTRY_BREAKOUT_BARS=20
ATR_WINDOW=14
COMPRESSION_LOOKBACK=100
COMPRESSION_MULTIPLE=Decimal("0.75")
TAKE_PROFIT=Decimal("1.04")
STOP_LOSS=Decimal("0.97")
MAX_HOLD=10

def true_range_at(rows:Sequence[dict[str,Any]],i:int)->Decimal:
    if i<=0: raise ValueError("TRUE_RANGE_NEEDS_PREVIOUS_CLOSE")
    high,low,prev=(core.D(rows[i][k]) for k in ("high","low"))
    prev_close=core.D(rows[i-1]["close"])
    return max(high-low,abs(high-prev_close),abs(low-prev_close))

def atr_pct_at(rows:Sequence[dict[str,Any]],i:int)->Decimal:
    """Simple ATR(14)/close at i, built exclusively from completed bars through i."""
    if i<ATR_WINDOW: raise ValueError("ATR_WARMUP_REQUIRED")
    atr=sum((true_range_at(rows,j) for j in range(i-ATR_WINDOW+1,i+1)),Decimal(0))/Decimal(ATR_WINDOW)
    close=core.D(rows[i]["close"])
    if close<=0: raise ValueError("CLOSE_MUST_BE_POSITIVE")
    return atr/close

def squeeze_entry_signal(rows:Sequence[dict[str,Any]],i:int)->bool:
    """At close i: close breaks prior 20 highs after ATR compression measured only up to i-1."""
    if i<max(ENTRY_BREAKOUT_BARS,COMPRESSION_LOOKBACK+ATR_WINDOW): return False
    prior_high=max(core.D(rows[j]["high"]) for j in range(i-ENTRY_BREAKOUT_BARS,i))
    if core.D(rows[i]["close"])<=prior_high: return False
    # The compression regime is fully known before the breakout candle closes.
    prior_atr=atr_pct_at(rows,i-1)
    historic=[atr_pct_at(rows,j) for j in range(i-COMPRESSION_LOOKBACK,i)]
    baseline=core.median_decimal(historic)
    return prior_atr<=baseline*COMPRESSION_MULTIPLE

def activate_frozen_rules():
    # Reuse the tested execution/accounting engine; only the signal and frozen exits differ.
    core.entry_signal=squeeze_entry_signal
    core.TP=TAKE_PROFIT
    core.SL=STOP_LOSS
    core.MAX_HOLD=MAX_HOLD

def _blocked(outdir:Path,error:str,manifest:dict[str,Any])->int:
    outdir.mkdir(parents=True,exist_ok=True)
    manifest["status"]="BLOCKED"; manifest["blocker"]=error
    (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8")
    (outdir/"report.md").write_text("# BTC volatility-squeeze breakout V1\n\nStatus: BLOCKED. No economic result is valid.\n\n"
      f"Blocker: {error}\n\nNo candle was interpolated or silently removed. Operational decision: NO_TRADE.\n",encoding="utf-8")
    print("BLOCKED:"+error,file=sys.stderr)
    return 2

def _cash_result(cash:Decimal,rows:Sequence[dict[str,Any]],a:int,b:int)->dict[str,Any]:
    return {"method":"CASH","final":cash,"pnl":Decimal(0),"return_pct":Decimal(0),"mdd_pct":Decimal(0),
      "trades":0,"wins":0,"losses":0,"fees":Decimal(0),"friction":Decimal(0),"rejects":0,"fills":[],
      "curve":[{"timestamp_utc":rows[a]["timestamp"],"phase":"START","equity_usdt":core.ds(cash)}]+[
       {"timestamp_utc":rows[i]["timestamp"],"phase":"CLOSE","equity_usdt":core.ds(cash)} for i in range(a,b)]}

def run(outdir:Path)->int:
    outdir.mkdir(parents=True,exist_ok=True)
    activate_frozen_rules()
    manifest={"protocol":PROTOCOL,"symbol":"BTCUSDT","interval":"1d",
      "start_utc":core.START.isoformat(),"end_exclusive_utc":core.END.isoformat(),
      "point_in_time_availability":"UNKNOWN","historical_exchange_filter_equivalence":"UNKNOWN",
      "historical_spread_slippage":"MODELED_ASSUMPTIONS_ONLY","main_changed":False,
      "operational_decision":"NO_TRADE","source_core_commit":"d2d26aa7604ba357fe3b4f8422afa64b97b080e0",
      "frozen_rule":{"breakout_close_over_prior_high_bars":ENTRY_BREAKOUT_BARS,
        "atr_window":ATR_WINDOW,"compression_baseline_bars":COMPRESSION_LOOKBACK,
        "prior_atr_pct_lte_baseline_multiple":str(COMPRESSION_MULTIPLE),
        "entry_fraction":str(core.ALLOC),"take_profit_multiple":str(TAKE_PROFIT),
        "stop_loss_multiple":str(STOP_LOSS),"max_holding_daily_candles":MAX_HOLD,
        "execution":"signal on completed close; entry and exit at next daily open; terminal close liquidation"}}
    try:
        rows,pages=core.fetch_history()
        quality=core.validate_candles(rows)
        manifest.update({"quality":quality,"row_count":len(rows),"raw_pages":pages,
                         "canonical_dataset_sha256":core.canonical_data_hash(rows)})
        if not quality["ok"]: return _blocked(outdir,"DATA_INTEGRITY_GATE_FAILED",manifest)
        filters,filter_evidence=core.fetch_filters()
        manifest.update({"exchange_info":filter_evidence,"filters_current":{k:core.ds(v) for k,v in filters.items()},
          "first_open_utc":rows[0]["timestamp"],"last_open_utc":rows[-1]["timestamp"]})
        with (outdir/"candles.csv").open("w",newline="",encoding="utf-8") as handle:
            writer=csv.DictWriter(handle,fieldnames=["open_ms","timestamp","close_ms","open","high","low","close","volume"])
            writer.writeheader()
            for row in rows:
                writer.writerow({"open_ms":row["open_ms"],"timestamp":row["timestamp"],"close_ms":row["close_ms"],
                  **{k:core.ds(row[k]) for k in ("open","high","low","close","volume")}})
        index={row["open_ms"]:i for i,row in enumerate(rows)}
        metrics=[]; fills=[]; equities=[]
        for wname,start,end in core.WINDOWS:
            if core.to_ms(start) not in index: raise core.DataIntegrityError("WINDOW_START_MISSING:"+wname)
            a=index[core.to_ms(start)]; b=index.get(core.to_ms(end),len(rows))
            if b<=a: raise core.DataIntegrityError("WINDOW_ORDER_INVALID:"+wname)
            for capital in core.CAPITALS:
                for cname in core.COSTS:
                    for method in (STRATEGY,"BTC_BUY_AND_HOLD","CASH"):
                        if method==STRATEGY:
                            result=core.simulate(rows,a,b,capital,cname,filters,wname,"VOLUME_SHOCK_REVERSAL")
                            result["method"]=STRATEGY
                            for fill in result["fills"]: fill["method"]=STRATEGY
                        elif method=="BTC_BUY_AND_HOLD":
                            result=core.simulate(rows,a,b,capital,cname,filters,wname,"BTC_BUY_AND_HOLD")
                        else:
                            result=_cash_result(capital,rows,a,b)
                        metrics.append(core.metric_row(wname,start,end,capital,cname,result))
                        fills.extend(result["fills"])
                        equities.extend({"window":wname,"capital":core.ds(capital),"cost":cname,
                           "method":result["method"],**point} for point in result["curve"])
        metric_fields=["window","start_utc","end_exclusive_utc","capital","cost","method","final_equity_usdt",
          "net_pnl_usdt","return_pct","max_drawdown_pct","closed_roundtrips","wins","losses","fees_usdt",
          "spread_slippage_usdt","rejected_orders"]
        fill_fields=["window","capital","cost","method","trade_id","side","reason","signal_utc","execution_utc",
          "execution_type","raw_price","fill_price","qty_btc","notional_usdt","fee_usdt","friction_usdt","cash_after"]
        equity_fields=["window","capital","cost","method","timestamp_utc","phase","equity_usdt"]
        core.csv_write(outdir/"summary.csv",metrics,metric_fields)
        core.csv_write(outdir/"fills.csv",fills,fill_fields)
        core.csv_write(outdir/"equity.csv",equities,equity_fields)
        # Reuse the existing frozen gate evaluator through an explicit strategy-name mapping.
        gate_metrics=[{**row,"method":"VOLUME_SHOCK_REVERSAL"} if row["method"]==STRATEGY else row for row in metrics]
        gates=core.gate_results(gate_metrics,True)
        manifest["status"]="COMPLETED"; manifest["strategy_gate"]=gates["overall"]
        manifest["output_counts"]={"summary_rows":len(metrics),"fill_rows":len(fills),"equity_rows":len(equities)}
        (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8")
        (outdir/"gate_results.json").write_text(json.dumps(gates,indent=2,sort_keys=True),encoding="utf-8")
        lines=["# BTC volatility-squeeze breakout V1","",
          "Frozen economic gate: "+gates["overall"],"",
          "Dataset SHA-256: "+manifest["canonical_dataset_sha256"],"Candle count: "+str(manifest["row_count"]),
          "Continuity validation: PASS; timestamp, close-time and OHLCV contracts passed.",
          "Point-in-time data availability: UNKNOWN; historic lot filters: UNKNOWN.",
          "Spread/slippage are modeled assumptions, not observed historic quotes.","",
          "## Strategy results across costs","",
          "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | Fees USDT | Friction USDT |",
          "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        for wname,_,_ in core.WINDOWS:
            for capital in core.CAPITALS:
                for cname in core.COSTS:
                    row=next(x for x in metrics if x["window"]==wname and x["capital"]==core.ds(capital)
                      and x["cost"]==cname and x["method"]==STRATEGY)
                    lines.append(f"| {wname} | {int(capital)} | {cname} | {row['net_pnl_usdt']} | {row['return_pct']} | "
                      f"{row['max_drawdown_pct']} | {row['closed_roundtrips']} | {row['fees_usdt']} | {row['spread_slippage_usdt']} |")
        lines += ["","## Frozen gate outcomes","","Passed: "+str(gates.get("passed",0))+"; failed: "+str(gates.get("failed",0)),"",
          "| Gate | Status | Details |","|---|---|---|"]
        lines += [f"| {item['gate']} | {item['status']} | {item.get('details','')} |" for item in gates["checks"]]
        lines += ["","Successful workflow means the program completed, not that the strategy is profitable.",
          "This historical archive has been inspected before; any gate pass would require prospective shadow validation.",
          "No real orders, credentials, leverage or main changes. Operational decision: NO_TRADE."]
        (outdir/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
        print(json.dumps({"status":"COMPLETED","rows":len(rows),"dataset_sha256":manifest["canonical_dataset_sha256"],
          "strategy_gate":gates["overall"],"passed":gates.get("passed"),"failed":gates.get("failed"),
          "main_changed":False},sort_keys=True))
        return 0
    except Exception as exc:
        return _blocked(outdir,type(exc).__name__+":"+str(exc),manifest)

def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-dir",default="squeeze-breakout-output")
    return run(Path(parser.parse_args(argv).output_dir))
if __name__=="__main__": raise SystemExit(main())
