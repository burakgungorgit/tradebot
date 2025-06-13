import os
import time
import math
import json
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException, BinanceRequestException
from ta.trend import EMAIndicator

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = Client(API_KEY, API_SECRET)

SYMBOL = "AVAXUSDT"
INTERVAL = Client.KLINE_INTERVAL_30MINUTE
COMMISSION_RATE = 0.001
MIN_USDT = 10

class BotState:
    def __init__(self):
        self.in_position = False
        self.entry_price = 0.0
        self.awaiting_confirmation = False
        self.last_signal_time = None

def save_state(state):
    with open("state.json", "w") as f:
        json.dump(state.__dict__, f)

def load_state():
    state = BotState()
    try:
        with open("state.json") as f:
            data = json.load(f)
            for k, v in data.items():
                setattr(state, k, v)
    except FileNotFoundError:
        pass
    return state

def write_log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("log.txt", "a") as f:
        f.write(f"[{now}] {msg}\n")

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass

def update_web(price=None, in_pos=None, entry=None):
    try:
        data = {}
        if price is not None: data["current_price"] = price
        if in_pos is not None: data["in_position"] = in_pos
        if entry is not None: data["entry_price"] = entry
        requests.post("http://localhost:5000/update", json=data)
    except:
        pass

def get_klines(symbol, interval, limit=100):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df[['timestamp', 'close']]

def calculate_ema(df, period):
    return EMAIndicator(close=df['close'], window=period).ema_indicator()

def get_balance(asset):
    balance = client.get_asset_balance(asset=asset)
    return float(balance['free'])

def place_order(symbol, side, quantity):
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        write_log(f"{side} emri gönderildi: {order}")
        return order
    except Exception as e:
        write_log(f"Emir Hatası: {e}")
        return None

def get_average_fill_price(order):
    try:
        if order.get('fills'):
            total = sum(float(f['price']) * float(f['qty']) for f in order['fills'])
            qty = sum(float(f['qty']) for f in order['fills'])
            return total / qty
        return float(order['cummulativeQuoteQty']) / float(order['executedQty'])
    except:
        return None

def buy_price(entry): return entry * (1 + COMMISSION_RATE)
def sell_price(price): return price * (1 - COMMISSION_RATE)
def calculate_pnl(entry, price): return sell_price(price) - buy_price(entry)

def round_quantity(symbol, qty):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step = float(f['stepSize'])
            precision = int(round(-math.log(step, 10), 0))
            return round(qty, precision)
    return qty

def log_trade(action, qty, price, pnl=None):
    now = datetime.utcnow().isoformat()
    pnl_str = f", Net: {round(pnl, 3)}" if pnl is not None else ""
    write_log(f"{action.upper()} | {now} | {qty} AVAX @ {price}{pnl_str}")
    with open("trades.csv", "a") as f:
        f.write(f"{now},{action},{qty},{price},{pnl if pnl else ''}\n")

def main():
    state = load_state()

    while True:
        try:
            df = get_klines(SYMBOL, INTERVAL)
            if len(df) < 52:
                time.sleep(30)
                continue

            df['ema20'] = calculate_ema(df, 20)
            df['ema50'] = calculate_ema(df, 50)

            prev, last = df.iloc[-2], df.iloc[-1]
            update_web(price=last['close'])

            # ALIM SİNYALİ
            if not state.in_position and not state.awaiting_confirmation:
                if (prev['ema20'] < prev['ema50'] and
                    last['ema20'] > last['ema50'] and
                    last['close'] > last['ema50']):

                    if str(last['timestamp']) != state.last_signal_time:
                        state.awaiting_confirmation = True
                        state.last_signal_time = str(last['timestamp'])
                        save_state(state)
                        write_log("Alım sinyali tespit edildi, onay bekleniyor...")

            # ALIM ONAYI
            elif state.awaiting_confirmation and pd.to_datetime(last['timestamp']) > pd.to_datetime(state.last_signal_time):
                usdt = get_balance("USDT")
                price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
                qty = round_quantity(SYMBOL, usdt / price)

                if usdt >= MIN_USDT and qty > 0:
                    order = place_order(SYMBOL, SIDE_BUY, qty)
                    if order:
                        entry = get_average_fill_price(order)
                        state.entry_price = entry
                        state.in_position = True
                        state.awaiting_confirmation = False
                        send_telegram(f"✅ ALIM: {qty} AVAX @ {entry}")
                        update_web(in_pos=True, entry=entry)
                        log_trade("BUY", qty, entry)
                        save_state(state)

            # SATIŞ KONTROLÜ
            elif state.in_position:
                current = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
                pnl = calculate_pnl(state.entry_price, current)
                pnl_ratio = pnl / buy_price(state.entry_price)

                if pnl_ratio >= 0.04 or pnl_ratio <= -0.015:
                    qty = round_quantity(SYMBOL, get_balance("AVAX"))
                    if qty > 0:
                        order = place_order(SYMBOL, SIDE_SELL, qty)
                        if order:
                            sell = get_average_fill_price(order) or current
                            pnl = calculate_pnl(state.entry_price, sell)
                            status = "💰 %4 KAR" if pnl > 0 else "⚠️ %1.5 ZARAR"
                            send_telegram(f"{status} ile SATIŞ: {qty} AVAX @ {sell} (Net: {round(pnl, 3)} USDT)")
                            update_web(in_pos=False, entry=None)
                            log_trade("SELL", qty, sell, pnl)
                            state.in_position = False
                            save_state(state)

        except (BinanceAPIException, BinanceRequestException) as e:
            msg = f"Binance Hatası: {e}"
            write_log(msg)
            send_telegram(f"⚡ Binance HATASI: {e}")
        except Exception as e:
            msg = f"Genel Hata: {e}"
            write_log(msg)
            send_telegram(f"🔥 Bot HATASI: {e}")

        # Akıllı bekleme (bir sonraki mum kapanışına kadar)
        try:
            last_time = df.iloc[-1]['timestamp']
            next_candle = last_time + pd.Timedelta(minutes=30)
            wait_sec = (next_candle - datetime.utcnow()).total_seconds()
            time.sleep(max(30, wait_sec))
        except:
            time.sleep(60)

if __name__ == "__main__":
    main()
