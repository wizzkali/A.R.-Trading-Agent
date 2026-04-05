import os
import sys
import json
from pathlib import Path

# Añadir el directorio actual al path para importar technical_lib
current_dir = Path(__file__).parent
sys.path.append(str(current_dir))

import technical_lib

MACRO_FILE = current_dir / "macro_filter.txt"

def update_macro():
    """Obtiene el Fear & Greed Index y actualiza macro_filter.txt."""
    print("[MACRO] Actualizando filtro macro desde Fear & Greed...")
    try:
        data = technical_lib.fetch_fear_greed()
        value = data.get("value", 50)
        classification = data.get("classification", "NEUTRAL").upper()
        
        # Lógica de sentimiento macro
        # 0-30: BEAR (Miedo extremo)
        # 30-70: NEUTRAL
        # 70-100: BULL (Codicia extrema)
        
        sentiment = "NEUTRAL"
        if value <= 30:
            sentiment = "BEAR"
        elif value >= 70:
            sentiment = "BULL"
        
        # Escribir al archivo
        with open(MACRO_FILE, "w") as f:
            f.write(sentiment)
            
        print(f"[MACRO] Filtro actualizado: {sentiment} (Indice: {value} - {classification})")
        
    except Exception as e:
        print(f"[MACRO ERROR] No se pudo actualizar el filtro macro: {e}")

if __name__ == "__main__":
    update_macro()
