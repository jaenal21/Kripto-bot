import os
import time
from datetime import datetime, timezone

import ccxt
import matplotlib
matplotlib.use("Agg")  # backend non-GUI untuk server
import matplotlib.pyplot as plt
import pandas as pd
import pandas_ta as ta
from flask import Flask, request

import telebot
from telebot import types

# =========================
#  ENV & CONFIG
# =========================

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("Env TELEGRAM_TOKEN belum di-set.")

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # contoh: https://crypto-macd-bot.onrender.com
if not WEBHOOK_HOST:
    raise ValueError("Env WEBHOOK_HOST belum di-set.")

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", f"/webhook/{TOKEN}")
WEBHOOK_URL = WEBHOOK_HOST + WEBHOOK_PATH

PORT = int(os.environ.get("PORT", 8080))  # Render akan set ini

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

EXCHANGE_NAME = "Binance"
exchange = ccxt.binance()

CRYPTO_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "PAXG/USDT",
    "XRP/USDT",
    "DOT/USDT",
]

CRYPTO_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]


def format_time_utc(ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


# =========================
#  DATA & INDIKATOR (untuk chart)
# =========================

def get_ohlcv_ccxt(symbol: str, timeframe: str, limit: int = 200):
    for attempt in range(2):
        try:
            return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except ccxt.NetworkError as e:
            print(f"[NetworkError] {symbol} {timeframe}: {e}")
            if attempt == 0:
                time.sleep(2)
                continue
            return None
        except ccxt.ExchangeError as e:
            print(f"[ExchangeError] {symbol} {timeframe}: {e}")
            return None
        except Exception as e:
            print(f"[Error get_ohlcv] {symbol} {timeframe}: {e}")
            return None


def build_df_with_indicators(ohlc):
    if not ohlc or len(ohlc) < 100:
        return None

    df = pd.DataFrame(
        ohlc,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)

    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return None

    df["MACD_12_26_9"] = macd_df["MACD_12_26_9"]
    df["MACDs_12_26_9"] = macd_df["MACDs_12_26_9"]
    df["MACDh_12_26_9"] = macd_df["MACDh_12_26_9"]

    return df


def plot_chart_with_macd(symbol: str, timeframe: str, limit: int = 200):
    ohlc = get_ohlcv_ccxt(symbol, timeframe, limit=limit)
    if not ohlc or len(ohlc) < 50:
        return None

    df = build_df_with_indicators(ohlc)
    if df is None:
        return None

    plt.figure(figsize=(10, 6))

    ax1 = plt.subplot(2, 1, 1)
    ax1.plot(df["time"], df["close"])
    ax1.set_title(f"{symbol} - {timeframe} Price")
    ax1.set_ylabel("Price")

    ax2 = plt.subplot(2, 1, 2)
    ax2.plot(df["time"], df["MACD_12_26_9"], label="MACD")
    ax2.plot(df["time"], df["MACDs_12_26_9"], label="Signal")
    ax2.bar(df["time"], df["MACDh_12_26_9"], width=0.01, label="Hist")
    ax2.set_title("MACD 12,26,9")
    ax2.legend(loc="best")

    plt.tight_layout()

    fname = f"chart_{symbol.replace('/', '')}_{timeframe}.png"
    plt.savefig(fname)
    plt.close()

    return fname


# =========================
#  TELEGRAM HANDLERS
# =========================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("CRYPTO"), types.KeyboardButton("Chart"))

    text = (
        "👋 *Crypto MACD Bot (Webhook Version)*\n\n"
        "Bot ini terkoneksi ke background worker yang mengirim sinyal MACD+EMA+RSI.\n\n"
        "📡 Pair yang dipantau:\n"
        f"- {', '.join([p.replace('/USDT', 'USDT') for p in CRYPTO_PAIRS])}\n"
        f"- TF: {', '.join(CRYPTO_TIMEFRAMES)}\n\n"
        "📊 Fitur chart:\n"
        "1. Tekan tombol *Chart*\n"
        "2. Ketik: `BTCUSDT 1h`, `ETHUSDT 4h`, `XRPUSDT 30m`, dll.\n\n"
        f"Chat ID kamu: `{chat_id}`\n"
        "ID ini bisa dipakai sebagai `TARGET_CHAT_ID` di worker."
    )
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text and m.text.upper() == "CRYPTO")
def crypto_info(message):
    lines = []
    for p in CRYPTO_PAIRS:
        lines.append(f"- {p} @ {', '.join(CRYPTO_TIMEFRAMES)}")

    text = (
        "📊 *CRYPTO yang dipantau oleh worker:*\n"
        + "\n".join(lines)
        + "\n\nSinyal akan dikirim dari worker ke chat ID yang kamu set sebagai `TARGET_CHAT_ID`."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower() == "chart")
def chart_menu(message):
    text = (
        "🧾 *Mode Chart Manual*\n\n"
        "Ketik dengan format:\n"
        "`BTCUSDT 1h`\n"
        "`ETHUSDT 4h`\n"
        "`XRPUSDT 30m`\n"
        "`PAXGUSDT 1d`\n\n"
        "Aturan:\n"
        "- Symbol pakai format Binance (BTCUSDT, ETHUSDT, dll)\n"
        "- Timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, dll.\n"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def text_handler(message):
    text = (message.text or "").strip().upper()

    if text in ["CRYPTO", "CHART", "/START"]:
        return

    parts = text.split()
    if len(parts) != 2:
        return

    symbol_raw, tf = parts[0], parts[1]

    if not symbol_raw.endswith("USDT"):
        bot.reply_to(
            message,
            "Format salah.\nContoh: `BTCUSDT 1h`",
            parse_mode="Markdown",
        )
        return

    base = symbol_raw.replace("USDT", "")
    symbol = f"{base}/USDT"

    try:
        bot.reply_to(message, f"⏳ Mengambil chart {symbol} timeframe {tf} dari {EXCHANGE_NAME}...")

        file_path = plot_chart_with_macd(symbol, tf)
        if not file_path:
            bot.reply_to(
                message,
                "Gagal membuat chart. Coba cek lagi symbol/timeframenya.\n"
                "Contoh: `BTCUSDT 1h`",
                parse_mode="Markdown",
            )
            return

        with open(file_path, "rb") as photo:
            caption = f"{symbol} - {tf} (Price + MACD)"
            bot.send_photo(message.chat.id, photo, caption=caption)
    except Exception as e:
        bot.reply_to(message, f"Error saat membuat chart: `{e}`", parse_mode="Markdown")


# =========================
#  FLASK ROUTES (WEBHOOK)
# =========================

@app.route("/", methods=["GET"])
def index():
    return "Crypto MACD Bot - Webhook OK"


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    else:
        return "Unsupported Media Type", 415


def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=WEBHOOK_URL)


if __name__ == "__main__":
    print("Starting Webhook bot...")
    print("Webhook URL:", WEBHOOK_URL)
    set_webhook()
    app.run(host="0.0.0.0", port=PORT)
