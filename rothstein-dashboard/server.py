#!/usr/bin/env python3
"""
ROTHSTEIN Dashboard v6 — Backend Server
Sindicato Lansky | FastAPI + API Key Auth

MEJORAS v6:
  - ELIMINADOS todos los subprocess.run() → imports directos (10x mas rapido)
  - Usa technical_lib.py para todos los calculos
  - Usa multi_signal_v4.py para analisis compuesto
  - Usa position_manager.py para gestion de posiciones
  - Health check paralelo (no secuencial)
  - Endpoint /api/technical/{pair} con TODOS los indicadores
  - Endpoint /api/position/plan para planificar entradas
  - Endpoint /api/position/check para validar posiciones abiertas
  - Error handling tipificado (no bare except)
  - Auto-refresh headers para dashboard
"""

import json, os, sys, time, hashlib, secrets, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

# Cargar .env
load_dotenv()

# Validación temprana de entorno (Fast fail)
required_env = ["BYBIT_API_KEY", "BYBIT_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
missing_env = [var for var in required_env if not os.getenv(var)]
if missing_env:
    print(f"ERROR: Faltan variables de entorno: {', '.join(missing_env)}")
    print("Asegúrate de tener un archivo .env en rothstein-dashboard/ con esas claves.")
    sys.exit(1)

# ─── DIRECT IMPORTS (no more subprocess!) ───
from technical_lib import (
    full_technical_analysis, indicator_consensus,
    fetch_binance_klines, fetch_fear_greed, fetch_ticker_price,
    check_api_health, calc_ema, calc_rsi, calc_atr
)
from multi_signal_v4 import analyze_v4
from position_manager import plan_entry, check_signal_invalidation

# ─── CONFIG ───
BASE_DIR = Path(__file__).parent
import sys

def validate_config(cfg: dict):
    required_keys = ["ema_fast", "ema_slow", "rsi_period", "atr_period", "sl_atr_mult", "tp_sl_ratio", "min_score"]
    for k in required_keys:
        if k not in cfg:
            print(f"[FATAL ERROR] Falta la clave requerida '{k}' en config.json")
            sys.exit(1)
    
    if cfg["ema_fast"] >= cfg["ema_slow"]:
        print("[FATAL ERROR] 'ema_fast' debe ser menor que 'ema_slow'. Revisa tu config.json")
        sys.exit(1)
        
    if not (1 <= cfg["min_score"] <= 11):
        print("[FATAL ERROR] 'min_score' debe estar entre 1 y 11. Revisa tu config.json")
        sys.exit(1)

try:
    CONFIG = json.loads((BASE_DIR / "config.json").read_text())
    validate_config(CONFIG)
except FileNotFoundError:
    print("[FATAL ERROR] Archivo config.json no encontrado.")
    sys.exit(1)
except json.JSONDecodeError:
    print("[FATAL ERROR] Archivo config.json tiene un formato JSON inválido.")
    sys.exit(1)

API_KEY = CONFIG.get("api_key", "DEFAULT_KEY")
PORT = CONFIG.get("port", 8080)
VERSION = "6.0.0 (V20 EMA)"

# ─── SUPABASE MODULE ───
SUPABASE_ENABLED = False
sb = None
try:
    import supabase_client as sb
    SUPABASE_ENABLED = sb.is_connected()
    print(f"[SUPABASE] {'Conectado' if SUPABASE_ENABLED else 'No configurado'}")
except ImportError:
    print("[SUPABASE] Modulo no disponible")

# ─── SECURITY MODULE ───
SECURITY_ENABLED = False
CERT_FILE = KEY_FILE = None
try:
    from security import create_security_middleware, sanitize_pair, sanitize_number
    SECURITY_ENABLED = True
    print("[SECURITY] Modulo cargado")
except ImportError:
    print("[SECURITY] No disponible")

# ─── STATE ───
STATE_FILE = BASE_DIR / "state.json"
DEFAULT_STATE = {
    "circuit_breaker": {
        "locked": False, "locked_at": None, "reason": None,
        "daily_pnl_pct": 0.0, "unlock_history": []
    },
    "system": {
        "paused": False, "paused_at": None, "mode": "paper_trading",
        "paper_trades_completed": 0, "paper_trades_target": 20
    },
    "last_signal": None, "last_macro_check": None,
    "last_fear_greed": None, "trade_log": [], "daily_pnl": [],
    "open_positions": []
}

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return DEFAULT_STATE.copy()
    return DEFAULT_STATE.copy()

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

state = load_state()

# ─── APP ───
app = FastAPI(title="Rothstein Dashboard", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

if SECURITY_ENABLED:
    CERT_FILE, KEY_FILE = create_security_middleware(app, str(BASE_DIR / "config.json"))

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

# Thread pool para health checks paralelos
executor = ThreadPoolExecutor(max_workers=4)

# ═══════════════════════════════════════════
# CORE ENDPOINTS
# ═══════════════════════════════════════════

@app.get("/")
async def serve_dashboard():
    return FileResponse(BASE_DIR / "static" / "index.html")

@app.get("/api/status", dependencies=[Depends(verify_api_key)])
async def get_status():
    return {
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": state["system"],
        "circuit_breaker": state["circuit_breaker"],
        "last_signal": state["last_signal"],
        "last_fear_greed": state["last_fear_greed"],
        "config": {
            "capital": CONFIG["capital_usdt"],
            "daily_loss_limit": CONFIG["daily_loss_limit_pct"],
            "max_position": CONFIG["max_position_pct"],
            "min_rr": CONFIG["min_rr_ratio"],
            "confidence_unlock": CONFIG["confidence_auto_unlock_pct"]
        },
        "connections": check_connections_parallel(),
        "trade_stats": calculate_stats()
    }

# ═══════════════════════════════════════════
# SIGNAL ENDPOINTS (v6: direct import, no subprocess)
# ═══════════════════════════════════════════

@app.get("/api/signal/{pair}", dependencies=[Depends(verify_api_key)])
async def get_signal(pair: str = "BTCUSDT"):
    """Senal EMA 20/50 — v6: calculo directo sin subprocess"""
    try:
        pair = pair.upper()
        data = fetch_binance_klines(pair, "4h", 60)
        if not data.get("ok"):
            raise HTTPException(status_code=502, detail="Binance API unavailable")

        closes = data["closes"]
        volumes = data["volumes"]
        
        efast_val = CONFIG["ema_fast"]
        eslow_val = CONFIG["ema_slow"]

        ema_fast_list = calc_ema(closes, efast_val)
        ema_slow_list = calc_ema(closes, eslow_val)
        rsi = calc_rsi(closes)
        price = closes[-1]
        e_fast, e_slow = ema_fast_list[-1], ema_slow_list[-1]

        if not e_fast or not e_slow:
            raise HTTPException(status_code=500, detail="Insufficient data for EMAs")

        # Volume
        vol_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        vol_increasing = volumes[-1] > vol_avg

        # Signal logic
        prev_e_fast = ema_fast_list[-2] if len(ema_fast_list) >= 2 and ema_fast_list[-2] else e_fast
        prev_e_slow = ema_slow_list[-2] if len(ema_slow_list) >= 2 and ema_slow_list[-2] else e_slow

        if e_fast > e_slow and prev_e_fast <= prev_e_slow:
            signal, signal_type = "COMPRAR", "GOLDEN_CROSS"
        elif e_fast < e_slow and prev_e_fast >= prev_e_slow:
            signal, signal_type = "VENDER", "DEATH_CROSS"
        elif e_fast > e_slow:
            signal, signal_type = "MANTENER_LONG", "TENDENCIA_ALCISTA"
        elif e_fast < e_slow:
            signal, signal_type = "FUERA", "TENDENCIA_BAJISTA"
        else:
            signal, signal_type = "ESPERAR", "NEUTRAL"

        # Confidence
        score = 0
        if signal in ("COMPRAR", "MANTENER_LONG"):
            if rsi < 70: score += 25
            if rsi > 30: score += 10
            if vol_increasing: score += 20
            if price > e_fast > e_slow: score += 25
            if abs(e_fast - e_slow) / e_slow * 100 > 0.5: score += 20
        elif signal in ("VENDER", "FUERA"):
            if rsi > 30: score += 25
            if rsi < 70: score += 10
            if vol_increasing: score += 20
            if price < e_fast < e_slow: score += 25
            if abs(e_fast - e_slow) / e_slow * 100 > 0.5: score += 20

        signal_data = {
            "pair": pair, "price": round(price, 2),
            "signal": signal, "signal_type": signal_type,
            "ema_fast": round(e_fast, 2), "ema_slow": round(e_slow, 2),
            "rsi": round(rsi, 2), "volume_increasing": vol_increasing,
            "volume_ratio": round(volumes[-1] / vol_avg, 2) if vol_avg > 0 else 1,
            "confidence": min(score, 100),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        state["last_signal"] = signal_data

        # Auto-unlock circuit breaker
        if (state["circuit_breaker"]["locked"] and
            signal_data["confidence"] >= CONFIG["confidence_auto_unlock_pct"]):
            state["circuit_breaker"]["locked"] = False
            state["circuit_breaker"]["unlock_history"].append({
                "time": datetime.now(timezone.utc).isoformat(),
                "method": "auto_confidence",
                "confidence": signal_data["confidence"]
            })
            if SUPABASE_ENABLED and sb:
                try:
                    sb.log_circuit_breaker("auto_unlock", "confidence_threshold", confidence=signal_data["confidence"])
                except Exception:
                    pass

        save_state(state)
        return signal_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/fear-greed", dependencies=[Depends(verify_api_key)])
async def get_fear_greed_endpoint():
    try:
        fg = fetch_fear_greed()
        state["last_fear_greed"] = fg
        save_state(state)
        return fg
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fear & Greed API: {e}")

# ═══════════════════════════════════════════
# TECHNICAL ANALYSIS v6 (NEW — all indicators)
# ═══════════════════════════════════════════

@app.get("/api/technical/{pair}", dependencies=[Depends(verify_api_key)])
async def get_technical(pair: str = "BTCUSDT"):
    """TODOS los indicadores tecnicos en una sola llamada"""
    try:
        analysis = full_technical_analysis(pair.upper(), CONFIG["ema_fast"], CONFIG["ema_slow"])
        if not analysis.get("ok"):
            raise HTTPException(status_code=502, detail=analysis.get("error", "Data error"))

        consensus = indicator_consensus(analysis)

        # Remove raw data arrays
        safe = {k: v for k, v in analysis.items() if not k.startswith("_")}
        safe["consensus"] = consensus
        return safe
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# FULL ANALYSIS v4 (composite multi-signal)
# ═══════════════════════════════════════════

@app.get("/api/full-analysis/{pair}", dependencies=[Depends(verify_api_key)])
async def full_analysis(pair: str = "BTCUSDT"):
    """Analisis compuesto v4: 9 indicadores + consenso"""
    try:
        data = analyze_v4(pair.upper(), CONFIG["ema_fast"], CONFIG["ema_slow"])

        # Persist to Supabase
        if SUPABASE_ENABLED and sb:
            try:
                sb.log_signal({
                    "pair": pair, "signal": data.get("verdict", ""),
                    "price": data.get("market", {}).get("price", 0),
                    "ema_fast": data.get("market", {}).get("ema_fast"),
                    "ema_slow": data.get("market", {}).get("ema_slow"),
                    "rsi": data.get("market", {}).get("rsi_4h"),
                    "confidence": data.get("confidence", 0),
                })
            except Exception:
                pass

        # Dashboard-compatible format
        return {
            "pair": pair, "version": "v4",
            "timestamp": data.get("timestamp"),
            "composite": {
                "score": data.get("composite_score", 50),
                "verdict": data.get("verdict", "NEUTRAL")
            },
            "consensus": data.get("consensus", {}),
            "modules": {
                "ema_signal": {
                    "signal": data.get("verdict"),
                    "price": data.get("market", {}).get("price"),
                    "ema_fast": data.get("market", {}).get("ema_fast"),
                    "ema_slow": data.get("market", {}).get("ema_slow"),
                    "rsi": data.get("market", {}).get("rsi_4h"),
                    "confidence": data.get("confidence")
                },
                "fear_greed": {
                    "value": data.get("market", {}).get("fear_greed"),
                },
                "macd": {
                    "line": data.get("market", {}).get("macd_line"),
                    "signal": data.get("market", {}).get("macd_signal"),
                    "crossover": data.get("market", {}).get("macd_crossover"),
                },
                "bollinger": {
                    "pct_b": data.get("market", {}).get("bollinger_pct_b"),
                    "squeeze": data.get("market", {}).get("bollinger_squeeze"),
                },
                "obv": {
                    "divergence": data.get("market", {}).get("obv_divergence"),
                }
            },
            "components": data.get("components", {}),
            "weights": data.get("weights", {}),
            "rationale": data.get("rationale", []),
            "confidence": data.get("confidence", 0)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# POSITION MANAGEMENT v5 (NEW)
# ═══════════════════════════════════════════

@app.get("/api/position/plan/{pair}", dependencies=[Depends(verify_api_key)])
async def position_plan(pair: str = "BTCUSDT", direction: str = "LONG"):
    """Planificar entrada: sizing, stops, targets basados en ATR"""
    try:
        plan = plan_entry(
            pair.upper(), direction.upper(),
            capital=CONFIG["capital_usdt"],
            win_rate=0.55, avg_rr=2.0
        )
        if not plan.get("ok"):
            raise HTTPException(status_code=500, detail=plan.get("error", "Plan failed"))
        return plan
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/position/check/{pair}", dependencies=[Depends(verify_api_key)])
async def position_check(pair: str = "BTCUSDT", direction: str = "LONG"):
    """Validar si una posicion abierta sigue siendo valida"""
    try:
        check = check_signal_invalidation(pair.upper(), direction.upper())
        return check
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════

@app.post("/api/circuit-breaker/unlock", dependencies=[Depends(verify_api_key)])
async def unlock_circuit_breaker():
    if not state["circuit_breaker"]["locked"]:
        return {"message": "No esta bloqueado", "locked": False}
    state["circuit_breaker"]["locked"] = False
    state["circuit_breaker"]["unlock_history"].append({
        "time": datetime.now(timezone.utc).isoformat(), "method": "manual"
    })
    if SUPABASE_ENABLED and sb:
        try: sb.log_circuit_breaker("unlock", "manual")
        except Exception: pass
    save_state(state)
    return {"message": "Desbloqueado manualmente", "locked": False}

@app.post("/api/circuit-breaker/lock", dependencies=[Depends(verify_api_key)])
async def lock_circuit_breaker():
    state["circuit_breaker"]["locked"] = True
    state["circuit_breaker"]["locked_at"] = datetime.now(timezone.utc).isoformat()
    state["circuit_breaker"]["reason"] = "manual_lock"
    if SUPABASE_ENABLED and sb:
        try: sb.log_circuit_breaker("lock", "manual")
        except Exception: pass
    save_state(state)
    return {"message": "Bloqueado manualmente", "locked": True}

@app.post("/api/circuit-breaker/simulate-loss", dependencies=[Depends(verify_api_key)])
async def simulate_loss(pnl_pct: float = -15.0):
    state["circuit_breaker"]["daily_pnl_pct"] = pnl_pct
    if pnl_pct <= -CONFIG["daily_loss_limit_pct"]:
        state["circuit_breaker"]["locked"] = True
        state["circuit_breaker"]["locked_at"] = datetime.now(timezone.utc).isoformat()
        state["circuit_breaker"]["reason"] = f"daily_loss_{pnl_pct}%"
    save_state(state)
    return {"daily_pnl_pct": pnl_pct, "locked": state["circuit_breaker"]["locked"]}

# ═══════════════════════════════════════════
# SYSTEM / TRADES
# ═══════════════════════════════════════════

@app.post("/api/system/pause", dependencies=[Depends(verify_api_key)])
async def pause_system():
    state["system"]["paused"] = not state["system"]["paused"]
    state["system"]["paused_at"] = datetime.now(timezone.utc).isoformat() if state["system"]["paused"] else None
    save_state(state)
    return {"paused": state["system"]["paused"]}

@app.post("/api/trade/log", dependencies=[Depends(verify_api_key)])
async def log_trade(request: Request):
    trade = await request.json()
    trade["id"] = len(state["trade_log"]) + 1
    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
    state["trade_log"].append(trade)
    state["system"]["paper_trades_completed"] = len(state["trade_log"])
    if SUPABASE_ENABLED and sb:
        try: sb.log_trade(trade)
        except Exception: pass
    save_state(state)
    return trade

@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
async def get_trades():
    return state["trade_log"]

@app.post("/api/state/reset", dependencies=[Depends(verify_api_key)])
async def reset_state():
    global state
    state = DEFAULT_STATE.copy()
    save_state(state)
    return {"message": "Estado reseteado"}

# ═══════════════════════════════════════════
# WEEKLY TREND (v6: direct, no subprocess)
# ═══════════════════════════════════════════

@app.get("/api/weekly-trend/{pair}", dependencies=[Depends(verify_api_key)])
async def get_weekly_trend(pair: str = "BTCUSDT"):
    try:
        pair = pair.upper()
        data = fetch_binance_klines(pair, "1w", 80)
        if not data.get("ok"):
            raise HTTPException(status_code=502, detail="Binance weekly data unavailable")

        closes = data["closes"]
        e21 = calc_ema(closes, 21)
        e55 = calc_ema(closes, 55)
        trend = "BULL" if (e21[-1] and e55[-1] and e21[-1] > e55[-1]) else "BEAR"

        return {
            "pair": pair, "price": round(closes[-1], 2),
            "ema21w": round(e21[-1], 2) if e21[-1] else None,
            "ema55w": round(e55[-1], 2) if e55[-1] else None,
            "weekly_trend": trend,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# POLYMARKET (still subprocess — separate module)
# ═══════════════════════════════════════════

@app.get("/api/polymarket/arb", dependencies=[Depends(verify_api_key)])
async def get_polymarket_arb(mode: str = "all"):
    try:
        import subprocess
        result = subprocess.run(
            ["python3", str(BASE_DIR / "polymarket_arb.py"), mode],
            capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR)
        )
        for line in reversed(result.stdout.split("\n")):
            if line.startswith("JSON_RESULT:"):
                return json.loads(line[12:])
        raise Exception(result.stderr[:300] or "No output")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/polymarket/divergence/{asset}", dependencies=[Depends(verify_api_key)])
async def get_polymarket_divergence(asset: str = "btc"):
    try:
        import subprocess
        result = subprocess.run(
            ["python3", str(BASE_DIR / "polymarket_signal.py"), asset.lower()],
            capture_output=True, text=True, timeout=90, cwd=str(BASE_DIR)
        )
        for line in reversed(result.stdout.split("\n")):
            if line.startswith("JSON_RESULT:"):
                return json.loads(line[12:])
        raise Exception(result.stderr[:300] or "No output")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# BACKTEST
# ═══════════════════════════════════════════

@app.get("/api/backtest", dependencies=[Depends(verify_api_key)])
async def run_backtest(pair: str = "BTCUSDT", days: int = 365):
    try:
        import subprocess
        result = subprocess.run(
            ["python3", str(BASE_DIR / "backtest_v3.py"), pair, str(days)],
            capture_output=True, text=True, timeout=120, cwd=str(BASE_DIR)
        )
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        for line in reversed(lines):
            try: return json.loads(line)
            except ValueError: continue
        return {"status": "completed", "output": result.stdout[-1000:]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════
# ARBITRAGE / SECURITY
# ═══════════════════════════════════════════

@app.get("/api/arbitrage/{mode}", dependencies=[Depends(verify_api_key)])
async def get_arbitrage(mode: str = "cross"):
    try:
        from arbitrage import ArbitrageScanner, CrossExchangeDetector, TriangularDetector
        scanner = ArbitrageScanner()
        det = TriangularDetector() if mode == "triangular" else CrossExchangeDetector()
        opps = det.scan()
        return {
            "mode": mode, "timestamp": datetime.now(timezone.utc).isoformat(),
            "opportunities": [o.__dict__ if hasattr(o, '__dict__') else o for o in opps],
            "count": len(opps)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/arbitrage", dependencies=[Depends(verify_api_key)])
async def get_arbitrage_default():
    return await get_arbitrage("cross")

@app.get("/api/security", dependencies=[Depends(verify_api_key)])
async def get_security_report():
    if SECURITY_ENABLED:
        from security import generate_security_report
        return generate_security_report()
    return {"status": "DISABLED"}

# ═══════════════════════════════════════════
# SUPABASE
# ═══════════════════════════════════════════

@app.get("/api/supabase/status", dependencies=[Depends(verify_api_key)])
async def supabase_status():
    return {"connected": SUPABASE_ENABLED}

@app.get("/api/supabase/stats", dependencies=[Depends(verify_api_key)])
async def supabase_stats():
    if not SUPABASE_ENABLED or not sb:
        return {"connected": False}
    return {"connected": True, "stats": sb.get_trade_stats()}

@app.get("/api/supabase/equity", dependencies=[Depends(verify_api_key)])
async def supabase_equity(days: int = 30):
    if not SUPABASE_ENABLED or not sb:
        return {"connected": False}
    return {"connected": True, "equity_curve": sb.get_equity_curve(days)}

# ═══════════════════════════════════════════
# HEALTH CHECK (v6: parallel, fast)
# ═══════════════════════════════════════════

@app.get("/api/health", dependencies=[Depends(verify_api_key)])
async def health_check():
    """Health check rapido de todos los servicios"""
    conns = check_connections_parallel()
    all_ok = all(c.get("status") == "online" for c in conns.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "version": VERSION,
        "connections": conns,
        "uptime": time.time()
    }

def check_connections_parallel() -> dict:
    """Health checks en paralelo (4x mas rapido que secuencial)"""
    checks = {
        "binance": ("https://api.binance.com/api/v3/ping", CONFIG["exchanges"]["binance"]["enabled"]),
        "kucoin": ("https://api.kucoin.com/api/v1/timestamp", CONFIG["exchanges"]["kucoin"]["enabled"]),
        "polymarket": ("https://gamma-api.polymarket.com/markets?limit=1&closed=false", CONFIG["exchanges"]["polymarket"]["enabled"]),
        "fear_greed": ("https://api.alternative.me/fng/", True),
    }

    results = {}
    futures = {}

    for name, (url, enabled) in checks.items():
        futures[executor.submit(check_api_health, url, 5)] = (name, enabled)

    for future in as_completed(futures, timeout=8):
        name, enabled = futures[future]
        try:
            ok = future.result()
            results[name] = {"status": "online" if ok else "offline", "enabled": enabled}
        except Exception:
            results[name] = {"status": "offline", "enabled": enabled}

    return results

def calculate_stats():
    trades = state["trade_log"]
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0}
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    return {
        "total": len(trades), "wins": len(wins),
        "losses": len(trades) - len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2)
    }

# ═══════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════
if __name__ == "__main__":
    protocol = "https" if (SECURITY_ENABLED and CERT_FILE) else "http"
    print(f"\n{'='*55}")
    print(f"  ROTHSTEIN v{VERSION} — Sindicato Lansky")
    print(f"  {protocol}://{CONFIG['host']}:{PORT}")
    print(f"  API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"  Security: {'ON' if SECURITY_ENABLED else 'OFF'}")
    print(f"  Supabase: {'ON' if SUPABASE_ENABLED else 'OFF'}")
    print(f"  Endpoints: signal, technical, full-analysis, position/plan")
    print(f"{'='*55}\n")

    ssl_kwargs = {}
    if SECURITY_ENABLED and CERT_FILE and KEY_FILE:
        ssl_kwargs = {"ssl_certfile": CERT_FILE, "ssl_keyfile": KEY_FILE}

    uvicorn.run(app, host=CONFIG["host"], port=PORT, **ssl_kwargs)
