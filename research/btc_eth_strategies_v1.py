from __future__ import annotations
import argparse, json, hashlib, math, statistics
from pathlib import Path
import numpy as np
import pandas as pd

_parser = argparse.ArgumentParser(description='Explore predefined BTC/ETH hourly strategies on a hash-verified dataset (virtual only).')
_parser.add_argument('--dataset', type=Path, default=Path('btc-eth-follow-365d-dataset.json'), help='normalized dataset JSON extracted from the source Actions artifact')
_parser.add_argument('--output', type=Path, default=Path('btc-eth-strategies-v1-results.json'), help='path for machine-readable results JSON')
_args = _parser.parse_args()
DATA_PATH = _args.dataset
OUT_PATH = _args.output
INITIAL = 31.0
FRACTION = 0.95
MAX_ENTRIES_30D = 5
BARS_30D = 30*24
WARMUP = 90*24
VAL_LEN = 60*24
EVAL_LEN = 365*24
COSTS = {
    'BASE': {'fee':0.001, 'spread_bps':10.0, 'slippage_bps':5.0},
    'STRESS_1': {'fee':0.001, 'spread_bps':25.0, 'slippage_bps':10.0},
    'STRESS_2': {'fee':0.002, 'spread_bps':50.0, 'slippage_bps':20.0},
}

bundle = json.loads(DATA_PATH.read_text())
canonical = lambda x: json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
assert hashlib.sha256(canonical(bundle['configuration'])).hexdigest() == bundle['configuration_sha256']
assert hashlib.sha256(canonical(bundle['data'])).hexdigest() == bundle['dataset_sha256']
assert bundle['dataset_sha256'] == '13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8'
assert bundle['configuration_sha256'] == '2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a'

frames = {}
for symbol in ('BTCUSDT','ETHUSDT'):
    rows = bundle['data']['history'][symbol]
    f = pd.DataFrame([{**r['candle'], 'timestamp':r['candle']['start']} for r in rows])
    f['timestamp'] = pd.to_datetime(f['timestamp'], utc=True)
    for col in ('open','high','low','close','volume'):
        f[col] = pd.to_numeric(f[col], errors='raise')
    assert len(f) == 15360
    assert f.timestamp.is_monotonic_increasing and f.timestamp.is_unique
    assert (f.timestamp.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert (f[['open','high','low','close']] > 0).all().all()
    assert (f.high >= f[['open','close','low']].max(axis=1)).all()
    assert (f.low <= f[['open','close','high']].min(axis=1)).all()
    frames[symbol] = f
assert frames['BTCUSDT'].timestamp.equals(frames['ETHUSDT'].timestamp)

# chronology is fixed before result inspection; validate folds precede the held-out eval.
folds = []
start_validation = WARMUP
for n in range(1,4):
    folds.append((f'VALIDATION_{n}', start_validation+(n-1)*VAL_LEN, start_validation+n*VAL_LEN))
eval_start = WARMUP + 3*VAL_LEN
eval_end = eval_start + EVAL_LEN
assert eval_end <= len(frames['ETHUSDT'])
assert all(end <= eval_start for _,_,end in folds)

# Causal indicators; EWM and rolling windows use only current/past observations.
def ema(s, period):
    return s.ewm(span=period, adjust=False, min_periods=period).mean().to_numpy(dtype=float)

def rsi_wilder(close, period=14):
    x = np.asarray(close, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) <= period: return out
    delta = np.diff(x)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    avg_g = float(gains[:period].mean())
    avg_l = float(losses[:period].mean())
    def val(g,l):
        if l == 0: return 100.0 if g > 0 else 50.0
        if g == 0: return 0.0
        return 100.0 - (100.0/(1.0+g/l))
    out[period] = val(avg_g, avg_l)
    for i in range(period+1, len(x)):
        avg_g = ((period-1)*avg_g + gains[i-1])/period
        avg_l = ((period-1)*avg_l + losses[i-1])/period
        out[i] = val(avg_g, avg_l)
    return out

def feature_frame(f):
    q = f.copy()
    c,h,l = q.close, q.high, q.low
    for n in (12,20,24,26,48,50,96,100,200):
        q[f'ema{n}'] = ema(c,n)
    q['rsi14'] = rsi_wilder(c.to_numpy(),14)
    q['mom24'] = c / c.shift(24) - 1
    q['mom72'] = c / c.shift(72) - 1
    q['mom168'] = c / c.shift(168) - 1
    q['donchian_high24_prev'] = h.rolling(24, min_periods=24).max().shift(1)
    q['donchian_low12_prev'] = l.rolling(12, min_periods=12).min().shift(1)
    return q

features = {s:feature_frame(f) for s,f in frames.items()}

def effective_filters(symbol):
    raw=bundle['data']['filters'][symbol]
    normal_step=float(raw['step_size']) if float(raw['step_size'])>0 else None
    market_step=float(raw['market_step_size']) if float(raw['market_step_size'])>0 else None
    step=market_step if market_step is not None else normal_step
    return {'min_qty':float(raw['market_min_qty']) if float(raw['market_min_qty'])>0 else float(raw['min_qty']),
            'step':step,
            'max_qty':float(raw['market_max_qty']) if float(raw['market_max_qty'])>0 else float(raw['max_qty']),
            'min_notional':float(raw['min_notional'])}

# Boolean desired exposure from information observable at the candle close.
def one_asset_signals(name, eth, btc):
    e = eth; close=e.close.to_numpy(); rsi=e.rsi14.to_numpy()
    if name == 'EMA12_26_RSI':
        fast=e.ema12.to_numpy(); slow=e.ema26.to_numpy()
        buy=np.zeros(len(e),bool); sell=np.zeros(len(e),bool)
        buy[1:] = (fast[:-1] <= slow[:-1]) & (fast[1:] > slow[1:]) & (rsi[1:] >= 50)
        sell[1:] = ((fast[:-1] >= slow[:-1]) & (fast[1:] < slow[1:])) | (rsi[1:] >= 70)
    elif name == 'EMA24_96_CROSS':
        fast=e.ema24.to_numpy(); slow=e.ema96.to_numpy()
        buy=np.zeros(len(e),bool); sell=np.zeros(len(e),bool)
        buy[1:] = (fast[:-1] <= slow[:-1]) & (fast[1:] > slow[1:])
        sell[1:] = (fast[:-1] >= slow[:-1]) & (fast[1:] < slow[1:])
    elif name == 'DONCHIAN_24_12':
        upper=e.donchian_high24_prev.to_numpy(); lower=e.donchian_low12_prev.to_numpy()
        buy=close > upper
        sell=close < lower
    elif name == 'RSI_MEANREV_EMA200':
        ema200=e.ema200.to_numpy()
        buy=np.zeros(len(e),bool); sell=np.zeros(len(e),bool)
        buy[1:] = (rsi[:-1] > 30) & (rsi[1:] <= 30) & (close[1:] > ema200[1:])
        sell = (rsi >= 55) | (close < ema200)
    elif name == 'BTC_GATED_ETH_TREND':
        be=btc.ema50.to_numpy(); ee=e.ema50.to_numpy(); mom=e.mom72.to_numpy(); bc=btc.close.to_numpy()
        buy=(close > ee) & (bc > be) & (mom > 0)
        sell=(close < ee) | (bc < be) | (mom <= 0)
    elif name == 'ETH_TSMOM_168H':
        mom=e.mom168.to_numpy(); e100=e.ema100.to_numpy()
        buy=(mom > 0) & (close > e100)
        sell=(mom <= 0) | (close < e100)
    else:
        raise ValueError(name)
    return buy, sell

STRATEGIES = ['EMA12_26_RSI','EMA24_96_CROSS','DONCHIAN_24_12','RSI_MEANREV_EMA200','BTC_GATED_ETH_TREND','ETH_TSMOM_168H','BTC_ETH_RS_ROTATION_168H']

# Single-asset long-only simulator. Signal at bar close; fill at next bar open.
def sim_one(symbol, strategy, scope_start, scope_end, cost):
    e=features['ETHUSDT']; b=features['BTCUSDT']
    buy_signals, sell_signals = one_asset_signals(strategy,e,b)
    f=frames[symbol]
    fee=cost['fee']; adverse=(cost['slippage_bps']+cost['spread_bps']/2)/10000.0
    cash=INITIAL; qty=0.0; pending=None; buys=[]; fills=[]; curve=[]; peak=INITIAL; maxdd=0.0
    for i in range(scope_start,scope_end):
        row=f.iloc[i]
        if pending == 'BUY' and qty <= 0:
            now=row.timestamp
            buys=[t for t in buys if (now-t).total_seconds() <= BARS_30D*3600]
            if len(buys) < MAX_ENTRIES_30D and cash > 0:
                px=float(row.open)*(1+adverse)
                spend=cash*FRACTION
                q=spend/(px*(1+fee))
                rules=effective_filters(symbol)
                if rules['step']:
                    q=math.floor(q/rules['step'] + 1e-10)*rules['step']
                notional=q*px
                if (q>0 and q>=rules['min_qty'] and q<=rules['max_qty'] and notional>=rules['min_notional']):
                    charge=notional*fee
                    cash-=notional+charge; qty=q; buys.append(now)
                    fills.append({'ts':now.isoformat(),'side':'BUY','price':px,'fee':charge})
        elif pending == 'SELL' and qty > 0:
            now=row.timestamp; px=float(row.open)*(1-adverse)
            proceeds=qty*px; charge=proceeds*fee; cash+=proceeds-charge
            fills.append({'ts':now.isoformat(),'side':'SELL','price':px,'fee':charge}); qty=0.0
        pending=None
        equity=cash+qty*float(row.close)
        peak=max(peak,equity); maxdd=min(maxdd,equity/peak-1 if peak else 0)
        curve.append(equity)
        if i < scope_end-1:
            if qty <= 0 and buy_signals[i]: pending='BUY'
            elif qty>0 and sell_signals[i]: pending='SELL'
    if qty>0:
        row=f.iloc[scope_end-1]; px=float(row.close)*(1-adverse); proceeds=qty*px; charge=proceeds*fee
        cash+=proceeds-charge
        fills.append({'ts':row.timestamp.isoformat(),'side':'SELL','price':px,'fee':charge,'terminal':True})
        qty=0.0
        peak=max(peak,curve[-1]); maxdd=min(maxdd,cash/peak-1 if peak else 0)
        curve[-1]=cash
    final=cash+qty*float(f.iloc[scope_end-1].close)
    return {'strategy':strategy,'asset':symbol,'scope':'','start':f.iloc[scope_start].timestamp.isoformat(),
            'end_exclusive':(f.iloc[scope_end-1].timestamp+pd.Timedelta(hours=1)).isoformat(),
            'final_usdt':final,'return_pct':(final/INITIAL-1)*100,'max_drawdown_pct':maxdd*100,
            'fills':len(fills),'buys':sum(x['side']=='BUY' for x in fills),'sells':sum(x['side']=='SELL' for x in fills),
            'fees_usdt':sum(x['fee'] for x in fills),'fills_detail':fills}

# Benchmark uses full capital, with same fee, spread, slippage on entry/terminal exit.
def sim_buy_hold(symbol, scope_start, scope_end, cost):
    f=frames[symbol].iloc[scope_start:scope_end]
    fee=cost['fee']; adverse=(cost['slippage_bps']+cost['spread_bps']/2)/10000.0
    px0=float(f.iloc[0].close)*(1+adverse)
    qty=INITIAL/(px0*(1+fee))
    # Mark-to-liquidation equity through each close; terminal figure exactly includes exit cost.
    close=f.close.to_numpy(float)
    mark=qty*close*(1-adverse)*(1-fee)
    peak=INITIAL; maxdd=0.0
    for x in mark:
        peak=max(peak,x); maxdd=min(maxdd,x/peak-1 if peak else 0)
    final=float(mark[-1])
    return {'strategy':f'BUY_HOLD_{symbol}','asset':symbol,'start':f.iloc[0].timestamp.isoformat(),
            'end_exclusive':(f.iloc[-1].timestamp+pd.Timedelta(hours=1)).isoformat(),
            'final_usdt':final,'return_pct':(final/INITIAL-1)*100,'max_drawdown_pct':maxdd*100,
            'fills':2,'buys':1,'sells':1,'fees_usdt':INITIAL*fee + (qty*float(f.iloc[-1].close)*(1-adverse))*fee}

# Two-asset relative strength rotation. Pick the asset with stronger positive 7-day momentum; otherwise cash.
def desired_rotation(i):
    b=features['BTCUSDT']; e=features['ETHUSDT']
    bm=float(b.mom168.iloc[i]) if pd.notna(b.mom168.iloc[i]) else np.nan
    em=float(e.mom168.iloc[i]) if pd.notna(e.mom168.iloc[i]) else np.nan
    if not np.isfinite(bm) or not np.isfinite(em): return 'CASH'
    candidates=[]
    if bm>0: candidates.append(('BTCUSDT',bm))
    if em>0: candidates.append(('ETHUSDT',em))
    return max(candidates,key=lambda x:x[1])[0] if candidates else 'CASH'

def sim_rotation(scope_start,scope_end,cost):
    fee=cost['fee']; adverse=(cost['slippage_bps']+cost['spread_bps']/2)/10000.0
    cash=INITIAL; asset='CASH'; qty=0.0; pending=None; buy_times=[]; fills=[]; curve=[]; peak=INITIAL; maxdd=0.0
    for i in range(scope_start,scope_end):
        ts=frames['ETHUSDT'].iloc[i].timestamp
        if pending is not None and pending != asset:
            # Sell the current asset at next hourly open before considering a new purchase.
            if asset!='CASH' and qty>0:
                px=float(frames[asset].iloc[i].open)*(1-adverse)
                proceeds=qty*px; charge=proceeds*fee; cash+=proceeds-charge
                fills.append({'ts':ts.isoformat(),'side':'SELL','asset':asset,'price':px,'fee':charge})
                qty=0.0; asset='CASH'
            if pending!='CASH' and asset=='CASH':
                buy_times=[t for t in buy_times if (ts-t).total_seconds() <= BARS_30D*3600]
                if len(buy_times)<MAX_ENTRIES_30D and cash>0:
                    px=float(frames[pending].iloc[i].open)*(1+adverse)
                    spend=cash*FRACTION; q=spend/(px*(1+fee)); rules=effective_filters(pending)
                    if rules['step']:
                        q=math.floor(q/rules['step'] + 1e-10)*rules['step']
                    notional=q*px
                    if q>0 and q>=rules['min_qty'] and q<=rules['max_qty'] and notional>=rules['min_notional']:
                        charge=notional*fee; cash-=notional+charge; qty=q; asset=pending; buy_times.append(ts)
                        fills.append({'ts':ts.isoformat(),'side':'BUY','asset':asset,'price':px,'fee':charge})
        pending=None
        equity=cash
        if asset!='CASH' and qty>0: equity+=qty*float(frames[asset].iloc[i].close)
        peak=max(peak,equity); maxdd=min(maxdd,equity/peak-1 if peak else 0); curve.append(equity)
        if i<scope_end-1:
            desired=desired_rotation(i)
            if desired!=asset: pending=desired
    if asset!='CASH' and qty>0:
        row=frames[asset].iloc[scope_end-1]; px=float(row.close)*(1-adverse); proceeds=qty*px; charge=proceeds*fee; cash+=proceeds-charge
        fills.append({'ts':row.timestamp.isoformat(),'side':'SELL','asset':asset,'price':px,'fee':charge,'terminal':True})
        qty=0.0; asset='CASH'; peak=max(peak,curve[-1]); maxdd=min(maxdd,cash/peak-1 if peak else 0); curve[-1]=cash
    final=cash
    return {'strategy':'BTC_ETH_RS_ROTATION_168H','asset':'BTCUSDT|ETHUSDT','start':frames['ETHUSDT'].iloc[scope_start].timestamp.isoformat(),
            'end_exclusive':(frames['ETHUSDT'].iloc[scope_end-1].timestamp+pd.Timedelta(hours=1)).isoformat(),
            'final_usdt':final,'return_pct':(final/INITIAL-1)*100,'max_drawdown_pct':maxdd*100,
            'fills':len(fills),'buys':sum(x['side']=='BUY' for x in fills),'sells':sum(x['side']=='SELL' for x in fills),
            'fees_usdt':sum(x['fee'] for x in fills),'fills_detail':fills}

# Anti-lookahead: alter all bars strictly after each cut and ensure decisions through cut are unchanged.
def _rotation_target_array(ff):
    bm=ff['BTCUSDT'].mom168.to_numpy(float)
    em=ff['ETHUSDT'].mom168.to_numpy(float)
    bm_ok=np.where(np.isfinite(bm) & (bm>0),bm,-np.inf)
    em_ok=np.where(np.isfinite(em) & (em>0),em,-np.inf)
    best=np.maximum(bm_ok,em_ok)
    targets=np.full(len(best),'CASH',dtype=object)
    valid=best>0
    targets[valid & (bm_ok>=em_ok)]='BTCUSDT'
    targets[valid & (em_ok>bm_ok)]='ETHUSDT'
    return targets

def anti_lookahead_check():
    cutoffs=[WARMUP+100, WARMUP+VAL_LEN-1, WARMUP+2*VAL_LEN+333, eval_start+100, eval_start+EVAL_LEN//2, eval_end-2]
    orig_rotation=_rotation_target_array(features)
    for cutoff in cutoffs:
        altered={k:v.copy() for k,v in frames.items()}
        for sym in ('BTCUSDT','ETHUSDT'):
            g=altered[sym].copy()
            if cutoff+1 < len(g):
                mult=1.37 if sym=='ETHUSDT' else 0.73
                for c in ('open','high','low','close'):
                    g.loc[cutoff+1:,c]=g.loc[cutoff+1:,c]*mult
            altered[sym]=g
        changed_feat={s:feature_frame(altered[s]) for s in altered}
        for name in STRATEGIES[:-1]:
            old=one_asset_signals(name,features['ETHUSDT'],features['BTCUSDT'])
            changed=one_asset_signals(name,changed_feat['ETHUSDT'],changed_feat['BTCUSDT'])
            if any(not np.array_equal(a[:cutoff+1],b[:cutoff+1], equal_nan=True) for a,b in zip(old,changed)):
                raise AssertionError(f'lookahead violation: {name} changed before cutoff {cutoff}')
        changed_rotation=_rotation_target_array(changed_feat)
        if not np.array_equal(orig_rotation[:cutoff+1],changed_rotation[:cutoff+1]):
            raise AssertionError(f'lookahead violation: rotation changed before cutoff {cutoff}')
    return {'status':'PASS','suffix_perturbations':len(cutoffs),'strategies_checked':len(STRATEGIES),'cutoffs':cutoffs,'tested_candle_scope':'every decision at or before each cutoff; both assets OHLC suffix perturbed'}

# Use feature-based signals from global causal history but reset account state for each fold.
anti=anti_lookahead_check()
# Calibration window from the prior canonical report; not used to choose strategy parameters.
ref_start=WARMUP; ref_end=WARMUP+EVAL_LEN
ref_base=sim_one('ETHUSDT','EMA12_26_RSI',ref_start,ref_end,COSTS['BASE'])
ref_bh=sim_buy_hold('ETHUSDT',ref_start,ref_end,COSTS['BASE'])
results=[]
scopes=[*folds,('EVALUATION',eval_start,eval_end)]
for scope_name,st,en in scopes:
    for cost_name,cost in COSTS.items():
        # ETH one-asset candidates
        for strategy in STRATEGIES[:-1]:
            r=sim_one('ETHUSDT',strategy,st,en,cost); r['scope']=scope_name; r['cost']=cost_name
            results.append(r)
        # rotation
        r=sim_rotation(st,en,cost); r['scope']=scope_name; r['cost']=cost_name; results.append(r)
        # same-cost benchmarks: cash and full-capital buy & hold BTC, ETH
        results.append({'strategy':'CASH','asset':'USDT','scope':scope_name,'cost':cost_name,
                        'start':frames['ETHUSDT'].iloc[st].timestamp.isoformat(),
                        'end_exclusive':(frames['ETHUSDT'].iloc[en-1].timestamp+pd.Timedelta(hours=1)).isoformat(),
                        'final_usdt':INITIAL,'return_pct':0.0,'max_drawdown_pct':0.0,'fills':0,'buys':0,'sells':0,'fees_usdt':0.0})
        for sym in ('BTCUSDT','ETHUSDT'):
            r=sim_buy_hold(sym,st,en,cost); r['scope']=scope_name; r['cost']=cost_name; results.append(r)

# Summaries without verbose fills, preserving all metrics.
summary=[]
for r in results:
    summary.append({k:r[k] for k in ('scope','cost','strategy','asset','start','end_exclusive','final_usdt','return_pct','max_drawdown_pct','fills','buys','sells','fees_usdt')})
# checks: validation all before evaluation; no strategy gets evaluation bars in any validation slice.
assert max(end for _,_,end in folds) == eval_start
assert all('2026-04' not in r['start'] for r in results if r['scope'].startswith('VALIDATION'))

out={
 'schema_version':1,
 'research_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'runtime':{'python':__import__('platform').python_version(),'numpy':np.__version__,'pandas':pd.__version__},
 'reference_calibration':{'scope':'prior canonical evaluation window; calibration only, not the new holdout','start':frames['ETHUSDT'].iloc[ref_start].timestamp.isoformat(),'end_exclusive':(frames['ETHUSDT'].iloc[ref_end-1].timestamp+pd.Timedelta(hours=1)).isoformat(),'EMA12_26_RSI_BASE_return_pct':ref_base['return_pct'],'EMA12_26_RSI_BASE_fills':ref_base['fills'],'ETH_buy_hold_BASE_return_pct':ref_bh['return_pct']},
 'source_artifact':'btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e',
 'source_workflow_run':39062915950,
 'dataset_sha256':bundle['dataset_sha256'],
 'configuration_sha256':bundle['configuration_sha256'],
 'acquired_at':bundle['acquired_at'],
 'bars_per_symbol':len(frames['BTCUSDT']),
 'data_start':frames['BTCUSDT'].iloc[0].timestamp.isoformat(),
 'data_end_inclusive':frames['BTCUSDT'].iloc[-1].timestamp.isoformat(),
 'available_at_unknown_bars':sum(x['available_at'] is None for x in bundle['data']['history']['BTCUSDT']),
 'finality_unknown_bars':sum(x['candle']['finality']=='UNKNOWN' for x in bundle['data']['history']['BTCUSDT']),
 'split':{
  'warmup_bars':WARMUP,'warmup_start':frames['ETHUSDT'].iloc[0].timestamp.isoformat(),'validation_folds':[
   {'scope':name,'start_index':st,'end_index_exclusive':en,'start':frames['ETHUSDT'].iloc[st].timestamp.isoformat(),'end_exclusive':(frames['ETHUSDT'].iloc[en-1].timestamp+pd.Timedelta(hours=1)).isoformat(),'bars':en-st} for name,st,en in folds],
  'evaluation':{'start_index':eval_start,'end_index_exclusive':eval_end,'start':frames['ETHUSDT'].iloc[eval_start].timestamp.isoformat(),'end_exclusive':(frames['ETHUSDT'].iloc[eval_end-1].timestamp+pd.Timedelta(hours=1)).isoformat(),'bars':eval_end-eval_start},
  'unused_trailing_bars':len(frames['ETHUSDT'])-eval_end,
 },
 'costs':COSTS,
 'execution_contract':{'initial_usdt':INITIAL,'position_fraction':FRACTION,'max_entries_per_rolling_30d':MAX_ENTRIES_30D,'bar_interval':'1h','signal_time':'closed candle t','fill_time':'next candle open','terminal_liquidation':'last evaluation close after spread/slippage and fee','leverage':'none','shorting':'none','exchange_filter_note':'effective lot/min-notional rules use the captured current snapshot; historical rule versions are unavailable, so this is a reproducibility guardrail not point-in-time market-rule evidence'},
 'anti_lookahead':anti,
 'strategies':{
  'EMA12_26_RSI':'ETH long-only; enter EMA12 crosses above EMA26 and RSI14>=50; exit EMA12 crosses below EMA26 or RSI14>=70',
  'EMA24_96_CROSS':'ETH long-only; EMA24/EMA96 crossover',
  'DONCHIAN_24_12':'ETH long-only; enter close exceeds max high of prior 24 candles; exit close is below min low of prior 12 candles',
  'RSI_MEANREV_EMA200':'ETH long-only; enter RSI14 crosses down to <=30 while close>EMA200; exit RSI14>=55 or close<EMA200',
  'BTC_GATED_ETH_TREND':'ETH long-only; enter close>ETH EMA50, BTC close>BTC EMA50, and ETH 72h momentum>0; exit when any gate fails',
  'ETH_TSMOM_168H':'ETH long-only; enter 168h momentum>0 and close>EMA100; exit when either condition fails',
  'BTC_ETH_RS_ROTATION_168H':'one long asset at a time; choose highest of BTC/ETH 168h momentum if positive, otherwise cash; target decided at close and switched at next open',
 },
 'metrics':summary,
}
OUT_PATH.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print('DATASET',out['dataset_sha256'])
print('CONFIG',out['configuration_sha256'])
print('SPLIT',json.dumps(out['split'],indent=2))
print('ANTI_LOOKAHEAD',anti)
for scope in ('VALIDATION_1','VALIDATION_2','VALIDATION_3','EVALUATION'):
 print('\n###',scope)
 for cost_name in COSTS:
  vals=[r for r in summary if r['scope']==scope and r['cost']==cost_name]
  print(cost_name)
  for r in sorted(vals,key=lambda x:x['return_pct'],reverse=True):
   print(f"{r['strategy']:<28} ret={r['return_pct']:>9.4f}% dd={r['max_drawdown_pct']:>9.4f}% fills={r['fills']:>3} final={r['final_usdt']:>9.4f}")
print('\nREFERENCE_CALIBRATION prior_window=2025-04-15..2026-04-15')
print('baseline_return_pct',ref_base['return_pct'],'fills',ref_base['fills'],'buy_hold_ETH_return_pct',ref_bh['return_pct'])
print('\nRESULT_FILE',OUT_PATH)
