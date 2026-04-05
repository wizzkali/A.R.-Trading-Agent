#!/usr/bin/env python3
"""
ROTHSTEIN — Multi-Signal Combiner v4
Sindicato Lansky | Upgrade de v3 con 9 indicadores + consenso de votacion

MEJORAS v3 → v4:
  - Usa technical_lib.py (sin duplicacion de codigo)
  - 9 fuentes vs 6: +MACD +Bollinger +OBV
  - Sistema de consenso: cada indicador VOTA (BULL/BEAR/NEUTRAL)
  - Pesos dinamicos: se ajustan segun volatilidad y fiabilidad
  - Scoring dual: weighted score + consensus voting
  - Bollinger squeeze detection (baja vol → explosion inminente)
  - MACD crossover bonus (confirma/contradice EMA cross)
  - OBV divergence penalty (volumen no confirma → reduce confianza)

SCORING FINAL = 60% weighted_score + 40% consensus_score
  → Mas robusto que v3 (solo weighted)
"""

import json
import sys
from datetime import datetime, timezone
from typing import Dict

# Importar libreria centralizada
from technical_lib import (
    full_technical_analysis, indicator_consensus,
    fetch_fear_greed, fetch_binance_klines,
    calc_ema, calc_atr
)


def analyze_v4(pair: str = "BTCUSDT", ema_fast: int = 15, ema_slow: int = 40) -> Dict:
    """Analisis compuesto v4: 9 indicadores + consenso"""

    result = {
        "version": "v4",
        "pair": pair,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {},
        "weights": {},
        "consensus": {},
        "composite_score": 50,
        "verdict": "NEUTRAL",
        "confidence": 0,
        "rationale": [],
        "improvements_over_v3": [
            "+MACD crossover confirmation",
            "+Bollinger Bands squeeze/position",
            "+OBV divergence detection",
            "+Consensus voting system",
            "+Dynamic weight adjustment",
            "+Dual scoring (weighted + consensus)"
        ]
    }

    # ══════════════════════════════════════
    # FETCH ALL DATA VIA TECHNICAL_LIB
    # ══════════════════════════════════════
    analysis = full_technical_analysis(pair, ema_fast, ema_slow)
    if not analysis.get("ok"):
        result["error"] = analysis.get("error", "Data fetch failed")
        return result

    price = analysis["price"]

    # ══════════════════════════════════════
    # 1. EMA dinamica — peso base 28%
    # ══════════════════════════════════════
    ema_score = 50.0
    ema_fast_val = analysis["ema_fast"]
    ema_slow_val = analysis["ema_slow"]

    if ema_fast_val and ema_slow_val:
        bull = ema_fast_val > ema_slow_val
        above_both = price > ema_fast_val and price > ema_slow_val

        if bull and above_both:
            ema_score = 80
            result["rationale"].append("EMA_FAST>EMA_SLOW + precio arriba = BULL fuerte")
        elif bull:
            ema_score = 65
            result["rationale"].append("EMA_FAST>EMA_SLOW pero precio entre EMAs = BULL debil")
        elif not bull and price < ema_fast_val and price < ema_slow_val:
            ema_score = 20
            result["rationale"].append("EMA_FAST<EMA_SLOW + precio abajo = BEAR fuerte")
        elif not bull:
            ema_score = 35
            result["rationale"].append("EMA_FAST<EMA_SLOW = BEAR")

        # Cross bonus
        cross = analysis.get("ema_cross", "NONE")
        if cross == "GOLDEN_CROSS":
            ema_score += 12
            result["rationale"].append("GOLDEN CROSS reciente (+12)")
        elif cross == "DEATH_CROSS":
            ema_score -= 12
            result["rationale"].append("DEATH CROSS reciente (-12)")

    ema_score = max(0, min(100, ema_score))
    result["components"]["ema"] = round(ema_score, 1)

    # ══════════════════════════════════════
    # 2. RSI — peso 12%
    # ══════════════════════════════════════
    rsi_1h = analysis["rsi_1h"]
    rsi_4h = analysis["rsi_4h"]

    rsi_score = 50.0
    if rsi_1h < 25:
        rsi_score = 85
        result["rationale"].append(f"RSI 1H {rsi_1h} SOBREVENTA EXTREMA")
    elif rsi_1h < 35:
        rsi_score = 70
        result["rationale"].append(f"RSI 1H {rsi_1h} sobreventa")
    elif rsi_1h > 75:
        rsi_score = 15
        result["rationale"].append(f"RSI 1H {rsi_1h} SOBRECOMPRA EXTREMA")
    elif rsi_1h > 65:
        rsi_score = 30
        result["rationale"].append(f"RSI 1H {rsi_1h} sobrecompra")
    else:
        rsi_score = 50

    # RSI divergence bonus
    if rsi_1h < 35 and ema_score >= 60:
        rsi_score += 10
        result["rationale"].append("RSI divergencia alcista en tendencia bull (+10)")

    rsi_score = max(0, min(100, rsi_score))
    result["components"]["rsi"] = round(rsi_score, 1)

    # ══════════════════════════════════════
    # 3. MACD — peso 12% (NUEVO en v4)
    # ══════════════════════════════════════
    macd = analysis["macd"]
    macd_score = 50.0

    if macd.get("ok"):
        if macd["trend"] == "BULL":
            macd_score = 65
            result["rationale"].append(f"MACD bullish (line={macd['macd']:.1f})")
        else:
            macd_score = 35
            result["rationale"].append(f"MACD bearish (line={macd['macd']:.1f})")

        # Crossover bonus
        if macd["crossover"] == "BULLISH":
            macd_score += 15
            result["rationale"].append("MACD BULLISH crossover (+15)")
        elif macd["crossover"] == "BEARISH":
            macd_score -= 15
            result["rationale"].append("MACD BEARISH crossover (-15)")

        # Momentum bonus
        if macd["momentum"] == "INCREASING" and macd["trend"] == "BULL":
            macd_score += 5
        elif macd["momentum"] == "DECREASING" and macd["trend"] == "BEAR":
            macd_score -= 5

        # MACD + EMA agreement bonus
        if (macd["trend"] == "BULL" and ema_score > 60) or (macd["trend"] == "BEAR" and ema_score < 40):
            macd_score += 5
            result["rationale"].append("MACD confirma tendencia EMA (+5)")
    else:
        result["rationale"].append("MACD datos insuficientes")

    macd_score = max(0, min(100, macd_score))
    result["components"]["macd"] = round(macd_score, 1)

    # ══════════════════════════════════════
    # 4. Bollinger Bands — peso 8% (NUEVO en v4)
    # ══════════════════════════════════════
    bb = analysis["bollinger"]
    bb_score = 50.0

    if bb.get("ok"):
        pct_b = bb["pct_b"]
        squeeze = bb["squeeze"]

        if bb["position"] == "BELOW_LOWER":
            bb_score = 80  # precio bajo banda inferior → oversold → compra
            result["rationale"].append(f"Bollinger: precio BAJO banda inferior (%B={pct_b:.2f})")
        elif bb["position"] == "ABOVE_UPPER":
            bb_score = 20  # precio sobre banda superior → overbought → venta
            result["rationale"].append(f"Bollinger: precio SOBRE banda superior (%B={pct_b:.2f})")
        elif pct_b < 0.3:
            bb_score = 65  # cerca de banda inferior
        elif pct_b > 0.7:
            bb_score = 35  # cerca de banda superior
        else:
            bb_score = 50

        if squeeze:
            result["rationale"].append(f"BOLLINGER SQUEEZE detectado (BW={bb['bandwidth']:.1f}%) — explosion de volatilidad inminente")
            # En squeeze, el score se acerca a neutral pero la confianza general sube
            bb_score = 50 + (bb_score - 50) * 0.5
    else:
        result["rationale"].append("Bollinger datos insuficientes")

    bb_score = max(0, min(100, bb_score))
    result["components"]["bollinger"] = round(bb_score, 1)

    # ══════════════════════════════════════
    # 5. OBV — peso 8% (NUEVO en v4)
    # ══════════════════════════════════════
    obv = analysis["obv"]
    obv_score = 50.0

    if obv.get("ok"):
        if obv["divergence"] == "BULLISH":
            obv_score = 75
            result["rationale"].append("OBV divergencia ALCISTA: volumen entra mientras precio baja → acumulacion")
        elif obv["divergence"] == "BEARISH":
            obv_score = 25
            result["rationale"].append("OBV divergencia BAJISTA: volumen sale mientras precio sube → distribucion")
        elif obv["divergence"] == "CONFIRMED":
            # Amplifica la tendencia
            obv_score = 50 + (ema_score - 50) * 0.6
            result["rationale"].append(f"OBV CONFIRMA tendencia ({obv['obv_trend']})")
        else:
            obv_score = 50
    else:
        result["rationale"].append("OBV datos insuficientes")

    obv_score = max(0, min(100, obv_score))
    result["components"]["obv"] = round(obv_score, 1)

    # ══════════════════════════════════════
    # 6. Fear & Greed — peso 12% (contrarian)
    # ══════════════════════════════════════
    fng = analysis["fear_greed"]
    fng_val = fng["value"]

    fng_score = 50.0
    if fng_val <= 10:
        fng_score = 90
        result["rationale"].append(f"F&G {fng_val} EXTREME FEAR → contrarian STRONG BUY")
    elif fng_val <= 25:
        fng_score = 75
        result["rationale"].append(f"F&G {fng_val} Fear → contrarian bullish")
    elif fng_val <= 45:
        fng_score = 60
    elif fng_val <= 55:
        fng_score = 50
    elif fng_val <= 75:
        fng_score = 35
        result["rationale"].append(f"F&G {fng_val} Greed → contrarian bearish")
    else:
        fng_score = 15
        result["rationale"].append(f"F&G {fng_val} EXTREME GREED → contrarian STRONG SELL")

    result["components"]["fear_greed"] = round(fng_score, 1)
    result["components"]["fear_greed_raw"] = fng_val

    # ══════════════════════════════════════
    # 7. Polymarket Divergence — peso 10%
    # ══════════════════════════════════════
    poly_score = 50.0
    poly_available = True
    try:
        from polymarket_signal import scan_divergences
        asset = "btc" if "BTC" in pair else "eth" if "ETH" in pair else "sol"
        div_data = scan_divergences(asset)
        if div_data.get("best_signal"):
            best = div_data["best_signal"]
            if best["signal"] == "BUY_YES":
                poly_score = 50 + min(40, best["divergence"] * 1.5)
                result["rationale"].append(f"Polymarket BUY_YES divergencia {best['divergence']}%")
            elif best["signal"] == "BUY_NO":
                poly_score = 50 - min(40, best["divergence"] * 1.5)
                result["rationale"].append(f"Polymarket BUY_NO divergencia {best['divergence']}%")
    except Exception:
        poly_available = False
        result["rationale"].append("Polymarket no disponible — peso redistribuido")

    poly_score = max(0, min(100, poly_score))
    result["components"]["polymarket"] = round(poly_score, 1)

    # ══════════════════════════════════════
    # 8. Volumen — peso 6%
    # ══════════════════════════════════════
    vol_ratio = analysis["volume_ratio"]
    vol_score = 50.0

    if vol_ratio > 1.5:
        vol_score = 50 + (ema_score - 50) * 0.6
        result["rationale"].append(f"Volumen x{vol_ratio:.1f} (alto) — confirma tendencia")
    elif vol_ratio > 1.0:
        vol_score = 50 + (ema_score - 50) * 0.3
    elif vol_ratio < 0.5:
        vol_score = 50
        result["rationale"].append(f"Volumen x{vol_ratio:.1f} (muy bajo) — sin conviccion")
    else:
        vol_score = 50

    vol_score = max(0, min(100, vol_score))
    result["components"]["volume"] = round(vol_score, 1)
    result["components"]["volume_ratio"] = round(vol_ratio, 2)

    # ══════════════════════════════════════
    # 9. Tendencia Semanal — peso 4%
    # ══════════════════════════════════════
    weekly_score = 50.0
    try:
        data_w = fetch_binance_klines(pair, "1w", 80)
        if data_w.get("ok"):
            e21w = calc_ema(data_w["closes"], 21)
            e55w = calc_ema(data_w["closes"], 55)
            if e21w[-1] and e55w[-1]:
                if e21w[-1] > e55w[-1]:
                    weekly_score = 75
                    result["rationale"].append("Tendencia semanal BULL")
                else:
                    weekly_score = 25
                    result["rationale"].append("Tendencia semanal BEAR")
    except Exception:
        pass

    result["components"]["weekly_trend"] = round(weekly_score, 1)

    # ══════════════════════════════════════
    # PESOS DINAMICOS v4
    # ══════════════════════════════════════
    atr_pct = analysis["atr_pct"]

    # Pesos base
    weights = {
        "ema": 0.28,
        "rsi": 0.12,
        "macd": 0.12,
        "bollinger": 0.08,
        "obv": 0.08,
        "fear_greed": 0.12,
        "polymarket": 0.10,
        "volume": 0.06,
        "weekly_trend": 0.04
    }

    # Si Polymarket no disponible, redistribuir
    if not poly_available:
        poly_weight = weights["polymarket"]
        weights["polymarket"] = 0
        weights["ema"] += poly_weight * 0.4
        weights["macd"] += poly_weight * 0.3
        weights["obv"] += poly_weight * 0.3

    # Alta volatilidad: dar mas peso a indicadores de momentum (MACD, RSI)
    if atr_pct > 4:
        weights["macd"] *= 1.2
        weights["rsi"] *= 1.1
        weights["ema"] *= 0.9
        # Re-normalizar
        total_w = sum(weights.values())
        weights = {k: v / total_w for k, v in weights.items()}

    # Bollinger squeeze: aumentar peso de Bollinger
    if bb.get("squeeze"):
        weights["bollinger"] *= 1.5
        total_w = sum(weights.values())
        weights = {k: v / total_w for k, v in weights.items()}

    result["weights"] = {k: round(v, 3) for k, v in weights.items()}

    # ══════════════════════════════════════
    # WEIGHTED SCORE (60% del final)
    # ══════════════════════════════════════
    weighted_score = 0
    for key in weights:
        score = result["components"].get(key, 50)
        weighted_score += score * weights[key]

    # Volatility dampening
    if atr_pct > 5:
        weighted_score = 50 + (weighted_score - 50) * 0.7
        result["rationale"].append(f"ATR alto ({atr_pct:.1f}%) — reduccion de senal")

    result["components"]["atr_pct"] = round(atr_pct, 2)

    # ══════════════════════════════════════
    # CONSENSUS SCORE (40% del final) — NUEVO v4
    # ══════════════════════════════════════
    consensus = indicator_consensus(analysis)
    result["consensus"] = consensus

    # Convertir consensus a score 0-100
    consensus_score = 50 + (consensus["bull_pct"] - consensus["bear_pct"]) * 0.5

    result["components"]["weighted_raw"] = round(weighted_score, 1)
    result["components"]["consensus_raw"] = round(consensus_score, 1)

    # ══════════════════════════════════════
    # COMPOSITE = 60% weighted + 40% consensus
    # ══════════════════════════════════════
    composite = weighted_score * 0.6 + consensus_score * 0.4

    # Agreement bonus: si weighted y consensus estan de acuerdo, amplificar
    if (weighted_score > 60 and consensus_score > 60) or (weighted_score < 40 and consensus_score < 40):
        composite = 50 + (composite - 50) * 1.15
        result["rationale"].append("Weighted + Consensus ALINEADOS — amplificacion +15%")
    elif (weighted_score > 55 and consensus_score < 45) or (weighted_score < 45 and consensus_score > 55):
        composite = 50 + (composite - 50) * 0.8
        result["rationale"].append("Weighted y Consensus en CONFLICTO — reduccion -20%")

    composite = max(0, min(100, round(composite)))

    # Verdict
    if composite >= 80:
        verdict = "STRONG BUY"
    elif composite >= 65:
        verdict = "BUY"
    elif composite >= 55:
        verdict = "LEAN BUY"
    elif composite >= 45:
        verdict = "NEUTRAL"
    elif composite >= 35:
        verdict = "LEAN SELL"
    elif composite >= 20:
        verdict = "SELL"
    else:
        verdict = "STRONG SELL"

    # Confidence: combinacion de distancia a 50 + agreement
    distance_confidence = min(100, abs(composite - 50) * 2.5)
    agreement_bonus = 10 if consensus["consensus"].startswith("STRONG") else 0
    confidence = min(100, distance_confidence + agreement_bonus)

    result["composite_score"] = composite
    result["verdict"] = verdict
    result["confidence"] = round(confidence, 1)

    # Market data summary
    result["market"] = {
        "price": analysis["price"],
        "ema_fast": analysis["ema_fast"],
        "ema_slow": analysis["ema_slow"],
        "rsi_1h": analysis["rsi_1h"],
        "rsi_4h": analysis["rsi_4h"],
        "macd_line": analysis["macd"]["macd"],
        "macd_signal": analysis["macd"]["signal"],
        "macd_crossover": analysis["macd"]["crossover"],
        "bollinger_pct_b": analysis["bollinger"]["pct_b"] if analysis["bollinger"]["ok"] else None,
        "bollinger_squeeze": analysis["bollinger"].get("squeeze", False),
        "obv_divergence": analysis["obv"]["divergence"] if analysis["obv"]["ok"] else None,
        "stoch_rsi_k": analysis["stoch_rsi"]["k"] if analysis["stoch_rsi"]["ok"] else None,
        "vwap": analysis["vwap"]["vwap"] if analysis["vwap"]["ok"] else None,
        "fear_greed": fng_val,
        "volume_ratio": round(vol_ratio, 2),
        "atr_pct": round(atr_pct, 2)
    }

    return result


def print_report_v4(result: Dict):
    score = result["composite_score"]
    verdict = result["verdict"]
    cons = result.get("consensus", {})

    print(f"\n{'='*65}")
    print(f"  ROTHSTEIN v4 — MULTI-SIGNAL COMPOSITE ANALYSIS")
    print(f"  {result['pair']} | {result['timestamp'][:19]}")
    print(f"{'='*65}")

    print(f"\n  COMPOSITE SCORE: {score}/100 — {verdict}")
    print(f"  CONFIDENCE: {result['confidence']}%")

    if cons:
        print(f"\n  CONSENSUS: {cons.get('consensus','?')} (Bull:{cons.get('bull',0)} Bear:{cons.get('bear',0)} Neutral:{cons.get('neutral',0)})")
        for name, vote in cons.get("votes", []):
            icon = "+" if vote == "BULL" else "-" if vote == "BEAR" else "~"
            print(f"    [{icon}] {name}: {vote}")

    print(f"\n  COMPONENTES (weighted + consensus):")
    w_raw = result["components"].get("weighted_raw", 0)
    c_raw = result["components"].get("consensus_raw", 0)
    print(f"    Weighted Score:  {w_raw:.1f}/100 (60%)")
    print(f"    Consensus Score: {c_raw:.1f}/100 (40%)")

    print(f"\n  DESGLOSE POR INDICADOR:")
    for key, w in result["weights"].items():
        val = result["components"].get(key, 50)
        bar = "█" * int(val / 5) + "░" * (20 - int(val / 5))
        print(f"    {key:16s} {val:5.1f}/100 [{bar}] ({w:.1%})")

    print(f"\n  MARKET DATA:")
    m = result.get("market", {})
    for k, v in m.items():
        print(f"    {k:20s}: {v}")

    print(f"\n  RATIONALE:")
    for r in result["rationale"]:
        print(f"    • {r}")

    print(f"\n  v4 IMPROVEMENTS:")
    for imp in result.get("improvements_over_v3", []):
        print(f"    ✓ {imp}")

    print(f"\n{'='*65}")
    print(f"JSON_RESULT:{json.dumps(result)}")


if __name__ == "__main__":
    pair = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    result = analyze_v4(pair)
    print_report_v4(result)
