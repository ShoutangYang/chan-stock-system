"""
技术指标基础层。
所有函数输入输出均为 pandas Series / DataFrame，不依赖外部数据源。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (dif, dea, hist)。hist 采用国内惯例 *2 放大。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def boll(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def volume_ratio(volume: pd.Series, n: int = 20) -> pd.Series:
    avg = volume.rolling(n).mean().replace(0.0, np.nan)
    return volume / avg


def donchian(df: pd.DataFrame, n: int = 20):
    """唐奇安通道：上轨=N日最高，下轨=N日最低（不含当日，避免自我突破）。"""
    upper = df["high"].rolling(n).max().shift(1)
    lower = df["low"].rolling(n).min().shift(1)
    return upper, lower


def kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3):
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0.0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def drawdown_from_high(close: pd.Series, window: int = 252) -> pd.Series:
    """当前价相对窗口内最高价的回撤比例（负值）。"""
    peak = close.rolling(window, min_periods=2).max()
    return (close / peak - 1.0).replace([np.inf, -np.inf], np.nan)


def pct_change(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1.0)


def annualized_vol(close: pd.Series, n: int = 60) -> float:
    """年化波动率（基于日收益率标准差）。"""
    ret = close.pct_change().dropna()
    if len(ret) < 2:
        return float("nan")
    ret = ret.tail(n)
    return float(ret.std(ddof=0) * np.sqrt(252))


# ----------------------------------------------------------------------------
# 量价指标
# ----------------------------------------------------------------------------
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """能量潮 OBV：涨日加量、跌日减量、平盘不计。反映资金累积方向。"""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).fillna(0.0).cumsum()


def typical_price(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def raw_money_flow(df: pd.DataFrame) -> pd.Series:
    """原始资金流：用收盘价在当日振幅中的位置判断主动性买卖，再乘以成交量。"""
    tp = typical_price(df)
    direction = np.sign(tp.diff().fillna(0.0))
    return direction * tp * df["volume"]


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """资金流量指标 MFI：成交量加权版的 RSI。>80 超买，<20 超卖。"""
    rmf = raw_money_flow(df)
    tp = typical_price(df)
    d = tp.diff()
    pos = rmf.where(d > 0, 0.0)
    neg = (-rmf).where(d < 0, 0.0)
    pos_sum = pos.rolling(n, min_periods=n).sum()
    neg_sum = neg.rolling(n, min_periods=n).sum()
    mr = pos_sum / neg_sum.replace(0.0, np.nan)
    out = 100 - 100 / (1 + mr)
    return out.fillna(50.0)


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """蔡金资金流 CMF：用收盘价在振幅中的位置衡量资金进出强度，取值 -1 ~ +1。"""
    high, low, close, vol = df["high"], df["low"], df["close"], df["volume"]
    rng = (high - low).replace(0.0, np.nan)
    mfv = ((close - low) - (high - close)) / rng
    mfv = mfv.fillna(0.0).clip(-1.0, 1.0) * vol
    return mfv.rolling(n, min_periods=n).sum() / vol.rolling(n, min_periods=n).sum().replace(0.0, np.nan)


def ad_line(df: pd.DataFrame) -> pd.Series:
    """累积/派发线 A/D：判断主力是在吸筹还是派发。"""
    high, low, close, vol = df["high"], df["low"], df["close"], df["volume"]
    rng = (high - low).replace(0.0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    return (mfm.fillna(0.0).clip(-1.0, 1.0) * vol).cumsum()


def vwap(df: pd.DataFrame, n: Optional[int] = None) -> pd.Series:
    """成交量加权均价。n 为空时返回滚动 VWAP，否则返回 N 日滚动 VWAP。"""
    tp = typical_price(df)
    pv = (tp * df["volume"]).cumsum()
    vv = df["volume"].cumsum().replace(0.0, np.nan)
    out = pv / vv
    if n:
        pv_n = (tp * df["volume"]).rolling(n, min_periods=1).sum()
        vv_n = df["volume"].rolling(n, min_periods=1).sum().replace(0.0, np.nan)
        out = pv_n / vv_n
    return out


def vol_std_ratio(volume: pd.Series, n: int = 20) -> pd.Series:
    """当前成交量相对近 N 日均量的标准差倍数，用于识别「极端放量」。"""
    mu = volume.rolling(n).mean().replace(0.0, np.nan)
    sd = volume.rolling(n).std(ddof=0)
    return (volume - mu) / sd.replace(0.0, np.nan)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """趋势强度指标，>25 通常认为有明确趋势。"""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_n = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1.0 / n, adjust=False, min_periods=n
    ).mean() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1.0 / n, adjust=False, min_periods=n
    ).mean() / atr_n
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan) * 100
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().fillna(0.0)
