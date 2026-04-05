#!/usr/bin/env python3
"""
ROTHSTEIN — Technical Analysis Library (Shared)
Sindicato Lansky | Libreria centralizada de indicadores tecnicos

INDICADORES:
  - EMA (cualquier periodo)
  - RSI (14 por defecto)
  - MACD (12, 26, 9)
  - Bollinger Bands (20, 2)
  - OBV (On-Balance Volume)
  - ATR (Average True Range)
  - VWAP (Volume Weighted Average Price)
  - Stochastic RSI

UTILIDADES:
  - Retry decorator para APIs
  - Binance data fetcher con fallback
  - Fear & Greed fetcher con cache
"""

import requests
import time
import functools
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timezone

# ═══════════════════════════════════════════
# RETRY DECORATOR
# ═══════════════════════════════════════════

def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, exceptions=(Exception,)):
    """Decorador de retry con backoff exponencial"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator


# ═══════════════════════════════════════════
# INDICADORES TECNICOS
# ═══════════════════════════════════════════

def calc_ema(prices: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average"""
    if len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return [None] * (period - 1) + ema


def calc_sma(prices: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average"""
    if len(prices) < period:
        return [None] * len(prices)
    result = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        result.append(sum(prices[i - period + 1:i + 1]) / period)
    return result


def calc_rsi(closes: List[float], period: int = 14) -> float:
    """Relative Strength Index (valor actual)"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def calc_rsi_series(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """RSI como serie completa (para detectar divergencias historicas)"""
    if len(closes) < period + 1:
        return [None] * len(closes)

    result = [None] * period
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Primer RSI con SMA
    gains = [d if d > 0 else 0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        result.append(round(100 - (100 / (1 + avg_gain / avg_loss)), 2))

    # RSI subsiguientes con EMA (Wilder smoothing)
    for i in range(period, len(deltas)):
        d = deltas[i]
        avg_gain = (avg_gain * (period - 1) + (d if d > 0 else 0)) / period
        avg_loss = (avg_loss * (period - 1) + (-d if d < 0 else 0)) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            result.append(round(100 - (100 / (1 + avg_gain / avg_loss)), 2))

    return result


def calc_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
    """
    MACD — Moving Average Convergence Divergence
    Returns: macd_line, signal_line, histogram, crossover status
    """
    if len(closes) < slow + signal:
        return {"macd": 0, "signal": 0, "histogram": 0, "crossover": "NONE", "ok": False}

    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    # MACD line = EMA fast - EMA slow
    macd_line = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line.append(ema_fast[i] - ema_slow[i])
        else:
            macd_line.append(None)

    # Filter None values for signal line calculation
    valid_macd = [m for m in macd_line if m is not None]
    if len(valid_macd) < signal:
        return {"macd": 0, "signal": 0, "histogram": 0, "crossover": "NONE", "ok": False}

    signal_ema = calc_ema(valid_macd, signal)

    macd_val = valid_macd[-1]
    signal_val = signal_ema[-1] if signal_ema[-1] is not None else 0
    histogram = macd_val - signal_val

    # Crossover detection
    crossover = "NONE"
    if len(valid_macd) >= 2 and len(signal_ema) >= 2:
        prev_macd = valid_macd[-2]
        prev_signal = signal_ema[-2] if signal_ema[-2] is not None else 0
        if prev_macd <= prev_signal and macd_val > signal_val:
            crossover = "BULLISH"
        elif prev_macd >= prev_signal and macd_val < signal_val:
            crossover = "BEARISH"

    return {
        "macd": round(macd_val, 4),
        "signal": round(signal_val, 4),
        "histogram": round(histogram, 4),
        "crossover": crossover,
        "trend": "BULL" if macd_val > signal_val else "BEAR",
        "momentum": "INCREASING" if histogram > 0 and (len(valid_macd) < 2 or histogram > valid_macd[-2] - (signal_ema[-2] or 0)) else "DECREASING",
        "ok": True
    }


def calc_bollinger(closes: List[float], period: int = 20, std_dev: float = 2.0) -> Dict:
    """
    Bollinger Bands
    Returns: upper, middle (SMA), lower, %B, bandwidth
    """
    if len(closes) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "pct_b": 0.5, "bandwidth": 0, "ok": False}

    sma = sum(closes[-period:]) / period

    # Standard deviation
    variance = sum((p - sma) ** 2 for p in closes[-period:]) / period
    std = variance ** 0.5

    upper = sma + std_dev * std
    lower = sma - std_dev * std

    price = closes[-1]

    # %B: donde esta el precio relativo a las bandas (0=lower, 1=upper)
    pct_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    # Bandwidth: volatilidad (bandas anchas = alta vol)
    bandwidth = ((upper - lower) / sma * 100) if sma > 0 else 0

    # Squeeze detection: bandwidth < 4% historicamente bajo
    squeeze = bandwidth < 4

    return {
        "upper": round(upper, 2),
        "middle": round(sma, 2),
        "lower": round(lower, 2),
        "pct_b": round(pct_b, 4),
        "bandwidth": round(bandwidth, 2),
        "squeeze": squeeze,
        "position": "ABOVE_UPPER" if price > upper else "BELOW_LOWER" if price < lower else "INSIDE",
        "ok": True
    }


def calc_obv(closes: List[float], volumes: List[float]) -> Dict:
    """
    On-Balance Volume — confirma tendencia con flujo de volumen
    OBV subiendo + precio subiendo = TENDENCIA FUERTE
    OBV bajando + precio subiendo = DIVERGENCIA BEARISH (alerta)
    """
    if len(closes) < 2 or len(volumes) < 2:
        return {"obv": 0, "obv_trend": "FLAT", "divergence": "NONE", "ok": False}

    n = min(len(closes), len(volumes))
    closes = closes[-n:]
    volumes = volumes[-n:]

    obv = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    # OBV trend (ultimas 10 velas)
    lookback = min(10, len(obv))
    obv_recent = obv[-lookback:]
    obv_trend = "RISING" if obv_recent[-1] > obv_recent[0] else "FALLING" if obv_recent[-1] < obv_recent[0] else "FLAT"

    # Price trend
    price_recent = closes[-lookback:]
    price_trend = "RISING" if price_recent[-1] > price_recent[0] else "FALLING"

    # Divergence detection
    divergence = "NONE"
    if price_trend == "RISING" and obv_trend == "FALLING":
        divergence = "BEARISH"  # precio sube pero volumen sale → peligro
    elif price_trend == "FALLING" and obv_trend == "RISING":
        divergence = "BULLISH"  # precio baja pero volumen entra → acumulacion
    elif price_trend == obv_trend:
        divergence = "CONFIRMED"  # tendencia confirmada

    # OBV momentum (% change over lookback)
    obv_change_pct = ((obv[-1] - obv[-lookback]) / abs(obv[-lookback]) * 100) if obv[-lookback] != 0 else 0

    return {
        "obv": round(obv[-1], 0),
        "obv_trend": obv_trend,
        "obv_change_pct": round(obv_change_pct, 1),
        "price_trend": price_trend,
        "divergence": divergence,
        "ok": True
    }


def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Average True Range — medida de volatilidad"""
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def calc_stoch_rsi(closes: List[float], rsi_period: int = 14, stoch_period: int = 14, k_smooth: int = 3, d_smooth: int = 3) -> Dict:
    """
    Stochastic RSI — RSI normalizado entre 0-100
    Mas sensible que RSI normal para detectar puntos de giro
    """
    rsi_series = calc_rsi_series(closes, rsi_period)
    valid_rsi = [r for r in rsi_series if r is not None]

    if len(valid_rsi) < stoch_period:
        return {"k": 50, "d": 50, "signal": "NEUTRAL", "ok": False}

    # StochRSI = (RSI - min(RSI)) / (max(RSI) - min(RSI))
    stoch_values = []
    for i in range(stoch_period - 1, len(valid_rsi)):
        window = valid_rsi[i - stoch_period + 1:i + 1]
        rsi_min = min(window)
        rsi_max = max(window)
        if rsi_max - rsi_min == 0:
            stoch_values.append(50)
        else:
            stoch_values.append(((valid_rsi[i] - rsi_min) / (rsi_max - rsi_min)) * 100)

    if not stoch_values:
        return {"k": 50, "d": 50, "signal": "NEUTRAL", "ok": False}

    # %K smoothing
    if len(stoch_values) >= k_smooth:
        k_val = sum(stoch_values[-k_smooth:]) / k_smooth
    else:
        k_val = stoch_values[-1]

    # %D smoothing (SMA of %K)
    d_val = k_val  # simplified

    signal = "NEUTRAL"
    if k_val < 20:
        signal = "OVERSOLD"
    elif k_val > 80:
        signal = "OVERBOUGHT"

    return {
        "k": round(k_val, 2),
        "d": round(d_val, 2),
        "signal": signal,
        "ok": True
    }


def calc_vwap(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict:
    """Volume Weighted Average Price — precio justo basado en volumen"""
    n = min(len(closes), len(highs), len(lows), len(volumes))
    if n < 2:
        return {"vwap": 0, "position": "NEUTRAL", "ok": False}

    typical_prices = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
    cum_tp_vol = sum(tp * v for tp, v in zip(typical_prices, volumes))
    cum_vol = sum(volumes)

    vwap = cum_tp_vol / cum_vol if cum_vol > 0 else closes[-1]
    price = closes[-1]

    return {
        "vwap": round(vwap, 2),
        "price": round(price, 2),
        "deviation_pct": round((price - vwap) / vwap * 100, 2) if vwap > 0 else 0,
        "position": "ABOVE" if price > vwap else "BELOW",
        "ok": True
    }


# ═══════════════════════════════════════════
# DATA FETCHERS CON RETRY
# ═══════════════════════════════════════════

BINANCE_API = "https://api.binance.com/api/v3"
BINANCE_FALLBACK = "https://api1.binance.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/"

# Cache simple en memoria
_cache: Dict[str, Tuple[float, any]] = {}
CACHE_TTL = 60  # 1 minuto


def _get_cached(key: str) -> Optional[any]:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
    return None


def _set_cache(key: str, value: any):
    _cache[key] = (time.time(), value)


@retry(max_attempts=3, delay=0.5, backoff=2.0, exceptions=(requests.RequestException, ValueError))
def fetch_binance_klines(symbol: str, interval: str, limit: int = 60) -> Dict:
    """Fetch Binance klines con retry y fallback"""
    cache_key = f"klines_{symbol}_{interval}_{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    for base_url in [BINANCE_API, BINANCE_FALLBACK]:
        try:
            r = requests.get(
                f"{base_url}/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=10
            )
            r.raise_for_status()
            candles = r.json()
            result = {
                "closes": [float(c[4]) for c in candles],
                "opens": [float(c[1]) for c in candles],
                "highs": [float(c[2]) for c in candles],
                "lows": [float(c[3]) for c in candles],
                "volumes": [float(c[5]) for c in candles],
                "timestamps": [int(c[0]) for c in candles],
                "ok": True
            }
            _set_cache(cache_key, result)
            return result
        except requests.RequestException:
            continue

    return {"ok": False, "error": "All Binance endpoints failed"}


@retry(max_attempts=2, delay=1.0, exceptions=(requests.RequestException,))
def fetch_fear_greed() -> Dict:
    """Fear & Greed Index con cache de 5 minutos"""
    cache_key = "fng"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    r = requests.get(FNG_URL, timeout=5)
    r.raise_for_status()
    d = r.json()["data"][0]
    result = {"value": int(d["value"]), "classification": d["value_classification"]}

    # Cache F&G por 5 minutos (cambia 1 vez al dia pero el endpoint es lento)
    _cache[cache_key] = (time.time(), result)
    return result


def fetch_ticker_price(symbol: str) -> Optional[float]:
    """Precio actual rapido"""
    cache_key = f"price_{symbol}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        r = requests.get(f"{BINANCE_API}/ticker/price", params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        price = float(r.json()["price"])
        _set_cache(cache_key, price)
        return price
    except Exception:
        return None


def check_api_health(url: str, timeout: int = 5) -> bool:
    """Health check rapido para cualquier API"""
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


# ═══════════════════════════════════════════
# ANALISIS COMPUESTO DE INDICADORES
# ═══════════════════════════════════════════

def full_technical_analysis(symbol: str = "BTCUSDT", ema_fast: int = 15, ema_slow: int = 40) -> Dict:
    """
    Ejecuta TODOS los indicadores sobre un par y devuelve un resumen unificado.
    Usado por multi_signal.py y server.py
    """
    data_4h = fetch_binance_klines(symbol, "4h", 60)
    data_1h = fetch_binance_klines(symbol, "1h", 30)

    if not data_4h.get("ok"):
        return {"ok": False, "error": "No Binance 4H data"}

    c4 = data_4h["closes"]
    h4 = data_4h["highs"]
    l4 = data_4h["lows"]
    v4 = data_4h["volumes"]

    c1 = data_1h["closes"] if data_1h.get("ok") else c4

    price = c4[-1]

    # Core EMAs
    ema_fast_list = calc_ema(c4, ema_fast)
    ema_slow_list = calc_ema(c4, ema_slow)

    # EMA cross detection
    cross = "NONE"
    if len(ema_fast_list) >= 2 and ema_fast_list[-1] and ema_fast_list[-2] and ema_slow_list[-1] and ema_slow_list[-2]:
        if ema_fast_list[-2] < ema_slow_list[-2] and ema_fast_list[-1] > ema_slow_list[-1]:
            cross = "GOLDEN_CROSS"
        elif ema_fast_list[-2] > ema_slow_list[-2] and ema_fast_list[-1] < ema_slow_list[-1]:
            cross = "DEATH_CROSS"

    # All indicators
    rsi_4h = calc_rsi(c4)
    rsi_1h = calc_rsi(c1)
    macd = calc_macd(c4)
    bollinger = calc_bollinger(c4)
    obv = calc_obv(c4, v4)
    atr = calc_atr(h4, l4, c4)
    atr_pct = (atr / price * 100) if price > 0 else 0
    stoch_rsi = calc_stoch_rsi(c4)
    vwap = calc_vwap(c4, h4, l4, v4)

    # Volume analysis
    avg_vol = sum(v4[-20:]) / 20 if len(v4) >= 20 else sum(v4) / max(len(v4), 1)
    vol_ratio = v4[-1] / avg_vol if avg_vol > 0 else 1

    # Fear & Greed
    try:
        fng = fetch_fear_greed()
    except Exception:
        fng = {"value": 50, "classification": "Neutral"}

    return {
        "ok": True,
        "symbol": symbol,
        "price": round(price, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # EMAs
        "ema_fast": round(ema_fast_list[-1], 2) if ema_fast_list[-1] else None,
        "ema_slow": round(ema_slow_list[-1], 2) if ema_slow_list[-1] else None,
        "ema_trend": "BULL" if (ema_fast_list[-1] and ema_slow_list[-1] and ema_fast_list[-1] > ema_slow_list[-1]) else "BEAR",
        "ema_cross": cross,
        # RSI
        "rsi_4h": rsi_4h,
        "rsi_1h": rsi_1h,
        # MACD
        "macd": macd,
        # Bollinger
        "bollinger": bollinger,
        # OBV
        "obv": obv,
        # ATR / Volatility
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        # Stochastic RSI
        "stoch_rsi": stoch_rsi,
        # VWAP
        "vwap": vwap,
        # Volume
        "volume_ratio": round(vol_ratio, 2),
        "volume_increasing": vol_ratio > 1.0,
        # Fear & Greed
        "fear_greed": fng,
        # Raw data for downstream
        "_closes_4h": c4,
        "_volumes_4h": v4,
    }


# ═══════════════════════════════════════════
# SCORING HELPERS
# ═══════════════════════════════════════════

def indicator_consensus(analysis: Dict) -> Dict:
    """
    Calcula el consenso de TODOS los indicadores (cada uno vota BULL/BEAR/NEUTRAL)
    Returns: votos, porcentaje bull, bear, neutral
    """
    votes = []

    # 1. EMA trend
    if analysis.get("ema_trend") == "BULL":
        votes.append(("EMA", "BULL"))
    else:
        votes.append(("EMA", "BEAR"))

    # 2. RSI
    rsi = analysis.get("rsi_4h", 50)
    if rsi < 30:
        votes.append(("RSI", "BULL"))  # oversold = contrarian buy
    elif rsi > 70:
        votes.append(("RSI", "BEAR"))  # overbought = contrarian sell
    else:
        votes.append(("RSI", "NEUTRAL"))

    # 3. MACD
    macd = analysis.get("macd", {})
    if macd.get("trend") == "BULL":
        votes.append(("MACD", "BULL"))
    elif macd.get("trend") == "BEAR":
        votes.append(("MACD", "BEAR"))
    else:
        votes.append(("MACD", "NEUTRAL"))

    # 4. Bollinger
    bb = analysis.get("bollinger", {})
    if bb.get("position") == "BELOW_LOWER":
        votes.append(("BOLLINGER", "BULL"))  # oversold
    elif bb.get("position") == "ABOVE_UPPER":
        votes.append(("BOLLINGER", "BEAR"))  # overbought
    else:
        votes.append(("BOLLINGER", "NEUTRAL"))

    # 5. OBV
    obv = analysis.get("obv", {})
    if obv.get("divergence") == "BULLISH":
        votes.append(("OBV", "BULL"))
    elif obv.get("divergence") == "BEARISH":
        votes.append(("OBV", "BEAR"))
    elif obv.get("divergence") == "CONFIRMED":
        # Confirma la tendencia EMA
        votes.append(("OBV", "BULL" if analysis.get("ema_trend") == "BULL" else "BEAR"))
    else:
        votes.append(("OBV", "NEUTRAL"))

    # 6. Stochastic RSI
    stoch = analysis.get("stoch_rsi", {})
    if stoch.get("signal") == "OVERSOLD":
        votes.append(("STOCH_RSI", "BULL"))
    elif stoch.get("signal") == "OVERBOUGHT":
        votes.append(("STOCH_RSI", "BEAR"))
    else:
        votes.append(("STOCH_RSI", "NEUTRAL"))

    # 7. VWAP
    vwap = analysis.get("vwap", {})
    if vwap.get("position") == "ABOVE":
        votes.append(("VWAP", "BULL"))
    elif vwap.get("position") == "BELOW":
        votes.append(("VWAP", "BEAR"))
    else:
        votes.append(("VWAP", "NEUTRAL"))

    # 8. Fear & Greed (contrarian)
    fng = analysis.get("fear_greed", {}).get("value", 50)
    if fng <= 25:
        votes.append(("F&G", "BULL"))  # fear = contrarian buy
    elif fng >= 75:
        votes.append(("F&G", "BEAR"))
    else:
        votes.append(("F&G", "NEUTRAL"))

    bull_count = sum(1 for _, v in votes if v == "BULL")
    bear_count = sum(1 for _, v in votes if v == "BEAR")
    neutral_count = sum(1 for _, v in votes if v == "NEUTRAL")
    total = len(votes)

    return {
        "votes": votes,
        "bull": bull_count,
        "bear": bear_count,
        "neutral": neutral_count,
        "total": total,
        "bull_pct": round(bull_count / total * 100, 1),
        "bear_pct": round(bear_count / total * 100, 1),
        "consensus": "STRONG_BULL" if bull_count >= 6 else "BULL" if bull_count >= 4 else "STRONG_BEAR" if bear_count >= 6 else "BEAR" if bear_count >= 4 else "MIXED"
    }


if __name__ == "__main__":
    import json
    import sys

    pair = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"

    print(f"\n{'='*60}")
    print(f"  ROTHSTEIN — Full Technical Analysis: {pair}")
    print(f"{'='*60}")

    analysis = full_technical_analysis(pair)

    if not analysis["ok"]:
        print(f"  ERROR: {analysis.get('error')}")
        sys.exit(1)

    print(f"\n  Price: ${analysis['price']:,.2f}")
    print(f"  EMA_FAST: {analysis['ema_fast']}  EMA_SLOW: {analysis['ema_slow']}  Trend: {analysis['ema_trend']}  Cross: {analysis['ema_cross']}")
    print(f"  RSI 4H: {analysis['rsi_4h']}  RSI 1H: {analysis['rsi_1h']}")
    print(f"  MACD: {analysis['macd']['macd']} | Signal: {analysis['macd']['signal']} | Cross: {analysis['macd']['crossover']}")
    print(f"  Bollinger: Upper={analysis['bollinger']['upper']} Mid={analysis['bollinger']['middle']} Low={analysis['bollinger']['lower']} %B={analysis['bollinger']['pct_b']}")
    print(f"  OBV Trend: {analysis['obv']['obv_trend']} | Divergence: {analysis['obv']['divergence']}")
    print(f"  ATR: {analysis['atr']} ({analysis['atr_pct']}%)")
    print(f"  StochRSI: K={analysis['stoch_rsi']['k']} D={analysis['stoch_rsi']['d']} Signal={analysis['stoch_rsi']['signal']}")
    print(f"  VWAP: {analysis['vwap']['vwap']} Position: {analysis['vwap']['position']}")
    print(f"  Volume Ratio: {analysis['volume_ratio']}x")
    print(f"  F&G: {analysis['fear_greed']['value']} ({analysis['fear_greed']['classification']})")

    consensus = indicator_consensus(analysis)
    print(f"\n  CONSENSUS: {consensus['consensus']}")
    print(f"  BULL: {consensus['bull']}/{consensus['total']} ({consensus['bull_pct']}%)")
    print(f"  BEAR: {consensus['bear']}/{consensus['total']} ({consensus['bear_pct']}%)")
    print(f"\n  Votes:")
    for name, vote in consensus["votes"]:
        icon = "+" if vote == "BULL" else "-" if vote == "BEAR" else "~"
        print(f"    [{icon}] {name}: {vote}")

    # Output JSON for pipeline
    safe = {k: v for k, v in analysis.items() if not k.startswith("_")}
    safe["consensus"] = consensus
    print(f"\nJSON_RESULT:{json.dumps(safe, default=str)}")
