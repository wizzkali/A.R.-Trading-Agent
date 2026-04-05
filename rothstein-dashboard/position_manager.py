#!/usr/bin/env python3
"""
ROTHSTEIN v5 — Position Manager
Sindicato Lansky | Trailing stops dinamicos, salidas parciales, ATR-based sizing

FEATURES:
  - Stop loss basado en ATR (no % fijo arbitrario)
  - Trailing stop que se activa al +10% y sigue precio con 1.5x ATR
  - Salidas parciales: 50% en TP1, 30% en TP2, 20% runner
  - Position sizing por Kelly Criterion simplificado
  - Deteccion de invalidacion de senal (EMA re-cross)
"""

import json
import sys
from datetime import datetime, timezone
from typing import Dict, Optional, List
from technical_lib import fetch_binance_klines, calc_ema, calc_atr, calc_rsi, fetch_ticker_price


class Position:
    """Representa una posicion abierta"""
    def __init__(self, pair: str, direction: str, entry_price: float,
                 stop_loss: float, tp1: float, tp2: float, tp3: float,
                 size_usdt: float, atr_at_entry: float):
        self.pair = pair
        self.direction = direction  # LONG / SHORT
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.original_stop = stop_loss
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp3 = tp3
        self.size_usdt = size_usdt
        self.remaining_pct = 100  # % de posicion abierta
        self.atr_at_entry = atr_at_entry
        self.trailing_active = False
        self.trailing_high = entry_price if direction == "LONG" else entry_price
        self.opened_at = datetime.now(timezone.utc).isoformat()
        self.partial_exits = []

    def to_dict(self) -> dict:
        return {
            "pair": self.pair, "direction": self.direction,
            "entry": self.entry_price, "stop_loss": round(self.stop_loss, 2),
            "original_stop": round(self.original_stop, 2),
            "tp1": round(self.tp1, 2), "tp2": round(self.tp2, 2), "tp3": round(self.tp3, 2),
            "size_usdt": round(self.size_usdt, 2),
            "remaining_pct": self.remaining_pct,
            "trailing_active": self.trailing_active,
            "trailing_high": round(self.trailing_high, 2),
            "opened_at": self.opened_at,
            "partial_exits": self.partial_exits
        }


def calculate_atr_stop(price: float, atr: float, direction: str, multiplier: float = 2.0) -> float:
    """Stop loss basado en ATR — mas inteligente que % fijo"""
    if direction == "LONG":
        return price - (atr * multiplier)
    else:
        return price + (atr * multiplier)


def calculate_targets(price: float, atr: float, direction: str, rr_ratio: float = 2.0) -> Dict:
    """Calcular TP1, TP2, TP3 basados en ATR"""
    risk = atr * 2.0  # stop distance

    if direction == "LONG":
        tp1 = price + (risk * rr_ratio)       # 2R
        tp2 = price + (risk * rr_ratio * 1.5)  # 3R
        tp3 = price + (risk * rr_ratio * 2.5)  # 5R (runner)
    else:
        tp1 = price - (risk * rr_ratio)
        tp2 = price - (risk * rr_ratio * 1.5)
        tp3 = price - (risk * rr_ratio * 2.5)

    return {"tp1": round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2)}


def kelly_position_size(capital: float, win_rate: float, avg_rr: float,
                        max_pct: float = 20, kelly_fraction: float = 0.25) -> float:
    """
    Kelly Criterion simplificado para position sizing
    kelly_fraction = 0.25 (quarter-Kelly, conservador)
    """
    if win_rate <= 0 or avg_rr <= 0:
        return capital * 0.05  # minimo 5%

    # Kelly % = W - (1-W)/R
    kelly_pct = win_rate - ((1 - win_rate) / avg_rr)

    if kelly_pct <= 0:
        return capital * 0.02  # si Kelly negativo, minimo 2%

    # Quarter-Kelly (conservador)
    position_pct = kelly_pct * kelly_fraction * 100
    position_pct = min(position_pct, max_pct)
    position_pct = max(position_pct, 2)

    return round(capital * position_pct / 100, 2)


def update_trailing_stop(position: Position, current_price: float, atr: float) -> Dict:
    """Actualizar trailing stop dinamico"""
    actions = []

    if position.direction == "LONG":
        pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
    else:
        pnl_pct = (position.entry_price - current_price) / position.entry_price * 100

    # Activar trailing al +10%
    if not position.trailing_active and pnl_pct >= 10:
        position.trailing_active = True
        actions.append(f"TRAILING activado al +{pnl_pct:.1f}%")

    if position.trailing_active:
        if position.direction == "LONG":
            if current_price > position.trailing_high:
                position.trailing_high = current_price
                new_stop = current_price - (atr * 1.5)
                if new_stop > position.stop_loss:
                    position.stop_loss = new_stop
                    actions.append(f"Trailing stop subido a ${new_stop:.2f}")
        else:
            if current_price < position.trailing_high:
                position.trailing_high = current_price
                new_stop = current_price + (atr * 1.5)
                if new_stop < position.stop_loss:
                    position.stop_loss = new_stop
                    actions.append(f"Trailing stop bajado a ${new_stop:.2f}")

    # Check partial exits
    if position.direction == "LONG":
        if current_price >= position.tp1 and position.remaining_pct > 50:
            position.remaining_pct = 50
            position.partial_exits.append({"target": "TP1", "price": current_price, "closed_pct": 50})
            # Mover stop a breakeven
            position.stop_loss = max(position.stop_loss, position.entry_price)
            actions.append(f"TP1 alcanzado — cerrado 50%, stop a breakeven")

        if current_price >= position.tp2 and position.remaining_pct > 20:
            position.remaining_pct = 20
            position.partial_exits.append({"target": "TP2", "price": current_price, "closed_pct": 30})
            actions.append(f"TP2 alcanzado — cerrado 30% mas, runner 20% activo")

        if current_price >= position.tp3 and position.remaining_pct > 0:
            position.remaining_pct = 0
            position.partial_exits.append({"target": "TP3", "price": current_price, "closed_pct": 20})
            actions.append(f"TP3 alcanzado — posicion completamente cerrada")

        if current_price <= position.stop_loss:
            actions.append(f"STOP LOSS HIT a ${position.stop_loss:.2f}")
            position.remaining_pct = 0
    else:
        if current_price <= position.tp1 and position.remaining_pct > 50:
            position.remaining_pct = 50
            position.partial_exits.append({"target": "TP1", "price": current_price, "closed_pct": 50})
            position.stop_loss = min(position.stop_loss, position.entry_price)
            actions.append(f"TP1 alcanzado — cerrado 50%, stop a breakeven")

        if current_price <= position.tp2 and position.remaining_pct > 20:
            position.remaining_pct = 20
            position.partial_exits.append({"target": "TP2", "price": current_price, "closed_pct": 30})
            actions.append(f"TP2 alcanzado — cerrado 30% mas")

        if current_price <= position.tp3 and position.remaining_pct > 0:
            position.remaining_pct = 0
            position.partial_exits.append({"target": "TP3", "price": current_price, "closed_pct": 20})
            actions.append(f"TP3 — cerrada completamente")

        if current_price >= position.stop_loss:
            actions.append(f"STOP LOSS HIT")
            position.remaining_pct = 0

    return {
        "pnl_pct": round(pnl_pct, 2),
        "trailing_active": position.trailing_active,
        "current_stop": round(position.stop_loss, 2),
        "remaining_pct": position.remaining_pct,
        "actions": actions,
        "position": position.to_dict()
    }


def check_signal_invalidation(pair: str, direction: str) -> Dict:
    """Detectar si la senal original se ha invalidado (EMA re-cross)"""
    data = fetch_binance_klines(pair, "4h", 60)
    if not data.get("ok"):
        return {"invalidated": False, "reason": "No data"}

    closes = data["closes"]
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)

    if not ema20[-1] or not ema50[-1]:
        return {"invalidated": False, "reason": "EMAs not ready"}

    rsi = calc_rsi(closes)
    price = closes[-1]

    invalidated = False
    reasons = []

    if direction == "LONG":
        if ema20[-1] < ema50[-1]:
            invalidated = True
            reasons.append("Death Cross — EMA20 cruzo por debajo de EMA50")
        if rsi > 80:
            reasons.append(f"RSI {rsi:.0f} extremadamente sobrecomprado")
        if price < ema50[-1]:
            invalidated = True
            reasons.append("Precio por debajo de EMA50 — tendencia rota")
    else:
        if ema20[-1] > ema50[-1]:
            invalidated = True
            reasons.append("Golden Cross — EMA20 cruzo por encima de EMA50")
        if rsi < 20:
            reasons.append(f"RSI {rsi:.0f} extremadamente sobrevendido")
        if price > ema50[-1]:
            invalidated = True
            reasons.append("Precio por encima de EMA50")

    return {
        "invalidated": invalidated,
        "reasons": reasons,
        "current_ema20": round(ema20[-1], 2),
        "current_ema50": round(ema50[-1], 2),
        "rsi": round(rsi, 1),
        "price": round(price, 2),
        "recommendation": "CERRAR POSICION" if invalidated else "MANTENER"
    }


def plan_entry(pair: str = "BTCUSDT", direction: str = "LONG",
               capital: float = 1000, win_rate: float = 0.55,
               avg_rr: float = 2.0) -> Dict:
    """
    Plan de entrada completo: sizing, stops, targets
    """
    data = fetch_binance_klines(pair, "4h", 60)
    if not data.get("ok"):
        return {"ok": False, "error": "No data"}

    closes = data["closes"]
    price = closes[-1]
    atr = calc_atr(data["highs"], data["lows"], closes)
    atr_pct = (atr / price * 100) if price > 0 else 0

    # Stop loss basado en ATR
    stop = calculate_atr_stop(price, atr, direction)
    risk_pct = abs(price - stop) / price * 100

    # Targets
    targets = calculate_targets(price, atr, direction)

    # Position size (Kelly)
    size = kelly_position_size(capital, win_rate, avg_rr)

    # R:R ratios
    rr1 = abs(targets["tp1"] - price) / abs(price - stop) if abs(price - stop) > 0 else 0
    rr2 = abs(targets["tp2"] - price) / abs(price - stop) if abs(price - stop) > 0 else 0

    return {
        "ok": True,
        "pair": pair,
        "direction": direction,
        "price": round(price, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "stop_loss": round(stop, 2),
        "risk_pct": round(risk_pct, 2),
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],
        "rr_tp1": round(rr1, 2),
        "rr_tp2": round(rr2, 2),
        "position_size_usdt": size,
        "position_pct_of_capital": round(size / capital * 100, 1),
        "kelly_inputs": {"win_rate": win_rate, "avg_rr": avg_rr},
        "partial_exit_plan": {
            "tp1_close": "50% de posicion",
            "tp1_action": "Mover stop a breakeven",
            "tp2_close": "30% adicional",
            "tp3_close": "20% runner con trailing stop 1.5x ATR"
        }
    }


if __name__ == "__main__":
    pair = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    direction = sys.argv[2].upper() if len(sys.argv) > 2 else "LONG"

    plan = plan_entry(pair, direction)
    if plan["ok"]:
        print(f"\n{'='*55}")
        print(f"  ROTHSTEIN v5 — POSITION PLAN")
        print(f"  {pair} | {direction}")
        print(f"{'='*55}")
        print(f"  Price:     ${plan['price']:,.2f}")
        print(f"  ATR:       ${plan['atr']:,.2f} ({plan['atr_pct']:.1f}%)")
        print(f"  Stop Loss: ${plan['stop_loss']:,.2f} (riesgo {plan['risk_pct']:.1f}%)")
        print(f"  TP1:       ${plan['tp1']:,.2f} (R:R {plan['rr_tp1']})")
        print(f"  TP2:       ${plan['tp2']:,.2f} (R:R {plan['rr_tp2']})")
        print(f"  TP3:       ${plan['tp3']:,.2f} (runner)")
        print(f"  Size:      ${plan['position_size_usdt']:,.2f} ({plan['position_pct_of_capital']}% capital)")
        print(f"{'='*55}")

    print(f"\nJSON_RESULT:{json.dumps(plan)}")
