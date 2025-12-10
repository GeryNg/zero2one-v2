# strategy1.py
import pandas as pd
import numpy as np
import ta


# ----------------------------------------------------------------------
# Helper Functions (unchanged)
# ----------------------------------------------------------------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def wpr(high, low, close, period):
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return -100 * (highest_high - close) / (highest_high - lowest_low)


def rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def sma(series, period):
    return series.rolling(window=period).mean()


def stdev(series, period):
    return series.rolling(window=period).std()


def rma(series, period):
    return series.ewm(alpha=1/period, adjust=False).mean()


def fixnan(series):
    return series.ffill()


def change(series):
    return series.diff()


def tr(high, low, close):
    return pd.Series(
        np.maximum(high - low,
                   np.abs(high - close.shift()),
                   np.abs(low - close.shift())),
        index=high.index,
    )


def hma(series, period):
    half_length = int(period / 2)
    sqrt_length = int(np.sqrt(period))
    wma_half = ta.trend.wma_indicator(series, window=half_length)
    wma_full = ta.trend.wma_indicator(series, window=period)
    return ta.trend.wma_indicator(2 * wma_half - wma_full, window=sqrt_length)


# ----------------------------------------------------------------------
# MAIN SIGNAL FUNCTION – **tunable defaults**
# ----------------------------------------------------------------------
def calculate_orion_signal(
    df,
    ema_short_period: int = 7,   # <-- change this
    ema_long_period: int = 15,  # <-- change this
    hma_period: int = 29,       # <-- change this
):
    """
    Orion composite signal.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: open, high, low, close, volume
    ema_short_period : int
        Short EMA length (default 7)
    ema_long_period : int
        Long EMA length (default 15)
    hma_period : int
        Hull Moving Average smoothing length (default 29)

    Returns
    -------
    pd.DataFrame
        Original df with three new columns:
        * output_signal
        * plFound  (long entry)
        * phFound  (short entry)
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ------------------------------------------------------------------
    # Component a – EMA-difference momentum
    # ------------------------------------------------------------------
    ema_short = ema(close, ema_short_period)
    ema_long = ema(close, ema_long_period)
    ema_diff = ema_short - ema_long
    ema_ema_diff = ema(ema_diff, 8)
    a = (ema_diff - ema_ema_diff) / 10

    # ------------------------------------------------------------------
    # Component b – Williams %R
    # ------------------------------------------------------------------
    b = wpr(high, low, close, 14)

    # ------------------------------------------------------------------
    # Component c – Bollinger-like deviation
    # ------------------------------------------------------------------
    ema21 = ema(close, 21)
    stdev21 = stdev(close, 21)
    c = 100 * (close + 2 * stdev21 - ema21) / (4 * stdev21)

    # ------------------------------------------------------------------
    # Component d – RSI of price vs EMA21
    # ------------------------------------------------------------------
    rsi_d = rsi(close - ema21, 14)
    d = (rsi_d * 2) - 100

    # ------------------------------------------------------------------
    # Component e – Trend strength (True-Range based)
    # ------------------------------------------------------------------
    tr_series = tr(high, low, close)
    change_high = change(high)
    change_low = change(low)

    cond1 = (change_high > change_low) & (change_high > 0)
    cond2 = (change_low > change_high) & (change_low > 0)

    cond1_series = pd.Series(np.where(cond1, change_high, 0), index=high.index)
    cond2_series = pd.Series(np.where(cond2, change_low, 0), index=high.index)

    rma1 = rma(cond1_series, 1)
    rma2 = rma(cond2_series, 1)
    rma_tr = rma(tr_series, 1)

    e_series = fixnan(100 * rma1 / rma_tr) - fixnan(100 * rma2 / rma_tr)
    rsi_e = rsi(e_series, 14)
    e = (rsi_e * 2) - 100

    # ------------------------------------------------------------------
    # Component f – Volume-weighted price momentum
    # ------------------------------------------------------------------
    sum_vol20 = volume.rolling(20).sum()
    term1 = (sum_vol20 - volume) / sum_vol20
    term2 = (volume * close) / sum_vol20
    numerator = close - term1 + term2
    denominator = (close + term1 + term2) / 2
    f_series = (numerator / denominator) * 100
    rsi_f = rsi(f_series, 14)
    f = rsi_f - 100

    # ------------------------------------------------------------------
    # Component g – Normalized price position
    # ------------------------------------------------------------------
    lowest_low14 = low.rolling(14).min()
    highest_high14 = high.rolling(14).max()
    g_series = (close - lowest_low14) / (highest_high14 - lowest_low14) - 0.5
    sma_g = sma(g_series, 2)
    rsi_g = rsi(sma_g, 14)
    g = (rsi_g * 2) - 100

    # ------------------------------------------------------------------
    # Combine & smooth with HMA
    # ------------------------------------------------------------------
    x = (a + b + c + d + e + f + g) / 7 * 2
    output_signal = hma(x, hma_period)

    # ------------------------------------------------------------------
    # Divergence detection (entry signals)
    # ------------------------------------------------------------------
    df["output_signal"] = output_signal
    df["plFound"] = (output_signal > output_signal.shift(1)) & (
        output_signal.shift(1) < output_signal.shift(2)
    )
    df["phFound"] = (output_signal < output_signal.shift(1)) & (
        output_signal.shift(1) > output_signal.shift(2)
    )

    return df