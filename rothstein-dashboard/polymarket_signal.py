#!/usr/bin/env python3
"""
ROTHSTEIN — Polymarket LLM Divergence Signal
Sindicato Lansky | Estrategia: precio Polymarket vs probabilidad estimada por EMA + macro

Estrategia documentada: el bot que ganó 1,322% en 48h usaba esta lógica:
1. Calcular probabilidad "real" del resultado usando EMA + RSI + macro
2. Comparar con precio del contrato en Polymarket (= probabilidad implícita del mercado)
3. Si divergencia ≥ umbral → SEÑAL DE ENTRADA (el mercado está equivocado)

PARES SOPORTADOS: BTC 15min, BTC 1h, ETH 15min, SOL 1h, crypto genérico
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL  = "https://clob.polymarket.com"
BINANCE   = "https://api.binance.com/api/v3"
FNG_URL   = "https://api.alternative.me/fng/"

# Umbral de divergencia para señal (% diferencia entre prob estimada y precio mercado)
DIVERGENCE_THRESHOLD = 12  # señal si diferencia ≥ 12%
MIN_VOLUME = 5000          # volumen mínimo 24h en USDC para considerar mercado

ASSET_KEYWORDS = {
    "btc": ["bitcoin", "btc", "bitcoin higher", "bitcoin lower", "btc above", "btc below"],
    "eth": ["ethereum", "eth", "ether"],
    "sol": ["solana", "sol"],
}

@dataclass
class DivergenceSignal:
    market_id: str
    question: str
    asset: str
    direction: str           # "HIGHER" / "LOWER"
    market_prob: float       # probabilidad implícita Polymarket (0-100)
    estimated_prob: float    # nuestra estimación basada en técnico
    divergence: float        # diferencia absoluta
    signal: str              # "BUY_YES" / "BUY_NO" / "NEUTRAL"
    confidence: float        # 0-100
    rationale: str
    yes_price: float
    volume_24h: float
    expires: Optional[str]
    timestamp: str

def calc_ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return [None] * (period - 1) + ema

def get_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def get_binance_data(symbol: str, interval: str = "1h", limit: int = 60) -> dict:
    """Obtener OHLCV de Binance"""
    try:
        r = requests.get(
            f"{BINANCE}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=8
        )
        r.raise_for_status()
        candles = r.json()
        closes = [float(c[4]) for c in candles]
        volumes = [float(c[5]) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        return {
            "closes": closes,
            "volumes": volumes,
            "highs": highs,
            "lows": lows,
            "current_price": closes[-1],
            "ok": True
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_fear_greed() -> int:
    """Obtener Fear & Greed Index"""
    try:
        r = requests.get(FNG_URL, timeout=5)
        return int(r.json()["data"][0]["value"])
    except:
        return 50

def estimate_probability_technical(asset: str, direction: str, timeframe_hours: int = 1) -> dict:
    """
    Estimar probabilidad de que el precio esté HIGHER o LOWER en X horas
    usando EMA 20/50 + RSI + volumen + tendencia
    """
    symbol_map = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT"}
    symbol = symbol_map.get(asset.lower(), "BTCUSDT")

    # Usar 4H para señal principal, 1H para contexto
    data_4h = get_binance_data(symbol, "4h", 60)
    data_1h = get_binance_data(symbol, "1h", 24)

    if not data_4h.get("ok"):
        return {"probability": 50.0, "confidence": 0.0, "rationale": "No data", "factors": {}}

    closes_4h = data_4h["closes"]
    closes_1h = data_1h["closes"] if data_1h.get("ok") else closes_4h

    # EMAs
    ema20_4h = calc_ema(closes_4h, 20)
    ema50_4h = calc_ema(closes_4h, 50)
    e20 = ema20_4h[-1]
    e50 = ema50_4h[-1]

    # RSI
    rsi_1h = get_rsi(closes_1h, 14)
    rsi_4h = get_rsi(closes_4h, 14)

    # Fear & Greed
    fng = get_fear_greed()

    price = closes_4h[-1]

    factors = {
        "price": price,
        "ema20_4h": round(e20, 2) if e20 else None,
        "ema50_4h": round(e50, 2) if e50 else None,
        "rsi_1h": rsi_1h,
        "rsi_4h": rsi_4h,
        "fear_greed": fng
    }

    # Scoring alcista (0-100)
    bull_score = 50.0
    rationale_parts = []

    if e20 and e50:
        if e20 > e50 and price > e20:
            bull_score += 15
            rationale_parts.append("EMA20>EMA50 + precio arriba (BULL)")
        elif e20 > e50 and price > e50:
            bull_score += 8
            rationale_parts.append("EMA20>EMA50 precio entre EMAs (neutral-bull)")
        elif e20 < e50 and price < e20:
            bull_score -= 15
            rationale_parts.append("EMA20<EMA50 + precio abajo (BEAR)")
        elif e20 < e50:
            bull_score -= 8
            rationale_parts.append("EMA20<EMA50 (tendencia bajista)")

        # Cross detection
        prev_e20 = ema20_4h[-2] if len(ema20_4h) >= 2 else e20
        prev_e50 = ema50_4h[-2] if len(ema50_4h) >= 2 else e50
        if prev_e20 < prev_e50 and e20 > e50:
            bull_score += 12
            rationale_parts.append("GOLDEN CROSS reciente (+12)")
        elif prev_e20 > prev_e50 and e20 < e50:
            bull_score -= 12
            rationale_parts.append("DEATH CROSS reciente (-12)")

    # RSI
    if rsi_1h < 30:
        bull_score += 10
        rationale_parts.append(f"RSI 1H sobreventa {rsi_1h} (rebote probable +10)")
    elif rsi_1h > 70:
        bull_score -= 10
        rationale_parts.append(f"RSI 1H sobrecompra {rsi_1h} (caída probable -10)")
    elif 40 <= rsi_1h <= 60:
        rationale_parts.append(f"RSI 1H neutro {rsi_1h}")

    # Fear & Greed
    if fng < 25:
        bull_score += 8
        rationale_parts.append(f"F&G Miedo extremo {fng} (contrarian bull +8)")
    elif fng > 75:
        bull_score -= 8
        rationale_parts.append(f"F&G Avaricia extrema {fng} (contrarian bear -8)")

    # Ajuste por timeframe: más incertidumbre en 15min que en 1h
    # Tirar probabilidad hacia 50% según timeframe corto
    if timeframe_hours <= 0.25:  # 15min
        bull_score = 50 + (bull_score - 50) * 0.4
        rationale_parts.append("Timeframe 15min → reducción señal (x0.4)")
    elif timeframe_hours <= 1:   # 1h
        bull_score = 50 + (bull_score - 50) * 0.65

    # Clamp 5-95
    bull_score = max(5.0, min(95.0, bull_score))

    # Si dirección es LOWER, invertir
    if direction.upper() == "LOWER":
        prob = 100.0 - bull_score
    else:
        prob = bull_score

    # Confidence: qué tan lejos está de 50
    confidence = abs(bull_score - 50) * 2  # 0-100

    return {
        "probability": round(prob, 1),
        "confidence": round(confidence, 1),
        "bull_score": round(bull_score, 1),
        "rationale": " | ".join(rationale_parts),
        "factors": factors
    }

def get_polymarket_crypto_markets(asset: str = "btc") -> List[dict]:
    """Obtener mercados crypto activos para un activo específico"""
    keywords = ASSET_KEYWORDS.get(asset.lower(), [asset.lower()])
    try:
        r = requests.get(
            f"{GAMMA_URL}/markets",
            params={"limit": 30, "active": "true", "closed": "false",
                    "order": "volume24hr", "ascending": "false"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        markets = data if isinstance(data, list) else data.get("markets", [])

        filtered = []
        for m in markets:
            q = m.get("question", "").lower()
            vol = float(m.get("volume24hr", 0) or 0)
            if any(k in q for k in keywords) and vol >= MIN_VOLUME:
                filtered.append(m)
        return filtered
    except:
        return []

def get_market_yes_price(market: dict) -> Optional[float]:
    """Obtener precio YES de un mercado"""
    # Intentar desde outcomePrices primero (más rápido)
    try:
        prices = market.get("outcomePrices")
        if prices:
            if isinstance(prices, str):
                prices = json.loads(prices)
            if len(prices) >= 1:
                return float(prices[0])
    except:
        pass
    # Fallback CLOB
    tokens = market.get("tokens", [])
    for t in tokens:
        if "yes" in t.get("outcome", "").lower():
            try:
                r = requests.get(
                    f"{CLOB_URL}/midpoint",
                    params={"token_id": t.get("token_id", "")},
                    timeout=5
                )
                if r.status_code == 200:
                    mid = r.json().get("mid")
                    if mid:
                        return float(mid)
            except:
                pass
    return None

def detect_direction_from_question(question: str) -> str:
    """Detectar si el mercado es HIGHER o LOWER basado en la pregunta"""
    q = question.lower()
    lower_words = ["lower", "below", "under", "fall", "drop", "down", "bearish"]
    if any(w in q for w in lower_words):
        return "LOWER"
    return "HIGHER"

def detect_timeframe_from_question(question: str) -> float:
    """Detectar timeframe en horas de la pregunta"""
    q = question.lower()
    if "15-minute" in q or "15 minute" in q or "15min" in q:
        return 0.25
    if "30-minute" in q or "30 minute" in q:
        return 0.5
    if "1-hour" in q or "1 hour" in q or "hourly" in q:
        return 1.0
    if "4-hour" in q or "4 hour" in q:
        return 4.0
    if "daily" in q or "today" in q or "24-hour" in q:
        return 24.0
    if "weekly" in q or "this week" in q:
        return 168.0
    return 1.0  # default 1h

def scan_divergences(asset: str = "btc") -> dict:
    """Escanear divergencias LLM vs Polymarket para un activo"""
    markets = get_polymarket_crypto_markets(asset)

    signals = []
    for market in markets[:10]:  # límite para no hacer demasiadas llamadas
        try:
            yes_price = get_market_yes_price(market)
            if yes_price is None:
                continue

            market_prob = yes_price * 100  # precio YES = probabilidad implícita
            question = market.get("question", "")
            direction = detect_direction_from_question(question)
            timeframe_h = detect_timeframe_from_question(question)

            # Estimar probabilidad con nuestro modelo técnico
            est = estimate_probability_technical(asset, direction, timeframe_h)
            estimated_prob = est["probability"]

            divergence = abs(market_prob - estimated_prob)

            # Solo reportar si divergencia supera umbral
            if divergence < DIVERGENCE_THRESHOLD:
                continue

            # Determinar señal
            if estimated_prob > market_prob + DIVERGENCE_THRESHOLD:
                signal = "BUY_YES"  # mercado subestima probabilidad → comprar YES
            elif estimated_prob < market_prob - DIVERGENCE_THRESHOLD:
                signal = "BUY_NO"   # mercado sobreestima → comprar NO
            else:
                signal = "NEUTRAL"

            volume = float(market.get("volume24hr", 0) or 0)
            end_date = market.get("endDate") or market.get("end_date_iso")

            sig = DivergenceSignal(
                market_id=market.get("id", ""),
                question=question[:80],
                asset=asset.upper(),
                direction=direction,
                market_prob=round(market_prob, 1),
                estimated_prob=round(estimated_prob, 1),
                divergence=round(divergence, 1),
                signal=signal,
                confidence=round(est["confidence"], 1),
                rationale=est["rationale"],
                yes_price=round(yes_price, 4),
                volume_24h=round(volume, 2),
                expires=str(end_date)[:10] if end_date else None,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            signals.append(sig)

        except:
            continue

    signals.sort(key=lambda x: x.divergence, reverse=True)

    result = {
        "asset": asset.upper(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets_analyzed": len(markets),
        "divergences_found": len(signals),
        "signals": [asdict(s) for s in signals],
        "best_signal": asdict(signals[0]) if signals else None,
        "divergence_threshold": DIVERGENCE_THRESHOLD
    }

    return result

def print_divergence_report(result: dict):
    print(f"\n{'='*60}")
    print(f"  ROTHSTEIN — POLYMARKET LLM DIVERGENCE")
    print(f"  Asset: {result['asset']} | {result['timestamp'][:19]}")
    print(f"{'='*60}")
    print(f"  Mercados analizados: {result['markets_analyzed']}")
    print(f"  Divergencias ≥{result['divergence_threshold']}%: {result['divergences_found']}")

    for sig in result["signals"]:
        emoji = "🟢" if sig["signal"] == "BUY_YES" else "🔴" if sig["signal"] == "BUY_NO" else "⚪"
        print(f"\n  {emoji} {sig['signal']} | Divergencia: {sig['divergence']}%")
        print(f"  Mercado: {sig['question']}")
        print(f"  Polymarket: {sig['market_prob']}% | Nuestra estimación: {sig['estimated_prob']}%")
        print(f"  Confianza: {sig['confidence']}% | YES price: {sig['yes_price']}")
        print(f"  Razón: {sig['rationale']}")

    if not result["signals"]:
        print(f"  Sin divergencias ≥{result['divergence_threshold']}% en este momento.")

    print(f"\n{'='*60}")
    print(f"JSON_RESULT:{json.dumps(result)}")

if __name__ == "__main__":
    import sys
    asset = sys.argv[1] if len(sys.argv) > 1 else "btc"
    result = scan_divergences(asset)
    print_divergence_report(result)
