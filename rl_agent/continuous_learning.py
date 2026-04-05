import os
import sys
import pandas as pd
import numpy as np
import json
import gymnasium as gym
from stable_baselines3 import PPO

# Añadir carpetas al path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.abspath(os.path.join(current_dir, '..', 'rothstein-dashboard')))

import database
import technical_lib

MODEL_PATH = os.path.join(current_dir, "models", "ppo_rothstein_v6.zip")

class ContinuousEnv(gym.Env):
    """Entorno especializado para re-aprender de trades especificos."""
    def __init__(self, trades_df):
        super(ContinuousEnv, self).__init__()
        self.df = trades_df
        self.current_step = 0
        
        # Espacios identicos al original
        self.action_space = gym.spaces.Discrete(3)
        self.observation_space = gym.spaces.Box(low=0, high=1, shape=(10,), dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        return self._get_obs(), {}

    def step(self, action):
        # En este entorno, el "action" del modelo no cambia el resultado fisico 
        # (porque el trade ya ocurrio), pero premiamos si el modelo coincide 
        # con un trade exitoso o penalizamos si no.
        row = self.df.iloc[self.current_step]
        
        target_action = 1 if row['side'] == 'BUY' else 2
        pnl = row['pnl']
        
        # Recompensa: coincidir con un trade ganador o evitar uno perdedor
        reward = 0
        if action == target_action:
            reward = pnl / 100.0 # Normalizado aproximado
        elif action == 0 and pnl < 0:
            reward = 0.01 # Bonus por no haber entrado en un trade perdedor
            
        self.current_step += 1
        done = self.current_step >= len(self.df)
        
        return self._get_obs(), reward, done, False, {}

    def _get_obs(self):
        # Generar obs sintetica basada en el trade (aproximacion)
        # Nota: Idealmente guardariamos la obs original en la BD
        return np.full((10,), 0.5, dtype=np.float32)

def run_continuous_learning():
    print("Iniciando aprendizaje continuo...")
    
    # 1. Obtener trades no usados
    unused_trades = database.get_unused_trades()
    if len(unused_trades) < 5:
        print(f"No hay suficientes trades nuevos ({len(unused_trades)}/5).")
        return
    
    df = pd.DataFrame(unused_trades)
    
    # 2. Cargar Modelo
    if not os.path.exists(MODEL_PATH):
        print("Error: No se encontro el modelo base.")
        return
        
    model = PPO.load(MODEL_PATH)
    
    # 3. Crear entorno de entrenamiento incremental
    env = ContinuousEnv(df)
    model.set_env(env)
    
    # 4. Entrenar (Fine-tuning)
    print(f"Entrenando con {len(df)} nuevos trades...")
    model.learn(total_timesteps=len(df) * 100, reset_num_timesteps=False)
    
    # 5. Guardar y marcar
    model.save(MODEL_PATH)
    database.mark_trades_as_trained([int(id) for id in df['id'].tolist()])
    
    print("Aprendizaje continuo completado y modelo actualizado.")

if __name__ == "__main__":
    run_continuous_learning()
