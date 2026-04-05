# ROTHSTEIN — Sistema de Trading Autónomo EMA 15/40

**Sindicato Lansky · Agente 7/11 · Abril 2026**

> "El Sindicato nunca apuesta — el Sindicato gestiona probabilidades."

## ¿Qué es esto?

Rothstein es un sistema de trading algorítmico para BTC/USDT basado en el cruce de medias móviles EMA 15/40 en velas 4H. El objetivo es operar de forma autónoma con gestión de riesgo estricta.

**Estado actual: Fase 1 — Paper Trading (0/20 operaciones)**

---

## Las 4 condiciones para operar (TODAS obligatorias)

1. **Señal EMA 15/40** confirmada en 4H (Golden Cross o Death Cross)
   - Golden Cross: EMA15 cruza por encima de EMA40.
   - Death Cross: EMA15 cruza por debajo de EMA40.
   - Solo se considera en el cierre de la vela 4H.

2. **RSI + Volumen confirman en 1H**
   - RSI(14) < 30 para LONG, > 70 para SHORT.
   - Volumen actual > media(volumen, 20) × 1.5.

3. **Contexto macro limpio**
   - Fear & Greed Index (fuente: alternative.me) entre 45 y 75.
   - No hay eventos de alto impacto (FED, CPI, NFP) en las próximas 4 horas.
   - El bot consulta automáticamente el archivo `macro_filter.txt`.

4. **Ratio R/R ≥ 1:2.5**
   - Stop Loss = ATR(14) × 1.5 (calculado en la vela de entrada).
   - Take Profit = SL × 2.5.
   - Si el riesgo es superior al 2% del capital, se reduce el tamaño.

> Si cualquier condición falla → NO OPERAR. Sin excepciones.

---

## Reglas de riesgo absolutas

- Stop Loss diario: máximo **15%** del capital → cierre automático.
- Position sizing: máximo **20%** del capital por operación.
- Take Profit por defecto: **2.5:1** (ganar 2.5 veces lo arriesgado).
- Trailing Stop: activar cuando la operación lleva **+10%** de ganancia.
- Reserva BTC: **SAGRADA** — estructuralmente separada del capital de trading.

---

## Roadmap — 4 Fases

| Fase | Estado | Descripción | Criterio de finalización |
|------|--------|-------------|---------------------------|
| Fase 1 | 🟡 ACTIVA | Paper trading manual | 20 operaciones completadas, win rate ≥ 50% |
| Fase 2 | ⏳ Pendiente | Alertas TradingView (Pine Script) | Script enviando alerts a Telegram automáticamente |
| Fase 3 | ⏳ Pendiente | Bot semi‑autónomo: señal → alerta → confirmación manual → ejecución | Wizz confirma con `/go` en Telegram; bot ejecuta en Bybit |
| Fase 4 | ⏳ Pendiente | Ejecución 100% autónoma en VPS | El bot opera sin confirmación humana, con kill switch remoto |

---

## Parámetros del algoritmo (versión final v20)

| Parámetro | Valor | Nota |
|-----------|-------|------|
| EMA Fast | 15 | Optimizado desde 20 tras backtest |
| EMA Slow | 40 | Optimizado desde 50 tras backtest |
| SL | ATR(14) × 1.5 | Dinámico, se calcula en la entrada |
| TP | SL × 2.5 | Ratio R/R mínimo 1:2.5 |
| Score mínimo | ≥ 7/11 | Según el composite scorer v4 |
| Filtros obligatorios | Macro + 1D + 200EMA + Circuit | Todos deben cumplirse |

**Resultados backtest (BTC/USDT, 4H, 01/01/2025 – 28/02/2026, 333 días):**
- Condiciones: spread 0.05%, slippage 0.01%, comisiones 0.1%.
- Win Rate: 60% (18/30 operaciones)
- Profit Factor: 5.9
- Max Drawdown: 0.2% (sobre capital inicial 10.000 USDT)
- Informe completo: `docs/backtest_btc_2025_2026.csv`

---

## Seguridad obligatoria (antes de instalar)

- API key de Bybit: permisos SOLO spot trading + lectura. NUNCA retiro.
- Whitelist IP: vincular la key a la IP del VPS.
- Variables de entorno: todas las claves en `.env`, nunca en el código.
- Rotación de claves cada 90 días.
- El repositorio es privado; `.env` y `*.log` en `.gitignore`.

---

## Plan de contingencia

- **Fallo de API de Bybit**: el bot reintenta 3 veces con backoff exponencial; si falla, envía alerta a Telegram y pausa operaciones.
- **Desconexión del VPS**: el bot se reinicia automáticamente con systemd o Docker healthcheck. Se monitoriza con UptimeRobot.
- **Pérdida de conexión a Internet**: el buffer local guarda las señales hasta 10 minutos; si se supera, se abortan las órdenes pendientes.
- **Error de lógica**: se escribe un log detallado en `logs/error.log` y se notifica a Telegram.

---

## Instalación rápida (Fase 3)

```bash
cd rothstein-dashboard
pip install -r requirements.txt
cp ../config/.env.template .env
# Editar .env con tus keys reales
python3 server.py
# Dashboard en http://localhost:8080
```

## Tests y validación continua

```bash
# Ejecutar tests unitarios (requiere pytest)
pytest tests/

# Validar backtest con los últimos datos
python scripts/validate_backtest.py --symbol BTCUSDT --days 30
```

## Stack tecnológico

* **Lenguaje:** Python 3.11+
* **Backend:** FastAPI v6 (8.069 LOC, 14 endpoints)
* **Análisis:** pandas-ta (EMA, RSI, ATR, volumen)
* **Exchange charting:** Binance API pública (sin key)
* **Exchange trading:** Bybit API (Fase 3, permisos mínimos)
* **Alertas:** Telegram Bot API
* **Persistencia:** Supabase / PostgreSQL
* **Deployment:** Docker + VPS (Hetzner ~4€/mes)
* **TradingView:** Pine Script con alertas Golden/Death Cross

*ROTHSTEIN v6.0 · Sindicato Lansky · Agente 7/11*
