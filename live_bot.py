# live_bot.py  <-- FINAL REVERSE VERSION (0.007 qty + reverse on opposite signal + 5 min heartbeat)
import ccxt
import pandas as pd
import numpy as np
import json
import time
import argparse
from datetime import datetime
from strategy2 import calculate_ema_super_signal

# ================== CONFIG ==================
SYMBOL          = 'BTCUSDT'
TIMEFRAME       = '1h'
QUANTITY        = 0.007           # <<< changed to 0.007 BTC per entry
LEVERAGE        = 10
PERCENT_EXIT    = 0.02
PERCENT_DROP    = 0.02
PYRAMID_MAX     = 10
LOOKBACK        = 200
MAX_DD_STOP     = 0.90
# ===========================================

def load_config(mode='demo'):
    with open('api.json') as f:
        cfg = json.load(f)
    key = f"algo{mode.capitalize()}1"
    return {
        'apiKey': cfg[key]['api_key'],
        'secret': cfg[key]['api_secret'],
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
        'timeout': 30000,
    }

def setup_exchange(config, mode):
    ex = ccxt.bybit(config)
    ex.enable_demo_trading(mode == 'demo')
    try:
        ex.set_leverage(LEVERAGE, SYMBOL)
    except:
        pass
    print(f"Connected to Bybit {'DEMO' if mode=='demo' else 'LIVE'} | {SYMBOL} PERPETUAL | 10x leverage")
    return ex

def get_balance(ex):
    try:
        bal = ex.fetch_balance(params={'type': 'swap'})
        return float(bal['USDT']['free'])
    except:
        return 50000.0

def close_position(ex, side, qty):
    if qty <= 0:
        return
    close_side = 'sell' if side == 'long' else 'buy'
    params = {'leverage': LEVERAGE, 'reduceOnly': True}
    try:
        order = ex.create_order(SYMBOL, 'market', close_side, qty, params=params)
        print(f"CLOSE {side.upper()} | {qty} BTC | FULL EXIT BEFORE REVERSE")
    except Exception as e:
        print(f"CLOSE FAILED: {e}")

def place_order(ex, side, qty):
    params = {'leverage': LEVERAGE, 'positionIdx': 0}
    try:
        order = ex.create_order(SYMBOL, 'market', side, qty, params=params)
        print(f"OPEN  {side.upper():4} | {qty} BTC")
        return order
    except Exception as e:
        print(f"OPEN FAILED: {e}")
        return None

def heartbeat(balance, dd):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"HEARTBEAT | {now} | Balance {balance:,.2f} USDT | Drawdown {dd*100:5.2f}%")

# ================== MAIN ==================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='demo', choices=['demo','live'])
    args = parser.parse_args()

    exchange = setup_exchange(load_config(args.mode), args.mode)

    # Always load 200 candles on start
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LOOKBACK)
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df.set_index('ts', inplace=True)

    position = None        # 'long' / 'short' / None
    trade_count = 0
    avg_price = 0.0
    last_ts = df.index[-1]

    balance = get_balance(exchange)
    peak_balance = balance
    print(f"Starting balance: {balance:,.2f} USDT | Perpetual mode active")

    heartbeat_counter = 0
    current_dd = 0.0

    while True:
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 60 == 0:          # every 5 minutes (5s loop * 60 = 300s)
                current_balance = get_balance(exchange)
                current_dd = (peak_balance - current_balance) / peak_balance
                peak_balance = max(peak_balance, current_balance)
                heartbeat(current_balance, current_dd)

            # new candle?
            new = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME,
                                      since=int(last_ts.timestamp()*1000)+1, limit=2)
            if new:
                ndf = pd.DataFrame(new, columns=['ts','open','high','low','close','volume'])
                ndf['ts'] = pd.to_datetime(ndf['ts'], unit='ms')
                ndf.set_index('ts', inplace=True)
                if not ndf.empty and ndf.index[-1] > last_ts:
                    df = pd.concat([df, ndf]).tail(LOOKBACK)
                    last_ts = df.index[-1]
                    print(f"NEW CANDLE | {last_ts}")

            df = calculate_ema_super_signal(df)
            cur  = df.iloc[-1]
            prev = df.iloc[-2]
            price = cur['close']

            long_signal = prev['plFound']
            short_signal = prev['phFound']

            # REVERSE LOGIC
            if position == 'long' and short_signal:
                close_position(exchange, 'long', trade_count * QUANTITY)
                place_order(exchange, 'sell', QUANTITY)   # open short
                position = 'short'
                trade_count = 1
                avg_price = price
                print("REVERSE LONG → SHORT")

            elif position == 'short' and long_signal:
                close_position(exchange, 'short', trade_count * QUANTITY)
                place_order(exchange, 'buy', QUANTITY)    # open long
                position = 'long'
                trade_count = 1
                avg_price = price
                print("REVERSE SHORT → LONG")

            # NORMAL ENTRY (only when flat)
            elif position is None:
                if long_signal:
                    place_order(exchange, 'buy', QUANTITY)
                    position = 'long'
                    trade_count = 1
                    avg_price = price
                    print("LONG ENTRY")
                elif short_signal:
                    place_order(exchange, 'sell', QUANTITY)
                    position = 'short'
                    trade_count = 1
                    avg_price = price
                    print("SHORT ENTRY")

            # PYRAMID (only same direction, on price pullback)
            if position == 'long' and price <= avg_price * (1 - PERCENT_DROP) and trade_count < PYRAMID_MAX:
                place_order(exchange, 'buy', QUANTITY)
                trade_count += 1
                avg_price = (avg_price * (trade_count-1) * QUANTITY + price * QUANTITY) / (trade_count * QUANTITY)
                print(f"PYRAMID LONG #{trade_count}")

            if position == 'short' and price >= avg_price * (1 + PERCENT_DROP) and trade_count < PYRAMID_MAX:
                place_order(exchange, 'sell', QUANTITY)
                trade_count += 1
                avg_price = (avg_price * (trade_count-1) * QUANTITY + price * QUANTITY) / (trade_count * QUANTITY)
                print(f"PYRAMID SHORT #{trade_count}")

            # TAKE PROFIT EXIT
            if position == 'long' and price >= avg_price * (1 + PERCENT_EXIT):
                close_position(exchange, 'long', trade_count * QUANTITY)
                pnl = (price - avg_price) * trade_count * QUANTITY * LEVERAGE
                print(f"LONG TP HIT | PnL ~{pnl:+.1f} USDT")
                position = None
                trade_count = 0

            if position == 'short' and price <= avg_price * (1 - PERCENT_EXIT):
                close_position(exchange, 'short', trade_count * QUANTITY)
                pnl = (avg_price - price) * trade_count * QUANTITY * LEVERAGE
                print(f"SHORT TP HIT | PnL ~{pnl:+.1f} USDT")
                position = None
                trade_count = 0

            # Global DD check
            current_balance = get_balance(exchange)
            current_dd = (peak_balance - current_balance) / peak_balance
            peak_balance = max(peak_balance, current_balance)
            if current_dd > MAX_DD_STOP:
                print("MAX DRAWDOWN 90% REACHED - BOT STOPPED")
                break

            time.sleep(5)

        except KeyboardInterrupt:
            print("\nBot stopped by user")
            break
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(30)