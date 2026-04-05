#!/usr/bin/env python3
import os, sys, json, time
import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

# Añadir la ruta del dashboard para importar technical_lib
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'rothstein-dashboard')))
import technical_lib

class TradingEnv(gym.Env):
    """Custom Trading Environment for Rothstein v6"""
    metadata = {'render_modes': ['human']}

    def __init__(self, df, initial_balance=10000, fee_pct=0.001):
        super(TradingEnv, self).__init__()
        self.df = df
        self.initial_balance = initial_balance
        self.fee_pct = fee_pct
        
        # Actions: 0=Hold, 1=Buy/Long, 2=Sell/Short
        self.action_space = spaces.Discrete(3)
        
        # Observaciones: RSI, EMA_Fast, EMA_Slow, MACD, etc.
        # Vamos a usar 10 caracteristicas tecnicas
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32)
        
        self.reset()

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.balance = self.initial_balance
        self.shares_held = 0
        self.net_worth = self.initial_balance
        self.current_step = 0
        self.history = []
        return self._get_observation(), {}

    def _get_observation(self):
        # Extraer indicadores de la fila actual
        row = self.df.iloc[self.current_step]
        obs = np.array([
            row['rsi'], row['ema_fast_dist'], row['ema_slow_dist'], 
            row['macd'], row['macd_signal'], row['atr'], 
            row['vol_ratio'], row['price_norm'], row['hour_norm'], row['day_norm']
        ], dtype=np.float32)
        return obs

    def step(self, action):
        current_price = self.df.iloc[self.current_step]['close']
        
        # Ejecutar Accion
        reward = 0
        if action == 1: # COMPRAR
            if self.shares_held == 0:
                self.shares_held = self.balance / (current_price * (1 + self.fee_pct))
                self.balance = 0
        elif action == 2: # VENDER
            if self.shares_held > 0:
                self.balance = self.shares_held * current_price * (1 - self.fee_pct)
                self.shares_held = 0
                
        # Siguiente paso
        self.current_step += 1
        done = self.current_step >= len(self.df) - 1
        
        # Calcular Net Worth
        new_net_worth = self.balance + (self.shares_held * current_price)
        reward = (new_net_worth - self.net_worth) / self.net_worth
        self.net_worth = new_net_worth
        
        return self._get_observation(), reward, done, False, {}

def prepare_data(pair="BTCUSDT"):
    """Fetch klines and calculate technical features using technical_lib"""
    print(f"Fetching data for {pair}...")
    data = technical_lib.fetch_binance_klines(pair, "1h", 1000)
    if not data.get("ok"):
        raise Exception("Failed to fetch klines from Binance")
        
    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    volumes = data["volumes"]
    
    # Calcular indicadores base
    ema_f = technical_lib.calc_ema(closes, 15)
    ema_s = technical_lib.calc_ema(closes, 40)
    rsi = technical_lib.calc_rsi(closes)
    atr = technical_lib.calc_atr(highs, lows, closes)
    
    # DataFrame para el entorno RL
    df = pd.DataFrame({
        'close': closes,
        'rsi': rsi / 100.0, # Normalizado
        'ema_fast_dist': (closes - np.array(ema_f)) / closes,
        'ema_slow_dist': (closes - np.array(ema_s)) / closes,
        'macd': (np.array(ema_f) - np.array(ema_s)) / closes,
        'macd_signal': 0, # Placeholder
        'atr': np.array(atr) / closes,
        'vol_ratio': 1.0, # Placeholder
        'price_norm': closes / max(closes),
        'hour_norm': 0, # Placeholder
        'day_norm': 0 # Placeholder
    }).dropna()
    
    return df

def train():
    # Cargar Config
    with open('config_rl.json') as f:
        config = json.load(f)
        
    df = prepare_data(config["env_params"]["symbol"])
    env = DummyVecEnv([lambda: TradingEnv(df, config["env_params"]["initial_balance"])])
    
    # Inicializar PPO
    print("Iniciando entrenamiento PPO...")
    model = PPO(
        config["agent_params"]["policy"],
        env,
        learning_rate=config["agent_params"]["learning_rate"],
        n_steps=config["agent_params"]["n_steps"],
        batch_size=config["agent_params"]["batch_size"],
        n_epochs=config["agent_params"]["n_epochs"],
        ent_coef=config["agent_params"]["ent_coef"],
        verbose=1
    )
    
    model.learn(total_timesteps=config["training"]["total_timesteps"])
    
    # Guardar Modelo
    os.makedirs('models', exist_ok=True)
    model.save("models/ppo_rothstein_v6")
    print("Modelo guardado exitosamente!")

if __name__ == "__main__":
    train()
