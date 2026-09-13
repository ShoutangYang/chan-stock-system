"""
自动选股策略层：超跌 / 趋势突破 / 反转。

每个扫描器返回 StrategyHit（含评分与命中原因），评分 0-100。
评分由「条件加权累加」构成，便于后续调参与解释。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from . import volprice as vp
from .chanlun import ChanEngine

STRATEGY_META = {
    "oversold": {
        "name": "超跌反弹",
        "desc": "深度回撤 + 超卖 + 缠论底背驰，捕捉跌透后的反弹机会",
        "color": "#2563eb",
    },
    "breakout": {
        "name": "趋势突破",
        "desc": "放量突破唐奇安通道上轨，均线多头排列，顺势跟随",
        "color": "#dc2626",
    },
    "reversal": {
        "name": "底部反转",
        "desc": "缠论一二类买点 + K线反转形态 + MACD 背离，确认转折",
        "color": "#059669",
    },
    "volprice": {
        "name": "量价共振",
        "desc": "放量突破 / 缩量回踩 / 地量止跌 / 量价底背离，用量能验证价格",
        "color": "#7c3aed",
    },
}

# 命中阈值：低于该分数不进入结果
MIN_SCORE = {
    "oversold": 55,
    "breakout": 60,
    "reversal": 55,
    "volprice": 58,
}


@dataclass
class StrategyHit:
    strategy: str
    symbol: str
    score: float
    price: float
    change_pct: float
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    signals: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "strategy_name": STRATEGY_META[self.strategy]["name"],
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "price": self.price,
            "change_pct": self.change_pct,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "signals": self.signals,
        }


# ----------------------------------------------------------------------------
# 公共指标预计算
# ----------------------------------------------------------------------------
def prepare(df: pd.DataFrame) -> Dict[str, Any]:
    """一次性算出所有扫描器需要的指标，避免重复计算。"""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    dif, dea, hist = ind.macd(close)
    ma5 = ind.sma(close, 5)
    ma10 = ind.sma(close, 10)
    ma20 = ind.sma(close, 20)
    ma60 = ind.sma(close, 60)
    boll_up, boll_mid, boll_low = ind.boll(close)
    rsi14 = ind.rsi(close, 14)
    vr = ind.volume_ratio(vol, 20)
    dc_up20, dc_low20 = ind.donchian(df, 20)
    dc_up55, dc_low55 = ind.donchian(df, 55)
    adx14 = ind.adx(df, 14)
    atr14 = ind.atr(df, 14)
    dd252 = ind.drawdown_from_high(close, 252)
    dd60 = ind.drawdown_from_high(close, 60)

    return {
        "df": df,
        "close": close,
        "high": high,
        "low": low,
        "vol": vol,
        "dif": dif,
        "dea": dea,
        "hist": hist,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "boll_up": boll_up,
        "boll_mid": boll_mid,
        "boll_low": boll_low,
        "rsi": rsi14,
        "vr": vr,
        "dc_up20": dc_up20,
        "dc_low20": dc_low20,
        "dc_up55": dc_up55,
        "dc_low55": dc_low55,
        "adx": adx14,
        "atr": atr14,
        "dd252": dd252,
        "dd60": dd60,
    }


def _f(x, default: float = float("nan")) -> float:
    """安全取最后一个值。"""
    try:
        v = float(x.iloc[-1]) if hasattr(x, "iloc") else float(x)
        return v
    except Exception:
        return default


def _prev(x, n: int = 1, default: float = float("nan")) -> float:
    try:
        return float(x.iloc[-1 - n])
    except Exception:
        return default


# ----------------------------------------------------------------------------
# K线形态
# ----------------------------------------------------------------------------
def candle_patterns(df: pd.DataFrame) -> List[str]:
    """识别最后一根K线的反转形态。"""
    if len(df) < 2:
        return []
    out = []
    o, h, l, c = [float(df[k].iloc[-1]) for k in ("open", "high", "low", "close")]
    po, pc = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])

    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return out

    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)

    # 锤子线 / 倒锤子
    if lower_shadow > 2 * body and upper_shadow < body * 0.8 and body / rng < 0.4:
        out.append("锤子线(下影线长，下方有承接)")
    if upper_shadow > 2 * body and lower_shadow < body * 0.8 and body / rng < 0.4:
        out.append("上吊线(上影线长，上方有压力)")

    # 看涨吞没
    if pc < po and c > o and c >= po and o <= pc:
        out.append("看涨吞没(阳线吞没前一日阴线)")
    # 看跌吞没
    if pc > po and c < o and c <= po and o >= pc:
        out.append("看跌吞没(阴线吞没前一日阳线)")

    # 十字星
    if body / rng < 0.1:
        out.append("十字星(多空胶着)")

    return out


# ----------------------------------------------------------------------------
# 策略一：超跌反弹
# ----------------------------------------------------------------------------
def detect_oversold(p: Dict[str, Any], chan: Dict[str, Any], symbol: str) -> Optional[StrategyHit]:
    score = 0.0
    reasons: List[str] = []
    price = _f(p["close"])
    if not np.isfinite(price) or price <= 0:
        return None

    dd252 = _f(p["dd252"])
    dd60 = _f(p["dd60"])

    # 1. 回撤深度（最高 40 分）
    if np.isfinite(dd252):
        if dd252 <= -0.40:
            score += 40
            reasons.append(f"距52周高点回撤 {dd252*100:.1f}%，跌幅充分")
        elif dd252 <= -0.30:
            score += 32
            reasons.append(f"距52周高点回撤 {dd252*100:.1f}%")
        elif dd252 <= -0.20:
            score += 24
            reasons.append(f"距52周高点回撤 {dd252*100:.1f}%")
        elif dd252 <= -0.12:
            score += 12
            reasons.append(f"距52周高点回撤 {dd252*100:.1f}%")

    # 2. 短期超跌（最高 15 分）
    if np.isfinite(dd60) and dd60 <= -0.20:
        score += 15
        reasons.append(f"近60日再跌 {dd60*100:.1f}%，短期杀跌明显")
    elif np.isfinite(dd60) and dd60 <= -0.12:
        score += 8

    # 3. RSI 超卖（最高 25 分）
    rsi = _f(p["rsi"])
    if np.isfinite(rsi):
        if rsi < 25:
            score += 25
            reasons.append(f"RSI {rsi:.1f} 严重超卖")
        elif rsi < 30:
            score += 20
            reasons.append(f"RSI {rsi:.1f} 超卖")
        elif rsi < 35:
            score += 12
            reasons.append(f"RSI {rsi:.1f} 接近超卖区")

    # 4. 价格触及/跌破布林下轨（最高 15 分）
    bl = _f(p["boll_low"])
    if np.isfinite(bl) and price <= bl * 1.01:
        score += 15
        reasons.append("价格触及布林带下轨，短线偏离均值过大")

    # 5. 缠论底背驰 / 一类二类买点（最高 35 分）
    sigs = chan.get("signals", [])
    codes = {s["code"]: s for s in sigs}
    if "B1" in codes:
        score += 35
        reasons.append(f"缠论{codes['B1']['name']} @ {codes['B1']['price']:.2f}（{codes['B1']['reason'][:40]}…）")
    elif any(d.get("kind") == "bull" for d in chan.get("divergences", [])):
        score += 18
        reasons.append("出现底背驰信号，下跌动能衰竭")
    if "B2" in codes:
        score += 12
        reasons.append(f"缠论{codes['B2']['name']} @ {codes['B2']['price']:.2f}")

    # 6. 底部放量（最高 12 分）
    vr = _f(p["vr"])
    if np.isfinite(vr) and vr >= 1.5:
        score += 12
        reasons.append(f"成交量放大至20日均量的 {vr:.2f} 倍，有资金介入")
    elif np.isfinite(vr) and vr >= 1.2:
        score += 6

    # 7. K线止跌形态（最高 12 分）
    pats = candle_patterns(p["df"])
    for pt in pats:
        if pt.startswith("锤子线") or pt.startswith("看涨吞没"):
            score += 12
            reasons.append(f"K线形态：{pt}")
            break

    # 8. 扣分：仍在加速下跌（无背驰且创新低）
    if not any(d.get("kind") == "bull" for d in chan.get("divergences", [])) and vr and np.isfinite(vr) and vr > 2 and np.isfinite(dd60) and dd60 < -0.25:
        score -= 10
        reasons.append("注意：放量下跌且未见背驰，可能尚未止跌")

    if score < MIN_SCORE["oversold"]:
        return None

    # 只保留最近有买点意义的信号
    buy_sigs = [s for s in sigs if s["side"] == "buy"]

    return StrategyHit(
        strategy="oversold",
        symbol=symbol,
        score=min(100.0, score),
        price=price,
        change_pct=pct_change_today(p),
        reasons=reasons,
        metrics={
            "rsi": rsi,
            "dd_52w": dd252,
            "dd_60d": dd60,
            "volume_ratio": vr,
            "boll_low": bl,
            "trend": chan.get("trend"),
        },
        signals=buy_sigs,
    )


# ----------------------------------------------------------------------------
# 策略二：趋势突破
# ----------------------------------------------------------------------------
def detect_breakout(p: Dict[str, Any], chan: Dict[str, Any], symbol: str) -> Optional[StrategyHit]:
    score = 0.0
    reasons: List[str] = []
    price = _f(p["close"])
    if not np.isfinite(price) or price <= 0:
        return None

    du20, du55 = _f(p["dc_up20"]), _f(p["dc_up55"])
    ma20, ma60 = _f(p["ma20"]), _f(p["ma60"])
    vr = _f(p["vr"])
    adx = _f(p["adx"])

    # 1. 突破唐奇安上轨（最高 45 分）
    broke55 = np.isfinite(du55) and price > du55
    broke20 = np.isfinite(du20) and price > du20
    if broke55:
        score += 45
        reasons.append(f"放量突破55日高点 {du55:.2f}，创中期新高")
    elif broke20:
        score += 32
        reasons.append(f"突破20日高点 {du20:.2f}")

    # 2. 成交量确认（最高 25 分）
    if np.isfinite(vr):
        if vr >= 2.0:
            score += 25
            reasons.append(f"成交量达20日均量 {vr:.2f} 倍，资金进场坚决")
        elif vr >= 1.5:
            score += 18
            reasons.append(f"成交量达20日均量 {vr:.2f} 倍")
        elif vr >= 1.2:
            score += 10
    else:
        score += 0

    # 3. 均线多头排列（最高 20 分）
    if np.isfinite(ma20) and np.isfinite(ma60):
        if ma20 > ma60 and price > ma20:
            score += 20
            reasons.append("均线多头排列（MA20 > MA60）且股价站上MA20")
        elif price > ma20:
            score += 10
            reasons.append("股价站上MA20")

    # 4. MACD 强势（最高 12 分）
    dif, dea, hist = _f(p["dif"]), _f(p["dea"]), _f(p["hist"])
    if np.isfinite(dif) and np.isfinite(dea):
        if dif > dea and dif > 0:
            score += 12
            reasons.append("MACD 在零轴上方金叉，多头动能强")
        elif dif > dea:
            score += 7
            reasons.append("MACD 金叉")

    # 5. 趋势强度（最高 10 分）
    if np.isfinite(adx) and adx > 25:
        score += 10
        reasons.append(f"ADX {adx:.1f} 趋势明确")
    elif np.isfinite(adx) and adx > 20:
        score += 5

    # 6. 缠论状态（最高 22 分，可负分）
    sigs = chan.get("signals", [])
    codes = {s["code"]: s for s in sigs}
    if chan.get("trend") == "up":
        score += 10
        reasons.append("缠论结构处于上涨趋势")
    if "B3" in codes:
        score += 22
        reasons.append(f"缠论{codes['B3']['name']} @ {codes['B3']['price']:.2f}（回调未回中枢）")
    if "S1" in codes:
        score -= 25
        reasons.append(f"警告：出现{codes['S1']['name']}，追高需谨慎")
    if "S3" in codes:
        score -= 15
        reasons.append(f"警告：出现{codes['S3']['name']}")

    if score < MIN_SCORE["breakout"]:
        return None

    return StrategyHit(
        strategy="breakout",
        symbol=symbol,
        score=min(100.0, score),
        price=price,
        change_pct=pct_change_today(p),
        reasons=reasons,
        metrics={
            "donchian_up20": du20,
            "donchian_up55": du55,
            "volume_ratio": vr,
            "ma20": ma20,
            "ma60": ma60,
            "adx": adx,
            "trend": chan.get("trend"),
        },
        signals=[s for s in sigs if s["side"] == "buy"],
    )


# ----------------------------------------------------------------------------
# 策略三：底部反转
# ----------------------------------------------------------------------------
def detect_reversal(p: Dict[str, Any], chan: Dict[str, Any], symbol: str) -> Optional[StrategyHit]:
    score = 0.0
    reasons: List[str] = []
    price = _f(p["close"])
    if not np.isfinite(price) or price <= 0:
        return None

    sigs = chan.get("signals", [])
    codes = {s["code"]: s for s in sigs}

    # 1. 缠论买点（最高 40 分）
    if "B1" in codes:
        score += 40
        reasons.append(f"缠论{codes['B1']['name']} @ {codes['B1']['price']:.2f}：{codes['B1']['reason'][:50]}")
    if "B2" in codes:
        score += 22
        reasons.append(f"缠论{codes['B2']['name']} @ {codes['B2']['price']:.2f}")
    if "B3" in codes:
        score += 18
        reasons.append(f"缠论{codes['B3']['name']} @ {codes['B3']['price']:.2f}")

    # 2. 底背驰（最高 20 分）
    bull_divs = [d for d in chan.get("divergences", []) if d.get("kind") == "bull"]
    if bull_divs:
        d = bull_divs[-1]
        score += 20
        reasons.append(
            f"MACD 底背驰：柱面积由 {d['prev_power']:.3f} 衰减至 {d['curr_power']:.3f}（比值 {d['ratio']:.2f}）"
        )

    # 3. 分型确认（最高 15 分）
    fractals = chan.get("fractals", [])
    if fractals and fractals[-1]["kind"] == "bottom":
        score += 8
        reasons.append(f"日线出现底分型（{fractals[-1]['date']}）")
    # 最近 5 根内出现底分型
    if any(f["kind"] == "bottom" for f in fractals[-3:]):
        score += 7
        reasons.append("近期形成底分型结构，下跌笔结束")

    # 4. MACD 金叉 / 柱翻红（最高 15 分）
    dif, dea, hist = _f(p["dif"]), _f(p["dea"]), _f(p["hist"])
    phist = _prev(p["hist"])
    if np.isfinite(hist) and np.isfinite(phist) and phist < 0 <= hist:
        score += 15
        reasons.append("MACD 柱由绿翻红，动能转向")
    elif np.isfinite(dif) and np.isfinite(dea) and dif > dea and _prev(p["dif"]) <= _prev(p["dea"]):
        score += 12
        reasons.append("MACD 金叉")

    # 5. RSI 脱离超卖（最高 15 分）
    rsi, prsi = _f(p["rsi"]), _prev(p["rsi"])
    if np.isfinite(rsi) and np.isfinite(prsi) and prsi < 35 <= rsi:
        score += 15
        reasons.append(f"RSI 由 {prsi:.1f} 回升至 {rsi:.1f}，脱离超卖区")
    elif np.isfinite(rsi) and np.isfinite(prsi) and rsi > prsi and rsi < 50:
        score += 6

    # 6. K线反转形态（最高 15 分）
    pats = candle_patterns(p["df"])
    for pt in pats:
        if pt.startswith("锤子线") or pt.startswith("看涨吞没"):
            score += 15
            reasons.append(f"K线形态：{pt}")
            break

    # 7. 站上短期均线（最高 12 分）
    ma5, ma10 = _f(p["ma5"]), _f(p["ma10"])
    if np.isfinite(ma5) and price > ma5 and _prev(p["close"]) <= _prev(p["ma5"]):
        score += 12
        reasons.append("收盘价上穿MA5，短期转强")
    elif np.isfinite(ma10) and price > ma10:
        score += 6

    # 8. 前置下跌（反转需要前提）（最高 10 分）
    dd60 = _f(p["dd60"])
    if np.isfinite(dd60) and dd60 <= -0.15:
        score += 10
        reasons.append(f"前期自高点回落 {dd60*100:.1f}%，具备反转空间")

    # 9. 扣分：出现卖点
    if "S1" in codes or "S2" in codes:
        score -= 20
        reasons.append("警告：同时存在缠论卖点，反转未确认")

    if score < MIN_SCORE["reversal"]:
        return None

    return StrategyHit(
        strategy="reversal",
        symbol=symbol,
        score=min(100.0, score),
        price=price,
        change_pct=pct_change_today(p),
        reasons=reasons,
        metrics={
            "rsi": rsi,
            "macd_hist": hist,
            "dd_60d": dd60,
            "ma5": ma5,
            "ma10": ma10,
            "trend": chan.get("trend"),
            "patterns": pats,
        },
        signals=[s for s in sigs if s["side"] == "buy"],
    )


# ----------------------------------------------------------------------------
# 量价关系
# ----------------------------------------------------------------------------
def run_volprice(
    df: pd.DataFrame,
    chan: Optional[Dict[str, Any]] = None,
    vp_params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """执行一次量价分析（买/卖信号 + 量价状态 + 量能图数据）。"""
    try:
        res = vp.analyze(df, params=vp_params,
                         chan_signals=(chan or {}).get("signals"))
        if not res.get("ok"):
            return None
        res["bars"] = vp.volume_bars(df, 120)
        return res
    except Exception:  # noqa: BLE001
        return None


def detect_volprice(
    p: Dict[str, Any],
    chan: Dict[str, Any],
    symbol: str,
    vp_params: Optional[Dict[str, Any]] = None,
) -> Optional[StrategyHit]:
    """
    量价选股：只把**买入**形态作为选股命中。
    卖出形态属于风险提示，走独立的「量价提醒」通道，不参与选股排名。
    """
    res = run_volprice(p["df"], chan, vp_params)
    if not res:
        return None
    buys = [s for s in res["signals"] if s["side"] == "buy"]
    if not buys:
        return None

    top = buys[0]
    if top["confidence"] < MIN_SCORE["volprice"]:
        return None

    reasons = [f"{top['name']}（{top['code']}）"]
    reasons.extend(top["reasons"])
    if top.get("resonance"):
        reasons.append("与缠论买点共振")

    metrics = {
        "量比": _round(top["metrics"].get("vr")),
        "MFI": _round(top["metrics"].get("mfi")),
        "CMF": _round(top["metrics"].get("cmf"), 3),
        "止损": _round(top.get("stop")),
        "目标": _round(top.get("target")),
        "量价状态": res["relation"]["label"],
    }
    return StrategyHit(
        strategy="volprice",
        symbol=symbol,
        score=top["confidence"],
        price=top["price"],
        change_pct=pct_change_today(p),
        reasons=reasons,
        metrics=metrics,
        signals=[top],
    )


def _round(x: Any, nd: int = 2) -> Optional[float]:
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return round(v, nd)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def pct_change_today(p: Dict[str, Any]) -> float:
    close = p["close"]
    if len(close) < 2:
        return 0.0
    prev = float(close.iloc[-2])
    cur = float(close.iloc[-1])
    if prev == 0:
        return 0.0
    return (cur / prev - 1) * 100.0


# ----------------------------------------------------------------------------
# 主分析入口：一只股票 -> 缠论 + 三策略
# ----------------------------------------------------------------------------
def analyze_symbol(
    df: pd.DataFrame,
    symbol: str,
    strategies: Optional[List[str]] = None,
    chan_kwargs: Optional[Dict] = None,
    vp_kwargs: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    对单只股票执行完整分析。
    返回：价格、涨跌、缠论结构、技术指标、量价关系、命中的策略列表。
    """
    if df is None or len(df) < 60:
        return {"symbol": symbol, "ok": False, "error": "历史数据不足（需至少60根K线）"}

    df = df.reset_index(drop=True)
    p = prepare(df)

    engine = ChanEngine(df, **(chan_kwargs or {}))
    chan = engine.analyze()

    want = strategies or ["oversold", "breakout", "reversal", "volprice"]
    hits: List[StrategyHit] = []
    if "oversold" in want:
        h = detect_oversold(p, chan, symbol)
        if h:
            hits.append(h)
    if "breakout" in want:
        h = detect_breakout(p, chan, symbol)
        if h:
            hits.append(h)
    if "reversal" in want:
        h = detect_reversal(p, chan, symbol)
        if h:
            hits.append(h)

    # 量价：无论是否命中选股，都保留完整结果（卖出提醒同样重要）
    volprice_res = None
    if "volprice" in want:
        volprice_res = run_volprice(df, chan, vp_kwargs)
        h = detect_volprice(p, chan, symbol, vp_kwargs)
        if h:
            hits.append(h)

    price = _f(p["close"])
    prev_close = _prev(p["close"])

    return {
        "symbol": symbol,
        "ok": True,
        "price": round(price, 4),
        "prev_close": round(prev_close, 4),
        "change_pct": pct_change_today(p),
        "date": str(df["date"].iloc[-1]) if "date" in df.columns else "",
        "volume": float(p["vol"].iloc[-1]),
        "indicators": {
            "rsi": _f(p["rsi"]),
            "ma20": _f(p["ma20"]),
            "ma60": _f(p["ma60"]),
            "macd_dif": _f(p["dif"]),
            "macd_dea": _f(p["dea"]),
            "macd_hist": _f(p["hist"]),
            "boll_up": _f(p["boll_up"]),
            "boll_low": _f(p["boll_low"]),
            "volume_ratio": _f(p["vr"]),
            "atr": _f(p["atr"]),
            "adx": _f(p["adx"]),
            "dd_52w": _f(p["dd252"]),
            "dd_60d": _f(p["dd60"]),
        },
        "chan": chan,
        "volprice": volprice_res,
        "hits": [h.to_dict() for h in hits],
        "best_score": max([h.score for h in hits], default=0.0),
    }
