#!/usr/bin/env python3
"""
ROTHSTEIN — Supabase Client
Sindicato Lansky | Persistencia de señales, trades, arb y divergencias

SETUP:
1. Ejecutar supabase_setup.sql en tu Supabase SQL Editor
2. Poner SUPABASE_URL y SUPABASE_KEY en config.json
3. pip install supabase --break-system-packages
"""

import json
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, List, Dict, Any

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

# Lazy import de supabase
_client = None

def _get_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}

def get_client():
    """Obtener o crear cliente Supabase (singleton)"""
    global _client
    if _client is not None:
        return _client

    config = _get_config()
    url = config.get("supabase_url") or os.environ.get("SUPABASE_URL", "")
    key = config.get("supabase_key") or os.environ.get("SUPABASE_KEY", "")

    if not url or not key:
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except ImportError:
        print("[SUPABASE] pip install supabase --break-system-packages")
        return None
    except Exception as e:
        print(f"[SUPABASE] Error: {e}")
        return None

def is_connected() -> bool:
    return get_client() is not None

# ═══════════════════════════════════════════
# SIGNALS
# ═══════════════════════════════════════════

def log_signal(signal_data: dict) -> Optional[dict]:
    """Guardar señal de trading en Supabase"""
    client = get_client()
    if not client:
        return None
    try:
        row = {
            "pair": signal_data.get("pair", "BTCUSDT"),
            "signal_type": signal_data.get("signal", "DESCONOCIDO"),
            "price": signal_data.get("price", 0),
            "ema20": signal_data.get("ema20"),
            "ema50": signal_data.get("ema50"),
            "rsi": signal_data.get("rsi"),
            "volume_increasing": signal_data.get("volume_increasing", False),
            "volume_ratio": signal_data.get("volume_ratio"),
            "confidence": signal_data.get("confidence", 0),
            "timeframe": signal_data.get("timeframe", "4h"),
            "metadata": json.dumps({k: v for k, v in signal_data.items()
                                    if k not in ["pair", "signal", "price", "ema20", "ema50", "rsi",
                                                 "volume_increasing", "volume_ratio", "confidence", "timeframe"]})
        }
        result = client.table("signals").insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[SUPABASE] Error log_signal: {e}")
        return None

def get_signals(pair: str = "BTCUSDT", limit: int = 20) -> List[dict]:
    """Obtener últimas señales"""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table("signals").select("*").eq("pair", pair).order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    except:
        return []

# ═══════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════

def log_trade(trade_data: dict) -> Optional[dict]:
    """Registrar nueva operación"""
    client = get_client()
    if not client:
        return None
    try:
        row = {
            "pair": trade_data.get("pair", "BTCUSDT"),
            "direction": trade_data.get("direction", "LONG"),
            "entry_price": trade_data.get("entry_price", 0),
            "stop_loss": trade_data.get("stop_loss"),
            "take_profit": trade_data.get("take_profit"),
            "position_size_usdt": trade_data.get("position_size", 0),
            "rr_ratio": trade_data.get("rr_ratio"),
            "signal_id": trade_data.get("signal_id"),
            "mode": trade_data.get("mode", "paper_trading"),
            "notes": trade_data.get("notes", ""),
        }
        result = client.table("trades").insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[SUPABASE] Error log_trade: {e}")
        return None

def close_trade(trade_id: int, exit_price: float, pnl_pct: float, pnl_usdt: float) -> Optional[dict]:
    """Cerrar una operación"""
    client = get_client()
    if not client:
        return None
    try:
        result = client.table("trades").update({
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", trade_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[SUPABASE] Error close_trade: {e}")
        return None

def get_trades(status: str = "all", limit: int = 50) -> List[dict]:
    """Obtener trades"""
    client = get_client()
    if not client:
        return []
    try:
        q = client.table("trades").select("*").order("created_at", desc=True).limit(limit)
        if status != "all":
            q = q.eq("status", status)
        return q.execute().data or []
    except:
        return []

def get_trade_stats() -> dict:
    """Estadísticas de trading"""
    trades = get_trades(status="closed", limit=1000)
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "avg_rr": 0}

    wins = [t for t in trades if (t.get("pnl_pct") or 0) > 0]
    total_pnl = sum(t.get("pnl_pct", 0) or 0 for t in trades)
    avg_rr = sum(t.get("rr_ratio", 0) or 0 for t in trades) / len(trades) if trades else 0

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(trades) - len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_rr": round(avg_rr, 2)
    }

# ═══════════════════════════════════════════
# ARBITRAGE
# ═══════════════════════════════════════════

def log_arb_opportunities(opportunities: List[dict]) -> int:
    """Guardar oportunidades de arbitraje"""
    client = get_client()
    if not client or not opportunities:
        return 0
    try:
        rows = [{
            "source": o.get("source", "unknown"),
            "market_id": o.get("market_id", ""),
            "question": o.get("question", "")[:200],
            "yes_price": o.get("yes_price"),
            "no_price": o.get("no_price"),
            "combined_cost": o.get("combined_cost"),
            "profit_pct": o.get("profit_pct"),
            "volume_24h": o.get("volume_24h", 0),
            "expires_at": o.get("expires"),
        } for o in opportunities]
        result = client.table("arb_opportunities").insert(rows).execute()
        return len(result.data) if result.data else 0
    except Exception as e:
        print(f"[SUPABASE] Error log_arb: {e}")
        return 0

# ═══════════════════════════════════════════
# DIVERGENCES
# ═══════════════════════════════════════════

def log_divergences(divergences: List[dict]) -> int:
    """Guardar señales de divergencia"""
    client = get_client()
    if not client or not divergences:
        return 0
    try:
        rows = [{
            "asset": d.get("asset", "BTC"),
            "market_id": d.get("market_id", ""),
            "question": d.get("question", "")[:200],
            "direction": d.get("direction"),
            "market_prob": d.get("market_prob"),
            "estimated_prob": d.get("estimated_prob"),
            "divergence_pct": d.get("divergence"),
            "signal_type": d.get("signal"),
            "confidence": d.get("confidence"),
            "rationale": d.get("rationale", "")[:500],
        } for d in divergences]
        result = client.table("divergences").insert(rows).execute()
        return len(result.data) if result.data else 0
    except Exception as e:
        print(f"[SUPABASE] Error log_divergences: {e}")
        return 0

# ═══════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════

def log_circuit_breaker(action: str, reason: str, daily_pnl: float = 0, confidence: float = 0) -> Optional[dict]:
    client = get_client()
    if not client:
        return None
    try:
        result = client.table("circuit_breaker_log").insert({
            "action": action,
            "reason": reason,
            "daily_pnl_pct": daily_pnl,
            "confidence_at_unlock": confidence
        }).execute()
        return result.data[0] if result.data else None
    except:
        return None

# ═══════════════════════════════════════════
# DAILY SNAPSHOT
# ═══════════════════════════════════════════

def save_daily_snapshot(capital: float, daily_pnl: float, cumulative_pnl: float) -> Optional[dict]:
    """Guardar snapshot diario de capital"""
    client = get_client()
    if not client:
        return None
    stats = get_trade_stats()
    today = date.today().isoformat()
    try:
        result = client.table("daily_snapshots").upsert({
            "snapshot_date": today,
            "capital_usdt": capital,
            "daily_pnl_pct": daily_pnl,
            "cumulative_pnl_pct": cumulative_pnl,
            "trades_count": stats["total"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": stats["win_rate"],
        }, on_conflict="snapshot_date").execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[SUPABASE] Error snapshot: {e}")
        return None

def get_equity_curve(days: int = 30) -> List[dict]:
    """Obtener curva de capital"""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table("daily_snapshots").select("*").order("snapshot_date", desc=True).limit(days).execute()
        return list(reversed(result.data)) if result.data else []
    except:
        return []

# ═══════════════════════════════════════════
# SUMMARY VIEW
# ═══════════════════════════════════════════

def get_today_summary() -> dict:
    """Resumen del día"""
    client = get_client()
    if not client:
        return {"connected": False}
    try:
        result = client.rpc("", {}).execute()  # fallback to manual queries
    except:
        pass

    return {
        "connected": True,
        "stats": get_trade_stats(),
        "last_signals": get_signals(limit=3),
        "open_trades": get_trades(status="open", limit=10),
        "equity_curve": get_equity_curve(7),
    }

if __name__ == "__main__":
    if is_connected():
        print("[SUPABASE] Conectado correctamente")
        summary = get_today_summary()
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("[SUPABASE] No conectado. Añade supabase_url y supabase_key a config.json")
