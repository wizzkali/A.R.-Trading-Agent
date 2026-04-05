#!/usr/bin/env python3
"""
ROTHSTEIN — Exchange Connectors
Sindicato Lansky | Polymarket + KuCoin + Binance

Cada conector tiene:
- ping()           → verificar conexión
- get_price(pair)  → precio actual
- get_orderbook()  → profundidad de mercado
- Trading: place_order(), get_balances()
"""

import json
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime
from urllib.parse import urlencode


# ═══════════════════════════════════════════════════════════════
# BINANCE CONNECTOR (ya existente, centralizado aquí)
# ═══════════════════════════════════════════════════════════════

class BinanceConnector:
    BASE_URL = "https://api.binance.com"

    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key} if api_key else {})

    def ping(self) -> dict:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v3/ping", timeout=5)
            return {"status": "online", "latency_ms": round(r.elapsed.total_seconds() * 1000)}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    def get_price(self, symbol="BTCUSDT") -> dict:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=5)
            data = r.json()
            return {"symbol": symbol, "price": float(data["price"]), "exchange": "binance"}
        except Exception as e:
            return {"error": str(e)}

    def get_orderbook(self, symbol="BTCUSDT", limit=5) -> dict:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v3/depth", params={"symbol": symbol, "limit": limit}, timeout=5)
            data = r.json()
            return {
                "symbol": symbol,
                "exchange": "binance",
                "best_bid": float(data["bids"][0][0]),
                "best_ask": float(data["asks"][0][0]),
                "spread": float(data["asks"][0][0]) - float(data["bids"][0][0]),
                "spread_pct": (float(data["asks"][0][0]) - float(data["bids"][0][0])) / float(data["asks"][0][0]) * 100,
                "bids": [[float(p), float(q)] for p, q in data["bids"][:limit]],
                "asks": [[float(p), float(q)] for p, q in data["asks"][:limit]],
            }
        except Exception as e:
            return {"error": str(e)}

    def get_klines(self, symbol="BTCUSDT", interval="4h", limit=100) -> list:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v3/klines",
                               params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
            return r.json()
        except Exception as e:
            return []


# ═══════════════════════════════════════════════════════════════
# KUCOIN CONNECTOR
# ═══════════════════════════════════════════════════════════════

class KuCoinConnector:
    BASE_URL = "https://api.kucoin.com"

    def __init__(self, api_key="", api_secret="", passphrase=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.session = requests.Session()

    def _sign(self, timestamp, method, endpoint, body=""):
        """Genera firma HMAC-SHA256 para auth KuCoin"""
        str_to_sign = f"{timestamp}{method}{endpoint}{body}"
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
        ).decode()
        passphrase_sign = base64.b64encode(
            hmac.new(self.api_secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return signature, passphrase_sign

    def _auth_headers(self, method, endpoint, body=""):
        """Headers autenticados para endpoints privados"""
        timestamp = str(int(time.time() * 1000))
        signature, passphrase_sign = self._sign(timestamp, method, endpoint, body)
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": passphrase_sign,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json"
        }

    def ping(self) -> dict:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v1/timestamp", timeout=5)
            data = r.json()
            if data.get("code") == "200000":
                return {"status": "online", "latency_ms": round(r.elapsed.total_seconds() * 1000),
                        "server_time": data["data"]}
            return {"status": "error", "detail": data}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    def get_price(self, symbol="BTC-USDT") -> dict:
        """KuCoin usa formato BTC-USDT (con guión)"""
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v1/market/orderbook/level1",
                               params={"symbol": symbol}, timeout=5)
            data = r.json()
            if data.get("code") == "200000":
                ticker = data["data"]
                return {
                    "symbol": symbol,
                    "price": float(ticker["price"]),
                    "best_bid": float(ticker["bestBid"]),
                    "best_ask": float(ticker["bestAsk"]),
                    "exchange": "kucoin"
                }
            return {"error": data.get("msg", "Unknown error")}
        except Exception as e:
            return {"error": str(e)}

    def get_orderbook(self, symbol="BTC-USDT", limit=20) -> dict:
        try:
            r = self.session.get(f"{self.BASE_URL}/api/v1/market/orderbook/level2_20",
                               params={"symbol": symbol}, timeout=5)
            data = r.json()
            if data.get("code") == "200000":
                book = data["data"]
                return {
                    "symbol": symbol,
                    "exchange": "kucoin",
                    "best_bid": float(book["bids"][0][0]),
                    "best_ask": float(book["asks"][0][0]),
                    "spread": float(book["asks"][0][0]) - float(book["bids"][0][0]),
                    "spread_pct": (float(book["asks"][0][0]) - float(book["bids"][0][0])) / float(book["asks"][0][0]) * 100,
                    "bids": [[float(p), float(q)] for p, q in book["bids"][:5]],
                    "asks": [[float(p), float(q)] for p, q in book["asks"][:5]],
                }
            return {"error": data.get("msg")}
        except Exception as e:
            return {"error": str(e)}

    def get_balances(self) -> dict:
        """Obtener balances (requiere API key)"""
        if not self.api_key:
            return {"error": "API key no configurada"}
        try:
            endpoint = "/api/v1/accounts"
            headers = self._auth_headers("GET", endpoint)
            r = self.session.get(f"{self.BASE_URL}{endpoint}", headers=headers, timeout=5)
            data = r.json()
            if data.get("code") == "200000":
                balances = {}
                for acc in data["data"]:
                    if float(acc["balance"]) > 0:
                        currency = acc["currency"]
                        if currency not in balances:
                            balances[currency] = {"available": 0, "holds": 0}
                        balances[currency]["available"] += float(acc["available"])
                        balances[currency]["holds"] += float(acc["holds"])
                return {"exchange": "kucoin", "balances": balances}
            return {"error": data.get("msg")}
        except Exception as e:
            return {"error": str(e)}

    def place_order(self, symbol, side, price, size, order_type="limit") -> dict:
        """Colocar orden (requiere API key)"""
        if not self.api_key:
            return {"error": "API key no configurada"}
        try:
            import uuid
            endpoint = "/api/v1/orders"
            body = json.dumps({
                "clientOid": str(uuid.uuid4()),
                "side": side,  # "buy" or "sell"
                "symbol": symbol,
                "type": order_type,
                "price": str(price),
                "size": str(size)
            })
            headers = self._auth_headers("POST", endpoint, body)
            r = self.session.post(f"{self.BASE_URL}{endpoint}", headers=headers, data=body, timeout=10)
            return r.json()
        except Exception as e:
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# POLYMARKET CONNECTOR
# ═══════════════════════════════════════════════════════════════

class PolymarketConnector:
    # Polymarket CLOB API (v2)
    BASE_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()

    def ping(self) -> dict:
        try:
            r = self.session.get(f"{self.GAMMA_URL}/markets?limit=1", timeout=5)
            if r.status_code == 200:
                return {"status": "online", "latency_ms": round(r.elapsed.total_seconds() * 1000)}
            return {"status": "error", "code": r.status_code}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    def get_markets(self, query="", limit=10, active=True) -> list:
        """Buscar mercados de predicción"""
        try:
            params = {"limit": limit, "active": str(active).lower()}
            if query:
                params["tag"] = query
            r = self.session.get(f"{self.GAMMA_URL}/markets", params=params, timeout=10)
            if r.status_code != 200:
                return []

            markets = r.json()
            results = []
            for m in markets:
                results.append({
                    "id": m.get("id"),
                    "question": m.get("question", ""),
                    "description": m.get("description", "")[:200],
                    "outcome_prices": m.get("outcomePrices", ""),
                    "volume": m.get("volume", 0),
                    "liquidity": m.get("liquidity", 0),
                    "end_date": m.get("endDate", ""),
                    "active": m.get("active", False),
                    "category": m.get("category", ""),
                })
            return results
        except Exception as e:
            return [{"error": str(e)}]

    def get_market(self, market_id: str) -> dict:
        """Detalle de un mercado específico"""
        try:
            r = self.session.get(f"{self.GAMMA_URL}/markets/{market_id}", timeout=5)
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def search_crypto_markets(self) -> list:
        """Buscar mercados relevantes para crypto/macro"""
        keywords = ["bitcoin", "btc", "ethereum", "fed", "interest rate", "crypto"]
        all_markets = []
        seen_ids = set()

        for kw in keywords:
            try:
                r = self.session.get(f"{self.GAMMA_URL}/markets",
                                    params={"limit": 5, "active": "true"},
                                    timeout=5)
                if r.status_code == 200:
                    for m in r.json():
                        q = m.get("question", "").lower()
                        if any(k in q for k in keywords) and m.get("id") not in seen_ids:
                            seen_ids.add(m.get("id"))
                            all_markets.append({
                                "id": m.get("id"),
                                "question": m.get("question"),
                                "outcome_prices": m.get("outcomePrices"),
                                "volume": m.get("volume", 0),
                            })
            except:
                continue

        return all_markets[:10]

    def get_orderbook(self, token_id: str) -> dict:
        """Orderbook de un mercado (CLOB API)"""
        try:
            r = self.session.get(f"{self.BASE_URL}/book",
                               params={"token_id": token_id}, timeout=5)
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# CONNECTOR MANAGER
# ═══════════════════════════════════════════════════════════════

class ConnectorManager:
    """Gestiona todos los conectores desde un punto central"""

    def __init__(self, config: dict):
        exchanges = config.get("exchanges", {})

        self.binance = BinanceConnector(
            api_key=exchanges.get("binance", {}).get("api_key", ""),
            api_secret=exchanges.get("binance", {}).get("api_secret", "")
        )
        self.kucoin = KuCoinConnector(
            api_key=exchanges.get("kucoin", {}).get("api_key", ""),
            api_secret=exchanges.get("kucoin", {}).get("api_secret", ""),
            passphrase=exchanges.get("kucoin", {}).get("passphrase", "")
        )
        self.polymarket = PolymarketConnector(
            api_key=exchanges.get("polymarket", {}).get("api_key", ""),
            api_secret=exchanges.get("polymarket", {}).get("api_secret", "")
        )

    def ping_all(self) -> dict:
        """Ping a todos los exchanges"""
        return {
            "binance": self.binance.ping(),
            "kucoin": self.kucoin.ping(),
            "polymarket": self.polymarket.ping(),
            "timestamp": datetime.utcnow().isoformat()
        }

    def get_btc_prices(self) -> dict:
        """Precio de BTC en todos los exchanges disponibles"""
        prices = {}
        prices["binance"] = self.binance.get_price("BTCUSDT")
        prices["kucoin"] = self.kucoin.get_price("BTC-USDT")
        prices["timestamp"] = datetime.utcnow().isoformat()

        # Calculate spread between exchanges
        b_price = prices["binance"].get("price")
        k_price = prices["kucoin"].get("price")
        if b_price and k_price:
            spread = abs(b_price - k_price)
            spread_pct = spread / max(b_price, k_price) * 100
            prices["cross_exchange"] = {
                "spread_usdt": round(spread, 2),
                "spread_pct": round(spread_pct, 4),
                "cheaper_on": "binance" if b_price < k_price else "kucoin",
                "arbitrage_viable": spread_pct > 0.1  # > 0.1% spread
            }

        return prices

    def get_orderbooks(self, pair_binance="BTCUSDT", pair_kucoin="BTC-USDT") -> dict:
        """Orderbooks de ambos exchanges para comparación"""
        return {
            "binance": self.binance.get_orderbook(pair_binance),
            "kucoin": self.kucoin.get_orderbook(pair_kucoin),
            "timestamp": datetime.utcnow().isoformat()
        }


# ─── SELF TEST ───
if __name__ == "__main__":
    print("ROTHSTEIN Connectors — Self-Test")
    print("=" * 40)

    # Binance (public, no key needed)
    b = BinanceConnector()
    print(f"\n[BINANCE]")
    ping = b.ping()
    print(f"  Ping: {ping['status']} ({ping.get('latency_ms', '?')}ms)")
    price = b.get_price("BTCUSDT")
    print(f"  BTC: ${price.get('price', 'error'):,.2f}")
    book = b.get_orderbook("BTCUSDT")
    print(f"  Spread: ${book.get('spread', 0):.2f} ({book.get('spread_pct', 0):.4f}%)")

    # KuCoin (public, no key needed)
    k = KuCoinConnector()
    print(f"\n[KUCOIN]")
    ping = k.ping()
    print(f"  Ping: {ping['status']} ({ping.get('latency_ms', '?')}ms)")
    price = k.get_price("BTC-USDT")
    print(f"  BTC: ${price.get('price', 'error'):,.2f}")

    # Polymarket
    p = PolymarketConnector()
    print(f"\n[POLYMARKET]")
    ping = p.ping()
    print(f"  Ping: {ping['status']} ({ping.get('latency_ms', '?')}ms)")
    markets = p.get_markets(limit=3)
    for m in markets[:3]:
        if "question" in m:
            print(f"  Market: {m['question'][:60]}...")

    # Cross-exchange
    print(f"\n[CROSS-EXCHANGE]")
    mgr = ConnectorManager({"exchanges": {"binance": {}, "kucoin": {}, "polymarket": {}}})
    prices = mgr.get_btc_prices()
    if "cross_exchange" in prices:
        ce = prices["cross_exchange"]
        print(f"  Spread BTC: ${ce['spread_usdt']} ({ce['spread_pct']}%)")
        print(f"  Más barato en: {ce['cheaper_on']}")
        print(f"  Arbitraje viable: {ce['arbitrage_viable']}")
