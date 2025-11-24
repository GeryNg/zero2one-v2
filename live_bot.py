# live_bot.py  <-- PURE REVERSE BOT (No TP, immediate entry on 1h signal, UTC+8 MYT)
import ccxt
import pandas as pd
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from strategy2 import calculate_ema_super_signal

# Malaysia timezone (UTC+8 Kuala Lumpur)
MYT = timezone(timedelta(hours=8))

# ================== CONFIG ==================
SYMBOL          = 'BTCUSDT'
TIMEFRAME       = '1h'
QUANTITY        = 0.007
LEVERAGE        = 10
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
    print(f"Connected to Bybit {'DEMO' if mode=='demo' else 'LIVE'} | {SYMBOL} PERPETUAL | 10x | REVERSE ONLY")
    return ex

def get_balance(ex):
    try:
        return float(ex.fetch_balance(params={'type': 'swap'})['USDT']['free'])
    except:
        return 50000.0

def close_and_reverse(ex, current_side, new_side):
    if current_side:
        close_side = 'sell' if current_side == 'long' else 'buy'
        try:
            ex.create_order(SYMBOL, 'market', close_side, QUANTITY, params={'reduceOnly': True})
            print(f"CLOSE {current_side.upper()} | {QUANTITY:.5f} BTC")
        except Exception as e:
            print(f"CLOSE FAILED: {e}")
    
    try:
        order = ex.create_order(SYMBOL, 'market', new_side, QUANTITY)
        price = ex.fetch_ticker(SYMBOL)['last']
        print(f"REVERSE → OPEN {new_side.upper()} | {QUANTITY:.5f} BTC @ {price}")
    except Exception as e:
        print(f"OPEN FAILED: {e}")

def heartbeat(balance, dd):
    now_myt = datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S")
    print(f"HEARTBEAT | {now_myt} MYT | Balance {balance:,.2f} USDT | DD {dd*100:5.2f}%")

# ================== MAIN ==================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='demo', choices=['demo','live'])
    args = parser.parse_args()

    exchange = setup_exchange(load_config(args.mode), args.mode)

    # Load 200 candles
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LOOKBACK)
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df.set_index('ts', inplace=True)

    position = None                    # 'long', 'short', or None
    last_ts = df.index[-1]

    balance = get_balance(exchange)
    peak_balance = balance
    print(f"Starting balance: {balance:,.2f} USDT | Pure reverse bot active")

    loop_count = 0
    current_dd = 0.0

    while True:
        try:
            loop_count += 1

            # Heartbeat every 5 min
            if loop_count % 60 == 0:
                current_balance = get_balance(exchange)
                current_dd = (peak_balance - current_balance) / peak_balance
                peak_balance = max(peak_balance, current_balance)
                heartbeat(current_balance, current_dd)

            # New 1h candle?
            new = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME,
                                      since=int(last_ts.timestamp()*1000)+1, limit=2)
            if new:
                ndf = pd.DataFrame(new, columns=['ts','open','high','low','close','volume'])
                ndf['ts'] = pd.to_datetime(ndf['ts'], unit='ms')
                ndf.set_index('ts', inplace=True)
                if not ndf.empty and ndf.index[-1] > last_ts:
                    df = pd.concat([df, ndf]).tail(LOOKBACK)
                    last_ts = df.index[-1]
                    print(f"NEW 1H CANDLE | {last_ts}")

                    # Compute signals on the new closed candle
                    df = calculate_ema_super_signal(df)
                    prev = df.iloc[-2]  # signal on previous closed candle

                    long_signal = prev['plFound']
                    short_signal = prev['phFound']

                    if long_signal or short_signal:
                        new_position = 'long' if long_signal else 'short'
                        new_side = 'buy' if long_signal else 'sell'
                        
                        if position != new_position:
                            print(f"{new_position.upper()} SIGNAL DETECTED → REVERSING NOW!")
                            close_and_reverse(exchange, position, new_side)
                            position = new_position

            # DD check
            current_balance = get_balance(exchange)
            current_dd = (peak_balance - current_balance) / peak_balance
            peak_balance = max(peak_balance, current_balance)
            if current_dd > MAX_DD_STOP:
                print("MAX DD 90% HIT - STOPPING BOT")
                break

            time.sleep(10)

        except KeyboardInterrupt:
            print("\nBot stopped by user")
            break
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(30)