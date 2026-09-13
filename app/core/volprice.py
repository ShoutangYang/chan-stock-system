"""
量价关系引擎（Volume-Price Analysis）

核心思想：价格是方向，成交量是动力。两者配合与否，决定趋势能否延续。

    价涨量增 = 健康上涨（顺势持有）
    价涨量缩 = 上涨乏力（警惕见顶）
    价跌量增 = 恐慌抛售（危险）
    价跌量缩 = 抛压衰竭（可能见底）

本模块识别 5 类买入形态与 5 类卖出形态，每类输出：
    置信度 / 触发价 / 止损位 / 目标位 / 文字理由

无未来函数保证：
    所有指标均为因果指标（只依赖历史数据），预计算完整序列后按位置 i 取用，
    等价于「站在第 i 根 K 线时只能看到过去」，因此可直接用于回测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind

# ----------------------------------------------------------------------------
# 信号元数据
# ----------------------------------------------------------------------------
SIGNAL_META: Dict[str, Dict[str, str]] = {
    # ---------------- 买入 ----------------
    "VP_B1": {
        "name": "放量突破",
        "side": "buy",
        "desc": "放量突破近期新高，量价齐升，主力资金推动趋势启动",
    },
    "VP_B2": {
        "name": "缩量回踩",
        "side": "buy",
        "desc": "上升趋势中缩量回调至均线附近，抛压枯竭，洗盘结束",
    },
    "VP_B3": {
        "name": "地量止跌",
        "side": "buy",
        "desc": "成交量萎缩至地量且价格止跌，卖压衰竭，接近局部底部",
    },
    "VP_B4": {
        "name": "底部放量反转",
        "side": "buy",
        "desc": "长期下跌后突然放量大阳/长下影，资金进场抄底",
    },
    "VP_B5": {
        "name": "量价底背离",
        "side": "buy",
        "desc": "价格创新低但 OBV/MFI 未创新低，下跌动能衰竭",
    },
    # ---------------- 卖出 ----------------
    "VP_S1": {
        "name": "放量滞涨",
        "side": "sell",
        "desc": "高位放量却涨不动（收阴或长上影），买盘承接不住卖压",
    },
    "VP_S2": {
        "name": "量价顶背离",
        "side": "sell",
        "desc": "价格创新高但 OBV/MFI 未跟随创新高，上涨失去量能支撑",
    },
    "VP_S3": {
        "name": "破位放量",
        "side": "sell",
        "desc": "放量跌破均线/前低支撑，趋势结构被破坏",
    },
    "VP_S4": {
        "name": "天量见顶",
        "side": "sell",
        "desc": "成交量创阶段天量却无法收高，情绪高潮往往对应阶段性顶部",
    },
    "VP_S5": {
        "name": "高位放量长阴",
        "side": "sell",
        "desc": "高位放量大阴线，典型的主力派发出货形态",
    },
}

BUY_CODES = [c for c, m in SIGNAL_META.items() if m["side"] == "buy"]
SELL_CODES = [c for c, m in SIGNAL_META.items() if m["side"] == "sell"]

# 提醒门槛：低于该置信度不产生提醒（回测时可调低以扩大样本）
DEFAULT_MIN_CONFIDENCE = 55.0

# 默认参数（可被 config 覆盖）
DEFAULT_PARAMS: Dict[str, Any] = {
    "breakout_vol": 1.8,      # 突破放量倍数
    "breakout_lookback": 20,  # 突破回看窗口
    "dryup_vol": 0.7,         # 缩量回踩上限
    "dryup_lookback": 10,     # 回踩回看窗口
    "lowvol_vol": 0.5,        # 地量阈值
    "lowvol_window": 60,      # 地量比较窗口
    "heavy_vol": 3.0,         # 天量倍数
    "pivot_w": 5,             # 波段枢轴点窗口（背离检测用）
    "risk_reward": 2.0,       # 目标盈亏比
    "atr_stop_mult": 1.5,     # ATR 止损倍数下限
    "max_risk_pct": 0.15,     # 单笔最大风险（超过则降级）
    "min_confidence": DEFAULT_MIN_CONFIDENCE,
}


@dataclass
class VolPriceSignal:
    code: str
    side: str
    date: str
    raw_idx: int
    price: float
    confidence: float
    score: float
    stop: Optional[float] = None
    target: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    resonance: bool = False

    def to_dict(self) -> Dict[str, Any]:
        meta = SIGNAL_META.get(self.code, {})
        conf = self.confidence
        level = "高" if conf >= 75 else ("中" if conf >= 62 else "低")
        return {
            "code": self.code,
            "name": meta.get("name", self.code),
            "side": self.side,
            "date": self.date,
            "raw_idx": self.raw_idx,
            "price": self.price,
            "confidence": round(conf, 1),
            "score": round(self.score, 1),
            "stop": self.stop,
            "target": self.target,
            "level": level,
            "resonance": self.resonance,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "desc": meta.get("desc", ""),
        }


# ----------------------------------------------------------------------------
# 特征预计算
# ----------------------------------------------------------------------------
def compute_features(df: pd.DataFrame, pivot_w: int = 5) -> pd.DataFrame:
    """
    预计算量价特征。所有字段均为因果指标，第 i 行只依赖 0..i 的数据。
    """
    f = pd.DataFrame(index=df.index)
    f["date"] = df["date"].astype(str)
    f["open"] = df["open"].astype(float)
    f["high"] = df["high"].astype(float)
    f["low"] = df["low"].astype(float)
    f["close"] = df["close"].astype(float)
    f["volume"] = df["volume"].astype(float)

    close, high, low, vol = f["close"], f["high"], f["low"], f["volume"]

    # ---- 量能 ----
    f["vol_ma20"] = vol.rolling(20, min_periods=20).mean()
    f["vol_ma5"] = vol.rolling(5, min_periods=5).mean()
    f["vr"] = vol / f["vol_ma20"].replace(0.0, np.nan)          # 量比（相对20日均量）
    f["vr5"] = vol / f["vol_ma5"].replace(0.0, np.nan)          # 量比（相对5日均量）
    f["vol_std"] = ind.vol_std_ratio(vol, 20)

    # ---- 价格结构 ----
    f["ma5"] = ind.sma(close, 5)
    f["ma10"] = ind.sma(close, 10)
    f["ma20"] = ind.sma(close, 20)
    f["ma60"] = ind.sma(close, 60)
    f["atr"] = ind.atr(df, 14)
    f["chg"] = close.pct_change() * 100.0

    # ---- 量价结合指标 ----
    f["obv"] = ind.obv(close, vol)
    f["mfi"] = ind.mfi(df, 14)
    f["cmf"] = ind.cmf(df, 20)
    f["ad"] = ind.ad_line(df)
    f["vwap20"] = ind.vwap(df, 20)

    # ---- K线形态特征 ----
    rng = (high - low).replace(0.0, np.nan)
    body = (close - f["open"]).abs()
    upper = high - np.maximum(close, f["open"])
    lower = np.minimum(close, f["open"]) - low
    f["rng"] = rng
    f["body_pct"] = body / rng                       # 实体占振幅比
    f["upper_shadow"] = upper / rng                  # 上影线占比
    f["lower_shadow"] = lower / rng                  # 下影线占比
    f["close_pos"] = (close - low) / rng             # 收盘在振幅中的位置 0~1

    # ---- 位置 ----
    f["hh20"] = high.rolling(20).max().shift(1)      # 前20日最高（不含当日）
    f["ll20"] = low.rolling(20).min().shift(1)
    f["hh60"] = high.rolling(60).max().shift(1)
    f["ll60"] = low.rolling(60).min().shift(1)
    f["dd60"] = close / close.rolling(60, min_periods=20).max() - 1.0
    f["dd252"] = close / close.rolling(252, min_periods=60).max() - 1.0

    # ---- 资金流趋势（相对自身近期水平） ----
    f["cmf_ma5"] = f["cmf"].rolling(5, min_periods=5).mean()
    f["ad_ma10"] = f["ad"].rolling(10, min_periods=10).mean()

    # ---- 波段枢轴点标记（用于背离检测） ----
    # 标记本身用居中窗口计算，但「使用」时只取 j+pivot_w <= i 的已确认点，
    # 因此不会引入未来数据。
    w = int(pivot_w)
    if w > 0:
        rh = high.rolling(2 * w + 1, center=True, min_periods=w + 1).max()
        rl = low.rolling(2 * w + 1, center=True, min_periods=w + 1).min()
        f["is_ph"] = (high >= rh) & rh.notna()
        f["is_pl"] = (low <= rl) & rl.notna()

    return f


# ----------------------------------------------------------------------------
# 单位置检测
# ----------------------------------------------------------------------------
def _safe(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return default if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return default


def _has(f: pd.Series, i: int, *keys: str) -> bool:
    """检查第 i 行指定字段是否全部有效。"""
    for k in keys:
        if k not in f.columns:
            return False
        v = f[k].iloc[i]
        try:
            if pd.isna(v):
                return False
        except Exception:
            return False
    return True


def _risk_level(conf: float) -> float:
    return conf


def _calc_levels(f: pd.DataFrame, i: int, side: str, params: Dict[str, Any],
                 structure_stop: Optional[float] = None):
    """
    计算止损与目标位。

    若给出 structure_stop（如背离信号的枢轴点），优先用它——因为那才是「形态证伪位」；
    否则退化为近 10 日极值，并保证至少留出 atr_stop_mult × ATR 的空间。
    目标按盈亏比 risk_reward 推算。
    """
    close = _safe(f["close"].iloc[i])
    atr = _safe(f["atr"].iloc[i], 0.0)
    mult = float(params.get("atr_stop_mult", 1.5))
    rr = float(params.get("risk_reward", 2.0))
    if close <= 0:
        return None, None, None

    if structure_stop is not None and structure_stop > 0:
        stop = float(structure_stop)
        # 结构位方向错误（已在价格另一侧）时，退化为 ATR 止损
        if side == "buy" and stop >= close:
            stop = close - mult * atr
        elif side == "sell" and stop <= close:
            stop = close + mult * atr
    elif side == "buy":
        recent_low = _safe(f["low"].iloc[max(0, i - 9): i + 1].min(), close)
        stop = min(recent_low, close - mult * atr)
    else:
        recent_high = _safe(f["high"].iloc[max(0, i - 9): i + 1].max(), close)
        stop = max(recent_high, close + mult * atr)

    if side == "buy":
        risk = close - stop
        if risk <= 0:
            stop, risk = close * 0.93, close * 0.07
        target = close + rr * risk
    else:
        risk = stop - close
        if risk <= 0:
            stop, risk = close * 1.07, close * 0.07
        target = close - rr * risk

    return stop, target, risk / close


def _find_pivots(f: pd.DataFrame, i: int, w: int,
                 kind: str, max_back: int = 180, need: int = 3) -> List[int]:
    """
    找出截至第 i 根为止「已确认」的波段枢轴点。

    枢轴高点：high[j] 是 [j-w, j+w] 区间内的最大值。
    需要 j+w <= i 才算确认（右肩已走完），因此不含未来数据。
    """
    col = "is_ph" if kind == "high" else "is_pl"
    if col not in f.columns:
        return []
    lo = max(w, i - max_back)
    hi = i - w                      # j + w <= i  ⇒  j <= i - w
    if hi < lo:
        return []
    arr = f[col].to_numpy(dtype=bool)
    idxs = np.flatnonzero(arr[lo:hi + 1]) + lo
    if len(idxs) == 0:
        return []
    return [int(x) for x in idxs[-need:]]


def _divergence(f: pd.DataFrame, i: int, kind: str, w: int = 5) -> Optional[Dict[str, Any]]:
    """
    检测量价背离（基于相邻两个波段枢轴点）。

    top  : 后一个价格高点更高，但 OBV/MFI 反而更低 → 顶背离
    bottom: 后一个价格低点更低，但 OBV/MFI 反而更高 → 底背离
    """
    pivots = _find_pivots(f, i, w, "high" if kind == "top" else "low")
    if len(pivots) < 2:
        return None
    j1, j2 = pivots[-2], pivots[-1]
    if i - j2 > w + 8:          # 信号必须新鲜，太旧的枢轴没有意义
        return None

    px_key = "high" if kind == "top" else "low"
    p1 = _safe(f[px_key].iloc[j1])
    p2 = _safe(f[px_key].iloc[j2])
    o1, o2 = _safe(f["obv"].iloc[j1]), _safe(f["obv"].iloc[j2])
    m1, m2 = _safe(f["mfi"].iloc[j1]), _safe(f["mfi"].iloc[j2])
    if min(p1, p2) <= 0:
        return None

    if kind == "top":
        new_high = p2 > p1 * 1.01
        obv_div = o2 < o1 * 0.995
        mfi_div = m2 < m1 - 8
        if not (new_high and (obv_div or mfi_div)):
            return None
    else:
        new_low = p2 < p1 * 0.99
        obv_div = o2 > o1 * 1.005
        mfi_div = m2 > m1 + 8
        if not (new_low and (obv_div or mfi_div)):
            return None

    return {
        "p1": p1, "p2": p2, "o1": o1, "o2": o2, "m1": m1, "m2": m2,
        "idx1": int(j1), "idx2": int(j2),
        "date1": str(f["date"].iloc[j1]), "date2": str(f["date"].iloc[j2]),
        "obv_div": bool(obv_div), "mfi_div": bool(mfi_div),
        "diff_pct": (p2 / p1 - 1) * 100,
    }


def detect_at(f: pd.DataFrame, i: int, params: Optional[Dict[str, Any]] = None) -> List[VolPriceSignal]:
    """在第 i 根 K 线检测量价信号（只用 <= i 的数据）。"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    pw = int(p.get("pivot_w", 5))
    out: List[VolPriceSignal] = []
    if i < 60 or i >= len(f):
        return out

    date = str(f["date"].iloc[i])
    close = _safe(f["close"].iloc[i])
    open_ = _safe(f["open"].iloc[i])
    high = _safe(f["high"].iloc[i])
    low = _safe(f["low"].iloc[i])
    vol = _safe(f["volume"].iloc[i])
    if close <= 0 or vol <= 0:
        return out

    vr = _safe(f["vr"].iloc[i])
    ma20 = _safe(f["ma20"].iloc[i])
    ma60 = _safe(f["ma60"].iloc[i])
    mfi = _safe(f["mfi"].iloc[i], 50.0)
    cmf = _safe(f["cmf"].iloc[i], 0.0)
    obv = _safe(f["obv"].iloc[i])
    chg = _safe(f["chg"].iloc[i], 0.0)
    dd60 = _safe(f["dd60"].iloc[i], 0.0)
    up_sh = _safe(f["upper_shadow"].iloc[i], 0.0)
    lo_sh = _safe(f["lower_shadow"].iloc[i], 0.0)
    cpos = _safe(f["close_pos"].iloc[i], 0.5)
    hh20 = _safe(f["hh20"].iloc[i])
    ll20 = _safe(f["ll20"].iloc[i])
    rng = _safe(f["rng"].iloc[i], 0.0)

    is_up = close > open_
    is_dn = close < open_

    # ================================================================ 买入
    # VP_B1 放量突破
    if (_has(f, i, "vr", "ma20", "ma60", "hh20") and vr >= p["breakout_vol"]
            and close > hh20 and is_up and close > ma20 and ma20 > ma60):
        conf = 52.0
        reasons = [f"放量 {vr:.2f} 倍突破 20 日新高", "收盘价站上 MA20 且均线多头排列"]
        conf += min((vr - p["breakout_vol"]) * 12, 12)
        if hh20 > 0:
            conf += min((close / hh20 - 1) * 400, 8)
        if cmf > 0:
            conf += 8
            reasons.append(f"CMF={cmf:+.2f}，资金净流入")
        if cpos > 0.75:
            conf += 6
            reasons.append("收盘接近当日最高，买盘强势")
        if mfi > 80:
            conf -= 8
            reasons.append(f"但 MFI={mfi:.0f} 已超买，追高风险")
        _add(out, _mk("VP_B1", f, i, date, close, conf, p, reasons,
                       {"vr": vr, "cmf": cmf, "mfi": mfi, "dd60": dd60}))

    # VP_B2 缩量回踩
    look = int(p["dryup_lookback"])
    if (_has(f, i, "vr", "ma20", "ma60") and vr <= p["dryup_vol"]
            and ma20 > ma60 and close > ma20):
        recent_high = _safe(f["high"].iloc[i - look: i].max() if i >= look else high, high)
        pulled_back = close < recent_high
        near_ma = abs(close / ma20 - 1) < 0.05 if ma20 > 0 else False
        stabilized = is_up or cpos > 0.6
        if pulled_back and (near_ma or stabilized):
            conf = 50.0
            reasons = [f"缩量至 {vr:.2f} 倍均量", "回踩 MA20 附近但仍在均线上方，上升结构未破"]
            conf += min((p["dryup_vol"] - vr) * 30, 12)
            if stabilized:
                conf += 8
                reasons.append("当日收阳/收于振幅上半部，出现止跌")
            if cmf > -0.05:
                conf += 6
                reasons.append(f"CMF={cmf:+.2f}，未出现资金持续流出")
            _add(out, _mk("VP_B2", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "cmf": cmf, "mfi": mfi}))

    # VP_B3 地量止跌
    win = int(p["lowvol_window"])
    if _has(f, i, "vr", "ma60", "dd60") and vr <= p["lowvol_vol"]:
        is_lowest = False
        if i >= win:
            vol_slice = f["volume"].iloc[i - win: i + 1]
            is_lowest = vol <= vol_slice.min() * 1.02
        low_position = close < ma60 or dd60 < -0.08
        stabilized = chg >= 0 or lo_sh > 0.35
        if is_lowest and low_position and stabilized:
            conf = 50.0
            reasons = [f"成交量萎缩至 {vr:.2f} 倍均量，创 {win} 日地量",
                       f"近 60 日回撤 {dd60 * 100:.1f}%，处于相对低位"]
            conf += min((p["lowvol_vol"] - vr) * 30, 10)
            if is_up:
                conf += 8
                reasons.append("当日收阳，抛压衰竭")
            if lo_sh > 0.35:
                conf += 6
                reasons.append("下影线较长，下方有承接")
            _add(out, _mk("VP_B3", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "dd60": dd60, "mfi": mfi}))

    # VP_B4 底部放量反转
    if _has(f, i, "vr", "dd60") and dd60 < -0.12:
        big_green = is_up and chg > 3.0 and vr >= 2.0
        long_lower = lo_sh > 0.4 and vr >= 1.5
        if big_green or long_lower:
            conf = 52.0
            reasons = [f"近 60 日已回撤 {dd60 * 100:.1f}%", f"放量 {vr:.2f} 倍"]
            if big_green:
                conf += 12
                reasons.append(f"放量大阳线（+{chg:.1f}%），资金进场信号")
            if long_lower:
                conf += 10
                reasons.append("长下影线，低位有明显承接盘")
            if cmf > 0 or mfi > 35:
                conf += 8
                reasons.append(f"CMF={cmf:+.2f} / MFI={mfi:.0f}，资金面转暖")
            _add(out, _mk("VP_B4", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "dd60": dd60, "cmf": cmf, "mfi": mfi}))

    # VP_B5 量价底背离（基于波段枢轴点）
    div = _divergence(f, i, "bottom", pw)
    if div:
        conf = 52.0
        reasons = [
            f"价格自 {div['date1']} 的 {div['p1']:.2f} 下探至 {div['date2']} 的 {div['p2']:.2f}"
            f"（{div['diff_pct']:+.1f}%），创出更低的低点"
        ]
        if div["obv_div"]:
            conf += 12
            reasons.append("但 OBV 未同步创新低，说明下跌并未伴随更大抛压")
        if div["mfi_div"]:
            conf += 10
            reasons.append(f"MFI 自 {div['m1']:.0f} 回升至 {div['m2']:.0f}，资金开始回流")
        if mfi < 30:
            conf += 6
            reasons.append(f"MFI={mfi:.0f} 处于超卖区")
        # 背离信号需等右肩走完才确认，天然滞后；过久则失去意义
        age = i - div["idx2"]
        if age > 18:
            conf = -1          # 已错过时机，不再提示
        elif age > 12:
            conf -= 15
            reasons.append(f"距低点已 {age} 根 K 线，最佳买点可能已过")
        if conf > 0:
            # 形态证伪位：跌破前一个低点即失效
            _add(out, _mk("VP_B5", f, i, date, close, conf, p, reasons,
                          {"mfi": mfi, "dd60": dd60, "div": div},
                          structure_stop=div["p2"] * 0.99))

    # ================================================================ 卖出
    # VP_S1 放量滞涨
    if _has(f, i, "vr", "hh20", "ma20") and vr >= 1.5 and close > ma20 and hh20 > 0:
        near_high = close > hh20 * 0.97
        stall = chg < 1.0 or is_dn or up_sh > 0.45
        if near_high and stall:
            conf = 50.0
            reasons = [f"放量 {vr:.2f} 倍却未能有效上涨（当日 {chg:+.1f}%）"]
            if is_dn:
                conf += 12
                reasons.append("高位放量收阴，买盘承接不足")
            if up_sh > 0.45:
                conf += 10
                reasons.append(f"上影线占振幅 {up_sh * 100:.0f}%，上方抛压重")
            if chg < 1.0 and not is_dn:
                conf += 6
                reasons.append("量增价滞，上涨动能衰减")
            if mfi > 75:
                conf += 6
                reasons.append(f"MFI={mfi:.0f} 超买")
            _add(out, _mk("VP_S1", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "chg": chg, "up_shadow": up_sh, "mfi": mfi}))

    # VP_S2 量价顶背离（基于波段枢轴点）
    div = _divergence(f, i, "top", pw)
    if div:
        conf = 52.0
        reasons = [
            f"价格自 {div['date1']} 的 {div['p1']:.2f} 推高至 {div['date2']} 的 {div['p2']:.2f}"
            f"（{div['diff_pct']:+.1f}%），创出更高的高点"
        ]
        if div["obv_div"]:
            conf += 12
            reasons.append("但 OBV 未同步创新高，增量资金不足")
        if div["mfi_div"]:
            conf += 10
            reasons.append(f"MFI 自 {div['m1']:.0f} 回落至 {div['m2']:.0f}，资金流与价格背离")
        if cmf < 0:
            conf += 6
            reasons.append(f"CMF={cmf:+.2f}，资金净流出")
        # 背离信号需等右肩走完才确认，天然滞后；过久则失去意义
        age = i - div["idx2"]
        if age > 18:
            conf = -1          # 已错过时机，不再提示
        elif age > 12:
            conf -= 15
            reasons.append(f"距高点已 {age} 根 K 线，最佳卖点可能已过")
        if conf > 0:
            # 形态证伪位：突破前一个高点即失效
            _add(out, _mk("VP_S2", f, i, date, close, conf, p, reasons,
                          {"mfi": mfi, "cmf": cmf, "dd60": dd60, "div": div},
                          structure_stop=div["p2"] * 1.01))

    # VP_S3 破位放量
    if _has(f, i, "vr", "ma20", "ll20") and vr >= 1.3:
        below_ma = close < ma20
        broke_low = close < ll20
        if is_dn and (below_ma or broke_low):
            conf = 50.0
            reasons = [f"放量 {vr:.2f} 倍下跌"]
            if broke_low:
                conf += 14
                reasons.append("跌破 20 日最低价，支撑位失守")
            else:
                conf += 8
                reasons.append("跌破 MA20，短期均线支撑失效")
            if vr >= 2.0:
                conf += 8
                reasons.append("放量幅度大，抛压集中释放")
            if cmf < 0:
                conf += 6
                reasons.append(f"CMF={cmf:+.2f}，资金持续流出")
            _add(out, _mk("VP_S3", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "cmf": cmf, "dd60": dd60}))

    # VP_S4 天量见顶
    if _has(f, i, "vr", "dd60") and vr >= p["heavy_vol"]:
        is_heaviest = False
        if i >= 60:
            is_heaviest = vol >= f["volume"].iloc[i - 60: i + 1].max() * 0.995
        high_pos = dd60 > -0.08
        no_follow = is_dn or up_sh > 0.4 or chg < 2.0
        if is_heaviest and high_pos and no_follow:
            conf = 54.0
            reasons = [f"成交量放大至 {vr:.2f} 倍，创 60 日天量",
                       "股价处于阶段高位"]
            if is_dn:
                conf += 12
                reasons.append("天量收阴，典型的高位换手出货")
            elif up_sh > 0.4:
                conf += 10
                reasons.append("天量长上影，冲高回落")
            else:
                conf += 5
                reasons.append(f"天量仅涨 {chg:+.1f}%，量能未转化为涨幅")
            _add(out, _mk("VP_S4", f, i, date, close, conf, p, reasons,
                           {"vr": vr, "dd60": dd60, "chg": chg}))

    # VP_S5 高位放量长阴
    if _has(f, i, "vr", "ma20") and vr >= 1.8 and chg < -3.0 and close > ma20:
        conf = 54.0
        reasons = [f"放量大阴线（{chg:.1f}%，{vr:.2f} 倍量）", "股价仍处 MA20 上方的高位区"]
        if cpos < 0.2:
            conf += 10
            reasons.append("收盘接近当日最低，空头完全主导")
        if vr >= 2.5:
            conf += 8
            reasons.append("量能显著放大，出货特征明显")
        if cmf < -0.1:
            conf += 6
            reasons.append(f"CMF={cmf:+.2f}，资金大幅流出")
        _add(out, _mk("VP_S5", f, i, date, close, conf, p, reasons,
                       {"vr": vr, "chg": chg, "cmf": cmf, "dd60": dd60}))

    return out


def _mk(code: str, f: pd.DataFrame, i: int, date: str, price: float,
        conf: float, params: Dict[str, Any], reasons: List[str],
        metrics: Dict[str, Any],
        structure_stop: Optional[float] = None) -> Optional[VolPriceSignal]:
    """
    统一构造信号：计算止损目标、按风险扣分、裁剪置信度。
    止损空间超过 max_risk_pct 的 1.8 倍时直接放弃该信号（风险不可控）。
    """
    meta = SIGNAL_META[code]
    side = meta["side"]
    stop, target, risk_pct = _calc_levels(f, i, side, params, structure_stop)

    score = conf
    maxr = float(params.get("max_risk_pct", 0.15))
    if risk_pct is not None:
        if risk_pct > maxr * 1.8:
            return None
        if risk_pct > maxr:
            score -= min((risk_pct - maxr) * 250, 25)
            reasons.append(f"止损空间 {risk_pct * 100:.1f}% 偏大，建议降低仓位")

    score = max(0.0, min(100.0, score))
    return VolPriceSignal(
        code=code, side=side, date=date, raw_idx=i, price=price,
        confidence=score, score=score, stop=stop, target=target,
        reasons=reasons, metrics={k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                                  for k, v in metrics.items()},
    )


def _add(out: List[VolPriceSignal], sig: Optional[VolPriceSignal]) -> None:
    """_mk 可能因风险过大返回 None，这里统一过滤。"""
    if sig is not None:
        out.append(sig)


# ----------------------------------------------------------------------------
# 对外主入口
# ----------------------------------------------------------------------------
def analyze(
    df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    min_confidence: Optional[float] = None,
    chan_signals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    分析最新一根 K 线的量价状态并给出买卖提醒。

    chan_signals: 缠论信号列表，用于共振加分（同向则提升置信度）。
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    if df is None or len(df) < 80:
        return {"ok": False, "error": "历史数据不足（需至少 80 根 K 线）", "signals": []}

    f = compute_features(df, pivot_w=int(p.get("pivot_w", 5)))
    i = len(f) - 1
    sigs = detect_at(f, i, p)

    # 与缠论共振
    if chan_signals:
        chan_side = set()
        for s in chan_signals:
            code = str(s.get("code", "")).upper()
            side = s.get("side")
            if side:
                chan_side.add(str(side).lower())
            elif code.startswith("B"):
                chan_side.add("buy")
            elif code.startswith("S"):
                chan_side.add("sell")
        for s in sigs:
            if s.side in chan_side:
                s.resonance = True
                s.confidence = min(100.0, s.confidence + 12)
                s.score = s.confidence
                s.reasons.append("与缠论买卖点同向共振，可信度提升")

    thr = float(min_confidence if min_confidence is not None else p.get("min_confidence", 55))
    sigs = [s for s in sigs if s.confidence >= thr]
    sigs.sort(key=lambda x: -x.confidence)

    last = f.iloc[-1]
    state = {
        "vr": _round(_safe(last.get("vr"))),
        "vol": _safe(last.get("volume")),
        "vol_ma20": _safe(last.get("vol_ma20")),
        "mfi": _round(_safe(last.get("mfi"), 50.0)),
        "cmf": _round(_safe(last.get("cmf"), 0.0), 3),
        "close_pos": _round(_safe(last.get("close_pos"), 0.5), 2),
        "ma20": _round(_safe(last.get("ma20"))),
        "ma60": _round(_safe(last.get("ma60"))),
        "dd60": _round(_safe(last.get("dd60"), 0.0), 4),
    }
    return {
        "ok": True,
        "symbol": "",
        "date": str(last["date"]),
        "price": _safe(last["close"]),
        "state": state,
        "relation": describe_relation(f, i),
        "signals": [s.to_dict() for s in sigs],
    }


def describe_relation(f: pd.DataFrame, i: int) -> Dict[str, Any]:
    """用一句话概括当前量价配合状态（供前端展示）。"""
    chg = _safe(f["chg"].iloc[i], 0.0)
    vr = _safe(f["vr"].iloc[i], 1.0)
    if chg >= 0 and vr >= 1.2:
        label, tone = "价涨量增", "good"
        note = "量价配合健康，上涨有量能支撑"
    elif chg >= 0 and vr < 1.2:
        label, tone = "价涨量缩", "warn"
        note = "上涨缺乏量能配合，注意动能衰减"
    elif chg < 0 and vr >= 1.2:
        label, tone = "价跌量增", "bad"
        note = "放量下跌，抛压明显，谨慎接刀"
    else:
        label, tone = "价跌量缩", "neutral"
        note = "缩量下跌，抛压逐步衰竭，观察企稳信号"
    return {"label": label, "tone": tone, "note": note,
            "chg": _round(chg, 2), "vr": _round(vr, 2)}


def scan_history(
    df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    min_confidence: Optional[float] = None,
    confirm_delay: int = 1,
    min_gap: int = 3,
    side: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    扫描全部历史，产出信号序列（回测用）。

    confirm_delay: 信号确认延迟（根）。默认为 1，表示第 i 根收盘确认、
                   最早在第 i+1 根开盘成交，避免用收盘价成交的偷价问题。
    min_gap: 同方向信号的最小间隔（根），避免连续重复触发。
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    if df is None or len(df) < 120:
        return []
    f = compute_features(df, pivot_w=int(p.get("pivot_w", 5)))
    thr = float(min_confidence if min_confidence is not None else p.get("min_confidence", 55))

    out: List[Dict[str, Any]] = []
    last_idx = {"buy": -999, "sell": -999}
    for i in range(80, len(f)):
        for s in detect_at(f, i, p):
            if s.confidence < thr:
                continue
            if side and s.side != side:
                continue
            if i - last_idx[s.side] < min_gap:
                continue
            last_idx[s.side] = i
            d = s.to_dict()
            d["signal_idx"] = i
            d["entry_idx"] = i + confirm_delay
            out.append(d)
    return out


def volume_bars(df: pd.DataFrame, n: int = 120) -> List[Dict[str, Any]]:
    """前端量能柱图数据：最近 n 根 K 线的量、量比、涨跌。"""
    f = compute_features(df)
    tail = f.tail(n).reset_index(drop=True)
    bars = []
    for _, r in tail.iterrows():
        bars.append({
            "d": str(r["date"]),
            "v": _safe(r["volume"]),
            "vr": _round(_safe(r["vr"]), 2),
            "c": _safe(r["close"]),
            "up": bool(_safe(r["chg"], 0.0) >= 0),
        })
    return bars


def _round(x: Any, nd: int = 2) -> Optional[float]:
    v = _safe(x, float("nan"))
    if np.isnan(v):
        return None
    return round(v, nd)
