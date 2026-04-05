#!/usr/bin/env python3
"""
ROTHSTEIN — Polymarket YES/NO Book Arbitrage Scanner
Sindicato Lansky | CLOB API pública (sin auth)

Estrategia: Cuando YES_price + NO_price < 0.97 → oportunidad de book arb
(pagan $1.00 al vencer, combinación cuesta < $0.97 → profit garantizado neto de fees)

Bonus: Cross-arb Polymarket vs Kalshi en mismo contrato BTC/ETH
"""

import requests
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Optional

CLOB_URL = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Threshold mínimo de ganancia neta después de fees (~2% taker round-trip)
MIN_PROFIT_PCT = 2.5   # % ganancia mínima para reportar
MAX_CONTRACTS = 50     # máx contratos a escanear por ronda

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

@dataclass
class ArbOpportunity:
    market_id: str
    question: str
    yes_price: float
    no_price: float
    combined_cost: float
    profit_pct: float
    volume_24h: float
    liquidity: float
    expires: Optional[str]
    source: str  # "polymarket_book" o "cross_kalshi"
    timestamp: str

def get_active_crypto_markets(limit: int = MAX_CONTRACTS) -> List[dict]:
    """Obtener mercados crypto activos de Gamma API"""
    try:
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "tag_slug": "crypto",
            "order": "volume24hr",
            "ascending": "false"
        }
        r = requests.get(f"{GAMMA_URL}/markets", params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        # Gamma returns list directly or wrapped
        if isinstance(data, list):
            return data
        return data.get("markets", [])
    except Exception as e:
        # Fallback: buscar mercados BTC/ETH/SOL sin filtro tag
        try:
            r = requests.get(f"{GAMMA_URL}/markets", params={"limit": limit, "active": "true", "closed": "false"}, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                markets = data
            else:
                markets = data.get("markets", [])
            # Filtrar por keywords crypto
            keywords = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "price"]
            return [m for m in markets if any(k in m.get("question", "").lower() for k in keywords)]
        except:
            return []

def get_token_prices(market: dict) -> tuple:
    """Obtener precios YES/NO de un mercado via CLOB"""
    yes_price = no_price = None

    # Los tokens están en el campo tokens o clob_token_ids
    tokens = market.get("tokens", [])
    if not tokens:
        # Try clobTokenIds
        token_ids = market.get("clobTokenIds", [])
        if len(token_ids) >= 2:
            tokens = [{"token_id": token_ids[0], "outcome": "Yes"},
                      {"token_id": token_ids[1], "outcome": "No"}]

    for token in tokens:
        token_id = token.get("token_id") or token.get("tokenId", "")
        outcome = token.get("outcome", "")
        if not token_id:
            continue
        try:
            r = requests.get(
                f"{CLOB_URL}/midpoint",
                params={"token_id": token_id},
                headers=HEADERS,
                timeout=5
            )
            if r.status_code == 200:
                mid = r.json().get("mid", None)
                if mid is not None:
                    price = float(mid)
                    if "yes" in outcome.lower():
                        yes_price = price
                    elif "no" in outcome.lower():
                        no_price = price
        except:
            pass

    # Fallback: usar lastTradePrice del market data
    if yes_price is None and "outcomePrices" in market:
        try:
            prices = json.loads(market["outcomePrices"]) if isinstance(market["outcomePrices"], str) else market["outcomePrices"]
            if len(prices) >= 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
        except:
            pass

    return yes_price, no_price

def scan_book_arb() -> List[ArbOpportunity]:
    """Escanear oportunidades YES+NO < threshold en Polymarket"""
    opportunities = []
    markets = get_active_crypto_markets()

    if not markets:
        return []

    for market in markets:
        try:
            yes_price, no_price = get_token_prices(market)
            if yes_price is None or no_price is None:
                continue
            if yes_price <= 0 or no_price <= 0:
                continue

            combined = yes_price + no_price
            # Profit = (1 - combined) / combined * 100
            # Net después de fees (≈2% round-trip taker)
            gross_profit = (1.0 - combined) * 100
            net_profit = gross_profit - 2.0  # descontar fees estimadas

            if net_profit >= MIN_PROFIT_PCT:
                volume = float(market.get("volume24hr", 0) or 0)
                liquidity = float(market.get("liquidity", 0) or 0)
                end_date = market.get("endDate") or market.get("end_date_iso")

                opp = ArbOpportunity(
                    market_id=market.get("id", ""),
                    question=market.get("question", "")[:80],
                    yes_price=round(yes_price, 4),
                    no_price=round(no_price, 4),
                    combined_cost=round(combined, 4),
                    profit_pct=round(net_profit, 2),
                    volume_24h=round(volume, 2),
                    liquidity=round(liquidity, 2),
                    expires=str(end_date)[:10] if end_date else None,
                    source="polymarket_book",
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                opportunities.append(opp)
        except:
            continue

    # Ordenar por profit descendente
    opportunities.sort(key=lambda x: x.profit_pct, reverse=True)
    return opportunities

def scan_cross_arb_kalshi() -> List[ArbOpportunity]:
    """Buscar mismo mercado en Polymarket y Kalshi con precios distintos"""
    opportunities = []
    try:
        # Obtener mercados BTC de Kalshi
        r = requests.get(
            f"{KALSHI_URL}/markets",
            params={"limit": 20, "status": "open", "ticker": "KXBTC"},
            headers=HEADERS,
            timeout=8
        )
        if r.status_code != 200:
            return []

        kalshi_markets = r.json().get("markets", [])
        poly_markets = get_active_crypto_markets(20)

        for km in kalshi_markets:
            km_title = km.get("title", "").lower()
            km_yes = float(km.get("yes_ask", 0) or 0)
            km_no = float(km.get("no_ask", 0) or 0)

            if km_yes <= 0:
                continue

            # Buscar mercado similar en Polymarket
            for pm in poly_markets:
                pm_q = pm.get("question", "").lower()
                if "bitcoin" not in pm_q and "btc" not in pm_q:
                    continue

                pm_yes, pm_no = get_token_prices(pm)
                if pm_yes is None:
                    continue

                # Arb: comprar YES en el más barato, vender en el más caro
                if km_yes < pm_yes:
                    spread = pm_yes - km_yes
                elif pm_yes < km_yes:
                    spread = km_yes - pm_yes
                else:
                    continue

                net_profit = (spread - 0.02) * 100  # descontar fees

                if net_profit >= MIN_PROFIT_PCT:
                    opp = ArbOpportunity(
                        market_id=km.get("ticker", ""),
                        question=f"CROSS: {pm_q[:40]} | {km_title[:40]}",
                        yes_price=pm_yes,
                        no_price=km_yes,
                        combined_cost=round(pm_yes + km_yes, 4),
                        profit_pct=round(net_profit, 2),
                        volume_24h=0,
                        liquidity=0,
                        expires=km.get("close_time", "")[:10],
                        source="cross_kalshi",
                        timestamp=datetime.now(timezone.utc).isoformat()
                    )
                    opportunities.append(opp)
    except:
        pass

    return opportunities

def run_full_scan(mode: str = "all") -> dict:
    """Ejecutar escaneo completo"""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "book_arb": [],
        "cross_arb": [],
        "total_opportunities": 0,
        "best_opportunity": None
    }

    if mode in ("all", "book"):
        book = scan_book_arb()
        results["book_arb"] = [asdict(o) for o in book]

    if mode in ("all", "cross"):
        cross = scan_cross_arb_kalshi()
        results["cross_arb"] = [asdict(o) for o in cross]

    all_opps = results["book_arb"] + results["cross_arb"]
    results["total_opportunities"] = len(all_opps)

    if all_opps:
        best = max(all_opps, key=lambda x: x["profit_pct"])
        results["best_opportunity"] = best

    return results

def print_report(results: dict):
    print(f"\n{'='*60}")
    print(f"  ROTHSTEIN — POLYMARKET ARB SCANNER")
    print(f"  {results['timestamp']}")
    print(f"{'='*60}")
    print(f"  Oportunidades encontradas: {results['total_opportunities']}")

    all_opps = results["book_arb"] + results["cross_arb"]
    for opp in sorted(all_opps, key=lambda x: x["profit_pct"], reverse=True)[:5]:
        print(f"\n  [{opp['source'].upper()}] {opp['profit_pct']}% NET PROFIT")
        print(f"  Mercado: {opp['question']}")
        print(f"  YES={opp['yes_price']} | NO={opp['no_price']} | SUMA={opp['combined_cost']}")
        print(f"  Vol 24h: ${opp['volume_24h']:,} | Liquidity: ${opp['liquidity']:,}")
        print(f"  Expira: {opp['expires']}")

    if not all_opps:
        print("  Sin oportunidades de arbitraje en este momento.")
        print("  (Mercado eficiente o fees > spread)")

    print(f"\n{'='*60}")
    print(f"JSON_RESULT:{json.dumps(results)}")

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = run_full_scan(mode)
    print_report(results)
