"""
基本面分析引擎：估值快照 / 历史估值对比 / 重要事件。

------------------------------------------------------------------------------
一、数据源（全部实测可用，无付费依赖）
------------------------------------------------------------------------------
  估值快照   新浪实时报价
               美股 gb_XXX     字段[13]=PE、[14]=EPS、[12]=市值、[8][9]=52周高低
               港股 rt_hkXXXXX 字段[13]=PE、[15][16]=52周高低
  日线       沿用 data.get_history（美股新浪 / 港股腾讯，前复权，带磁盘缓存）
  公司行动   腾讯港股日K自带 FHcontent（分红）/ HGcontent（回购）——真实披露数据

------------------------------------------------------------------------------
二、口径与假设（必须诚实，页面会标注）
------------------------------------------------------------------------------
1. **历史 PE 带是「EPS 不变假设」下的估算**：
   免费 API 不提供历史 PE 序列。这里用 当前EPS × 历史价格 反推
   （即假设盈利能力不变，看"按今天的赚钱能力，过去每个时点的买入估值"）。
   它回答的是「现在的价格在过去贵不贵」，**不是**「过去的真实 PE」。

2. **价格分位不依赖任何假设**，纯历史价格统计，两个口径并列展示互相印证。

3. 估值评级只基于历史分位（向后看），不构成对未来盈利的预测。

4. 公司行动仅港股可得（腾讯日K披露回购/分红）；美股无免费结构化事件源，
   用「异常波动日」（|涨跌|≥6% 或 量比≥3）作为重大市场事件的代理。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.core import data
from app.core.market import HK, US, normalize

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_TTL = 4 * 3600


def _cache_path(sym: str) -> str:
    return os.path.join(CACHE_DIR, f"fund_{sym}.json")


def _cached(sym: str) -> Optional[Dict]:
    p = _cache_path(sym)
    try:
        if time.time() - os.path.getmtime(p) < CACHE_TTL:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save(sym: str, payload: Dict) -> None:
    try:
        with open(_cache_path(sym), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _round(v, d=2):
    return None if v is None else round(float(v), d)


# ----------------------------------------------------------------------------
# 数据获取
# ----------------------------------------------------------------------------
def _load_df(symbol: str, bars: int) -> pd.DataFrame:
    """拉尽可能长的日线：美股新浪 / 港股腾讯。"""
    mkt, sym = normalize(symbol)
    if mkt == HK:
        return data.hk_history(sym, bars=bars)
    return data.sina_history(sym, bars=bars)


def _quote(symbol: str) -> Dict:
    mkt, sym = normalize(symbol)
    q = data.hk_quote(sym) if mkt == HK else data.sina_quote(sym)
    return q or {}


# ----------------------------------------------------------------------------
# 一、估值快照
# ----------------------------------------------------------------------------
def valuation_snapshot(symbol: str) -> Dict:
    mkt, sym = normalize(symbol)
    q = _quote(sym)
    price = q.get("price")
    pe = q.get("pe")
    eps = q.get("eps") if mkt == US else (None if not pe or pe <= 0 else price / pe)
    mcap = q.get("market_cap")
    yh, yl = q.get("year_high"), q.get("year_low")

    pos_52w = None
    if price and yh and yl and yh > yl:
        pos_52w = (price - yl) / (yh - yl) * 100

    # 每股股息拿不到可靠免费源，不编造
    return {
        "market": mkt, "symbol": sym,
        "name": q.get("name") or "",
        "price": _round(price),
        "pe": _round(pe),
        "eps": _round(eps),
        "market_cap": mcap,
        "year_high": _round(yh), "year_low": _round(yl),
        "pos_52w": _round(pos_52w, 1),
        "currency": "HK$" if mkt == HK else "$",
    }


# ----------------------------------------------------------------------------
# 二、历史估值对比
# ----------------------------------------------------------------------------
def _pctile(arr: np.ndarray, v: float) -> float:
    return float((arr < v).mean() * 100)


def valuation_history(symbol: str, years: int = 3) -> Dict:
    """
    返回三个口径：
      price_pct  价格分位（无假设，纯历史价格）
      pe_band    PE 带（EPS 不变假设）
      pos_52w    52 周位置
    """
    mkt, sym = normalize(symbol)
    bars = min(1000, max(260, int(years) * 252 + 40))
    df = _load_df(sym, bars=bars)
    if df is None or len(df) < 120:
        return {"ok": False, "error": "历史数据不足（至少 120 个交易日）"}

    q = _quote(sym)
    price = q.get("price") or float(df["close"].iloc[-1])
    pe = q.get("pe")
    eps = (q.get("eps") if mkt == US else
           (None if not pe or pe <= 0 else price / pe))

    closes = df["close"].astype(float).values
    dates = df["date"].astype(str).values
    n_years = len(closes) / 252.0

    out: Dict = {
        "ok": True, "years_covered": round(n_years, 1), "bars": len(closes),
        "as_of": str(dates[-1]),
    }

    # ---- 口径 A：价格分位（1 / 3 / 5 年，纯价格，无假设） ----
    price_pct = {}
    for label, n in (("1y", 252), ("3y", 756), ("5y", 1260)):
        if len(closes) >= n:
            arr = closes[-n:]
            price_pct[label] = {
                "percentile": _round(_pctile(arr, price), 1),
                "min": _round(arr.min()), "max": _round(arr.max()),
                "median": _round(float(np.median(arr))),
                "mean": _round(float(arr.mean())),
            }
    out["price_pct"] = price_pct

    # ---- 口径 B：PE 带（EPS 不变假设） ----
    if pe and eps and eps > 0:
        pe_series = closes / float(eps)      # 假设 EPS 恒为当前值
        cur_pe = float(pe)
        band = {}
        for label, n in (("1y", 252), ("3y", 756), ("5y", 1260)):
            if len(pe_series) >= n:
                arr = pe_series[-n:]
                band[label] = {
                    "current": _round(cur_pe),
                    "min": _round(arr.min()),
                    "p25": _round(float(np.percentile(arr, 25))),
                    "median": _round(float(np.median(arr))),
                    "p75": _round(float(np.percentile(arr, 75))),
                    "max": _round(arr.max()),
                    "percentile": _round(_pctile(arr, cur_pe), 1),
                }
        out["pe_band"] = band
        out["pe_assumption"] = (
            f"以当前 EPS（{eps:.2f}）乘以历史价格反推，假设盈利能力不变。"
            "它衡量「按今天的赚钱能力，当前价格在过去贵不贵」，不是过去的真实 PE。")
    else:
        out["pe_band"] = {}
        out["pe_assumption"] = "该标的暂无有效 PE/EPS（可能处于亏损），仅展示价格分位。"

    # ---- 口径 C：52 周位置 ----
    if len(closes) >= 252:
        arr = closes[-252:]
        out["pos_52w"] = {
            "high": _round(arr.max()), "low": _round(arr.min()),
            "pos": _round((price - arr.min()) / (arr.max() - arr.min()) * 100, 1)
            if arr.max() > arr.min() else None,
            "high_date": str(dates[-252:][int(arr.argmax())]),
            "low_date": str(dates[-252:][int(arr.argmin())]),
            "days_since_high": int(len(closes) - (len(closes) - 252 + int(arr.argmax()))),
        }
    return out


# ----------------------------------------------------------------------------
# 三、重要事件
# ----------------------------------------------------------------------------
def key_events(symbol: str, lookback: int = 252) -> Dict:
    """公司行动（港股）+ 异常波动日 + 新高新低。"""
    mkt, sym = normalize(symbol)
    bars = max(lookback + 30, 320)
    raw = None

    if mkt == HK:
        # 腾讯日K带公司行动字段，直接取原始响应
        import re
        from app.core.data import TX_HK_KLINE
        try:
            text = data._http_get(
                TX_HK_KLINE.format(sym=sym, n=bars, adj="qfq"),
                retries=2, timeout=25)
            j = json.loads(text)
            raw = (j.get("data") or {}).get("hk" + sym, {}).get("qfqday") or []
        except Exception:
            raw = None

    if raw is not None:
        closes = [float(k[2]) for k in raw]
        vols = [float(k[5]) for k in raw]
        dates = [str(k[0]) for k in raw]
        corp = []
        for k in raw[-lookback:]:
            meta = k[6] if len(k) > 6 and isinstance(k[6], dict) else {}
            fh = (meta.get("FHcontent") or "").strip()
            hg = (meta.get("HGcontent") or "").strip()
            if fh:
                corp.append({"date": str(k[0]), "type": "分红", "detail": fh})
            if hg:
                corp.append({"date": str(k[0]), "type": "回购", "detail": hg})
    else:
        df = _load_df(sym, bars=bars)
        if df is None or len(df) < 60:
            return {"ok": False, "error": "历史数据不足"}
        sub = df.tail(lookback)
        closes = sub["close"].astype(float).tolist()
        vols = sub["volume"].astype(float).tolist()
        dates = sub["date"].astype(str).tolist()
        corp = []

    # 异常波动日：|涨跌|≥6% 或 量比≥3
    vol_events = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev <= 0:
            continue
        chg = (cur / prev - 1) * 100
        win = vols[max(0, i - 20):i]
        avg20 = sum(win) / len(win) if win else 0
        vr = vols[i] / avg20 if avg20 > 0 else 0
        if abs(chg) >= 6 or vr >= 3:
            vol_events.append({
                "date": dates[i],
                "chg_pct": _round(chg),
                "vol_ratio": _round(vr, 1),
                "close": _round(cur),
                "kind": ("放量大涨" if chg >= 0 and vr >= 2 else
                         "放量下跌" if chg <= 0 and vr >= 2 else
                         "大涨" if chg >= 0 else "大跌"),
            })
    vol_events = vol_events[-12:]

    # 统计摘要
    corp_buyback = [c for c in corp if c["type"] == "回购"]
    corp_div = [c for c in corp if c["type"] == "分红"]
    summary = {
        "buyback_days": len(corp_buyback),
        "dividend_days": len(corp_div),
    }
    if mkt == HK and corp_buyback:
        summary["buyback_note"] = (
            f"近 {lookback // 21} 个月披露回购 {len(corp_buyback)} 个交易日"
            + (f"，最近一次 {corp_buyback[-1]['date']}" if corp_buyback else ""))

    return {
        "ok": True,
        "market": mkt,
        "corporate_actions": corp[-12:],          # 最近 12 条
        "volatility_events": list(reversed(vol_events)),
        "summary": summary,
        "corp_source": "腾讯港股日K披露" if mkt == HK else "美股无免费事件源，仅展示波动异动",
    }


# ----------------------------------------------------------------------------
# 四、综合评估
# ----------------------------------------------------------------------------
def _grade_from_pctile(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    if p < 20:
        return "历史上明显便宜"
    if p < 40:
        return "低于历史中枢"
    if p <= 60:
        return "历史中枢附近"
    if p <= 80:
        return "高于历史中枢"
    return "历史上明显偏贵"


def analyze_fundamentals(symbol: str, years: int = 3) -> Dict:
    mkt, sym = normalize(symbol)
    cached = _cached(sym)
    if cached:
        cached["cached"] = True
        return cached

    snap = valuation_snapshot(sym)
    hist = valuation_history(sym, years=years)
    events = key_events(sym)

    # 估值评级：优先 PE 分位（3 年），退化用价格分位，再退化用 52 周位置
    pe_band = (hist or {}).get("pe_band") or {}
    price_pct = (hist or {}).get("price_pct") or {}
    basis, pct = None, None
    for label in ("3y", "1y"):
        if pe_band.get(label):
            basis, pct = f"PE 分位（{label}，EPS 不变假设）", pe_band[label]["percentile"]
            break
    if pct is None:
        for label in ("3y", "1y"):
            if price_pct.get(label):
                basis, pct = f"价格分位（{label}）", price_pct[label]["percentile"]
                break
    if pct is None:
        basis, pct = "52 周位置", snap.get("pos_52w")

    grade = _grade_from_pctile(pct)

    verdict = None
    if grade:
        tone = ("便宜" if "便宜" in grade else
                "偏贵" if "偏贵" in grade else "中性")
        verdict = {
            "grade": grade, "basis": basis, "percentile": pct, "tone": tone,
            "caveat": "评级只反映当前价格在自身历史中的相对位置，不预测未来盈利变化",
        }

    out = {
        "ok": True,
        "symbol": sym, "market": mkt,
        "snapshot": snap,
        "history": hist,
        "events": events,
        "verdict": verdict,
        "method": {
            "pe_band": "历史 PE 带 = 当前 EPS × 历史价格（EPS 不变假设），衡量「按今天的盈利，当前价在过去贵不贵」",
            "price_pct": "价格分位为纯历史价格统计，不依赖任何假设",
            "events": "公司行动来自腾讯港股日K披露（回购/分红）；异动日 = 单日涨跌超 6% 或量比超 3",
            "limits": "免费数据源无历史 PE 序列、股息率、ROE 与财报日历；这些字段缺失时不编造",
        },
    }
    if hist and not hist.get("ok"):
        out["history_error"] = hist.get("error")
    if events and not events.get("ok"):
        out["events_error"] = events.get("error")
    _save(sym, out)
    return out
