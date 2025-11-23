import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd
import pandas_ta as ta
import requests

# =========================
#  ENV CONFIG
# =========================
# Wajib di-set di Render:
#   TELEGRAM_TOKEN : token bot dari BotFather
#   TARGET_CHAT_ID : chat ID Telegram yang akan menerima sinyal (contoh: 123456789)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise ValueError("Env TELEGRAM_TOKEN belum diset.")

if not TARGET_CHAT_ID:
    raise ValueError("Env TARGET_CHAT_ID belum diset (chat id Telegram tujuan sinyal).")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


# =========================
#  CRYPTO CONFIG
# =========================

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

# Simpan sinyal terakhir untuk menghindari spam:
# key: (symbol, timeframe) -> "BUY"/"SELL"
LAST_SIGNAL = {}


# =========================
#  UTIL
# =========================

def format_time_utc(ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    """
    Kirim pesan ke Telegram tanpa library bot, langsung via HTTP.
    """
    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(TELEGRAM_API_URL, data=payload, timeout=10)
        if resp.status_code != 200:
            print("Gagal kirim ke Telegram:", resp.status_code, resp.text)
    except Exception as e:
        print("Error requests ke Telegram:", e)


# =========================
#  DATA & INDIKATOR
# =========================

def get_ohlcv_ccxt(symbol: str, timeframe: str, limit: int = 300):
    """
    Ambil OHLCV dari Binance (via ccxt).
    """
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
    """
    ohlc: list dari ccxt.fetch_ohlcv
          [ [ts, open, high, low, close, volume], ... ]
    return: df dengan kolom:
        time, open, high, low, close, volume,
        ema50, ema200,
        MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9,
        rsi
    """
    if not ohlc or len(ohlc) < 100:
        return None

    df = pd.DataFrame(
        ohlc,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)

    # EMA 50 & 200
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)

    # MACD 12,26,9
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return None

    df["MACD_12_26_9"] = macd_df["MACD_12_26_9"]
    df["MACDs_12_26_9"] = macd_df["MACDs_12_26_9"]
    df["MACDh_12_26_9"] = macd_df["MACDh_12_26_9"]

    # RSI 14
    df["rsi"] = ta.rsi(df["close"], length=14)

    # drop bar awal yang masih NaN indikator
    df = df.dropna().reset_index(drop=True)
    if len(df) < 5:
        return None

    return df


def generate_signal_from_df(df, symbol, timeframe):
    """
    df: DataFrame dari build_df_with_indicators
    return: (side, reason, snapshot_dict) -> ("BUY"/"SELL"/None, string, dict)
    """
    if df is None or len(df) < 3:
        return None, None, None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last["close"])
    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"])

    macd_now = float(last["MACD_12_26_9"])
    signal_now = float(last["MACDs_12_26_9"])
    hist_now = float(last["MACDh_12_26_9"])
    hist_prev = float(prev["MACDh_12_26_9"])

    rsi_now = float(last["rsi"])
    rsi_prev = float(prev["rsi"])

    # 1) Trend filter
    is_uptrend = (close > ema200) and (ema50 > ema200)
    is_downtrend = (close < ema200) and (ema50 < ema200)

    # 2) Retrace ke EMA50 (jarak <= 1%)
    near_ema50 = abs(close - ema50) / ema50 <= 0.01

    # 3) MACD momentum
    macd_bullish_momentum = (hist_prev < 0) and (hist_now > hist_prev)
    macd_bearish_momentum = (hist_prev > 0) and (hist_now < hist_prev)

    # 4) RSI filter (di zona 40–60 dan mulai berbalik)
    rsi_bullish_ok = (rsi_prev < rsi_now) and (40 <= rsi_now <= 60)
    rsi_bearish_ok = (rsi_prev > rsi_now) and (40 <= rsi_now <= 60)

    buy_signal = is_uptrend and near_ema50 and macd_bullish_momentum and rsi_bullish_ok
    sell_signal = is_downtrend and near_ema50 and macd_bearish_momentum and rsi_bearish_ok

    snapshot = {
        "price": close,
        "ema50": ema50,
        "ema200": ema200,
        "macd": macd_now,
        "signal": signal_now,
        "hist": hist_now,
        "rsi": rsi_now,
        "time": last["time"],
    }

    if buy_signal:
        reason = (
            "Trend *UP* (close > EMA200 & EMA50>EMA200), harga retrace ke EMA50, "
            "MACD histogram naik dari area negatif, RSI naik di zona 40–60 (bullish pullback)."
        )
        return "BUY", reason, snapshot

    if sell_signal:
        reason = (
            "Trend *DOWN* (close < EMA200 & EMA50<EMA200), harga retrace ke EMA50, "
            "MACD histogram turun dari area positif, RSI turun di zona 40–60 (bearish pullback)."
        )
        return "SELL", reason, snapshot

    return None, None, snapshot


def build_signal_message(symbol, tf, df):
    """
    Bangun teks sinyal dari DataFrame + rule generate_signal_from_df.
    """
    side, reason, snap = generate_signal_from_df(df, symbol, tf)
    if not side or not snap:
        return None, None

    msg = (
        f"🚨 *CRYPTO MACD+EMA+RSI Signal (Worker)*\n\n"
        f"Exchange: *{EXCHANGE_NAME}*\n"
        f"Pair: *{symbol}*\n"
        f"Timeframe: *{tf}*\n"
        f"Sinyal: *{side}*\n\n"
        f"Price: `{snap['price']:.5f}`\n"
        f"EMA50: `{snap['ema50']:.5f}`\n"
        f"EMA200: `{snap['ema200']:.5f}`\n"
        f"MACD: `{snap['macd']:.6f}`\n"
        f"Signal: `{snap['signal']:.6f}`\n"
        f"Histogram: `{snap['hist']:.6f}`\n"
        f"RSI(14): `{snap['rsi']:.2f}`\n"
        f"Waktu candle: {snap['time']}\n\n"
        f"Alasan:\n{reason}\n\n"
        f"Update worker: {format_time_utc()}"
    )
    return msg, side


def mark_and_should_send(symbol, tf, side):
    """
    Hindari spam: kirim sinyal baru hanya kalau side berubah
    dari BUY->SELL atau SELL->BUY atau None->BUY/SELL.
    """
    key = (symbol, tf)
    last = LAST_SIGNAL.get(key)
    if last == side:
        return False
    LAST_SIGNAL[key] = side
    return True


# =========================
#  LOOP WORKER
# =========================

def main_loop():
    print("===== CRYPTO MACD+EMA+RSI WORKER STARTED =====")
    print("Exchange :", EXCHANGE_NAME)
    print("Pairs    :", ", ".join(CRYPTO_PAIRS))
    print("TF       :", ", ".join(CRYPTO_TIMEFRAMES))
    print("Target chat id:", TARGET_CHAT_ID)
    print("===============================================")

    while True:
        try:
            for symbol in CRYPTO_PAIRS:
                for tf in CRYPTO_TIMEFRAMES:
                    try:
                        ohlc = get_ohlcv_ccxt(symbol, timeframe=tf, limit=300)
                        if not ohlc:
                            continue

                        df = build_df_with_indicators(ohlc)
                        if df is None:
                            continue

                        msg, side = build_signal_message(symbol, tf, df)
                        if msg and side and mark_and_should_send(symbol, tf, side):
                            print(f"[{format_time_utc()}] New signal {symbol} {tf}: {side}")
                            send_telegram_message(msg)

                    except Exception as e:
                        print(f"[Error proses] {symbol} {tf}: {e}")
                        continue

            # jeda antara satu full scan ke berikutnya
            time.sleep(60)

        except Exception as e:
            print("[Loop error]:", e)
            time.sleep(60)


if __name__ == "__main__":
    main_loop()
