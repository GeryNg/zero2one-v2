import pandas as pd
import numpy as np
from ta.volatility import AverageTrueRange

def calculate_ema_super_signal(
    df,
    #ema_fast_period: int = 9,
    ema_fast_period = 9,
    ema_slow_period = 21,
    atr_period: int = 10,
    factor: float = 4.0,   # exactly as your TradingView script
    use_filter: bool = True,  # set False if you want to test without filter (but I recommend True)
):
    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMA 9 & 21
    ema_fast = close.ewm(span=ema_fast_period, adjust=False).mean()
    ema_slow = ema_fast_period = close.ewm(span=ema_slow_period, adjust=False).mean()

    # Supertrend (exact match Pine Script)
    atr = AverageTrueRange(high=high, low=low, close=close, window=atr_period).average_true_range()

    upper_band = (high + low) / 2 + factor * atr
    lower_band = (high + low) / 2 - factor * atr

    supertrend = pd.Series(0.0, index=df.index)
    supertrend.iloc[0] = upper_band.iloc[0]   # initial

    for i in range(1, len(df)):
        if close.iloc[i] > supertrend.iloc[i-1]:
            supertrend.iloc[i] = max(lower_band.iloc[i], supertrend.iloc[i-1])
        else:
            supertrend.iloc[i] = min(upper_band.iloc[i], supertrend.iloc[i-1])

    df["supertrend"] = supertrend

    supertrend_direction = (close > supertrend).astype(int)   # 1 = uptrend, 0 = downtrend

    # Crossover / Crossunder detection
    crossover = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
    crossunder = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

    # plFound = long entry, phFound = short entry
    df["plFound"] = crossover & (supertrend_direction == 1)
    df["phFound"] = crossunder & (supertrend_direction == 0)   # downtrend

    # 0 if you want only long, set plFound = crossover, phFound = False

    # optional output_signal for plotting if you want
    df["output_signal"] = np.where(ema_fast > ema_slow, 100, -100)

    return df