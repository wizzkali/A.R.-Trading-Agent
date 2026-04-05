# Onboarding Valentín — Rothstein

No preguntes, solo ejecuta estos comandos en orden.

## 1. Clonar repositorio

```bash
git clone https://github.com/sindicato/rothstein.git
cd rothstein
```

## 2. Configurar entorno Python

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r rothstein-dashboard/requirements.txt
```

## 3. Configurar variables de entorno

```bash
cp config/.env.template rothstein-dashboard/.env
nano rothstein-dashboard/.env   # edita con tus claves reales
```
Claves necesarias:
* `BYBIT_API_KEY`, `BYBIT_API_SECRET` (permisos spot, whitelist IP)
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
* `SUPABASE_URL`, `SUPABASE_KEY` (opcional)

## 4. Probar el dashboard

```bash
cd rothstein-dashboard
python3 server.py
# Abrir http://localhost:8080
```

## 5. Ejecutar paper trading

Abrir `paper-trading/registro_operaciones.xlsx` y seguir el formato.
Cada vez que generes una señal, anota: fecha, precio, score, resultado, P&L.

## 6. Subir cambios a GitHub

```bash
git add .
git commit -m "Actualización paper trading"
git push origin main
```

## 7. Despliegue en VPS (Fase 4)

```bash
# En el VPS
docker-compose up -d
```

Si algo falla, revisa `logs/error.log` y Telegram.
