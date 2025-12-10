import pandas as pd
import numpy as np

def calculate_trend_forecast_signal(df: pd.DataFrame, length: int = 50, trend_length: int = 3, samples: int = 10) -> pd.DataFrame:
    """
    Calculates the Trend Duration Forecast signals (HMA trend detection).

    :param df: Input DataFrame with 'close' prices.
    :param length: Smoothing Length for HMA (Pine Script's 'length').
    :param trend_length: Trend Detection Sensitivity (Pine Script's 'trendLength').
    :param samples: Trend Sample Size (Pine Script's 'samples', used for average trend length).
    :return: DataFrame with HMA, trend status, and probable trend length columns.
    """

    # --- 1. HMA Calculation (Hull Moving Average) ---
    # The HMA calculation involves three weighted moving averages (WMA):
    # HMA = WMA(2 * WMA(C, L/2) - WMA(C, L), sqrt(L))

    # WMA helper function
    def wma(series, period):
        weights = np.arange(1, period + 1)
        return series.rolling(period).apply(lambda prices: np.dot(prices, weights) / weights.sum(), raw=True)

    # 1. WMA(C, L/2)
    wma1 = wma(df['close'], int(length / 2))
    # 2. WMA(C, L)
    wma2 = wma(df['close'], length)
    # 3. 2 * WMA(C, L/2) - WMA(C, L)
    diff_wma = 2 * wma1 - wma2
    # 4. HMA
    df['hma'] = wma(diff_wma, int(np.sqrt(length)))
    
    # --- 2. Trend Detection ---
    # Trend is detected by checking if the HMA is 'rising' or 'falling' for 'trend_length' bars.
    
    # The Pine Script uses ta.rising(hma, trendLength) and ta.falling(hma, trendLength).
    # In Python, this is equivalent to checking the slope over the past 'trend_length' bars.
    
    # 'trend_up' is True if HMA is strictly increasing for 'trend_length' bars (current > prev > prev-1 ...)
    df['trend_up'] = (
        (df['hma'] > df['hma'].shift(1)) &
        (df['hma'].shift(1) > df['hma'].shift(2))
    ) # Simplified for trend_length=3, as the condition needs to hold for 'trendLength' bars. 
      # A general implementation requires a rolling application or a loop, but for small trendLength (like 3), 
      # checking the difference is simpler.

    # Let's use a simpler, more robust method for a general `trend_length`:
    def is_rising(series, period):
        """Checks if the series is strictly increasing for 'period' bars."""
        # Check if the value is greater than the value 'n' bars ago
        # And all intermediate differences are positive
        rising = (series.diff(1) > 0).rolling(period).apply(lambda x: x.all(), raw=True)
        return rising.fillna(False).astype(bool)

    def is_falling(series, period):
        """Checks if the series is strictly decreasing for 'period' bars."""
        falling = (series.diff(1) < 0).rolling(period).apply(lambda x: x.all(), raw=True)
        return falling.fillna(False).astype(bool)

    df['trend_up_signal'] = is_rising(df['hma'], trend_length)
    df['trend_dn_signal'] = is_falling(df['hma'], trend_length)
    
    # The actual 'trend' variable in Pine Script is a persistent state.
    # It only changes when a trend signal is confirmed.

    # Initialize a Series for the current trend status (True for up, False for down)
    trend_series = pd.Series(dtype=bool)
    current_trend = np.nan
    
    # A loop is necessary to maintain the persistent state logic (var trend = bool(na))
    for i in range(len(df)):
        if df['trend_up_signal'].iloc[i]:
            current_trend = True
        elif df['trend_dn_signal'].iloc[i]:
            current_trend = False
        
        trend_series.at[df.index[i]] = current_trend

    df['trend'] = trend_series
    
    # --- 3. Trend Duration Tracking ---
    # This logic is key for calculating the average probable length.
    
    # Initialize variables to hold the counts (equivalent to Pine Script's arrays)
    bullish_counts = []
    bearish_counts = []
    current_trend_count = 0
    
    # Initialize Series for the results
    df['probable_long_length'] = np.nan
    df['probable_short_length'] = np.nan
    
    for i in range(1, len(df)):
        current_trend = df['trend'].iloc[i]
        prev_trend = df['trend'].iloc[i-1]
        
        if pd.notna(current_trend):
            # Increment the counter for the current trend
            current_trend_count += 1
            
            # Check for a trend switch (trend != trend[1])
            if current_trend != prev_trend:
                # Store the length of the just-finished trend
                finished_trend_length = current_trend_count - 1
                
                if prev_trend is True: # The finished trend was Bullish (now switching to Bearish)
                    bullish_counts.append(finished_trend_length)
                    # Keep only the last 'samples' counts
                    if len(bullish_counts) > samples:
                        bullish_counts.pop(0)
                        
                elif prev_trend is False: # The finished trend was Bearish (now switching to Bullish)
                    bearish_counts.append(finished_trend_length)
                    # Keep only the last 'samples' counts
                    if len(bearish_counts) > samples:
                        bearish_counts.pop(0)

                # Reset counter
                current_trend_count = 1
                
            # Calculate and store the average probable length *before* the switch
            avg_bullish = np.mean(bullish_counts) if bullish_counts else np.nan
            avg_bearish = np.mean(bearish_counts) if bearish_counts else np.nan
            
            df.loc[df.index[i], 'probable_long_length'] = avg_bullish
            df.loc[df.index[i], 'probable_short_length'] = avg_bearish
            
            
    # --- 4. Trading Signals (Based on the new trend detection) ---
    # 'plFound' (Pine Script's TrendUP / new bullish trend) is True when trend switches from False to True.
    # 'phFound' (Pine Script's TrendDN / new bearish trend) is True when trend switches from True to False.
    df['plFound'] = (df['trend'] == True) & (df['trend'].shift(1) == False)
    df['phFound'] = (df['trend'] == False) & (df['trend'].shift(1) == True)
    
    return df