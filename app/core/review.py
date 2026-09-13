"""
信号复盘：回溯每个缠论买卖点的后续表现，统计真实胜率。

判定规则：
  - 买点（B1/B2/B3）成功 = N 个交易日后价格高于信号价
  - 卖点（S1/S2/S3）成功 = N 个交易日后价格低于信号价

同时记录该区间内的最大有利偏移(MFE)与最大不利偏移(MAE)，
用于评估信号的盈亏比，而不只是胜率。
"""
from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from . import data as data_mod
from . import db

HORIZONS = (5, 10, 20)  # 观察窗口（交易日）


# ----------------------------------------------------------------------------
# 表结构：为 signal_history 增加 outcome 列
# ----------------------------------------------------------------------------
def ensure_schema() -> None:
    conn = db._conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_history)")]
    if "outcome" not in cols:
        conn.execute("ALTER TABLE signal_history ADD COLUMN outcome TEXT")
        conn.commit()


# ----------------------------------------------------------------------------
# 单条信号评估
# ----------------------------------------------------------------------------
def evaluate_signal(
    symbol: str,
    sig_date: str,
    side: str,
    sig_price: float,
    df: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """
    计算信号后的收益表现。返回 None 表示数据不足（信号太新）。
    """
    try:
        if df is None:
            df = data_mod.get_history(symbol, period="1y", interval="1d")
        if df is None or len(df) < 5:
            return None
    except Exception:
        return None

    dates = df["date"].astype(str).tolist()

    # 定位信号日在序列中的位置（信号日期可能非交易日，取其后第一个交易日）
    idx = None
    for i, d in enumerate(dates):
        if d >= sig_date:
            idx = i
            break
    if idx is None:
        return None

    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    if not sig_price or sig_price <= 0:
        return None

    out: Dict[str, Any] = {"computed_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    has_any = False

    for h in HORIZONS:
        j = idx + h
        if j >= len(closes):
            out[f"d{h}"] = None
            continue
        has_any = True
        ret = (closes[j] / sig_price - 1) * 100
        out[f"d{h}"] = round(float(ret), 2)
        # 胜/负：买点看涨、卖点看跌
        out[f"win{h}"] = bool(ret > 0) if side == "buy" else bool(ret < 0)

        # 区间内最大有利/不利偏移
        seg_h = highs[idx + 1: j + 1]
        seg_l = lows[idx + 1: j + 1]
        if len(seg_h) and len(seg_l):
            if side == "buy":
                mfe = (float(seg_h.max()) / sig_price - 1) * 100
                mae = (float(seg_l.min()) / sig_price - 1) * 100
            else:
                mfe = (1 - float(seg_l.min()) / sig_price) * 100
                mae = (1 - float(seg_h.max()) / sig_price) * 100
            out[f"mfe{h}"] = round(float(mfe), 2)
            out[f"mae{h}"] = round(float(mae), 2)

    if not has_any:
        return None
    return out


# ----------------------------------------------------------------------------
# 批量复盘
# ----------------------------------------------------------------------------
def run_review(limit: int = 400, force: bool = False) -> Dict[str, Any]:
    """
    遍历信号历史，补全尚未评估（或需要刷新）的条目，输出汇总统计。
    """
    ensure_schema()
    signals = db.get_signal_history(limit=limit)
    if not signals:
        return {"ok": True, "total": 0, "evaluated": 0, "stats": {}, "items": []}

    # 按 symbol 批量拉取行情，避免重复请求
    symbols = sorted({s["symbol"] for s in signals})
    frames: Dict[str, Optional[pd.DataFrame]] = {}

    def fetch(sym: str):
        try:
            frames[sym] = data_mod.get_history(sym, period="1y", interval="1d")
        except Exception:
            frames[sym] = None

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(fetch, symbols))

    evaluated = 0
    conn = db._conn()

    for s in signals:
        if not force and s.get("outcome"):
            continue
        df = frames.get(s["symbol"])
        if df is None or len(df) < 5:
            continue
        res = evaluate_signal(
            s["symbol"], s["sig_date"], s["side"], float(s["price"] or 0), df=df
        )
        if res is None:
            continue
        try:
            conn.execute(
                "UPDATE signal_history SET outcome=? WHERE id=?",
                (json.dumps(res, ensure_ascii=False), s["id"]),
            )
            evaluated += 1
        except Exception:
            pass
    conn.commit()

    # 重新读取（含新写入的 outcome）
    return summarize(db.get_signal_history(limit=limit), evaluated)


def summarize(signals: List[Dict[str, Any]], evaluated: int = 0) -> Dict[str, Any]:
    """根据带 outcome 的信号列表生成汇总统计。"""
    items: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {}

    for s in signals:
        raw = s.get("outcome")
        oc = None
        if raw:
            try:
                oc = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                oc = None
        items.append({**s, "outcome": oc})

    def _group(key_fn) -> Dict[str, Dict[str, Any]]:
        g: Dict[str, Dict[str, Any]] = {}
        for it in items:
            oc = it.get("outcome")
            if not oc:
                continue
            k = key_fn(it)
            g.setdefault(k, {"n": 0, "wins": {}, "rets": {}, "mfe": {}, "mae": {}})
            g[k]["n"] += 1
            for h in HORIZONS:
                r = oc.get(f"d{h}")
                w = oc.get(f"win{h}")
                if r is None or w is None:
                    continue
                g[k]["wins"][h] = g[k]["wins"].get(h, 0) + (1 if w else 0)
                g[k]["rets"].setdefault(h, []).append(r)
                if oc.get(f"mfe{h}") is not None:
                    g[k]["mfe"].setdefault(h, []).append(oc[f"mfe{h}"])
                if oc.get(f"mae{h}") is not None:
                    g[k]["mae"].setdefault(h, []).append(oc[f"mae{h}"])
        # 计算比率
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in g.items():
            rec: Dict[str, Any] = {"count": v["n"], "win_rate": {}, "avg_ret": {},
                                   "avg_mfe": {}, "avg_mae": {}}
            for h in HORIZONS:
                n = v["wins"].get(h, 0)
                cnt = v["n"]
                rec["win_rate"][h] = round(n / cnt * 100, 1) if cnt else None
                rs = v["rets"].get(h) or []
                rec["avg_ret"][h] = round(sum(rs) / len(rs), 2) if rs else None
                ms = v["mfe"].get(h) or []
                rec["avg_mfe"][h] = round(sum(ms) / len(ms), 2) if ms else None
                ms = v["mae"].get(h) or []
                rec["avg_mae"][h] = round(sum(ms) / len(ms), 2) if ms else None
            out[k] = rec
        return out

    stats["by_code"] = _group(lambda it: it["code"])
    stats["by_side"] = _group(lambda it: "买点" if it["side"] == "buy" else "卖点")
    stats["overall"] = _group(lambda it: "全部")

    pending = sum(1 for it in items if not it.get("outcome"))

    return {
        "ok": True,
        "total": len(items),
        "evaluated": evaluated,
        "pending": pending,
        "horizons": list(HORIZONS),
        "stats": stats,
        "items": items[:200],
    }
