# live_bot.py  <-- FINAL FIXED VERSION (Perpetual + 90% DD + 200 candles)
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
QUANTITY        = 0.001
LEVERAGE        = 10
PERCENT_EXIT    = 0.02
PERCENT_DROP    = 0.02
PYRAMID_MAX     = 10
POLL_INTERVAL   = 300
LOOKBACK        = 200
MAX_DD_STOP     = 0.90            # 90% drawdown allowed
TRADES_FILE     = 'live_trades.csv'
# ===========================================

def load_config(mode='demo'):
    with open('api.json') as f:
        cfg = json.load(f)
    key = f"algo{mode.capitalize()}1"
    return {
        'apiKey': cfg[key]['api_key'],
        'secret': cfg[key]['api_secret'],
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},   # <<< USDT Perpetual
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

def get_position(ex):
    try:
        pos = ex.fetch_positions([SYMBOL])
        for p in pos:
            if p['contracts'] != 0 and p['symbol'] == f'{SYMBOL}:USDT':
                return {'side': 'long' if p['side'] == 'long' else 'short',
                        'size': abs(p['contracts']),
                        'entry': float(p['entryPrice'])}
    except:
        pass
    return None

def place_order(ex, side, qty, reduce=False):
    params = {'leverage': LEVERAGE, 'positionIdx': 0}
    if reduce:
        params['reduceOnly'] = True
    try:
        order = ex.create_order(SYMBOL, 'market', side, qty, params=params)
        print(f"ORDER {side.upper():4} | {qty} BTC | {'[CLOSE]' if reduce else '[OPEN ]'}")
        return order
    except Exception as e:
        print(f"ORDER FAILED: {e}")
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

    long_pos = short_pos = None
    long_count = short_count = 0
    long_avg = short_avg = 0.0
    last_ts = df.index[-1]

    balance = get_balance(exchange)
    peak_balance = balance
    print(f"Starting balance: {balance:,.2f} USDT | Perpetual mode active")

    heartbeat_counter = 0
    current_dd = 0.0   # <<< THIS WAS MISSING BEFORE

    while True:
        try:
            heartbeat_counter += 1
            if heartbeat_counter % 12 == 0:          # every ~1 hour
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

            # LONG
            if long_pos is None and prev['plFound']:
                place_order(exchange, 'buy', QUANTITY)
                long_pos = 'long'
                long_count = 1
                long_avg = price
                print("LONG ENTRY  | EMA9 > EMA21 + Supertrend up")

            if long_pos == 'long':
                if price <= long_avg * (1 - PERCENT_DROP) and long_count < PYRAMID_MAX:
                    place_order(exchange, 'buy', QUANTITY)
                    long_count += 1
                    long_avg = (long_avg * (long_count-1) * QUANTITY + price * QUANTITY) / (long_count * QUANTITY)
                    print(f"PYRAMID LONG #{long_count}")

                if price >= long_avg * (1 + PERCENT_EXIT) or prev['phFound']:
                    place_order(exchange, 'sell', long_count * QUANTITY, reduce=True)
                    pnl = (price - long_avg) * long_count * QUANTITY * LEVERAGE
                    print(f"LONG EXIT   | PnL ~{pnl:+.1f} USDT")
                    long_pos = None
                    long_count = 0

            # SHORT
            if short_pos is None and prev['phFound']:
                place_order(exchange, 'sell', QUANTITY)
                short_pos = 'short'
                short_count = 1
                short_avg = price
                print("SHORT ENTRY | EMA9 < EMA21 + Supertrend down")

            if short_pos == 'short':
                if price >= short_avg * (1 + PERCENT_DROP) and short_count < PYRAMID_MAX:
                    place_order(exchange, 'sell', QUANTITY)
                    short_count += 1
                    short_avg = (short_avg * (short_count-1) * QUANTITY + price * QUANTITY) / (short_count * QUANTITY)
                    print(f"PYRAMID SHORT #{short_count}")

                if price <= short_avg * (1 - PERCENT_EXIT) or prev['plFound']:
                    place_order(exchange, 'buy', short_count * QUANTITY, reduce=True)
                    pnl = (short_avg - price) * short_count * QUANTITY * LEVERAGE
                    print(f"SHORT EXIT  | PnL ~{pnl:+.1f} USDT")
                    short_pos = None
                    short_count = 0

            # Global drawdown check
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