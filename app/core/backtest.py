"""
缠论信号回测引擎。

设计要点：
  1. **严格避免未来函数**：回测位置 i 只能使用 end_raw + confirm_delay <= i 的笔与中枢，
     即"当时已确认"的结构。与实盘共用 ChanEngine.find_signals 同一套逻辑。
  2. **真实成交模拟**：信号在次日开盘成交（信号当日无法预知），
     跳空时按不利价格成交（开盘价低于止损则以开盘价成交）。
  3. **可对比出场方式**：固定止损止盈 vs 缠论卖点出场，用于验证卖点是否真的有价值。

不做的事：不模拟手续费、不模拟滑点（美股流动性好，影响小于信号误差）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .chanlun import ChanEngine


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
@dataclass
class ExitRule:
    """出场规则。"""
    stop_pct: float = 0.06          # 止损幅度（相对入场价）
    take_pct: float = 0.12          # 止盈幅度
    max_hold: int = 20              # 最大持有K线数
    use_chan_stop: bool = True      # 是否采用缠论给出的关键位（取更近者）
    exit_on_reverse: bool = True    # 出现反向缠论信号时出场
    trail_pct: Optional[float] = None  # 移动止损：从最高点回撤该比例平仓


DEFAULT_CHAN_PARAMS = {
    "min_stroke_bars": 4,
    "power_ratio": 1.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
}


# ----------------------------------------------------------------------------
# 交易记录
# ----------------------------------------------------------------------------
@dataclass
class Trade:
    symbol: str
    code: str
    side: str                 # 'long' | 'short'
    entry_idx: int
    entry_date: str
    entry_price: float
    exit_idx: int
    exit_date: str
    exit_price: float
    exit_reason: str          # stop_loss / take_profit / max_hold / reverse_signal / end_of_data
    pnl_pct: float
    mfe: float                # 持仓期最大有利偏移 %
    mae: float                # 持仓期最大不利偏移 %
    bars_held: int
    stop_price: float = 0.0
    target_price: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("entry_price", "exit_price", "pnl_pct", "mfe", "mae",
                  "stop_price", "target_price"):
            d[k] = round(float(d[k]), 4)
        return d


# ----------------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------------
def compute_stats(trades: List[Trade], equity: List[float]) -> Dict[str, Any]:
    if not trades:
        return {
            "n_trades": 0, "win_rate": None, "avg_pnl": None, "avg_win": None,
            "avg_loss": None, "profit_factor": None, "expectancy": None,
            "max_drawdown": None, "sharpe": None, "avg_bars": None,
            "total_return": None, "best": None, "worst": None,
        }

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_win = float(np.sum(wins)) if wins else 0.0
    gross_loss = float(abs(np.sum(losses))) if losses else 0.0

    # 最大回撤（基于权益曲线）
    max_dd = 0.0
    if equity:
        arr = np.array(equity, dtype=float)
        peak = np.maximum.accumulate(arr)
        dd = (arr - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(abs(dd.min()) * 100)

    # 夏普（按交易序列，年化按平均持仓周期折算）
    sharpe = None
    if len(pnls) > 2 and float(np.std(pnls)) > 0:
        avg_bars = max(float(np.mean([t.bars_held for t in trades])), 1.0)
        periods_per_year = 252.0 / avg_bars
        sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(periods_per_year))

    total_return = (float(equity[-1]) - 1.0) * 100 if equity else None

    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(pnls) * 100, 1),
        "avg_pnl": round(float(np.mean(pnls)), 3),
        "avg_win": round(float(np.mean(wins)), 3) if wins else None,
        "avg_loss": round(float(np.mean(losses)), 3) if losses else None,
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
        "expectancy": round(float(np.mean(pnls)), 3),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "avg_bars": round(float(np.mean([t.bars_held for t in trades])), 1),
        "total_return": round(total_return, 2) if total_return is not None else None,
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "avg_mfe": round(float(np.mean([t.mfe for t in trades])), 2),
        "avg_mae": round(float(np.mean([t.mae for t in trades])), 2),
    }


# ----------------------------------------------------------------------------
# 单标的回测
# ----------------------------------------------------------------------------
def backtest_symbol(
    df: pd.DataFrame,
    symbol: str = "",
    chan_params: Optional[Dict[str, Any]] = None,
    rule: Optional[ExitRule] = None,
    mode: str = "long_only",
    confirm_delay: int = 1,
    warmup: int = 80,
    entry_from_ratio: float = 0.0,
) -> Dict[str, Any]:
    """
    对单只股票回放缠论信号。

    mode: 'long_only' 只做多（卖点用于平仓）；'long_short' 卖点做空。
    entry_from_ratio: 只在该比例之后的位置入场，用于样本外验证
                      （结构仍用全部历史计算，避免重新预热导致的结构缺失）。
    """
    df = df.reset_index(drop=True)
    n = len(df)
    if n < warmup + 30:
        return {"symbol": symbol, "ok": False, "error": "数据不足", "trades": [], "stats": {}}

    entry_from = int(n * max(0.0, min(1.0, entry_from_ratio)))

    chan_params = {**DEFAULT_CHAN_PARAMS, **(chan_params or {})}
    rule = rule or ExitRule()

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    dates = df["date"].astype(str).tolist()

    # 一次性构建完整缠论结构
    engine = ChanEngine(df, **chan_params)
    engine.analyze()

    trades: List[Trade] = []
    pos: Optional[Dict[str, Any]] = None

    for i in range(warmup, n):
        # ---------- 1. 持仓管理（先出场，后入场） ----------
        if pos is not None:
            exit_price = None
            reason = ""

            if pos["side"] == "long":
                # 止损（跳空按开盘价成交，取更不利者）
                if lows[i] <= pos["stop"]:
                    exit_price = min(pos["stop"], opens[i])
                    reason = "stop_loss"
                elif highs[i] >= pos["target"]:
                    exit_price = max(pos["target"], opens[i])
                    reason = "take_profit"
                # 移动止损
                elif rule.trail_pct and pos["peak"] > 0:
                    trail = pos["peak"] * (1 - rule.trail_pct)
                    if lows[i] <= trail and trail > pos["stop"]:
                        exit_price = min(trail, opens[i])
                        reason = "trailing_stop"
            else:  # short
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

            # 超时
            if exit_price is None and (i - pos["entry_idx"]) >= rule.max_hold:
                exit_price = closes[i]
                reason = "max_hold"

            if exit_price is not None:
                if pos["side"] == "long":
                    pnl = (exit_price / pos["entry_price"] - 1) * 100
                else:
                    pnl = (pos["entry_price"] / exit_price - 1) * 100
                trades.append(
                    Trade(
                        symbol=symbol, code=pos["code"], side=pos["side"],
                        entry_idx=pos["entry_idx"], entry_date=pos["entry_date"],
                        entry_price=pos["entry_price"],
                        exit_idx=i, exit_date=dates[i], exit_price=exit_price,
                        exit_reason=reason, pnl_pct=pnl,
                        mfe=pos["mfe"], mae=pos["mae"],
                        bars_held=i - pos["entry_idx"],
                        stop_price=pos["stop"], target_price=pos["target"],
                    )
                )
                pos = None

        # 更新持仓内的 MFE/MAE 与极值
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

        # ---------- 2. 信号检测与入场 ----------
        sigs = engine.find_signals(visible_until=i, confirm_delay=confirm_delay)
        if not sigs:
            continue

        buy_sigs = [s for s in sigs if s.side == "buy"]
        sell_sigs = [s for s in sigs if s.side == "sell"]

        # 反向信号出场
        if pos is not None and rule.exit_on_reverse:
            reverse = sell_sigs if pos["side"] == "long" else buy_sigs
            if reverse:
                exit_price = closes[i]
                pnl = ((exit_price / pos["entry_price"] - 1) * 100
                       if pos["side"] == "long"
                       else (pos["entry_price"] / exit_price - 1) * 100)
                trades.append(
                    Trade(
                        symbol=symbol, code=pos["code"], side=pos["side"],
                        entry_idx=pos["entry_idx"], entry_date=pos["entry_date"],
                        entry_price=pos["entry_price"],
                        exit_idx=i, exit_date=dates[i], exit_price=exit_price,
                        exit_reason="reverse_signal", pnl_pct=pnl,
                        mfe=pos["mfe"], mae=pos["mae"],
                        bars_held=i - pos["entry_idx"],
                        stop_price=pos["stop"], target_price=pos["target"],
                    )
                )
                pos = None

        if pos is not None:
            continue

        # 入场：只取未交易过的最新信号，次日开盘成交
        if i < entry_from:
            continue

        cand = None
        if buy_sigs:
            cand = buy_sigs[0]
            side = "long"
        elif sell_sigs and mode == "long_short":
            cand = sell_sigs[0]
            side = "short"
        if cand is None:
            continue

        # 次日开盘入场（避免当日收盘价可知的未来偏差）
        if i + 1 >= n:
            continue
        entry_price = opens[i + 1]
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        if side == "long":
            stop = entry_price * (1 - rule.stop_pct)
            target = entry_price * (1 + rule.take_pct)
            # 缠论关键位：若更近则采用
            if rule.use_chan_stop and cand.stop_loss:
                cs = float(cand.stop_loss)
                if entry_price * (1 - rule.stop_pct * 2) < cs < entry_price:
                    stop = max(stop, cs) if cs > stop else cs
        else:
            stop = entry_price * (1 + rule.stop_pct)
            target = entry_price * (1 - rule.take_pct)
            if rule.use_chan_stop and cand.stop_loss:
                cs = float(cand.stop_loss)
                if entry_price < cs < entry_price * (1 + rule.stop_pct * 2):
                    stop = min(stop, cs) if cs < stop else cs

        pos = {
            "side": side, "code": cand.code,
            "entry_idx": i + 1, "entry_date": dates[i + 1],
            "entry_price": entry_price,
            "stop": stop, "target": target,
            "mfe": 0.0, "mae": 0.0,
            "peak": entry_price, "trough": entry_price,
            "sig_idx": i,
        }

    # 结束仍未平仓的按最后收盘价结算
    if pos is not None:
        exit_price = closes[-1]
        pnl = ((exit_price / pos["entry_price"] - 1) * 100
               if pos["side"] == "long"
               else (pos["entry_price"] / exit_price - 1) * 100)
        trades.append(
            Trade(
                symbol=symbol, code=pos["code"], side=pos["side"],
                entry_idx=pos["entry_idx"], entry_date=pos["entry_date"],
                entry_price=pos["entry_price"],
                exit_idx=n - 1, exit_date=dates[-1], exit_price=exit_price,
                exit_reason="end_of_data", pnl_pct=pnl,
                mfe=pos["mfe"], mae=pos["mae"],
                bars_held=n - 1 - pos["entry_idx"],
                stop_price=pos["stop"], target_price=pos["target"],
            )
        )

    # 权益曲线（按交易顺序复利）
    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t.pnl_pct / 100.0))

    return {
        "symbol": symbol,
        "ok": True,
        "trades": trades,
        "stats": compute_stats(trades, equity),
        "equity": [round(x, 4) for x in equity],
        "params": {"chan": chan_params, "exit": asdict(rule), "mode": mode},
    }


# ----------------------------------------------------------------------------
# 多标的组合回测
# ----------------------------------------------------------------------------
def backtest_universe(
    frames: Dict[str, pd.DataFrame],
    chan_params: Optional[Dict[str, Any]] = None,
    rule: Optional[ExitRule] = None,
    mode: str = "long_only",
    confirm_delay: int = 1,
    entry_from_ratio: float = 0.0,
) -> Dict[str, Any]:
    """对多只股票回测并汇总（等权组合）。"""
    all_trades: List[Trade] = []
    per_symbol: List[Dict[str, Any]] = []

    for sym, df in frames.items():
        try:
            r = backtest_symbol(df, symbol=sym, chan_params=chan_params,
                                rule=rule, mode=mode, confirm_delay=confirm_delay,
                                entry_from_ratio=entry_from_ratio)
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

    # 组合权益：资金等权分散在并发持仓上。
    # 若按交易顺序串联复利，会假设全部资金连续押注单笔，回撤会被严重高估。
    n_concurrent = 1.0
    if all_trades and frames:
        # 平均并发持仓数 = 总持仓K线数 / 回测区间长度
        avg_len = float(np.mean([len(d) for d in frames.values()]))
        span = max(avg_len - 80.0, 1.0)
        total_held = float(sum(t.bars_held for t in all_trades))
        n_concurrent = max(1.0, total_held / span)

    equity = [1.0]
    if all_trades:
        ordered = sorted(all_trades, key=lambda t: (t.exit_date, t.symbol))
        for t in ordered:
            equity.append(equity[-1] * (1 + t.pnl_pct / 100.0 / n_concurrent))

    return {
        "ok": True,
        "stats": compute_stats(all_trades, equity),
        "equity": [round(x, 4) for x in equity],
        "n_concurrent": round(n_concurrent, 2),
        "trades": [t.to_dict() for t in sorted(all_trades, key=lambda t: t.exit_date)],
        "per_symbol": per_symbol,
        "params": {"chan": chan_params or DEFAULT_CHAN_PARAMS,
                   "exit": asdict(rule or ExitRule()), "mode": mode},
    }


def result_to_dict(r: Dict[str, Any], include_trades: bool = True) -> Dict[str, Any]:
    out = {
        "symbol": r.get("symbol"),
        "ok": r.get("ok"),
        "error": r.get("error"),
        "stats": r.get("stats", {}),
        "equity": r.get("equity", []),
        "params": r.get("params", {}),
        "per_symbol": r.get("per_symbol"),
    }
    if include_trades:
        out["trades"] = [t.to_dict() if isinstance(t, Trade) else t
                         for t in r.get("trades", [])]
    return out
