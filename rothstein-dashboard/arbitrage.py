#!/usr/bin/env python3
"""
ROTHSTEIN — Arbitrage Detector
Sindicato Lansky | Cross-Exchange + Triangular

Tipos de arbitraje:
1. CROSS-EXCHANGE: Comprar en exchange A, vender en exchange B
2. TRIANGULAR: BTC→ETH→USDT→BTC dentro del mismo exchange

Umbral mínimo: 0.5% (conservador) — configurable
El sistema DETECTA y ALERTA. No ejecuta automáticamente.
"""

import json
import time
import requests
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import List, Optional


# ─── CONFIG ───
DEFAULT_THRESHOLD_PCT = 0.5  # Mínimo spread para alertar
EXCHANGES_FEES = {
    "binance": {"maker": 0.1, "taker": 0.1},   # % per side
    "kucoin":  {"maker": 0.1, "taker": 0.1},
}
# Pairs to monitor (Binance format → KuCoin format)
CROSS_PAIRS = {
    "BTCUSDT":  "BTC-USDT",
    "ETHUSDT":  "ETH-USDT",
    "SOLUSDT":  "SOL-USDT",
    "BNBUSDT":  "BNB-USDT",
}
# Triangular paths (within single exchange)
TRIANGULAR_PATHS = [
    ["BTCUSDT", "ETHBTC", "ETHUSDT"],      # BTC→ETH→USDT→BTC
    ["BTCUSDT", "SOLBTC", "SOLUSDT"],       # BTC→SOL→USDT→BTC
    ["BTCUSDT", "BNBBTC", "BNBUSDT"],       # BTC→BNB→USDT→BTC
    ["ETHUSDT", "SOLETH", "SOLUSDT"],        # ETH→SOL→USDT→ETH
]


@dataclass
class ArbitrageOpportunity:
    type: str                    # "cross_exchange" or "triangular"
    timestamp: str
    pair: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_spread_pct: float
    fees_pct: float
    net_profit_pct: float
    viable: bool
    path: Optional[str] = None   # For triangular
    details: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# CROSS-EXCHANGE ARBITRAGE DETECTOR
# ═══════════════════════════════════════════════════════════════

class CrossExchangeDetector:
    """Detecta oportunidades de arbitraje entre Binance y KuCoin"""

    def __init__(self, threshold_pct=DEFAULT_THRESHOLD_PCT):
        self.threshold = threshold_pct
        self.session = requests.Session()

    def _get_binance_price(self, symbol: str) -> Optional[float]:
        try:
            r = self.session.get(f"https://api.binance.com/api/v3/ticker/bookTicker",
                               params={"symbol": symbol}, timeout=5)
            data = r.json()
            return {
                "bid": float(data["bidPrice"]),
                "ask": float(data["askPrice"]),
                "bid_qty": float(data["bidQty"]),
                "ask_qty": float(data["askQty"]),
            }
        except:
            return None

    def _get_kucoin_price(self, symbol: str) -> Optional[float]:
        try:
            r = self.session.get(f"https://api.kucoin.com/api/v1/market/orderbook/level1",
                               params={"symbol": symbol}, timeout=5)
            data = r.json()
            if data.get("code") == "200000":
                return {
                    "bid": float(data["data"]["bestBid"]),
                    "ask": float(data["data"]["bestAsk"]),
                    "bid_qty": float(data["data"].get("bestBidSize", 0)),
                    "ask_qty": float(data["data"].get("bestAskSize", 0)),
                }
        except:
            return None

    def scan(self) -> List[ArbitrageOpportunity]:
        """Escanea todos los pares configurados"""
        opportunities = []
        now = datetime.utcnow().isoformat()

        for binance_pair, kucoin_pair in CROSS_PAIRS.items():
            b = self._get_binance_price(binance_pair)
            k = self._get_kucoin_price(kucoin_pair)

            if not b or not k:
                continue

            # Dirección 1: Comprar en Binance (ask), vender en KuCoin (bid)
            if k["bid"] > b["ask"] and b["ask"] > 0:
                gross = (k["bid"] - b["ask"]) / b["ask"] * 100
                fees = EXCHANGES_FEES["binance"]["taker"] + EXCHANGES_FEES["kucoin"]["taker"]
                net = gross - fees

                opp = ArbitrageOpportunity(
                    type="cross_exchange",
                    timestamp=now,
                    pair=binance_pair,
                    buy_exchange="binance",
                    sell_exchange="kucoin",
                    buy_price=b["ask"],
                    sell_price=k["bid"],
                    gross_spread_pct=round(gross, 4),
                    fees_pct=fees,
                    net_profit_pct=round(net, 4),
                    viable=net >= self.threshold,
                    details=f"Comprar {binance_pair} en Binance @ ${b['ask']:,.2f}, vender en KuCoin @ ${k['bid']:,.2f}"
                )
                if gross > 0.01:  # Solo reportar si hay spread positivo
                    opportunities.append(opp)

            # Dirección 2: Comprar en KuCoin (ask), vender en Binance (bid)
            if b["bid"] > k["ask"] and k["ask"] > 0:
                gross = (b["bid"] - k["ask"]) / k["ask"] * 100
                fees = EXCHANGES_FEES["binance"]["taker"] + EXCHANGES_FEES["kucoin"]["taker"]
                net = gross - fees

                opp = ArbitrageOpportunity(
                    type="cross_exchange",
                    timestamp=now,
                    pair=binance_pair,
                    buy_exchange="kucoin",
                    sell_exchange="binance",
                    buy_price=k["ask"],
                    sell_price=b["bid"],
                    gross_spread_pct=round(gross, 4),
                    fees_pct=fees,
                    net_profit_pct=round(net, 4),
                    viable=net >= self.threshold,
                    details=f"Comprar {kucoin_pair} en KuCoin @ ${k['ask']:,.2f}, vender en Binance @ ${b['bid']:,.2f}"
                )
                if gross > 0.01:
                    opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x.net_profit_pct, reverse=True)


# ═══════════════════════════════════════════════════════════════
# TRIANGULAR ARBITRAGE DETECTOR
# ═══════════════════════════════════════════════════════════════

class TriangularDetector:
    """Detecta arbitraje triangular dentro de Binance"""

    def __init__(self, threshold_pct=DEFAULT_THRESHOLD_PCT):
        self.threshold = threshold_pct
        self.session = requests.Session()
        self._prices = {}

    def _fetch_all_tickers(self):
        """Obtiene todos los precios de una sola llamada (eficiente)"""
        try:
            r = self.session.get("https://api.binance.com/api/v3/ticker/bookTicker", timeout=10)
            tickers = r.json()
            self._prices = {}
            for t in tickers:
                self._prices[t["symbol"]] = {
                    "bid": float(t["bidPrice"]),
                    "ask": float(t["askPrice"]),
                }
        except Exception as e:
            print(f"Error fetching tickers: {e}")

    def _calculate_path_profit(self, path: list) -> Optional[ArbitrageOpportunity]:
        """Calcula profit de un camino triangular"""
        # Para simplificar, asumimos:
        # Path [A, B, C] = Start con USDT:
        #   1. Comprar par A (ej: BTCUSDT) → tenemos BTC
        #   2. Vender BTC por ETH (ej: ETHBTC) → tenemos ETH
        #   3. Vender ETH por USDT (ej: ETHUSDT) → tenemos USDT

        now = datetime.utcnow().isoformat()

        try:
            # Step 1: Buy first asset with USDT (usar ask)
            p1 = self._prices.get(path[0])
            if not p1 or p1["ask"] == 0:
                return None

            # Step 2: Convert via second pair
            p2 = self._prices.get(path[1])
            if not p2:
                return None

            # Step 3: Sell back to USDT (usar bid)
            p3 = self._prices.get(path[2])
            if not p3 or p3["bid"] == 0:
                return None

            # Calculate: start with 1 USDT
            # Step 1: Buy BTC with USDT → amount = 1 / ask_BTCUSDT
            btc_amount = 1.0 / p1["ask"]

            # Step 2: Depends on pair direction
            # If pair is ETHBTC → we sell BTC to get ETH → eth = btc * bid_ETHBTC... wait
            # Actually ETHBTC means 1 ETH = X BTC
            # So to convert BTC to ETH: eth = btc / ask_ETHBTC (we buy ETH with BTC)
            if path[1].endswith("BTC"):
                eth_amount = btc_amount / p2["ask"]
            else:
                eth_amount = btc_amount * p2["bid"]

            # Step 3: Sell ETH for USDT → usdt = eth * bid_ETHUSDT
            final_usdt = eth_amount * p3["bid"]

            # Apply fees (3 trades × taker fee)
            total_fees_pct = EXCHANGES_FEES["binance"]["taker"] * 3
            final_after_fees = final_usdt * (1 - total_fees_pct / 100) ** 3

            gross_profit = (final_usdt - 1.0) * 100
            net_profit = (final_after_fees - 1.0) * 100

            path_str = " → ".join(path)

            return ArbitrageOpportunity(
                type="triangular",
                timestamp=now,
                pair=path_str,
                buy_exchange="binance",
                sell_exchange="binance",
                buy_price=p1["ask"],
                sell_price=final_usdt,
                gross_spread_pct=round(gross_profit, 4),
                fees_pct=round(total_fees_pct * 3, 2),
                net_profit_pct=round(net_profit, 4),
                viable=net_profit >= self.threshold,
                path=path_str,
                details=f"${1:.4f} → {btc_amount:.8f} BTC → {eth_amount:.6f} ALT → ${final_usdt:.4f} USDT (net: ${final_after_fees:.4f})"
            )
        except Exception as e:
            return None

    def scan(self) -> List[ArbitrageOpportunity]:
        """Escanea todos los caminos triangulares"""
        self._fetch_all_tickers()

        opportunities = []
        for path in TRIANGULAR_PATHS:
            opp = self._calculate_path_profit(path)
            if opp and abs(opp.gross_spread_pct) > 0.001:
                opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x.net_profit_pct, reverse=True)


# ═══════════════════════════════════════════════════════════════
# UNIFIED ARBITRAGE SCANNER
# ═══════════════════════════════════════════════════════════════

class ArbitrageScanner:
    """Scanner unificado — detecta cross-exchange Y triangular"""

    def __init__(self, threshold_pct=DEFAULT_THRESHOLD_PCT):
        self.cross = CrossExchangeDetector(threshold_pct)
        self.triangular = TriangularDetector(threshold_pct)
        self.threshold = threshold_pct
        self.history = []  # Log de oportunidades detectadas

    def full_scan(self) -> dict:
        """Escaneo completo de ambos tipos"""
        cross_opps = self.cross.scan()
        tri_opps = self.triangular.scan()

        all_opps = cross_opps + tri_opps
        viable = [o for o in all_opps if o.viable]

        # Log viable opportunities
        for o in viable:
            self.history.append(asdict(o))

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "threshold_pct": self.threshold,
            "cross_exchange": {
                "scanned": len(CROSS_PAIRS),
                "opportunities": [asdict(o) for o in cross_opps],
                "viable_count": len([o for o in cross_opps if o.viable])
            },
            "triangular": {
                "paths_scanned": len(TRIANGULAR_PATHS),
                "opportunities": [asdict(o) for o in tri_opps],
                "viable_count": len([o for o in tri_opps if o.viable])
            },
            "summary": {
                "total_opportunities": len(all_opps),
                "viable_opportunities": len(viable),
                "best_opportunity": asdict(viable[0]) if viable else None,
                "alert": len(viable) > 0
            }
        }


# ─── SELF TEST ───
if __name__ == "__main__":
    print("ROTHSTEIN Arbitrage Scanner — Full Scan")
    print("=" * 50)
    print(f"Umbral mínimo: {DEFAULT_THRESHOLD_PCT}%")
    print()

    scanner = ArbitrageScanner(threshold_pct=DEFAULT_THRESHOLD_PCT)
    result = scanner.full_scan()

    # Cross-exchange results
    print("CROSS-EXCHANGE ARBITRAGE:")
    print("-" * 40)
    for opp in result["cross_exchange"]["opportunities"]:
        status = "✅ VIABLE" if opp["viable"] else "❌"
        print(f"  {opp['pair']:10s} | Buy {opp['buy_exchange']:8s} @ ${opp['buy_price']:>10,.2f} | "
              f"Sell {opp['sell_exchange']:8s} @ ${opp['sell_price']:>10,.2f} | "
              f"Gross: {opp['gross_spread_pct']:+.4f}% | Net: {opp['net_profit_pct']:+.4f}% | {status}")

    if not result["cross_exchange"]["opportunities"]:
        print("  No se detectaron spreads significativos entre exchanges")

    # Triangular results
    print(f"\nTRIANGULAR ARBITRAGE (Binance):")
    print("-" * 40)
    for opp in result["triangular"]["opportunities"]:
        status = "✅ VIABLE" if opp["viable"] else "❌"
        print(f"  {opp['path']:30s} | Gross: {opp['gross_spread_pct']:+.4f}% | "
              f"Net: {opp['net_profit_pct']:+.4f}% | {status}")

    if not result["triangular"]["opportunities"]:
        print("  No se detectaron oportunidades triangulares")

    # Summary
    print(f"\nRESUMEN:")
    print(f"  Oportunidades totales: {result['summary']['total_opportunities']}")
    print(f"  Viables (>{DEFAULT_THRESHOLD_PCT}% net): {result['summary']['viable_opportunities']}")
    if result["summary"]["best_opportunity"]:
        best = result["summary"]["best_opportunity"]
        print(f"  Mejor: {best['type']} | {best['pair']} | Net: {best['net_profit_pct']}%")
    else:
        print(f"  Mejor: Ninguna oportunidad viable en este momento")
    print(f"\n  NOTA: El mercado crypto tiene spreads muy ajustados.")
    print(f"  Las oportunidades de arbitraje son raras y fugaces.")
    print(f"  El Sindicato gestiona probabilidades, no apuesta.")
