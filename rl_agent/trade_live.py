#!/usr/bin/env python3
import os, sys, json, time
import numpy as np
import pandas as pd
from stable_baselines3 import PPO

# Añadir la ruta del dashboard para importar technical_lib
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'rothstein-dashboard')))
import technical_lib

def live_run():
    # Cargar Config
    with open('config_rl.json') as f:
        config = json.load(f)
        
    print(f"Iniciando ejecucion en vivo (Paper Trading) para {config['env_params']['symbol']}")
    
    # Cargar Modelo Entrenado
    model_path = config["training"]["model_path"]
    if not os.path.exists(f"{model_path}.zip"):
        print(f"Error: Modelo no encontrado en {model_path}.zip. Ejecuta train_agent.py primero.")
        sys.exit(1)
        
    model = PPO.load(model_path)
    print("Modelo cargado correctamente.")
    
    while True:
        try:
            # 1. Fetch data actual
            data = technical_lib.fetch_binance_klines(config["env_params"]["symbol"], "1h", 100)
            if not data.get("ok"):
                print("Error de datos, reintentando en 60s...")
                time.sleep(60)
                continue
                
            closes = data["closes"]
            ema_f = technical_lib.calc_ema(closes, 15)
            ema_s = technical_lib.calc_ema(closes, 40)
            rsi = technical_lib.calc_rsi(closes)
            atr = technical_lib.calc_atr(data["highs"], data["lows"], closes)
            
            # 2. Preparar observacion (debe coincidir con TradingEnv)
            obs = np.array([
                rsi[-1] / 100.0,
                (closes[-1] - ema_f[-1]) / closes[-1] if ema_f[-1] else 0,
                (closes[-1] - ema_s[-1]) / closes[-1] if ema_s[-1] else 0,
                (ema_f[-1] - ema_s[-1]) / closes[-1] if ema_f[-1] and ema_s[-1] else 0,
                0, # MACD signal placeholder
                atr[-1] / closes[-1] if atr[-1] else 0,
                1.0, # Volume ratio placeholder
                1.0, # Price norm placeholder
                0, # Hour norm
                0  # Day norm
            ], dtype=np.float32)
            
            # 3. Predecir accion
            action, _states = model.predict(obs, deterministic=True)
            
            actions_str = {0: "HOLD", 1: "BUY/LONG", 2: "SELL/SHORT"}
            print(f"[{time.strftime('%H:%M:%S')}] Precio: {closes[-1]} | Accion: {actions_str[int(action)]}")
            
            # 4. (Opcional) Loguear al Dashboard via state.json o API
            # log_action_to_dashboard(actions_str[int(action)], closes[-1])
            
        except KeyboardInterrupt:
            print("Cerrando agente RL...")
            break
        except Exception as e:
            print(f"Error en loop: {e}")
            
        time.sleep(600) # Chequear cada 10 minutos para 1h timeframe

if __name__ == "__main__":
    live_run()
