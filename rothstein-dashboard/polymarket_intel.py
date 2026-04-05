#!/usr/bin/env python3
"""
ROTHSTEIN — Polymarket Intelligence Hub
Sindicato Lansky | Multi-Asset Prediction Markets

Cobertura completa:
  CRYPTO:       BTC, ETH, SOL, XRP
  METALES:      Gold, Silver, Platinum
  ENERGIA:      Oil (WTI, Brent), Gas
  ACCIONES:     Palantir, Nvidia, Tesla, S&P500
  MACRO/GUERRA: FED, CPI, Geopolitica, Conflictos
  REGULACION:   Crypto regulation, ETF approvals

Uso: python polymarket_intel.py [categoria]
     python polymarket_intel.py all
     python polymarket_intel.py btc
     python polymarket_intel.py gold
"""

import sys, json, requests, time, re
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Optional

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL  = "https://clob.polymarket.com"

# ─── CATEGORIA KEYWORDS ─────────────────────────────────────────
CATEGORIES = {
    "btc":       ["bitcoin", "btc", "bitcoin price", "bitcoin etf"],
    "eth":       ["ethereum", "eth", "ethereum price"],
    "crypto":    ["crypto", "bitcoin", "ethereum", "solana", "xrp", "defi", "stablecoin", "altcoin", "binance"],
    "gold":      ["gold", "gold price", "gold etf", "precious metal", "xau"],
    "silver":    ["silver", "silver price", "xag"],
    "metals":    ["gold", "silver", "platinum", "palladium", "copper", "metal"],
    "oil":       ["oil", "crude", "wti", "brent", "opec", "petroleum", "gas price"],
    "energy":    ["oil", "crude", "natural gas", "energy", "opec", "petroleum"],
    "palantir":  ["palantir", "pltr", "data analytics"],
    "nvidia":    ["nvidia", "nvda", "ai chip", "gpu"],
    "stocks":    ["nasdaq", "s&p", "sp500", "dow jones", "stock market", "ipo", "earnings", "fed", "recession"],
    "fed":       ["federal reserve", "fed rate", "interest rate", "fomc", "powell", "rate cut", "rate hike"],
    "war":       ["war", "conflict", "ukraine", "russia", "nato", "middle east", "israel", "iran", "china", "taiwan", "troops", "ceasefire", "military"],
    "macro":     ["recession", "inflation", "cpi", "gdp", "unemployment", "economy", "debt", "deficit"],
    "geopolitics": ["election", "trump", "biden", "sanctions", "nuclear", "treaty", "coup", "protest"],
    "regulation": ["regulation", "sec", "crypto ban", "etf approval", "cbdc", "stablecoin law"],
}

# Sindicato Lansky relevance score weights
RELEVANCE = {
    "btc": 10, "eth": 8, "crypto": 7,
    "gold": 9, "silver": 8, "metals": 7,
    "oil": 6, "energy": 6,
    "palantir": 7, "nvidia": 5, "stocks": 5,
    "fed": 9, "war": 8, "macro": 8,
    "geopolitics": 7, "regulation": 8,
}


@dataclass
class MarketIntel:
    id: str
    question: str
    category: str
    yes_probability: float     # 0-100%
    no_probability: float
    volume_usd: float
    liquidity_usd: float
    end_date: str
    active: bool
    sindicato_signal: str      # BULLISH / BEARISH / NEUTRAL / UNKNOWN
    relevance_score: int
    tags: List[str]
    url: str


class PolymarketIntelHub:
    """Scanner de inteligencia multi-asset de Polymarket para el Sindicato"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Rothstein/3.0"})

    def ping(self) -> bool:
        try:
            r = self.session.get(f"{GAMMA_URL}/markets?limit=1", timeout=5)
            return r.status_code == 200
        except:
            return False

    def _fetch_markets(self, limit=100, offset=0, active=True) -> list:
        try:
            # closed=false returns only markets that haven't been resolved
            params = {"limit": limit, "offset": offset, "closed": "false"}
            r = self.session.get(f"{GAMMA_URL}/markets", params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"  [WARN] Polymarket fetch error: {e}")
        return []

    def _classify_market(self, question: str, description: str = "") -> tuple:
        """Clasifica un mercado por categoria y calcula relevancia"""
        text = (question + " " + description).lower()
        matched_cats = []
        for cat, keywords in CATEGORIES.items():
            for kw in keywords:
                # Word-boundary matching to avoid false positives (e.g. "warriors" != "war")
                pattern = r'\b' + re.escape(kw) + r'\b'
                if re.search(pattern, text):
                    matched_cats.append(cat)
                    break

        if not matched_cats:
            return ["other"], 1

        # Primary category = highest relevance
        matched_cats.sort(key=lambda c: RELEVANCE.get(c, 0), reverse=True)
        score = max(RELEVANCE.get(c, 1) for c in matched_cats)
        return matched_cats[:3], score

    def _parse_probability(self, outcome_prices: str) -> tuple:
        """Parsea probabilidades del campo outcomePrices"""
        try:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if isinstance(prices, list) and len(prices) >= 2:
                yes = float(prices[0]) * 100
                no  = float(prices[1]) * 100
                return round(yes, 1), round(no, 1)
        except:
            pass
        return 50.0, 50.0

    def _derive_signal(self, question: str, yes_prob: float, categories: list) -> str:
        """Deriva señal para el Sindicato basada en probabilidad y pregunta"""
        q = question.lower()

        # Crypto: ¿sube BTC?
        if any(c in categories for c in ["btc", "eth", "crypto"]):
            if "above" in q or "reach" in q or "exceed" in q or "bull" in q:
                if yes_prob >= 55: return "BULLISH"
                elif yes_prob <= 45: return "BEARISH"
            elif "below" in q or "crash" in q or "fall" in q or "bear" in q:
                if yes_prob >= 55: return "BEARISH"
                elif yes_prob <= 45: return "BULLISH"

        # Oro/Plata: ¿sube?
        if any(c in categories for c in ["gold", "silver", "metals"]):
            if "above" in q or "reach" in q or "rise" in q:
                if yes_prob >= 55: return "BULLISH"
                elif yes_prob <= 45: return "BEARISH"

        # FED: ¿baja tipos? → Bullish para activos
        if "fed" in categories:
            if "rate cut" in q or "lower" in q or "reduce" in q:
                if yes_prob >= 55: return "BULLISH"  # Rate cut = bullish
            elif "rate hike" in q or "raise" in q:
                if yes_prob >= 55: return "BEARISH"  # Rate hike = bearish

        # Guerra: conflicto = bearish para risk assets, bullish para safe havens
        if "war" in categories or "geopolitics" in categories:
            if yes_prob >= 60:
                return "RISK_OFF"  # War likely → flight to safety
            elif yes_prob <= 40:
                return "RISK_ON"   # Conflict resolving → risk on

        # Oil: sube = inflación = bearish para crypto, neutral para acciones
        if "oil" in categories or "energy" in categories:
            if "above" in q or "rise" in q:
                if yes_prob >= 60: return "INFLATIONARY"

        return "NEUTRAL"

    def scan_all(self, min_volume=0, limit_per_batch=100) -> List[MarketIntel]:
        """Escanea TODOS los mercados activos de Polymarket"""
        print(f"\n[POLYMARKET] Escaneando mercados activos...")
        all_raw = []

        # Fetch multiple pages (up to 500 markets)
        for offset in range(0, 500, 100):
            batch = self._fetch_markets(limit=100, offset=offset)
            if not batch:
                break
            all_raw.extend(batch)
            time.sleep(0.3)
            if len(batch) < 100:
                break

        # Filter: skip fully resolved (price at 0.00 or 1.00) and old sports markets
        truly_active = []
        sports_skip = ['nba:', 'nfl:', 'nhl:', 'mlb:', 'mls:', 'ncaab:', 'ncaaf:', 'in-game',
                       'will the', 'beat the', 'win their', 'score more']
        for m in all_raw:
            q = m.get("question", "").lower()
            if any(s in q for s in sports_skip):
                continue
            prices_str = m.get("outcomePrices", "[0.5, 0.5]") or "[0.5, 0.5]"
            try:
                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                if isinstance(prices, list) and len(prices) >= 2:
                    p0, p1 = float(prices[0]), float(prices[1])
                    is_resolved = (p0 <= 0.001 or p0 >= 0.999)
                    if is_resolved:
                        continue
            except:
                pass
            truly_active.append(m)

        all_raw = truly_active
        print(f"[POLYMARKET] {len(all_raw)} mercados activos (sin deportes, sin resueltos)")

        markets = []
        for m in all_raw:
            if not m.get("active", False):
                continue

            question = m.get("question", "")
            description = m.get("description", "")[:500]
            volume = float(m.get("volume", 0) or 0)
            liquidity = float(m.get("liquidity", 0) or 0)

            if volume < min_volume:
                continue

            categories, relevance = self._classify_market(question, description)

            # Skip irrelevant markets (entertainment, sports)
            if relevance <= 1 and not any(c in categories for c in ["btc", "crypto", "gold", "fed", "war"]):
                continue

            yes_prob, no_prob = self._parse_probability(m.get("outcomePrices", "[0.5, 0.5]"))
            signal = self._derive_signal(question, yes_prob, categories)

            market_id = str(m.get("id", ""))
            url = f"https://polymarket.com/event/{m.get('slug', market_id)}" if m.get("slug") else f"https://polymarket.com"

            intel = MarketIntel(
                id=market_id,
                question=question[:120],
                category=categories[0] if categories else "other",
                yes_probability=yes_prob,
                no_probability=no_prob,
                volume_usd=round(volume, 2),
                liquidity_usd=round(liquidity, 2),
                end_date=m.get("endDate", "")[:10] if m.get("endDate") else "N/A",
                active=True,
                sindicato_signal=signal,
                relevance_score=relevance,
                tags=categories[:3],
                url=url,
            )
            markets.append(intel)

        # Sort by relevance then volume
        markets.sort(key=lambda x: (x.relevance_score, x.volume_usd), reverse=True)
        print(f"[POLYMARKET] {len(markets)} mercados relevantes filtrados")
        return markets

    def get_sindicato_dashboard(self) -> dict:
        """Dashboard completo de inteligencia para el Sindicato Lansky"""
        markets = self.scan_all(min_volume=0)

        # Group by category
        dashboard = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_markets_scanned": len(markets),
            "categories": {},
            "top_signals": [],
            "sindicato_bias": {},
        }

        cat_groups = {}
        for m in markets:
            cat = m.category
            if cat not in cat_groups:
                cat_groups[cat] = []
            cat_groups[cat].append(m)

        # Build category summaries
        priority_cats = ["btc", "eth", "crypto", "gold", "silver", "metals", "oil", "energy",
                        "palantir", "stocks", "fed", "war", "macro", "geopolitics", "regulation"]

        for cat in priority_cats:
            if cat not in cat_groups:
                continue
            cat_markets = cat_groups[cat][:5]  # Top 5 per category

            # Calculate category bias
            bull_signals = sum(1 for m in cat_markets if m.sindicato_signal in ["BULLISH", "RISK_ON"])
            bear_signals = sum(1 for m in cat_markets if m.sindicato_signal in ["BEARISH", "RISK_OFF"])
            total_signals = len(cat_markets)

            if bull_signals > bear_signals:
                cat_bias = "BULLISH"
                cat_confidence = bull_signals / total_signals * 100 if total_signals else 50
            elif bear_signals > bull_signals:
                cat_bias = "BEARISH"
                cat_confidence = bear_signals / total_signals * 100 if total_signals else 50
            else:
                cat_bias = "NEUTRAL"
                cat_confidence = 50

            dashboard["categories"][cat] = {
                "bias": cat_bias,
                "confidence_pct": round(cat_confidence, 0),
                "markets_count": len(cat_groups[cat]),
                "top_markets": [asdict(m) for m in cat_markets],
            }

            dashboard["sindicato_bias"][cat] = {
                "bias": cat_bias,
                "confidence": round(cat_confidence, 0),
            }

        # Top signals overall (most relevant + highest conviction)
        high_conviction = [m for m in markets if m.sindicato_signal not in ["NEUTRAL", "UNKNOWN"] and (m.yes_probability > 60 or m.yes_probability < 40)]
        high_conviction.sort(key=lambda x: abs(x.yes_probability - 50), reverse=True)
        dashboard["top_signals"] = [asdict(m) for m in high_conviction[:10]]

        return dashboard

    def get_trade_filter(self, asset: str = "btc") -> dict:
        """Filtro de trading específico para un activo — GO/NO-GO"""
        markets = self.scan_all(min_volume=0)
        cat_keywords = CATEGORIES.get(asset.lower(), [asset.lower()])

        relevant = [m for m in markets if m.category == asset.lower()
                   or any(kw in m.question.lower() for kw in cat_keywords)][:10]

        if not relevant:
            return {"asset": asset, "filter": "NO_DATA", "reason": "Sin mercados relevantes en Polymarket", "go": True}

        bull = sum(1 for m in relevant if m.sindicato_signal == "BULLISH")
        bear = sum(1 for m in relevant if m.sindicato_signal == "BEARISH")
        neutral = len(relevant) - bull - bear

        score = (bull - bear) / len(relevant) * 100  # -100 to +100

        if score > 20:
            filter_result = "BULLISH_CONFIRMED"
            go = True
            reason = f"Polymarket: {bull} mercados alcistas vs {bear} bajistas"
        elif score < -20:
            filter_result = "BEARISH_BLOCKED"
            go = False
            reason = f"Polymarket: {bear} mercados bajistas vs {bull} alcistas — NO OPERAR LONG"
        else:
            filter_result = "NEUTRAL"
            go = True
            reason = f"Polymarket neutral ({bull} bull / {bear} bear / {neutral} neutral)"

        return {
            "asset": asset,
            "filter": filter_result,
            "go": go,
            "score": round(score, 1),
            "reason": reason,
            "markets_analyzed": len(relevant),
            "top_markets": [{"q": m.question[:80], "yes%": m.yes_probability, "signal": m.sindicato_signal} for m in relevant[:5]]
        }


# ─── REPORT FORMATTER ────────────────────────────────────────────

def print_dashboard(dashboard: dict):
    sep = "=" * 70
    sep2 = "-" * 70

    print(f"\n{sep}")
    print(f"ROTHSTEIN — POLYMARKET INTELLIGENCE HUB")
    print(f"Sindicato Lansky | {dashboard['timestamp'][:19]}")
    print(f"{sep}")
    print(f"Mercados escaneados: {dashboard['total_markets_scanned']}")
    print(f"{sep2}")

    cat_display = {
        "btc":        "BTC / BITCOIN",
        "eth":        "ETH / ETHEREUM",
        "crypto":     "CRYPTO (general)",
        "gold":       "ORO / GOLD",
        "silver":     "PLATA / SILVER",
        "metals":     "METALES PRECIOSOS",
        "oil":        "PETROLEO / OIL",
        "energy":     "ENERGIA",
        "palantir":   "PALANTIR (PLTR)",
        "stocks":     "ACCIONES / EQUITIES",
        "fed":        "FED / TIPOS DE INTERES",
        "war":        "GUERRA / GEOPOLITICA",
        "macro":      "MACRO / ECONOMIA",
        "regulation": "REGULACION CRYPTO",
    }

    for cat, label in cat_display.items():
        if cat not in dashboard["categories"]:
            continue
        data = dashboard["categories"][cat]
        bias = data["bias"]
        conf = data["confidence_pct"]
        n    = data["markets_count"]

        bias_icon = "▲" if bias == "BULLISH" else ("▼" if bias == "BEARISH" else "─")
        print(f"\n  {bias_icon} {label:25s} [{bias:8s}] {conf:.0f}% confianza | {n} mercados")

        for m in data["top_markets"][:3]:
            signal_tag = f"[{m['sindicato_signal']}]" if m["sindicato_signal"] != "NEUTRAL" else ""
            print(f"      YES={m['yes_probability']:4.0f}%  {m['question'][:60]}  {signal_tag}")

    print(f"\n{sep2}")
    print(f"TOP SEÑALES DE ALTA CONVICCION (>60% o <40%):")
    for i, m in enumerate(dashboard["top_signals"][:8], 1):
        direction = "▲" if m["sindicato_signal"] in ["BULLISH", "RISK_ON"] else "▼"
        print(f"  {i:>2}. {direction} [{m['sindicato_signal']:12s}] YES={m['yes_probability']:4.0f}%  {m['question'][:60]}")

    print(f"\n{sep}")
    print(f"SESGO GLOBAL DEL SINDICATO:")
    for cat, bias_data in dashboard.get("sindicato_bias", {}).items():
        icon = "▲" if bias_data["bias"] == "BULLISH" else ("▼" if bias_data["bias"] == "BEARISH" else "─")
        if bias_data["bias"] != "NEUTRAL":
            print(f"  {icon} {cat.upper():15s}: {bias_data['bias']:8s} ({bias_data['confidence']:.0f}%)")
    print(f"{sep}")


# ─── MAIN ────────────────────────────────────────────────────────

if __name__ == "__main__":
    hub = PolymarketIntelHub()

    if not hub.ping():
        print("[ERROR] Polymarket no disponible")
        sys.exit(1)

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if mode == "all":
        dashboard = hub.get_sindicato_dashboard()
        print_dashboard(dashboard)
        # Save JSON
        out = Path(__file__).parent
        out.mkdir(exist_ok=True)
        fname = f"polymarket_intel_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        (out / fname).write_text(json.dumps(dashboard, indent=2))
        print(f"\n[SAVED] {out / fname}")

    elif mode in CATEGORIES:
        result = hub.get_trade_filter(mode)
        print(f"\nFILTRO POLYMARKET — {mode.upper()}")
        print(f"{'='*40}")
        print(f"Resultado: {result['filter']}")
        print(f"GO/NO-GO:  {'✅ GO' if result['go'] else '🚫 NO-GO'}")
        print(f"Score:     {result['score']:+.1f}")
        print(f"Razon:     {result['reason']}")
        print(f"\nMercados analizados: {result['markets_analyzed']}")
        for m in result.get("top_markets", []):
            print(f"  YES={m['yes%']:4.0f}% | {m['signal']:12s} | {m['q']}")
        print("JSON_RESULT:" + json.dumps(result))
    else:
        print(f"Categorias disponibles: {', '.join(CATEGORIES.keys())}")
