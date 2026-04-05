import os
import sys
import numpy as np
import pandas as pd
import torch
from stable_baselines3 import PPO

# Añadir carpetas al path para importar dependencias
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.abspath(os.path.join(current_dir, '..', 'rothstein-dashboard')))
import technical_lib

MODEL_PATH = os.path.join(current_dir, "models", "ppo_rothstein_v6.zip")

class RLPredictor:
    def __init__(self):
        self.model = None
        if os.path.exists(MODEL_PATH):
            try:
                self.model = PPO.load(MODEL_PATH)
                print(f"[RL] Agent loaded successfully from {MODEL_PATH}")
            except Exception as e:
                print(f"[RL ERROR] Failed to load model: {e}")
        else:
            print(f"[RL] No model found at {MODEL_PATH}")

    def get_prediction(self, pair="BTCUSDT"):
        if not self.model:
            return {"action": "NONE", "confidence": 0, "ok": false}
        
        try:
            # 1. Fetch data actual (necesitamos suficientes para EMAs/RSI)
            data = technical_lib.fetch_binance_klines(pair, "1h", 100)
            if not data.get("ok"):
                return {"action": "DATA_ERROR", "confidence": 0, "ok": False}
                
            closes = np.array(data["closes"])
            highs = np.array(data["highs"])
            lows = np.array(data["lows"])
            
            # 2. Calcular indicadores (idéntico a train_agent.py)
            ema_f = technical_lib.calc_ema(data["closes"], 15)
            ema_s = technical_lib.calc_ema(data["closes"], 40)
            rsi = technical_lib.calc_rsi_series(data["closes"])
            
            # Simple ATR
            trs = [0.0]
            for i in range(1, len(closes)):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                trs.append(tr)
            atr = pd.Series(trs).rolling(window=14).mean().tolist()
            
            # 3. Preparar observacion (Step actual)
            # Normalización idéntica a train_agent.py
            curr_close = closes[-1]
            curr_ema_f = ema_f[-1]
            curr_ema_s = ema_s[-1]
            curr_rsi = rsi[-1]
            curr_atr = atr[-1]
            
            if curr_ema_f is None or curr_ema_s is None or curr_rsi is None or curr_atr is None:
                return {"action": "CALC_ERROR", "confidence": 0, "ok": False}
            
            # Features según TradingEnv._get_observation
            rsi_norm = curr_rsi / 100.0
            ema_f_dist = (curr_close - curr_ema_f) / curr_close
            ema_s_dist = (curr_close - curr_ema_s) / curr_close
            macd_approx = (curr_ema_f - curr_ema_s) / curr_close
            atr_norm = curr_atr / curr_close
            
            obs = np.array([
                np.clip(rsi_norm, 0, 1),
                np.clip(ema_f_dist + 0.5, 0, 1),
                np.clip(ema_s_dist + 0.5, 0, 1),
                np.clip(macd_approx * 10 + 0.5, 0, 1),
                0.5, # macd_signal
                np.clip(atr_norm * 20, 0, 1),
                0.2, # vol_ratio (placeholder, needs volumes[-1] / vol_avg)
                1.0, # price_norm
                0.5, # hour_norm
                0.5  # day_norm
            ], dtype=np.float32)
            
            # 4. Predecir Acción y Confianza
            # SB3 PPO uses Categorical distribution under the hood for Discrete action spaces.
            # We get the probabilities using the policy.
            obs_tensor, _ = self.model.policy.obs_to_tensor(obs)
            with torch.no_grad():
                # policy.get_distribution returns a Categorical/DiagGaussian depending on space
                dist = self.model.policy.get_distribution(obs_tensor)
                probs = dist.distribution.probs[0].cpu().numpy() # [prob_hold, prob_buy, prob_sell]
            
            action = np.argmax(probs)
            confidence = float(probs[action]) * 100
            
            actions_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
            return {
                "action": actions_map.get(int(action), "HOLD"),
                "confidence": round(confidence, 1),
                "ok": True
            }
            
        except Exception as e:
            print(f"[RL PREDICT ERROR] {e}")
            return {"action": "ERROR", "ok": False}

# Singleton instance
predictor = RLPredictor()
