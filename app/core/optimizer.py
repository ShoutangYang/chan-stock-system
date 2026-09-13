"""
参数自动迭代（自学习）。

流程：
  1. 随机采样参数组合（比全网格搜索高效得多）
  2. 在**样本内**（前 split 比例的时间段）评估，按目标函数排序
  3. 取 Top-K 在**样本外**（后 1-split 时间段）验证
  4. 选出样本外最优者，并与默认参数基线对比

防过拟合的三道防线：
  - 样本内/样本外时间分割（out-of-sample 才是真实检验）
  - 最少交易数门槛（样本太少的参数组直接淘汰）
  - 样本外显著劣于基线时，拒绝"优化结果"，提示保持默认
"""
from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .backtest import DEFAULT_CHAN_PARAMS, ExitRule, backtest_universe

# ----------------------------------------------------------------------------
# 参数搜索空间
# ----------------------------------------------------------------------------
PARAM_SPACE: Dict[str, List[Any]] = {
    # 缠论结构参数
    "min_stroke_bars": [3, 4, 5, 6],
    "power_ratio": [0.8, 0.9, 1.0, 1.15],
    # 出场参数
    "stop_pct": [0.03, 0.05, 0.07, 0.10],
    "take_pct": [0.08, 0.12, 0.18, 0.30],
    "max_hold": [8, 15, 25, 40],
    "use_chan_stop": [True, False],
    "exit_on_reverse": [True, False],
}

OBJECTIVES = {
    "risk_adjusted": "收益/回撤比（推荐）",
    "expectancy": "平均单笔收益",
    "sharpe": "夏普比率",
    "profit_factor": "盈亏比",
    "win_rate": "胜率",
}


def score_of(stats: Dict[str, Any], objective: str, min_trades: int) -> float:
    """目标函数。样本不足或指标缺失时返回极差分数。"""
    if not stats or (stats.get("n_trades") or 0) < min_trades:
        return -999.0

    if objective == "expectancy":
        v = stats.get("avg_pnl")
        return float(v) if v is not None else -999.0

    if objective == "sharpe":
        v = stats.get("sharpe")
        return float(v) if v is not None else -999.0

    if objective == "profit_factor":
        v = stats.get("profit_factor")
        return min(float(v), 10.0) if v is not None else -999.0

    if objective == "win_rate":
        v = stats.get("win_rate")
        return float(v) if v is not None else -999.0

    # 默认：收益/回撤比，兼顾收益与风险
    avg = stats.get("avg_pnl") or 0.0
    dd = max(stats.get("max_drawdown") or 1.0, 1.0)
    return float(avg) / float(dd)


def sample_params(rng: random.Random, space: Dict[str, List[Any]]) -> Dict[str, Any]:
    return {k: rng.choice(v) for k, v in space.items()}


def split_params(p: Dict[str, Any]) -> tuple[Dict[str, Any], ExitRule]:
    """把扁平参数拆成缠论参数与出场规则。"""
    chan = {
        "min_stroke_bars": int(p.get("min_stroke_bars", 4)),
        "power_ratio": float(p.get("power_ratio", 1.0)),
    }
    rule = ExitRule(
        stop_pct=float(p.get("stop_pct", 0.06)),
        take_pct=float(p.get("take_pct", 0.12)),
        max_hold=int(p.get("max_hold", 20)),
        use_chan_stop=bool(p.get("use_chan_stop", True)),
        exit_on_reverse=bool(p.get("exit_on_reverse", True)),
    )
    return chan, rule


# ----------------------------------------------------------------------------
# 主优化流程
# ----------------------------------------------------------------------------
def optimize(
    frames: Dict[str, pd.DataFrame],
    n_samples: int = 60,
    objective: str = "risk_adjusted",
    split: float = 0.7,
    min_trades: int = 25,
    mode: str = "long_only",
    top_k: int = 5,
    seed: int = 42,
    space: Optional[Dict[str, List[Any]]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    运行参数搜索。frames: {symbol: DataFrame}
    """
    if not frames:
        return {"ok": False, "error": "没有可用数据"}

    space = space or PARAM_SPACE
    rng = random.Random(seed)
    total_steps = n_samples + top_k + 1

    def tick(i: int, msg: str):
        if progress_cb:
            try:
                progress_cb(i, total_steps, msg)
            except Exception:
                pass

    # ---------- 1. 样本内搜索 ----------
    in_frames = {}
    for sym, df in frames.items():
        cut = int(len(df) * split)
        if cut >= 160:
            in_frames[sym] = df.iloc[:cut].reset_index(drop=True)
    if not in_frames:
        in_frames = frames

    results: List[Dict[str, Any]] = []
    seen = set()
    step = 0

    for _ in range(n_samples):
        step += 1
        p = sample_params(rng, space)
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)

        chan, rule = split_params(p)
        try:
            r = backtest_universe(in_frames, chan_params=chan, rule=rule, mode=mode)
        except Exception:
            continue
        sc = score_of(r["stats"], objective, min_trades)
        results.append({
            "params": p,
            "in_sample": r["stats"],
            "score": round(sc, 4),
            "n_trades": r["stats"].get("n_trades", 0),
        })
        if step % 10 == 0:
            tick(step, f"样本内搜索 {step}/{n_samples}")

    if not results:
        return {"ok": False, "error": "未产生有效结果"}

    results.sort(key=lambda x: -x["score"])
    top = results[:top_k]

    # ---------- 2. 样本外验证 ----------
    for i, item in enumerate(top):
        step += 1
        chan, rule = split_params(item["params"])
        try:
            out = backtest_universe(frames, chan_params=chan, rule=rule, mode=mode,
                                    entry_from_ratio=split)
        except Exception:
            out = {"stats": {}}
        item["out_sample"] = out.get("stats", {})
        item["out_score"] = round(score_of(out.get("stats", {}), objective,
                                            max(5, min_trades // 3)), 4)
        tick(step, f"样本外验证 {i + 1}/{len(top)}")

    # ---------- 3. 基线对比 ----------
    step += 1
    tick(step, "计算默认参数基线")
    base_chan = dict(DEFAULT_CHAN_PARAMS)
    base_rule = ExitRule()
    try:
        base_in = backtest_universe(in_frames, chan_params=base_chan,
                                    rule=base_rule, mode=mode)["stats"]
        base_out = backtest_universe(frames, chan_params=base_chan, rule=base_rule,
                                     mode=mode, entry_from_ratio=split)["stats"]
    except Exception:
        base_in, base_out = {}, {}

    baseline = {
        "params": {"min_stroke_bars": 4, "power_ratio": 1.0, "stop_pct": 0.06,
                   "take_pct": 0.12, "max_hold": 20,
                   "use_chan_stop": True, "exit_on_reverse": True},
        "in_sample": base_in,
        "out_sample": base_out,
        "out_score": round(score_of(base_out, objective, max(5, min_trades // 3)), 4),
    }

    # ---------- 4. 决策 ----------
    # ⚠️ 关键：不能用样本外分数来「挑选」参数。
    # 一旦用测试集挑最优，它就成了训练集的一部分，报出来的样本外收益
    # 带有选择偏差（K 个候选取最大值），会系统性高估实盘表现。
    # 正确做法：参数只在样本内选出，样本外仅作「是否优于基线」的通过/不通过检验。
    best = top[0]                       # 样本内最优（已按 in-sample score 降序）
    best_out_score = best.get("out_score", -999)
    base_out_score = baseline["out_score"]

    # 附带记录：top-K 中样本外最好者，仅供对照，明确标注含选择偏差
    oos_pick = max(top, key=lambda x: x.get("out_score", -999))
    selection_bias = round(
        float(oos_pick.get("out_score", 0)) - float(best.get("out_score", 0)), 4
    )

    # 样本外没有明显优于基线 -> 拒绝，避免用噪声替换默认参数
    accepted = bool(best_out_score > base_out_score and best_out_score > -900)
    if accepted:
        verdict = ("样本外表现优于默认参数，建议应用"
                   if best_out_score > base_out_score * 1.1
                   else "样本外略优于默认参数，可谨慎应用")
    else:
        verdict = ("样本外未超越默认参数 —— 保持现状。\n"
                   "样本内的高收益多半来自过拟合，强行应用会在实盘亏损。")

    return {
        "ok": True,
        "objective": objective,
        "objective_name": OBJECTIVES.get(objective, objective),
        "n_samples": len(results),
        "split": split,
        "min_trades": min_trades,
        "best": best,
        "baseline": baseline,
        "accepted": accepted,
        "verdict": verdict,
        # 两个方法学提示，避免误读回测数字
        "oos_pick": oos_pick,
        "selection_bias": selection_bias,
        "method_note": (
            "参数仅依据样本内表现选出，样本外只用于「是否优于基线」的通过/不通过判定，"
            "因此样本外数字是无偏的。作为对照，oos_pick 是 top-K 中样本外最好者，"
            f"它比实际采用者高 {selection_bias}，这个差值就是「用测试集挑参数」带来的虚高，不应采信。"
        ),
        "improvement": (
            round(best_out_score - base_out_score, 4)
            if isinstance(best_out_score, (int, float))
            and isinstance(base_out_score, (int, float))
            else None
        ),
        "top": top,
        "all_results": results[:60],
    }


def apply_best_params(best: Dict[str, Any]) -> Dict[str, Any]:
    """把优化结果转换成可写入配置的结构。"""
    p = best.get("params", {})
    return {
        "chan": {
            "min_stroke_bars": int(p.get("min_stroke_bars", 4)),
            "power_ratio": float(p.get("power_ratio", 1.0)),
        },
        "exit": {
            "stop_pct": float(p.get("stop_pct", 0.06)),
            "take_pct": float(p.get("take_pct", 0.12)),
            "max_hold": int(p.get("max_hold", 20)),
            "use_chan_stop": bool(p.get("use_chan_stop", True)),
            "exit_on_reverse": bool(p.get("exit_on_reverse", True)),
        },
    }
