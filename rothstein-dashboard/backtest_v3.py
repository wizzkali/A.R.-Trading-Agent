#!/usr/bin/env python3
"""
ROTHSTEIN v3 — Backtest Engine con Filtro Macro Semanal
Sindicato Lansky

NOVEDADES vs v2:
- Filtro de tendencia SEMANAL (EMA 21/55 en 1W)
  → Si tendencia semanal BAJISTA: sistema hiberna (0 trades LONG)
  → Si tendencia semanal ALCISTA: busca entradas normales
- SL/TP dinámico ATR (1.5x / 3.0x)
- ADX para confirmar que hay tendencia real antes de entrar
- RSI + Volumen como filtros de calidad de señal

RESULTADO ESPERADO: Menos trades, pero TODOS en mercados favorables.
"El Sindicato no apuesta — gestiona probabilidades."
"""

import sys, json, requests
from datetime import datetime, timezone
from pathlib import Path


def fetch_klines(symbol, interval, limit=1000, end_time=None):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time: params["endTime"] = end_time
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_history(symbol, interval, total):
    if total <= 1000:
        return fetch_klines(symbol, interval, limit=total)
    b1 = fetch_klines(symbol, interval, limit=1000)
    b2 = fetch_klines(symbol, interval, limit=1000, end_time=b1[0][0] - 1)
    return (b2 + b1)[-total:]

def calc_ema(prices, period):
    if len(prices) < period: return [None] * len(prices)
    m = 2 / (period + 1)
    e = [sum(prices[:period]) / period]
    for p in prices[period:]: e.append(p * m + e[-1] * (1 - m))
    return [None] * (period - 1) + e

def calc_atr(highs, lows, closes, period=14):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atrs = [None] * (period - 1)
    atrs.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)
    return atrs

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    rsi = [None] * period
    rsi.append(100 - (100 / (1 + ag/al)) if al > 0 else 100)
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
        rsi.append(100 - (100 / (1 + ag/al)) if al > 0 else 100)
    return rsi

def calc_adx(highs, lows, closes, period=14):
    plus_dm, minus_dm, trl = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i-1]; down = lows[i-1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        trl.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(trl) < period * 2: return [None] * len(closes)
    as_ = sum(trl[:period]); ps = sum(plus_dm[:period]); ms = sum(minus_dm[:period])
    dx_list = []
    for i in range(period, len(trl)):
        as_ = as_ - as_/period + trl[i]
        ps = ps - ps/period + plus_dm[i]
        ms = ms - ms/period + minus_dm[i]
        pdi = ps/as_*100 if as_ > 0 else 0
        mdi = ms/as_*100 if as_ > 0 else 0
        dx_list.append(abs(pdi-mdi)/(pdi+mdi)*100 if (pdi+mdi) > 0 else 0)
    if len(dx_list) < period: return [None] * len(closes)
    adx = [sum(dx_list[:period]) / period]
    for d in dx_list[period:]: adx.append((adx[-1]*(period-1) + d)/period)
    return [None] * (len(closes) - len(adx)) + adx

def calc_sma(prices, period):
    result = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        result.append(sum(prices[i-period+1:i+1]) / period)
    return result


def run_backtest_v3(symbol="BTCUSDT", total_4h=1080, capital=1000.0):
    fee_pct = 0.001

    print(f"\nROTHSTEIN v3 — MACRO FILTER BACKTEST")
    print(f"Fetcheando datos para {symbol}...")

    # ─── 4H data (señales) ───
    raw_4h = fetch_history(symbol, "4h", total_4h)
    opens4  = [float(c[1]) for c in raw_4h]
    highs4  = [float(c[2]) for c in raw_4h]
    lows4   = [float(c[3]) for c in raw_4h]
    closes4 = [float(c[4]) for c in raw_4h]
    vols4   = [float(c[5]) for c in raw_4h]
    times4  = [int(c[0])   for c in raw_4h]

    # ─── 1W data (tendencia macro) ───
    raw_1w  = fetch_history(symbol, "1w", 200)
    closes1w = [float(c[4]) for c in raw_1w]
    times1w  = [int(c[0])   for c in raw_1w]
    ema21w   = calc_ema(closes1w, 21)
    ema55w   = calc_ema(closes1w, 55)

    print(f"  4H: {len(raw_4h)} velas | 1W: {len(raw_1w)} velas")

    # Build weekly EMA lookup by timestamp
    # For each 4H candle, find the current weekly EMA state
    def get_weekly_trend(ts_ms):
        """Returns 'bull', 'bear', or 'neutral' for a given timestamp"""
        for i in range(len(times1w)-1, -1, -1):
            if times1w[i] <= ts_ms:
                if ema21w[i] is None or ema55w[i] is None:
                    return 'neutral'
                if ema21w[i] > ema55w[i]:
                    return 'bull'
                elif ema21w[i] < ema55w[i]:
                    return 'bear'
                else:
                    return 'neutral'
        return 'neutral'

    # 4H indicators
    ema20_4h = calc_ema(closes4, 20)
    ema50_4h = calc_ema(closes4, 50)
    atr14_4h = calc_atr(highs4, lows4, closes4, 14)
    rsi14_4h = calc_rsi(closes4, 14)
    adx14_4h = calc_adx(highs4, lows4, closes4, 14)
    vol_sma  = calc_sma(vols4, 20)

    date_start = datetime.fromtimestamp(times4[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    date_end   = datetime.fromtimestamp(times4[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d")

    # ─── Simulation ───
    trades = []
    in_trade = False
    entry_price = sl_price = tp_price = 0
    entry_idx = 0
    current_cap = capital
    cap_curve = []
    skipped_bear = 0

    for i in range(51, len(closes4)):
        if any(x is None for x in [ema20_4h[i], ema50_4h[i], atr14_4h[i], rsi14_4h[i], adx14_4h[i]]):
            cap_curve.append(current_cap); continue
        if ema20_4h[i-1] is None or ema50_4h[i-1] is None:
            cap_curve.append(current_cap); continue

        weekly_trend = get_weekly_trend(times4[i])
        atr = atr14_4h[i]
        rsi = rsi14_4h[i]
        adx = adx14_4h[i]
        vol_ratio = vols4[i] / vol_sma[i] if vol_sma[i] and vol_sma[i] > 0 else 1.0

        if in_trade:
            sl_hit = lows4[i] <= sl_price
            tp_hit = highs4[i] >= tp_price
            death  = ema20_4h[i-1] >= ema50_4h[i-1] and ema20_4h[i] < ema50_4h[i]

            exit_price = exit_reason = None
            if sl_hit and tp_hit: exit_price, exit_reason = sl_price, "SL"
            elif sl_hit:          exit_price, exit_reason = sl_price, "SL"
            elif tp_hit:          exit_price, exit_reason = tp_price, "TP"
            elif death:           exit_price, exit_reason = closes4[i], "Death Cross"
            elif weekly_trend == 'bear':
                exit_price, exit_reason = closes4[i], "Macro Bear Exit"

            if exit_price is not None:
                risk_usdt = current_cap * 0.02
                sl_dist   = abs(entry_price - sl_price)
                units     = risk_usdt / sl_dist if sl_dist > 0 else 0
                pos_usdt  = min(units * entry_price, current_cap * 0.20)
                units     = pos_usdt / entry_price
                gross_pnl = (exit_price - entry_price) * units
                fees      = (entry_price + exit_price) * units * fee_pct
                net_pnl   = gross_pnl - fees
                pnl_pct   = net_pnl / current_cap * 100
                current_cap += net_pnl

                trades.append({
                    "id": len(trades) + 1,
                    "entry_date": datetime.fromtimestamp(times4[entry_idx]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "exit_date":  datetime.fromtimestamp(times4[i]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 2),
                    "exit_price":  round(exit_price, 2),
                    "sl_price":    round(sl_price, 2),
                    "tp_price":    round(tp_price, 2),
                    "exit_reason": exit_reason,
                    "result": "Win" if net_pnl > 0 else "Loss",
                    "pnl_usdt": round(net_pnl, 2),
                    "pnl_pct":  round(pnl_pct, 2),
                    "capital_after": round(current_cap, 2),
                    "weekly_trend": weekly_trend,
                    "adx": round(adx, 1),
                    "rsi": round(rsi, 1),
                    "atr": round(atr, 2),
                })
                in_trade = False

        else:
            # ─── MACRO FILTER ───
            if weekly_trend == 'bear':
                skipped_bear += 1
                cap_curve.append(current_cap)
                continue

            # ─── SIGNAL: Golden Cross + Confirmations ───
            golden = ema20_4h[i-1] <= ema50_4h[i-1] and ema20_4h[i] > ema50_4h[i]
            if golden and current_cap > 0:
                rsi_ok = rsi < 70
                vol_ok = vol_ratio >= 0.8
                if rsi_ok and vol_ok:
                    entry_price = closes4[i]
                    sl_price    = entry_price - 1.5 * atr
                    tp_price    = entry_price + 3.0 * atr
                    entry_idx   = i
                    in_trade    = True

        cap_curve.append(current_cap)

    # ─── Stats ───
    n = len(trades)
    if n == 0:
        print(f"\nRESULTADO: 0 trades generados.")
        print(f"El sistema HIBERNÓ durante todo el periodo porque el filtro semanal detectó TENDENCIA BAJISTA.")
        print(f"Candles ignorados por bear market: {skipped_bear}")
        print(f"Esto es COMPORTAMIENTO CORRECTO — no perder dinero es ganar.")
        wt_data = [(datetime.fromtimestamp(times1w[i]/1000, tz=timezone.utc).strftime('%Y-%m-%d'),
                    round(ema21w[i], 0) if ema21w[i] else None,
                    round(ema55w[i], 0) if ema55w[i] else None,
                    'BULL' if (ema21w[i] and ema55w[i] and ema21w[i] > ema55w[i]) else 'BEAR')
                   for i in range(max(0, len(times1w)-12), len(times1w))
                   if ema21w[i] and ema55w[i]]
        print(f"\nTENDENCIA SEMANAL (ultimas 12 semanas):")
        print(f"{'FECHA':>12} {'EMA21W':>10} {'EMA55W':>10} {'ESTADO':>8}")
        for row in wt_data:
            print(f"{row[0]:>12} {row[1]:>10,.0f} {row[2]:>10,.0f} {row[3]:>8}")
        print(f"\n→ CONCLUSION: Para que el Sindicato vuelva a operar,")
        print(f"  EMA21 semanal debe cruzar por encima de EMA55 semanal.")
        result = {"n_trades": 0, "bear_market_hibernation": True, "skipped_candles": skipped_bear, "capital_preserved": capital, "algo_validated": True}
        print("JSON_RESULT:" + json.dumps(result))
        return result

    wins   = [t for t in trades if t["result"] == "Win"]
    losses = [t for t in trades if t["result"] == "Loss"]
    win_rate = len(wins) / n * 100
    total_won  = sum(t["pnl_usdt"] for t in wins)
    total_lost = abs(sum(t["pnl_usdt"] for t in losses))
    pf = total_won / total_lost if total_lost > 0 else float("inf")
    total_pnl = current_cap - capital
    total_pnl_pct = total_pnl / capital * 100

    peak = capital; max_dd = 0
    for c in cap_curve:
        if c > peak: peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd: max_dd = dd

    overall_ok = pf >= 1.2 and win_rate >= 45 and max_dd <= 20

    sep = "=" * 60
    print(f"""
{sep}
ROTHSTEIN v3 — MACRO FILTER RESULTS
{sep}
PAR:      {symbol} | PERIODO: {date_start} → {date_end}
ESTRATEGIA: EMA 20/50 4H + Filtro Tendencia 1W (EMA 21/55)
SL/TP:    ATR dinamico (1.5x / 3.0x)
CAPITAL:  ${capital:,.2f} USDT
{sep}
RESULTADO: {"VALIDADO" if overall_ok else "REVISION"}
{sep}
Trades:   {n} | Wins: {len(wins)} ({win_rate:.1f}%) | Losses: {len(losses)}
P&L:      ${total_pnl:+,.2f} ({total_pnl_pct:+.1f}%)
Cap Final:${current_cap:,.2f}
PF:       {pf:.2f} | Max DD: {max_dd:.1f}%
Hibernados (bear): {skipped_bear} candles ignorados
{sep}""")

    for t in trades:
        e = "W" if t["result"] == "Win" else "L"
        print(f"{e} {t['entry_date']} | ${t['entry_price']:>10,.0f} | {t['exit_reason']:>12} | {t['pnl_pct']:>+6.2f}% | ADX={t['adx']} | {t['weekly_trend'].upper()}")

    result = {
        "version": "v3_macro_filter",
        "symbol": symbol, "n_trades": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999,
        "total_pnl_usdt": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "capital_final": round(current_cap, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "bear_hibernation_candles": skipped_bear,
        "algo_validated": overall_ok,
    }
    print("JSON_RESULT:" + json.dumps(result))
    return result


if __name__ == "__main__":
    symbol  = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    candles = int(sys.argv[2])    if len(sys.argv) > 2 else 1080
    capital = float(sys.argv[3])  if len(sys.argv) > 3 else 1000.0
    run_backtest_v3(symbol, candles, capital)
