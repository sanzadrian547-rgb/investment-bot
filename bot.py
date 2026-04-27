# “””
Investment Telegram Bot

- Análisis diario automático a las 8:00 AM
- Alerta inmediata si cambia la señal (BUY/HOLD/SELL)
- Comandos manuales: /analizar, /cartera, /ayuda

Instalación:
pip install python-telegram-bot yfinance pandas numpy apscheduler
“””

import os
import json
import logging
import asyncio
from datetime import datetime, time as dtime
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings(“ignore”)

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────

# CONFIGURACIÓN — edita estos valores

# ─────────────────────────────────────────────

BOT_TOKEN   = os.getenv(“TELEGRAM_BOT_TOKEN”, “AAGr5wmwBN688sriuQeSM8tVfSqWnbDUN4A”)
CHAT_ID     = os.getenv(“TELEGRAM_CHAT_ID”,   “8200800235”)

TICKERS = [“AAPL”, “MSFT”, “GOOGL”, “SPY”, “NVDA”, “JNJ”]

HORA_INFORME_DIARIO = dtime(8, 0)   # 08:00 AM (hora del servidor)

STATE_FILE = Path(“signals_state.json”)   # guarda señales anteriores

logging.basicConfig(
format=”%(asctime)s | %(levelname)s | %(message)s”,
level=logging.INFO
)
log = logging.getLogger(**name**)

# ─────────────────────────────────────────────

# BENCHMARKS Y PESOS (igual que el agente)

# ─────────────────────────────────────────────

SECTOR_PER = {
“Technology”: 30, “Healthcare”: 22, “Financial Services”: 15,
“Consumer Cyclical”: 25, “Consumer Defensive”: 20, “Industrials”: 22,
“Energy”: 14, “Utilities”: 18, “Real Estate”: 35,
“Basic Materials”: 16, “Communication Services”: 24,
“ETF”: 25, “Unknown”: 22,
}

WEIGHTS = {
“per”: 20, “growth”: 20, “margin”: 15,
“roe”: 20, “debt”: 15, “trend”: 10,
}

# ─────────────────────────────────────────────

# MOTOR DE ANÁLISIS

# ─────────────────────────────────────────────

def fetch(ticker: str) -> dict:
t = yf.Ticker(ticker)
info = t.info
is_etf = info.get(“quoteType”, “”).upper() in (“ETF”, “MUTUALFUND”)
sector  = “ETF” if is_etf else (info.get(“sector”) or “Unknown”)

```
price = info.get("currentPrice") or info.get("regularMarketPrice")
per   = info.get("trailingPE") or info.get("forwardPE")
growth  = info.get("revenueGrowth")
margin  = info.get("profitMargins")
roe     = info.get("returnOnEquity")
de      = info.get("debtToEquity")
if de and de > 20:
    de /= 100

hist = t.history(period="1y", auto_adjust=True)
ma200 = hist["Close"].rolling(min(200, len(hist))).mean().iloc[-1] if len(hist) >= 50 else None

return dict(ticker=ticker, name=info.get("shortName", ticker),
            sector=sector, price=price, per=per, growth=growth,
            margin=margin, roe=roe, de=de, ma200=ma200)
```

def _s(val, low, mid_low, mid_high, high, none_score=50):
“”“Score genérico en 4 tramos.”””
if val is None:
return none_score
if val >= high:    return 100
if val >= mid_high: return 80
if val >= mid_low:  return 60
if val >= low:      return 35
return 10

def score_per(per, sector):
if per is None or per <= 0: return 50
r = per / SECTOR_PER.get(sector, 22)
if r <= 0.7: return 100
if r <= 0.9: return 80
if r <= 1.1: return 60
if r <= 1.4: return 35
return 10

def score_growth(g):
if g is None: return 50
g *= 100
return _s(g, 0, 5, 10, 20)

def score_margin(m):
if m is None: return 50
m *= 100
return _s(m, 0, 8, 15, 25)

def score_roe(r):
if r is None: return 50
r *= 100
return _s(r, 8, 15, 20, 30)

def score_debt(d):
if d is None: return 60
if d <= 0.3: return 100
if d <= 0.7: return 80
if d <= 1.5: return 55
if d <= 3.0: return 25
return 5

def score_trend(price, ma200):
if price is None or ma200 is None: return 50
diff = (price - ma200) / ma200 * 100
if diff >= 10:  return 85
if diff >= 0:   return 70
if diff >= -5:  return 45
if diff >= -15: return 25
return 5

def analyse_one(ticker: str) -> dict:
d = fetch(ticker)
scores = {
“per”:    score_per(d[“per”], d[“sector”]),
“growth”: score_growth(d[“growth”]),
“margin”: score_margin(d[“margin”]),
“roe”:    score_roe(d[“roe”]),
“debt”:   score_debt(d[“de”]),
“trend”:  score_trend(d[“price”], d[“ma200”]),
}
total = sum(scores[k] * WEIGHTS[k] / 100 for k in scores)

```
# Overrides duros → SELL
hard_sell = (
    (d["margin"] is not None and d["margin"] < -0.05) or
    (d["de"]     is not None and d["de"]     > 4)     or
    (d["growth"] is not None and d["growth"] < -0.15)
)
if hard_sell:
    rec = "SELL"
elif total >= 68:
    rec = "BUY"
elif total >= 45:
    rec = "HOLD"
else:
    rec = "SELL"

return {**d, "scores": scores, "total": round(total, 1), "rec": rec}
```

def analyse_all() -> list[dict]:
results = []
for t in TICKERS:
try:
results.append(analyse_one(t))
except Exception as e:
log.warning(f”Error analizando {t}: {e}”)
results.sort(key=lambda x: x[“total”], reverse=True)
return results

# ─────────────────────────────────────────────

# ASIGNACIÓN DE CARTERA

# ─────────────────────────────────────────────

def allocation(results: list[dict]) -> dict[str, float]:
eligible = [r for r in results if r[“rec”] != “SELL”]
if not eligible:
return {r[“ticker”]: 0.0 for r in results}
total = sum(r[“total”] for r in eligible)
alloc = {}
for r in results:
alloc[r[“ticker”]] = round(r[“total”] / total * 100, 1) if r[“rec”] != “SELL” else 0.0
return alloc

# ─────────────────────────────────────────────

# FORMATEO DE MENSAJES TELEGRAM

# ─────────────────────────────────────────────

EMOJI_REC = {“BUY”: “🟢”, “HOLD”: “🟡”, “SELL”: “🔴”}
SCORE_BAR_CHARS = “▓”

def bar(score: float, w: int = 10) -> str:
filled = int(score / 100 * w)
return “▓” * filled + “░” * (w - filled)

def fmt_pct(v):
if v is None: return “N/A”
return f”{v*100:+.1f}%”

def fmt_val(v, dec=2):
if v is None: return “N/A”
return f”{v:.{dec}f}”

def build_daily_message(results: list[dict], alloc: dict[str, float]) -> str:
now = datetime.now().strftime(”%d/%m/%Y %H:%M”)
lines = [
f”📊 *INFORME DIARIO DE INVERSIÓN*”,
f”🕐 {now}\n”,
f”{‘─’*32}”,
]

```
for i, r in enumerate(results, 1):
    emoji = EMOJI_REC[r["rec"]]
    weight = alloc.get(r["ticker"], 0)
    trend_diff = ""
    if r["price"] and r["ma200"]:
        diff = (r["price"] - r["ma200"]) / r["ma200"] * 100
        trend_diff = f"({diff:+.1f}% vs MA200)"

    lines += [
        f"\n*{i}. {r['ticker']}* — {r['name']}",
        f"Sector: {r['sector']}",
        f"Precio: ${fmt_val(r['price'])} {trend_diff}",
        f"",
        f"`{'Métrica':<14} {'Valor':>8}  Score`",
        f"`{'─'*35}`",
        f"`{'PER':<14} {fmt_val(r['per'],1):>8}  {bar(r['scores']['per'])}`",
        f"`{'Crec.Ingresos':<14} {fmt_pct(r['growth']):>8}  {bar(r['scores']['growth'])}`",
        f"`{'Margen':<14} {fmt_pct(r['margin']):>8}  {bar(r['scores']['margin'])}`",
        f"`{'ROE':<14} {fmt_pct(r['roe']):>8}  {bar(r['scores']['roe'])}`",
        f"`{'Deuda/Equity':<14} {fmt_val(r['de']):>8}  {bar(r['scores']['debt'])}`",
        f"`{'Tendencia':<14} {'MA200':>8}  {bar(r['scores']['trend'])}`",
        f"",
        f"🎯 Score: *{r['total']}/100*  {bar(r['total'], 15)}",
        f"Señal: {emoji} *{r['rec']}*",
        f"Peso cartera: `{weight:.1f}%`",
        f"{'─'*32}",
    ]

# Resumen cartera
lines += [
    f"\n💼 *DISTRIBUCIÓN SUGERIDA*",
]
for r in results:
    w = alloc.get(r["ticker"], 0)
    if w > 0:
        bar_w = int(w / 5)
        lines.append(f"{EMOJI_REC[r['rec']]} `{r['ticker']:<6}` {'█'*bar_w} `{w:.1f}%`")

lines += [
    f"\n⚠️ _Solo para largo plazo. No es asesoramiento financiero._"
]
return "\n".join(lines)
```

def build_alert_message(ticker: str, old_rec: str, new_rec: str, result: dict) -> str:
emoji_old = EMOJI_REC.get(old_rec, “⚪”)
emoji_new = EMOJI_REC[new_rec]
now = datetime.now().strftime(”%d/%m/%Y %H:%M”)

```
# Tipo de cambio
if old_rec == "HOLD" and new_rec == "BUY":
    header = "🚀 MEJORA DE SEÑAL"
elif old_rec == "BUY" and new_rec == "HOLD":
    header = "⚠️ SEÑAL REBAJADA"
elif new_rec == "SELL":
    header = "🚨 SEÑAL DE VENTA"
elif new_rec == "BUY":
    header = "✅ NUEVA COMPRA"
else:
    header = "🔄 CAMBIO DE SEÑAL"

return (
    f"🔔 *ALERTA — {header}*\n"
    f"🕐 {now}\n\n"
    f"*{ticker}* — {result['name']}\n"
    f"Sector: {result['sector']}\n"
    f"Precio: ${fmt_val(result['price'])}\n\n"
    f"Señal anterior: {emoji_old} *{old_rec}*\n"
    f"Nueva señal:    {emoji_new} *{new_rec}*\n\n"
    f"Score: *{result['total']}/100*\n\n"
    f"_Revisa el informe completo con /analizar_"
)
```

# ─────────────────────────────────────────────

# ESTADO (persistencia de señales)

# ─────────────────────────────────────────────

def load_state() -> dict:
if STATE_FILE.exists():
return json.loads(STATE_FILE.read_text())
return {}

def save_state(state: dict):
STATE_FILE.write_text(json.dumps(state, indent=2))

# ─────────────────────────────────────────────

# TAREAS PROGRAMADAS

# ─────────────────────────────────────────────

async def job_daily_report(bot: Bot):
“”“Envía el informe diario completo.”””
log.info(“Ejecutando informe diario…”)
try:
results = analyse_all()
alloc   = allocation(results)
msg     = build_daily_message(results, alloc)
await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
log.info(“Informe diario enviado ✓”)
except Exception as e:
log.error(f”Error en informe diario: {e}”)
await bot.send_message(chat_id=CHAT_ID, text=f”⚠️ Error generando informe: {e}”)

async def job_check_signals(bot: Bot):
“”“Comprueba cambios de señal y envía alertas.”””
log.info(“Comprobando cambios de señal…”)
state = load_state()
results = []
try:
results = analyse_all()
except Exception as e:
log.error(f”Error obteniendo datos: {e}”)
return

```
changed = False
for r in results:
    ticker  = r["ticker"]
    new_rec = r["rec"]
    old_rec = state.get(ticker)

    if old_rec and old_rec != new_rec:
        log.info(f"Cambio señal: {ticker} {old_rec} → {new_rec}")
        msg = build_alert_message(ticker, old_rec, new_rec, r)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        changed = True

    state[ticker] = new_rec

save_state(state)
if not changed:
    log.info("Sin cambios de señal.")
```

# ─────────────────────────────────────────────

# COMANDOS DEL BOT

# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
msg = (
“👋 *Investment Bot activo*\n\n”
“📋 *Comandos disponibles:*\n”
“/analizar — Análisis completo ahora\n”
“/cartera  — Solo distribución de cartera\n”
“/ayuda    — Esta ayuda\n\n”
“📅 Informe automático cada día a las 08:00\n”
“🔔 Alertas en tiempo real si cambia la señal”
)
await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_analizar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(“⏳ Analizando activos… (puede tardar 20-30 seg)”)
try:
results = analyse_all()
alloc   = allocation(results)
msg     = build_daily_message(results, alloc)
await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
except Exception as e:
await update.message.reply_text(f”❌ Error: {e}”)

async def cmd_cartera(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(“⏳ Calculando cartera…”)
try:
results = analyse_all()
alloc   = allocation(results)
lines = [“💼 *DISTRIBUCIÓN DE CARTERA*\n”]
for r in results:
w = alloc.get(r[“ticker”], 0)
emoji = EMOJI_REC[r[“rec”]]
bar_w = int(w / 5)
lines.append(
f”{emoji} *{r[‘ticker’]}* — Score {r[‘total’]}/100\n”
f”   {‘█’*bar_w}{‘░’*(20-bar_w)} `{w:.1f}%`\n”
f”   Señal: {r[‘rec’]}\n”
)
await update.message.reply_text(”\n”.join(lines), parse_mode=ParseMode.MARKDOWN)
except Exception as e:
await update.message.reply_text(f”❌ Error: {e}”)

async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
msg = (
“ℹ️ *Cómo funciona el bot*\n\n”
“🟢 *BUY* — Score ≥ 68: buenos fundamentales\n”
“🟡 *HOLD* — Score 45-67: posición neutral\n”
“🔴 *SELL* — Score < 45 o señal de deterioro\n\n”
“*Métricas analizadas:*\n”
“• PER vs benchmark del sector\n”
“• Crecimiento de ingresos (YoY)\n”
“• Margen de beneficio neto\n”
“• ROE (Return on Equity)\n”
“• Ratio Deuda/Equity\n”
“• Precio vs Media Móvil 200 días\n\n”
“⚠️ *Solo largo plazo. No es asesoramiento financiero.*”
)
await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────

# MAIN

# ─────────────────────────────────────────────

def main():
if BOT_TOKEN == “TU_TOKEN_AQUI”:
print(“❌ Configura TELEGRAM_BOT_TOKEN en las variables de entorno”)
return

```
log.info("Iniciando Investment Bot...")

app = Application.builder().token(BOT_TOKEN).build()
bot = app.bot

# Comandos
app.add_handler(CommandHandler("start",    cmd_start))
app.add_handler(CommandHandler("analizar", cmd_analizar))
app.add_handler(CommandHandler("cartera",  cmd_cartera))
app.add_handler(CommandHandler("ayuda",    cmd_ayuda))

# Scheduler
scheduler = AsyncIOScheduler()

# Informe diario a las 08:00
scheduler.add_job(
    job_daily_report,
    trigger="cron",
    hour=HORA_INFORME_DIARIO.hour,
    minute=HORA_INFORME_DIARIO.minute,
    kwargs={"bot": bot},
    id="daily_report",
)

# Comprobar cambios de señal cada 4 horas (en horario de mercado)
scheduler.add_job(
    job_check_signals,
    trigger="cron",
    hour="9,13,17,21",
    minute=0,
    kwargs={"bot": bot},
    id="signal_check",
)

scheduler.start()
log.info(f"✓ Informe diario: {HORA_INFORME_DIARIO.strftime('%H:%M')}")
log.info("✓ Alertas: cada 4 horas en mercado")
log.info("✓ Bot escuchando comandos...")

app.run_polling(drop_pending_updates=True)
```

if **name** == “**main**”:
main()
