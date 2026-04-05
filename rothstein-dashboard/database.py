import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "trades.db"

def init_db():
    """Inicializa la base de datos y crea la tabla de trades si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            sl REAL,
            tp REAL,
            resultado TEXT,
            pnl REAL,
            score INTEGER,
            used_for_training INTEGER DEFAULT 0
        )
    ''')
    # Manejar caso donde la tabla ya existe sin la columna
    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN used_for_training INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass # La columna ya existe
    conn.commit()
    conn.close()

def insert_trade(trade_data):
    """Inserta un nuevo trade en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Asegurar que el timestamp sea actual si no se proporciona
    timestamp = trade_data.get("timestamp") or datetime.now(timezone.utc).isoformat()
    
    cursor.execute('''
        INSERT INTO trades (timestamp, symbol, side, entry_price, sl, tp, resultado, pnl, score, used_for_training)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    ''', (
        timestamp,
        trade_data.get("symbol", "UNKNOWN"),
        trade_data.get("side", "UNKNOWN"),
        trade_data.get("entry_price", 0.0),
        trade_data.get("sl"),
        trade_data.get("tp"),
        trade_data.get("resultado"),
        trade_data.get("pnl", 0.0),
        trade_data.get("score")
    ))
    conn.commit()
    conn.close()

def get_unused_trades():
    """Recupera trades pendientes de procesar por el agente RL."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trades WHERE used_for_training = 0 ORDER BY timestamp ASC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def mark_trades_as_trained(ids):
    """Marca una lista de IDs de trades como ya entrenados."""
    if not ids: return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(ids))
    cursor.execute(f'UPDATE trades SET used_for_training = 1 WHERE id IN ({placeholders})', ids)
    conn.commit()
    conn.close()

def get_training_metadata():
    """Obtiene info sobre el ultimo entrenamiento."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM trades WHERE used_for_training = 1')
    count = cursor.fetchone()[0]
    cursor.execute('SELECT MAX(timestamp) FROM trades WHERE used_for_training = 1')
    last_ts = cursor.fetchone()[0]
    conn.close()
    return {"trained_count": count, "last_training": last_ts}

def get_all_trades():
    """Recupera el historial completo de trades desde SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trades ORDER BY timestamp DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Inicializar al importar
init_db()
