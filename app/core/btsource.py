"""
多信号源回测：缠论 vs 量价 vs 两者共振。

目的是回答一个具体问题：**量价信号到底有没有增量价值？**

三种信号源：
    chan        —— 仅缠论买卖点（B1/B2/B3、S1/S2/S3）
    volprice    —— 仅量价形态（VP_B1~B5、VP_S1~S5）
    resonance   —— 两者同向共振才入场（信号少，但理论胜率更高）

无未来函数：缠论走 find_signals(visible_until=i)，量价走 detect_at(f, i)，
两者在第 i 根都只能看到 0..i 的数据；成交统一在 i+confirm_delay 的开盘价。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest import ChanEngine, ExitRule, Trade, compute_stats
from . import volprice as vp

SOURCES = ("chan", "volprice", "resonance")

SOURCE_META = {
    "chan": {"name": "缠论买卖点", "desc": "分型-笔-中枢-背驰体系下的三类买卖点"},
    "volprice": {"name": "量价关系", "desc": "量能配合与背离形态识别出的买卖点"},
    "resonance": {"name": "缠论+量价共振", "desc": "两套体系同向时才入场，牺牲数量换质量"},
}

DEFAULT_CHAN_PARAMS = {
    "min_stroke_bars": 4,
    "power_ratio": 1.0,
}


# ----------------------------------------------------------------------------
# 单标的回测
# ----------------------------------------------------------------------------
def backtest_signal_source(
    df: pd.DataFrame,
    symbol: str = "",
    source: str = "volprice",
    chan_params: Optional[Dict[str, Any]] = None,
    vp_params: Optional[Dict[str, Any]] = None,
    rule: Optional[ExitRule] = None,
    mode: str = "long_only",
    confirm_delay: int = 1,
    warmup: int = 90,
    entry_from_ratio: float = 0.0,
    use_signal_levels: bool = True,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """
    按指定信号源回放单只股票。

    use_signal_levels: 是否采用信号自带的止损/目标位（量价信号为 ATR 推算），
                       关闭则使用 ExitRule 里的固定百分比。
    """
    if source not in SOURCES:
        return {"symbol": symbol, "ok": False, "error": f"未知信号源 {source}"}

    df = df.reset_index(drop=True)
    n = len(df)
    if n < warmup + 30:
        return {"symbol": symbol, "ok": False, "error": "数据不足", "trades": [], "stats": {}}

    entry_from = int(n * max(0.0, min(1.0, entry_from_ratio)))
    rule = rule or ExitRule()
    chan_params = {**DEFAULT_CHAN_PARAMS, **(chan_params or {})}

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    dates = df["date"].astype(str).tolist()

    # 信号源所需的预计算
    engine = None
    feat = None
    if source in ("chan", "resonance"):
        engine = ChanEngine(df, **chan_params)
        engine.analyze()
    if source in ("volprice", "resonance"):
        feat = vp.compute_features(df)

    trades: List[Trade] = []
    pos: Optional[Dict[str, Any]] = None

    for i in range(warmup, n):
        # ---------------- 1. 出场管理 ----------------
        if pos is not None:
            exit_price, reason = None, ""
            if pos["side"] == "long":
                if lows[i] <= pos["stop"]:
                    exit_price = min(pos["stop"], opens[i])
                    reason = "stop_loss"
                elif highs[i] >= pos["target"]:
                    exit_price = max(pos["target"], opens[i])
                    reason = "take_profit"
                elif rule.trail_pct and pos["peak"] > 0:
                    trail = pos["peak"] * (1 - rule.trail_pct)
                    if lows[i] <= trail and trail > pos["stop"]:
                        exit_price = min(trail, opens[i])
                        reason = "trailing_stop"
            else:
                if highs[i] >= pos["stop"]:
                    exit_price = max(pos["stop"], opens[i])
                    reason = "stop_loss"
                elif lows[i] <= pos["target"]:
                    exit_price = min(pos["target"], opens[i])
                    reason = "take_profit"
                elif rule.trail_pct and pos["trough"] > 0:
                    trail = pos["trough"] * (1 + rule.trail_pct)
                    if highs[i] >= trail and trail < pos["stop"]:
                        exit_price = max(trail, opens[i])
                        reason = "trailing_stop"

            if exit_price is None and (i - pos["entry_idx"]) >= rule.max_hold:
                exit_price = closes[i]
                reason = "max_hold"

            # 反向信号出场
            if exit_price is None and rule.exit_on_reverse:
                cur = _sigs_at(engine, feat, i, source, confirm_delay,
                               vp_params, min_confidence)
                rev = ([s for s in cur if s["side"] == "sell"] if pos["side"] == "long"
                       else [s for s in cur if s["side"] == "buy"])
                if rev:
                    exit_price = closes[i]
                    reason = "reverse_signal"

            if exit_price is not None:
                pnl = ((exit_price / pos["entry_price"] - 1) * 100
                       if pos["side"] == "long"
                       else (pos["entry_price"] / exit_price - 1) * 100)
                trades.append(Trade(
                    symbol=symbol, code=pos["code"], side=pos["side"],
                    entry_idx=pos["entry_idx"], entry_date=pos["entry_date"],
                    entry_price=pos["entry_price"],
                    exit_idx=i, exit_date=dates[i], exit_price=exit_price,
                    exit_reason=reason, pnl_pct=pnl,
                    mfe=pos["mfe"], mae=pos["mae"],
                    bars_held=i - pos["entry_idx"],
                    stop_price=pos["stop"], target_price=pos["target"],
                ))
                pos = None

        if pos is not None:
            if pos["side"] == "long":
                mfe_now = (highs[i] / pos["entry_price"] - 1) * 100
                mae_now = (lows[i] / pos["entry_price"] - 1) * 100
                pos["peak"] = max(pos.get("peak", highs[i]), highs[i])
            else:
                mfe_now = (1 - lows[i] / pos["entry_price"]) * 100
                mae_now = (1 - highs[i] / pos["entry_price"]) * 100
                pos["trough"] = min(pos.get("trough", lows[i]), lows[i])
            pos["mfe"] = max(pos["mfe"], mfe_now)
            pos["mae"] = min(pos["mae"], mae_now)

        # ---------------- 2. 入场 ----------------
        if pos is not None or i < entry_from:
            continue

        cur = _sigs_at(engine, feat, i, source, confirm_delay, vp_params, min_confidence)
        if not cur:
            continue

        cand = None
        side = None
        buys = [s for s in cur if s["side"] == "buy"]
        sells = [s for s in cur if s["side"] == "sell"]
        if buys:
            cand, side = buys[0], "long"
        elif sells and mode == "long_short":
            cand, side = sells[0], "short"
        if cand is None:
            continue

        if i + confirm_delay >= n:
            continue
        entry_idx = i + confirm_delay
        entry_price = opens[entry_idx]
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        stop, target = _levels(entry_price, side, cand, rule, use_signal_levels)

        pos = {
            "side": side, "code": cand["code"],
            "entry_idx": entry_idx, "entry_date": dates[entry_idx],
            "entry_price": entry_price, "stop": stop, "target": target,
            "mfe": 0.0, "mae": 0.0, "peak": entry_price, "trough": entry_price,
        }

    if pos is not None:
        exit_price = closes[-1]
        pnl = ((exit_price / pos["entry_price"] - 1) * 100
               if pos["side"] == "long"
               else (pos["entry_price"] / exit_price - 1) * 100)
        trades.append(Trade(
            symbol=symbol, code=pos["code"], side=pos["side"],
            entry_idx=pos["entry_idx"], entry_date=pos["entry_date"],
            entry_price=pos["entry_price"],
            exit_idx=n - 1, exit_date=dates[-1], exit_price=exit_price,
            exit_reason="end_of_data", pnl_pct=pnl,
            mfe=pos["mfe"], mae=pos["mae"],
            bars_held=n - 1 - pos["entry_idx"],
            stop_price=pos["stop"], target_price=pos["target"],
        ))

    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t.pnl_pct / 100.0))

    return {
        "symbol": symbol, "ok": True, "source": source,
        "trades": trades,
        "stats": compute_stats(trades, equity),
        "equity": [round(x, 4) for x in equity],
        "params": {"source": source, "chan": chan_params,
                   "exit": asdict(rule), "mode": mode},
    }


def _sigs_at(engine, feat, i: int, source: str, confirm_delay: int,
             vp_params, min_confidence) -> List[Dict[str, Any]]:
    """取第 i 根 K 线上「信号源」给出的信号（统一格式）。"""
    chan_sigs: List[Dict[str, Any]] = []
    vp_sigs: List[Dict[str, Any]] = []

    if source in ("chan", "resonance") and engine is not None:
        for s in engine.find_signals(visible_until=i, confirm_delay=confirm_delay):
            chan_sigs.append({
                "code": s.code, "side": s.side, "price": s.price,
                "confidence": float(s.confidence or 0) * 100,
                "stop": s.stop_loss, "target": None,
            })

    if source in ("volprice", "resonance") and feat is not None:
        for s in vp.detect_at(feat, i, vp_params):
            conf = s.confidence
            thr = min_confidence if min_confidence is not None else \
                (vp_params or {}).get("min_confidence", vp.DEFAULT_MIN_CONFIDENCE)
            if conf >= float(thr):
                vp_sigs.append({
                    "code": s.code, "side": s.side, "price": s.price,
                    "confidence": conf, "stop": s.stop, "target": s.target,
                })

    if source == "chan":
        return chan_sigs
    if source == "volprice":
        return vp_sigs

    # 共振：取同向的交集，置信度相加
    out: List[Dict[str, Any]] = []
    for side in ("buy", "sell"):
        c = [x for x in chan_sigs if x["side"] == side]
        v = [x for x in vp_sigs if x["side"] == side]
        if c and v:
            merged = {
                "code": f"{c[0]['code']}+{v[0]['code']}",
                "side": side,
                "price": v[0]["price"],
                "confidence": min(100.0, (c[0]["confidence"] + v[0]["confidence"]) / 2 + 10),
                "stop": v[0]["stop"] if v[0]["stop"] else c[0]["stop"],
                "target": v[0]["target"],
            }
            out.append(merged)
    return out


def _levels(entry_price: float, side: str, cand: Dict[str, Any],
            rule: ExitRule, use_signal_levels: bool):
    """确定止损与目标位。"""
    sig_stop = cand.get("stop")
    sig_target = cand.get("target")

    if side == "long":
        stop = entry_price * (1 - rule.stop_pct)
        target = entry_price * (1 + rule.take_pct)
        if use_signal_levels and sig_stop and 0 < sig_stop < entry_price:
            stop = sig_stop
        if use_signal_levels and sig_target and sig_target > entry_price:
            target = sig_target
    else:
        stop = entry_price * (1 + rule.stop_pct)
        target = entry_price * (1 - rule.take_pct)
        if use_signal_levels and sig_stop and sig_stop > entry_price:
            stop = sig_stop
        if use_signal_levels and sig_target and 0 < sig_target < entry_price:
            target = sig_target
    return stop, target


# ----------------------------------------------------------------------------
# 组合对比
# ----------------------------------------------------------------------------
def backtest_source_universe(
    frames: Dict[str, pd.DataFrame],
    source: str = "volprice",
    chan_params: Optional[Dict[str, Any]] = None,
    vp_params: Optional[Dict[str, Any]] = None,
    rule: Optional[ExitRule] = None,
    mode: str = "long_only",
    confirm_delay: int = 1,
    entry_from_ratio: float = 0.0,
    use_signal_levels: bool = True,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """多标的按同一信号源回测并汇总。"""
    all_trades: List[Trade] = []
    per_symbol: List[Dict[str, Any]] = []

    for sym, df in frames.items():
        try:
            r = backtest_signal_source(
                df, symbol=sym, source=source, chan_params=chan_params,
                vp_params=vp_params, rule=rule, mode=mode,
                confirm_delay=confirm_delay, entry_from_ratio=entry_from_ratio,
                use_signal_levels=use_signal_levels, min_confidence=min_confidence,
            )
        except Exception as e:  # noqa: BLE001
            per_symbol.append({"symbol": sym, "ok": False, "error": str(e), "n_trades": 0})
            continue
        if not r.get("ok"):
            per_symbol.append({"symbol": sym, "ok": False,
                               "error": r.get("error", ""), "n_trades": 0})
            continue
        all_trades.extend(r["trades"])
        per_symbol.append({
            "symbol": sym, "ok": True,
            "n_trades": len(r["trades"]),
            "win_rate": r["stats"].get("win_rate"),
            "avg_pnl": r["stats"].get("avg_pnl"),
        })

    # 组合权益：与 backtest_universe 一致，按平均并发持仓数分散资金
    n_concurrent = 1.0
    if all_trades and frames:
        avg_len = float(np.mean([len(d) for d in frames.values()]))
        span = max(avg_len - 90.0, 1.0)
        total_held = float(sum(t.bars_held for t in all_trades))
        n_concurrent = max(1.0, total_held / span)

    equity = [1.0]
    if all_trades:
        ordered = sorted(all_trades, key=lambda t: (t.exit_date, t.symbol))
        for t in ordered:
            equity.append(equity[-1] * (1 + t.pnl_pct / 100.0 / n_concurrent))

    return {
        "ok": True, "source": source,
        "stats": compute_stats(all_trades, equity),
        "equity": [round(x, 4) for x in equity],
        "n_concurrent": round(n_concurrent, 2),
        "trades": [t.to_dict() for t in sorted(all_trades, key=lambda t: t.exit_date)],
        "per_symbol": per_symbol,
    }


def compare_sources(
    frames: Dict[str, pd.DataFrame],
    sources: Optional[List[str]] = None,
    chan_params: Optional[Dict[str, Any]] = None,
    vp_params: Optional[Dict[str, Any]] = None,
    rule: Optional[ExitRule] = None,
    mode: str = "long_only",
    oos_ratio: float = 0.7,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """
    对比多种信号源，并给出样本外（后 30% 时段）表现。

    这是判断「新信号有没有用」的核心依据：样本内好不算好，样本外好才算。
    """
    sources = list(sources or SOURCES)
    rows: List[Dict[str, Any]] = []

    for src in sources:
        ins = backtest_source_universe(
            frames, source=src, chan_params=chan_params, vp_params=vp_params,
            rule=rule, mode=mode, entry_from_ratio=0.0,
            min_confidence=min_confidence,
        )
        oos = backtest_source_universe(
            frames, source=src, chan_params=chan_params, vp_params=vp_params,
            rule=rule, mode=mode, entry_from_ratio=oos_ratio,
            min_confidence=min_confidence,
        )
        rows.append({
            "source": src,
            "name": SOURCE_META[src]["name"],
            "desc": SOURCE_META[src]["desc"],
            "in_sample": ins["stats"],
            "out_sample": oos["stats"],
            "in_equity": ins["equity"],
            "out_equity": oos["equity"],
            "per_symbol": ins["per_symbol"],
        })

    return {
        "ok": True,
        "mode": mode,
        "oos_ratio": oos_ratio,
        "symbols": list(frames.keys()),
        "rows": rows,
        "verdict": _verdict(rows),
    }


def _verdict(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据样本外表现给出结论：量价信号是否值得加入系统。"""
    by_src = {r["source"]: r for r in rows}
    chan_oos = by_src.get("chan", {}).get("out_sample", {})
    vp_oos = by_src.get("volprice", {}).get("out_sample", {})
    res_oos = by_src.get("resonance", {}).get("out_sample", {})

    def _exp(s: Dict[str, Any]) -> Optional[float]:
        return s.get("avg_pnl")

    base = _exp(chan_oos)
    best_src, best_exp = "chan", base if base is not None else -1e9
    for k, s in (("volprice", vp_oos), ("resonance", res_oos)):
        e = _exp(s)
        if e is not None and e > best_exp:
            best_src, best_exp = k, e

    enough = (vp_oos.get("n_trades") or 0) >= 20

    return {
        "best_by_expectancy": best_src,
        "best_name": SOURCE_META.get(best_src, {}).get("name", best_src),
        "volprice_enough_samples": enough,
        "chan_oos_expectancy": base,
        "volprice_oos_expectancy": _exp(vp_oos),
        "resonance_oos_expectancy": _exp(res_oos),
        "note": _verdict_note(chan_oos, vp_oos, res_oos, best_src, enough),
    }


def _verdict_note(chan: Dict, vp: Dict, res: Dict, best: str, enough: bool) -> str:
    ce, ve, re_ = (chan.get("avg_pnl"), vp.get("avg_pnl"), res.get("avg_pnl"))
    if ve is None or (vp.get("n_trades") or 0) < 5:
        return "量价信号在样本外样本过少，结论不可靠，建议扩大标的或放宽门槛后再评估。"
    if not enough:
        return (f"量价信号样本外仅 {vp.get('n_trades')} 笔，虽均值为 {ve:+.2f}%，"
                f"但样本量不足 20 笔，统计意义有限，仅供参考。")
    if best == "volprice":
        return (f"量价信号样本外单笔期望 {ve:+.2f}%，优于缠论的 {ce:+.2f}%，"
                f"说明量价维度提供了增量信息，建议纳入系统。")
    if best == "resonance":
        return (f"共振信号样本外单笔期望 {re_:+.2f}%（缠论 {ce:+.2f}% / 量价 {ve:+.2f}%），"
                f"两套体系互相验证后质量最高，适合作为高置信度提醒。")
    return (f"量价信号样本外单笔期望 {ve:+.2f}%，未超过缠论的 {ce:+.2f}%，"
            f"可作为辅助确认，但不建议单独作为入场依据。")
