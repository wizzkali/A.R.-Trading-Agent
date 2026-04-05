# ESTADO DEL PROYECTO — ROTHSTEIN

Ultima actualizacion: 2026-04-05

## Phase 1: Paper Trading — ACTIVA

- Trades completados: 0/20
- Primera senal: NO TRADE / WAIT
- Capital paper: 10,000 USDT

## Infraestructura

| Componente | Estado |
|------------|--------|
| Backend FastAPI v6 (Dashboard) | OK — 8,069 LOC, 14 endpoints |
| Estructura Modular | OK — v20 EMA 15/40 implementada |
| Dashboard UI | OK — localhost:8080 |
| Pine Script TradingView | OK — `rothstein_ema_15_40.pine` |
| Binance (charting) | OK — sin KYC |
| GitHub repo | OK — estructura completa |
| Supabase | PENDIENTE — schema listo |
| Bybit API | PENDIENTE — Phase 3 |
| Telegram bot | PENDIENTE — Phase 3 |
| VPS | PENDIENTE — Phase 4 |

## Pendientes inmediatos

1. Completar 20 paper trades.
2. Migrar de `config.template.json` a `config.json` e iniciar bot para verificar alertas.
3. Definir metricas de exito para pasar a Phase 2.

## Metricas requeridas para Phase 2

- Win rate > 50%
- Ratio R/R mínimo aceptado: 1:2.5
- Max drawdown < 15%
- Minimo 20 trades registrados en `paper-trading/registro_operaciones.xlsx`
