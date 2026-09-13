"""
期权模块：期权链获取 + 基于缠论信号的期权策略建议。

思路：
  1. 拿到期权链后，用 Black-Scholes 计算 Greeks（主要是 Delta），用于精准筛选合约。
  2. 用「隐含波动率 IV vs 已实现波动率 HV」判断期权贵贱，决定买方/卖方倾向。
  3. 缠论信号 -> 对应期权策略：
       B1/B2（超跌、反转）  -> 现金担保卖出看跌 CSP（支撑位接货）或 买Call
       B3/突破               -> 买Call / 牛市价差
       S1/S2（持股）        -> 备兑看涨 Covered Call
       S1/S2/S3（无持股）   -> 买Put / 熊市价差
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import data as data_mod
from . import indicators as ind

_OPT_CACHE: Dict[str, Dict[str, Any]] = {}
_OPT_CACHE_TTL = 30 * 60  # 30 分钟

RISK_FREE_RATE = 0.042  # 约当前美债短端利率，可调整


# ----------------------------------------------------------------------------
# Black-Scholes
# ----------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> Dict[str, float]:
    """计算 Delta / Gamma / Theta / Vega。kind: 'call' | 'put'。"""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return {"delta": float("nan"), "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(S / K) + (r + sigma * sigma / 2.0) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if kind == "call":
        delta = _norm_cdf(d1)
        theta = (
            -S * math.exp(-0) * (1 / (sigma * math.sqrt(T))) * math.exp(-(d1 ** 2) / 2) / math.sqrt(2 * math.pi) / 2
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
        )
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -S * (1 / (sigma * math.sqrt(T))) * math.exp(-(d1 ** 2) / 2) / math.sqrt(2 * math.pi) / 2
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        )

    gamma = math.exp(-(d1 ** 2) / 2) / (S * sigma * math.sqrt(T) * math.sqrt(2 * math.pi))
    vega = S * math.exp(-(d1 ** 2) / 2) * math.sqrt(T) / math.sqrt(2 * math.pi) / 100.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta / 365.0, 4),
        "vega": round(vega, 4),
    }


# ----------------------------------------------------------------------------
# 期权链获取
# ----------------------------------------------------------------------------
def get_expirations(symbol: str) -> List[str]:
    try:
        yf = data_mod._yf()
        return list(yf.Ticker(symbol).options or [])
    except Exception:
        return []


def get_chain(symbol: str, expiration: Optional[str] = None, max_expirations: int = 4) -> Dict[str, Any]:
    """
    获取期权链（含 Greeks）。
    若未指定到期日，取最近的若干个到期日。
    """
    symbol = symbol.upper().strip()
    key = f"{symbol}|{expiration}|{max_expirations}"
    now = time.time()
    cached = _OPT_CACHE.get(key)
    if cached and now - cached["ts"] < _OPT_CACHE_TTL:
        return cached["data"]

    result: Dict[str, Any] = {
        "symbol": symbol,
        "ok": False,
        "expirations": [],
        "selected_expiration": None,
        "calls": [],
        "puts": [],
        "underlying": None,
        "error": None,
    }

    try:
        yf = data_mod._yf()
        t = yf.Ticker(symbol)
        exps = list(t.options or [])
        result["expirations"] = exps
        if not exps:
            result["error"] = "该标的无可用期权（可能不支持或数据源未覆盖）"
            _OPT_CACHE[key] = {"ts": now, "data": result}
            return result

        # 标的价格
        try:
            fi = t.fast_info
            price = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
        except Exception:
            price = None
        if not price:
            try:
                h = t.history(period="5d", interval="1d")
                price = float(h["Close"].iloc[-1]) if h is not None and not h.empty else None
            except Exception:
                price = None
        result["underlying"] = float(price) if price else None

        target = expiration or _pick_expiration(exps, target_dte=45)
        if target not in exps:
            target = exps[0]
        result["selected_expiration"] = target

        # 已实现波动率（用于判断期权贵贱）
        hv = float("nan")
        try:
            hist = t.history(period="6mo", interval="1d")
            if hist is not None and len(hist) > 30:
                hv = ind.annualized_vol(pd.Series(hist["Close"].astype(float).values), n=60)
        except Exception:
            pass
        result["hv"] = None if not np.isfinite(hv) else round(hv, 4)

        chain = t.option_chain(target)
        S = result["underlying"]
        dte = _dte(target)
        T = max(dte / 365.0, 1 / 365.0)

        calls = _normalize_chain(chain.calls, S, T, "call")
        puts = _normalize_chain(chain.puts, S, T, "put")

        result["calls"] = calls
        result["puts"] = puts
        result["dte"] = dte
        result["ok"] = True

    except Exception as e:  # noqa: BLE001
        result["error"] = f"实时期权链不可用（{e}）"

        # ---- 降级：基于历史波动率的理论链（Black-Scholes 估算） ----
        try:
            hist = data_mod.get_history(symbol, period="1y", interval="1d")
            if hist is not None and len(hist) > 30:
                S = float(hist["close"].iloc[-1])
                sigma = ind.annualized_vol(pd.Series(hist["close"].values), n=60)
                if not np.isfinite(sigma) or sigma <= 0:
                    sigma = 0.35
                theo = build_theoretical_chain(S, sigma, dte=_dte(expiration) if expiration else 45)
                theo["symbol"] = symbol
                theo["fallback_reason"] = result["error"]
                _OPT_CACHE[key] = {"ts": now, "data": theo}
                return theo
        except Exception:
            pass

        _OPT_CACHE[key] = {"ts": now, "data": result}
        return result

    _OPT_CACHE[key] = {"ts": now, "data": result}
    return result


def _dte(expiration: str) -> int:
    from datetime import datetime

    try:
        d = datetime.strptime(expiration, "%Y-%m-%d").date()
        return max((d - datetime.now().date()).days, 0)
    except Exception:
        return 30


def _pick_expiration(exps: List[str], target_dte: int = 45) -> str:
    """选择最接近目标剩余天数的到期日。"""
    best, best_gap = exps[0], 10 ** 9
    for e in exps:
        gap = abs(_dte(e) - target_dte)
        if gap < best_gap:
            best, best_gap = e, gap
    return best


def bs_price(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> float:
    """Black-Scholes 理论价格。"""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + sigma * sigma / 2.0) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def build_theoretical_chain(S: float, sigma: float, dte: int = 45) -> Dict[str, Any]:
    """
    当实时期权链不可用时（如国内网络无法访问 Yahoo），基于 Black-Scholes
    与历史波动率生成理论报价。仅用于策略推演，非真实可成交价格。
    """
    if S <= 0 or not np.isfinite(sigma) or sigma <= 0:
        sigma = 0.35
    T = max(dte / 365.0, 1 / 365.0)
    sigma = float(np.clip(sigma, 0.10, 2.0))

    # 行权价：围绕现价 ±30%，按 2.5% 递进；大盘股间距可更小
    step = max(round(S * 0.025 * 2) / 2, 0.5)
    if S > 300:
        step = max(round(S * 0.02 * 2) / 2, 1.0)
    if S < 20:
        step = max(round(S * 0.05 * 2) / 2, 0.5)

    lo, hi = S * 0.72, S * 1.32
    strikes: List[float] = []
    k = round(lo / step) * step
    while k <= hi and len(strikes) < 60:
        if k > 0:
            strikes.append(round(k, 2))
        k += step

    calls, puts = [], []
    exps: List[str] = []
    from datetime import datetime, timedelta
    for d in (30, 45, 60, 90):
        exps.append((datetime.now().date() + timedelta(days=d)).strftime("%Y-%m-%d"))

    for K in strikes:
        for kind, bucket in (("call", calls), ("put", puts)):
            price = bs_price(S, K, T, RISK_FREE_RATE, sigma, kind)
            if price < 0.01:
                continue
            g = bs_greeks(S, K, T, RISK_FREE_RATE, sigma, kind)
            iv_adj = sigma * (1.0 + 0.12 * abs(K / S - 1.0))  # 波动率微笑近似
            bucket.append(
                {
                    "strike": K,
                    "last": round(price, 2),
                    "bid": round(price * 0.97, 2),
                    "ask": round(price * 1.03, 2),
                    "volume": 0,
                    "open_interest": int(max(50, 800 * (1 - abs(K / S - 1) * 2))),
                    "iv": round(iv_adj, 4),
                    "delta": g.get("delta"),
                    "gamma": g.get("gamma"),
                    "theta": g.get("theta"),
                    "vega": g.get("vega"),
                    "moneyness": round(K / S - 1, 4),
                    "theoretical": True,
                }
            )

    return {
        "symbol": "",
        "ok": True,
        "theoretical": True,
        "expirations": exps,
        "selected_expiration": exps[1] if len(exps) > 1 else exps[0],
        "calls": calls,
        "puts": puts,
        "underlying": S,
        "dte": dte,
        "hv": round(sigma, 4),
        "error": None,
    }


def _normalize_chain(df: pd.DataFrame, S: Optional[float], T: float, kind: str) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    cols = {c.lower(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in cols:
                return df[cols[n]]
        return None

    strikes = col("strike")
    last = col("lastprice", "last")
    bid = col("bid")
    ask = col("ask")
    vol = col("volume")
    oi = col("openinterest", "oi")
    iv = col("impliedvolatility", "iv")

    out: List[Dict[str, Any]] = []
    for i in range(len(df)):
        try:
            K = float(strikes.iloc[i]) if strikes is not None else float("nan")
            if not np.isfinite(K):
                continue
            mid = float("nan")
            if last is not None:
                mid = float(last.iloc[i] or 0)
            if (not np.isfinite(mid) or mid <= 0) and bid is not None and ask is not None:
                b, a = float(bid.iloc[i] or 0), float(ask.iloc[i] or 0)
                if b > 0 and a > 0:
                    mid = (b + a) / 2
            if not np.isfinite(mid) or mid <= 0:
                continue

            iv_v = float(iv.iloc[i]) if iv is not None else float("nan")
            greeks = {}
            if S and np.isfinite(iv_v) and iv_v > 0:
                greeks = bs_greeks(S, K, T, RISK_FREE_RATE, iv_v, kind)

            out.append(
                {
                    "strike": round(K, 2),
                    "last": round(mid, 2),
                    "bid": round(float(bid.iloc[i] or 0), 2) if bid is not None else None,
                    "ask": round(float(ask.iloc[i] or 0), 2) if ask is not None else None,
                    "volume": int(vol.iloc[i] or 0) if vol is not None else 0,
                    "open_interest": int(oi.iloc[i] or 0) if oi is not None else 0,
                    "iv": round(iv_v, 4) if np.isfinite(iv_v) else None,
                    "delta": greeks.get("delta"),
                    "gamma": greeks.get("gamma"),
                    "theta": greeks.get("theta"),
                    "vega": greeks.get("vega"),
                    "moneyness": round(K / S - 1, 4) if S else None,
                }
            )
        except Exception:
            continue

    return out


# ----------------------------------------------------------------------------
# 合约筛选
# ----------------------------------------------------------------------------
def pick_contract(
    chain: List[Dict[str, Any]],
    target_delta: Optional[float] = None,
    target_moneyness: Optional[float] = None,
    min_oi: int = 10,
    prefer_liquid: bool = True,
) -> Optional[Dict[str, Any]]:
    """按 delta 或 moneyness 挑选最合适的合约。"""
    if not chain:
        return None

    cands = [c for c in chain if (c.get("open_interest") or 0) >= min_oi or c.get("volume", 0) >= min_oi]
    if not cands:
        cands = list(chain)

    def score(c):
        # 注意：delta 可能为负（put），比较时必须取绝对值
        s = 0.0
        d = c.get("delta")
        if target_delta is not None and d is not None and np.isfinite(d):
            s = abs(abs(d) - abs(target_delta))
        elif target_moneyness is not None and c.get("moneyness") is not None:
            s = abs(c["moneyness"] - target_moneyness)
        if prefer_liquid:
            s -= min((c.get("open_interest") or 0) / 10000.0, 0.05)
        return s

    return min(cands, key=score)


# ----------------------------------------------------------------------------
# 策略建议
# ----------------------------------------------------------------------------
def build_option_strategies(
    symbol: str,
    price: Optional[float],
    chan_signals: List[Dict[str, Any]],
    strategy_hint: Optional[str] = None,
    has_position: bool = False,
    chain_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    根据缠论买卖点生成期权策略建议。
    """
    out: Dict[str, Any] = {
        "symbol": symbol,
        "price": price,
        "expiration": None,
        "dte": None,
        "iv_env": None,
        "hv": None,
        "strategies": [],
        "error": None,
    }

    chain = chain_data or get_chain(symbol)
    if not chain.get("ok"):
        out["error"] = chain.get("error", "期权数据不可用")
        return out

    S = price or chain.get("underlying")
    if not S:
        out["error"] = "无法获取标的价格"
        return out

    calls = chain.get("calls", [])
    puts = chain.get("puts", [])
    out["expiration"] = chain.get("selected_expiration")
    out["dte"] = chain.get("dte")
    out["hv"] = chain.get("hv")
    out["theoretical"] = bool(chain.get("theoretical"))
    if chain.get("fallback_reason"):
        out["data_note"] = (
            "实时期权链不可用，以下报价为 Black-Scholes 基于历史波动率的理论估算值，"
            "仅供策略推演，实际成交请以券商报价为准。"
        )

    # 期权贵贱环境：ATM IV vs HV
    atm_iv = None
    if calls:
        atm = min(calls, key=lambda c: abs((c.get("strike") or 0) - S))
        atm_iv = atm.get("iv")
    hv = chain.get("hv")
    if atm_iv and hv and np.isfinite(atm_iv) and np.isfinite(hv):
        ratio = atm_iv / hv if hv > 0 else 1.0
        if ratio > 1.25:
            out["iv_env"] = {"level": "high", "ratio": round(ratio, 2),
                             "advice": "期权偏贵，优先做卖方（卖Put/备兑Call/价差）"}
        elif ratio < 0.85:
            out["iv_env"] = {"level": "low", "ratio": round(ratio, 2),
                             "advice": "期权偏便宜，优先做买方（买Call/买Put）"}
        else:
            out["iv_env"] = {"level": "normal", "ratio": round(ratio, 2),
                             "advice": "期权定价合理，按信号方向选择买方策略"}
    out["atm_iv"] = atm_iv

    codes = {s["code"]: s for s in (chan_signals or [])}
    buy_codes = [c for c in ("B1", "B2", "B3") if c in codes]
    sell_codes = [c for c in ("S1", "S2", "S3") if c in codes]

    iv_level = (out["iv_env"] or {}).get("level", "normal")

    # ---------- 买入信号 ----------
    if buy_codes:
        primary = buy_codes[0]
        sig = codes[primary]

        if primary in ("B1", "B2") or strategy_hint == "oversold":
            # 现金担保卖出看跌：行权价放在支撑位/止损位附近
            target_k = min(sig.get("stop_loss") or S * 0.93, S * 0.97)
            put = pick_contract(puts, target_moneyness=target_k / S - 1, target_delta=None)
            if put:
                premium = put["last"] * 100
                breakeven = put["strike"] - put["last"]
                out["strategies"].append(
                    {
                        "name": "现金担保卖出看跌 (Cash-Secured Put)",
                        "tag": "卖方 / 收租 + 低位接货",
                        "direction": "bullish",
                        "suit": f"缠论{primary}（{sig['name']}），愿意在 {put['strike']:.2f} 接货",
                        "legs": [{"action": "SELL", "type": "PUT", **put}],
                        "premium": round(premium, 2),
                        "max_profit": round(premium, 2),
                        "max_loss": round((put["strike"] - put["last"]) * 100, 2),
                        "breakeven": round(breakeven, 2),
                        "capital": round(put["strike"] * 100, 2),
                        "note": (
                            f"卖出 {out['dte']} 天到期、行权价 {put['strike']:.2f} 的 Put，"
                            f"收取权利金 ${premium:.0f}。若被行权，实际接货成本 {breakeven:.2f}，"
                            f"低于当前价 {(1-breakeven/S)*100:.1f}%。"
                        ),
                        "risk": "标的大幅下跌时仍需按行权价买入，需预留全额资金",
                    }
                )

            # 替代：买 Call
            call = pick_contract(calls, target_delta=0.55)
            if call:
                out["strategies"].append(
                    {
                        "name": "买入看涨期权 (Long Call)",
                        "tag": "买方 / 高杠杆博反弹",
                        "direction": "bullish",
                        "suit": f"缠论{primary} 信号，看好反弹但对正股下行有顾虑",
                        "legs": [{"action": "BUY", "type": "CALL", **call}],
                        "premium": round(call["last"] * 100, 2),
                        "max_profit": "不限",
                        "max_loss": round(call["last"] * 100, 2),
                        "breakeven": round(call["strike"] + call["last"], 2),
                        "capital": round(call["last"] * 100, 2),
                        "note": (
                            f"Delta {call.get('delta')}, IV {call.get('iv')}，"
                            f"需上涨 {(call['strike']+call['last'])/S-1:+.1%} 才能盈亏平衡。"
                        ),
                        "risk": "时间价值衰减，到期可能归零",
                    }
                )

        elif primary == "B3" or strategy_hint == "breakout":
            call = pick_contract(calls, target_delta=0.6)
            if call:
                out["strategies"].append(
                    {
                        "name": "买入看涨期权 (Long Call)",
                        "tag": "买方 / 顺势跟随突破",
                        "direction": "bullish",
                        "suit": f"缠论三类买点，中枢上方企稳，趋势延续概率高",
                        "legs": [{"action": "BUY", "type": "CALL", **call}],
                        "premium": round(call["last"] * 100, 2),
                        "max_profit": "不限",
                        "max_loss": round(call["last"] * 100, 2),
                        "breakeven": round(call["strike"] + call["last"], 2),
                        "capital": round(call["last"] * 100, 2),
                        "note": f"选择 Delta 约 {call.get('delta')} 的实值/平值 Call，时间价值损耗相对可控。",
                        "risk": "突破失败则损失全部权利金",
                    }
                )

            # 牛市价差：降低成本
            if iv_level == "high" and len(calls) > 1:
                buy = pick_contract(calls, target_delta=0.6)
                sell_c = [c for c in calls if buy and c["strike"] > buy["strike"] * 1.08]
                sell_c = min(sell_c, key=lambda c: abs(c["delta"] + 0.25)) if sell_c else None
                if buy and sell_c:
                    net = (buy["last"] - sell_c["last"]) * 100
                    width = (sell_c["strike"] - buy["strike"]) * 100
                    out["strategies"].append(
                        {
                            "name": "牛市看涨价差 (Bull Call Spread)",
                            "tag": "买方 / 低成本，适合IV偏高时",
                            "direction": "bullish",
                            "suit": "IV 偏高，用价差降低权利金成本与时间损耗",
                            "legs": [
                                {"action": "BUY", "type": "CALL", **buy},
                                {"action": "SELL", "type": "CALL", **sell_c},
                            ],
                            "premium": round(net, 2),
                            "max_profit": round(width - net, 2),
                            "max_loss": round(net, 2),
                            "breakeven": round(buy["strike"] + net / 100, 2),
                            "capital": round(net, 2),
                            "note": f"买入 {buy['strike']:.0f} Call、卖出 {sell_c['strike']:.0f} Call，净成本 ${net:.0f}，最大收益 ${width-net:.0f}。",
                            "risk": "收益封顶，但最大亏损有限",
                        }
                    )

    # ---------- 卖出信号 ----------
    if sell_codes:
        primary_s = sell_codes[0]
        sig_s = codes[primary_s]

        if has_position:
            # 备兑看涨
            target_k = max(sig_s.get("stop_loss") or S * 1.07, S * 1.03)
            call = pick_contract(calls, target_moneyness=target_k / S - 1)
            if call:
                out["strategies"].append(
                    {
                        "name": "备兑看涨 (Covered Call)",
                        "tag": "持股增强收益 / 高位减仓",
                        "direction": "neutral_bearish",
                        "suit": f"持有正股且出现{primary_s}（{sig_s['name']}），上涨空间受限",
                        "legs": [{"action": "SELL", "type": "CALL", **call}],
                        "premium": round(call["last"] * 100, 2),
                        "max_profit": round((call["strike"] - S) * 100 + call["last"] * 100, 2),
                        "max_loss": "正股下跌风险（权利金部分对冲）",
                        "breakeven": round(S - call["last"], 2),
                        "capital": "需持有 100 股正股",
                        "note": (
                            f"每 100 股卖出 1 张 {call['strike']:.0f} Call，收取 ${call['last']*100:.0f}。"
                            f"若涨过 {call['strike']:.2f} 被行权，相当于在该价位止盈。"
                        ),
                        "risk": "若股价大涨，收益被封顶",
                    }
                )
        else:
            put = pick_contract(puts, target_delta=-0.5)
            if put:
                out["strategies"].append(
                    {
                        "name": "买入看跌期权 (Long Put)",
                        "tag": "买方 / 对冲或博下跌",
                        "direction": "bearish",
                        "suit": f"缠论{primary_s}（{sig_s['name']}），预期下行",
                        "legs": [{"action": "BUY", "type": "PUT", **put}],
                        "premium": round(put["last"] * 100, 2),
                        "max_profit": round((put["strike"] - put["last"]) * 100, 2),
                        "max_loss": round(put["last"] * 100, 2),
                        "breakeven": round(put["strike"] - put["last"], 2),
                        "capital": round(put["last"] * 100, 2),
                        "note": f"Delta {put.get('delta')}，需下跌 {1-(put['strike']-put['last'])/S:+.1%} 才能盈亏平衡。",
                        "risk": "判断错误则损失全部权利金",
                    }
                )

    # ---------- 无信号：给中性策略 ----------
    if not out["strategies"]:
        if has_position:
            call = pick_contract(calls, target_delta=0.3)
            if call:
                out["strategies"].append(
                    {
                        "name": "备兑看涨 (Covered Call)",
                        "tag": "持股增强收益",
                        "direction": "neutral",
                        "suit": "当前无明确买卖点，持股期间卖出虚值 Call 增厚收益",
                        "legs": [{"action": "SELL", "type": "CALL", **call}],
                        "premium": round(call["last"] * 100, 2),
                        "max_profit": round((call["strike"] - S) * 100 + call["last"] * 100, 2),
                        "max_loss": "正股下跌风险",
                        "breakeven": round(S - call["last"], 2),
                        "capital": "需持有 100 股正股",
                        "note": f"卖出 Delta {call.get('delta')} 的虚值 Call，行权价 {call['strike']:.0f}。",
                        "risk": "上涨收益封顶",
                    }
                )
        else:
            out["strategies"].append(
                {
                    "name": "观望",
                    "tag": "无信号",
                    "direction": "neutral",
                    "suit": "当前无缠论买卖点，等待结构演化",
                    "legs": [],
                    "note": "缠论强调「不测而测」，等待一类/二类/三类买卖点出现再介入。",
                    "risk": "-",
                }
            )

    # ---------- 期限 × 方向 策略矩阵 ----------
    try:
        # 只用「真实」到期日去匹配目标期限。
        # 理论链（降级）只生成近月到期日，若拿它做匹配，中长期会被退化成 90 天，
        # 因此这种情况下传 None，由 _exp_for_dte 按目标天数直接构造。
        exps = None if chain.get("theoretical") else chain.get("expirations")
        if not exps:
            exps = get_expirations(symbol) or None
        out["horizons"] = build_horizon_strategies(
            symbol=symbol, price=S, chan_signals=chan_signals,
            has_position=has_position, iv_level=iv_level, exps=exps,
        )
    except Exception as e:  # noqa: BLE001
        out["horizons"] = None
        out["horizon_error"] = str(e)

    return out


# ----------------------------------------------------------------------------
# 期限 × 方向 策略矩阵
# ----------------------------------------------------------------------------
# 期权的核心取舍只有一个：**时间**。
#   短期合约  = 高杠杆 + 高时间损耗，要求「方向对且快」；
#   长期合约  = 低杠杆 + 低时间损耗，允许「方向对但慢」。
# 因此同一笔缠论信号，在两种期限下的最优操作完全不同，必须分开给建议。
HORIZON_META: Dict[str, Dict[str, Any]] = {
    "short": {
        "key": "short",
        "name": "短期",
        "label": "短期 · 约 30–45 天",
        "target_dte": 45,
        "traits": [
            "时间价值衰减（Theta）最快，方向对了也可能被耗死",
            "Gamma 大，标的的小幅波动就能放大成高杠杆收益",
            "单笔资金占用小、最大亏损锁定，适合信号明确时快速兑现",
        ],
        "best_for": "缠论买卖点刚确认、趋势即将启动或破位的时点",
        "watch": "信号证伪要立刻走，不能像持股那样「扛一扛」",
    },
    "long": {
        "key": "long",
        "name": "中长期",
        "label": "中长期 · 约 6–12 个月（LEAPS）",
        "target_dte": 240,
        "traits": [
            "Theta 衰减慢，容错时间长，不必押注「马上就涨」",
            "深度实值 Call 可替代持股，资金占用显著降低",
            "适合趋势持有、低位分批建仓、以及长期持仓的保险",
        ],
        "best_for": "周线级别方向明确，或需要长期持有 / 保护已有持仓",
        "watch": "权利金总额高，一旦判断错误，绝对亏损金额不小",
    },
}


def _exp_for_dte(symbol: str, exps: Optional[List[str]], target_dte: int):
    """取最接近目标天数的到期日及其期权链。无真实到期日时构造日期（走理论链降级）。"""
    from datetime import date, timedelta

    exp = _pick_expiration(exps, target_dte) if exps else None
    if not exp:
        exp = (date.today() + timedelta(days=target_dte)).isoformat()
    return exp, get_chain(symbol, expiration=exp)


def _sig_context(chan_signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把缠论信号归纳成方向上下文。"""
    codes = {s.get("code") for s in (chan_signals or [])}
    buys = [c for c in ("B1", "B2", "B3") if c in codes]
    sells = [c for c in ("S1", "S2", "S3") if c in codes]
    if buys and not sells:
        direction = "bullish"
    elif sells and not buys:
        direction = "bearish"
    elif buys and sells:
        direction = "mixed"
    else:
        direction = "neutral"
    return {
        "direction": direction,
        "buys": buys,
        "sells": sells,
        "breakout": "B3" in codes,
        "label": {"bullish": "看多", "bearish": "看空",
                  "mixed": "多空信号并存", "neutral": "无明确信号"}[direction],
    }


def _score(base: float, stance: str, ctx: Dict[str, Any],
           iv_level: str, has_position: bool, role: str,
           need_position: bool = False) -> float:
    """按信号方向、IV 环境、持仓状态给策略打分（0-100）。"""
    s = base
    d = ctx["direction"]
    if stance == "bullish":
        s += 26 if d == "bullish" else (-22 if d == "bearish" else 0)
    elif stance == "bearish":
        s += 26 if d == "bearish" else (-22 if d == "bullish" else 0)
    else:  # neutral / defensive
        s += 8 if d == "neutral" else 0

    # IV 环境：贵了利于卖方，便宜了利于买方
    sell_side = role in ("卖方", "价差", "组合")
    if iv_level == "high":
        s += 12 if sell_side else -9
    elif iv_level == "low":
        s += 12 if role == "买方" else -6

    # 需要持仓才能做的策略
    if need_position:
        s += 18 if has_position else -45

    return max(0.0, min(100.0, s))


def _mk_leg(action: str, otype: str, c: Dict[str, Any]) -> Dict[str, Any]:
    return {"action": action, "type": otype, **c}


def _spread_legs(chain: List[Dict[str, Any]], buy_delta: float, sell_delta: float,
                 otype: str, higher_strike_is_farther: bool = True):
    """为价差策略挑两条腿：买入腿 + 更虚值的卖出腿。"""
    buy = pick_contract(chain, target_delta=buy_delta)
    if not buy:
        return None, None
    k = buy["strike"]
    if higher_strike_is_farther:
        cands = [c for c in chain if c["strike"] > k * 1.03]
    else:
        cands = [c for c in chain if c["strike"] < k * 0.97]
    if not cands:
        return buy, None
    sell = min(cands, key=lambda c: abs(abs(c.get("delta") or 0) - abs(sell_delta)))
    return buy, sell


def build_horizon_strategies(
    symbol: str,
    price: float,
    chan_signals: List[Dict[str, Any]],
    has_position: bool = False,
    iv_level: str = "normal",
    exps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    按「短期 / 中长期」×「Call / Put」生成期权策略矩阵。

    每个策略都给出：
        advice —— 操作建议（具体怎么做）
        reason —— 操作理由（为什么这么做）
    """
    ctx = _sig_context(chan_signals)
    S = float(price)
    result: Dict[str, Any] = {
        "context": ctx,
        "iv_level": iv_level,
        "horizons": {},
    }

    for hk, meta in HORIZON_META.items():
        target = meta["target_dte"]
        exp, ch = _exp_for_dte(symbol, exps, target)
        calls = ch.get("calls", []) or []
        puts = ch.get("puts", []) or []
        dte = ch.get("dte") or target

        block: Dict[str, Any] = {
            **meta,
            "expiration": exp,
            "dte": dte,
            "theoretical": bool(ch.get("theoretical")),
            "available": bool(calls or puts),
            "call": [],
            "put": [],
        }
        if not block["available"]:
            block["note"] = "未能获取该期限的期权报价（可尝试流动性更好的标的）"
            result["horizons"][hk] = block
            continue
        if abs(dte - target) > 60:
            block["note"] = (f"该标的可选到期日中，最接近目标的是 {dte} 天"
                             f"（目标 {target} 天），以下建议按实际到期日计算")

        # ---------------- CALL ----------------
        if hk == "short":
            c = pick_contract(calls, target_delta=0.6)
            if c:
                block["call"].append({
                    "name": "买入看涨期权", "en": "Long Call",
                    "option_type": "CALL", "role": "买方", "stance": "bullish",
                    "score": round(_score(52, "bullish", ctx, iv_level, has_position, "买方"), 0),
                    "suit": "缠论买点确认、或三类买点突破成立，预期短期内上行",
                    "advice": (f"买入 1 张约 {dte} 天到期、行权价 {c['strike']:.0f} "
                               f"（Delta {c.get('delta'):.2f}）的 Call；单笔投入控制在总资金的 2–5%"),
                    "reason": ("短期方向性工具里杠杆最高的一种，最大亏损就是权利金。"
                               "Delta 0.55–0.65 附近时间价值占比适中，不需要大涨即可获利，"
                               "又不像深度虚值那样几乎必然归零。"
                               "代价是 Theta 衰减快——信号一旦证伪要立刻离场，不能像持股那样扛"),
                    "legs": [_mk_leg("BUY", "CALL", c)],
                    "premium": round(c["last"] * 100, 2),
                    "max_profit": "不限（上涨空间越大赚越多）",
                    "max_loss": round(c["last"] * 100, 2),
                    "breakeven": round(c["strike"] + c["last"], 2),
                    "capital": round(c["last"] * 100, 2),
                    "risk": f"需上涨 {((c['strike'] + c['last']) / S - 1) * 100:.1f}% 才盈亏平衡；到期归零风险",
                })

            buy, sell = _spread_legs(calls, 0.6, 0.28, "CALL")
            if buy and sell:
                net = (buy["last"] - sell["last"]) * 100
                width = (sell["strike"] - buy["strike"]) * 100
                block["call"].append({
                    "name": "牛市看涨价差", "en": "Bull Call Spread",
                    "option_type": "CALL", "role": "价差", "stance": "bullish",
                    "score": round(_score(56, "bullish", ctx, iv_level, has_position, "价差"), 0),
                    "suit": "看多但不认为会暴涨，或 IV 偏高想降低成本",
                    "advice": (f"买入 1 张 {buy['strike']:.0f} Call，同时卖出 1 张同到期 "
                               f"{sell['strike']:.0f} Call，净成本约 ${net:.0f}"),
                    "reason": ("卖腿收的权利金直接降低净成本与 Theta 损耗，"
                               "IV 偏高时卖腿更值钱，性价比通常优于单腿买 Call。"
                               "代价是收益封顶——适合『方向有把握、幅度没把握』的情形"),
                    "legs": [_mk_leg("BUY", "CALL", buy), _mk_leg("SELL", "CALL", sell)],
                    "premium": round(net, 2),
                    "max_profit": round(width - net, 2),
                    "max_loss": round(net, 2),
                    "breakeven": round(buy["strike"] + net / 100, 2),
                    "capital": round(net, 2),
                    "risk": "收益封顶，但最大亏损有限且已知",
                })

            cc = pick_contract(calls, target_delta=0.28)
            if cc:
                block["call"].append({
                    "name": "备兑看涨", "en": "Covered Call",
                    "option_type": "CALL", "role": "卖方", "stance": "neutral",
                    "score": round(_score(50, "neutral", ctx, iv_level, has_position, "卖方",
                                          need_position=True), 0),
                    "suit": "已持有正股，且判断短期上涨乏力 / 进入震荡",
                    "advice": (f"每持有 100 股，卖出 1 张 {dte} 天到期、行权价 {cc['strike']:.0f} "
                               f"（Delta {cc.get('delta'):.2f}）的 Call"),
                    "reason": ("用已有持仓作抵押收租，把『横盘不涨』也变成收益，等于降低持仓成本。"
                               "Delta 0.25–0.3 的虚值 Call 被行权概率较低，能保留大部分上涨空间；"
                               "代价是股价若大涨，超出行权价的部分与你无关"),
                    "legs": [_mk_leg("SELL", "CALL", cc)],
                    "premium": round(cc["last"] * 100, 2),
                    "max_profit": round((cc["strike"] - S) * 100 + cc["last"] * 100, 2),
                    "max_loss": "正股下跌风险（权利金可部分对冲）",
                    "breakeven": round(S - cc["last"], 2),
                    "capital": "需持有 100 股正股",
                    "risk": "上涨收益封顶；正股大跌时仍需承担主要亏损",
                })

            # ---------------- PUT ----------------
            p = pick_contract(puts, target_delta=-0.5)
            if p:
                block["put"].append({
                    "name": "买入看跌期权", "en": "Long Put",
                    "option_type": "PUT", "role": "买方", "stance": "bearish",
                    "score": round(_score(52, "bearish", ctx, iv_level, has_position, "买方"), 0),
                    "suit": "缠论卖点出现（预期下行），或需要给持仓买一份保险",
                    "advice": (f"买入 1 张约 {dte} 天、行权价 {p['strike']:.0f} "
                               f"（Delta {p.get('delta'):.2f}）的 Put；"
                               f"若用于保护持仓，按每 100 股配 1 张"),
                    "reason": ("以有限权利金博取下跌收益，最大亏损锁定。"
                               "相比直接融券做空，风险更可控（不会爆仓、没有无限亏损）。"
                               "作为保险时，它给持仓设定了一个明确的『最坏情况』，"
                               "而上涨空间仍然全部保留"),
                    "legs": [_mk_leg("BUY", "PUT", p)],
                    "premium": round(p["last"] * 100, 2),
                    "max_profit": round((p["strike"] - p["last"]) * 100, 2),
                    "max_loss": round(p["last"] * 100, 2),
                    "breakeven": round(p["strike"] - p["last"], 2),
                    "capital": round(p["last"] * 100, 2),
                    "risk": f"需下跌 {(1 - (p['strike'] - p['last']) / S) * 100:.1f}% 才盈亏平衡；判断错误则权利金归零",
                })

            csp = pick_contract(puts, target_delta=-0.28)
            if csp:
                block["put"].append({
                    "name": "现金担保卖出看跌", "en": "Cash-Secured Put",
                    "option_type": "PUT", "role": "卖方", "stance": "bullish",
                    "score": round(_score(54, "bullish", ctx, iv_level, has_position, "卖方"), 0),
                    "suit": "缠论一/二类买点附近，愿意在更低价位接货",
                    "advice": (f"卖出 1 张 {dte} 天、行权价 {csp['strike']:.0f} 的 Put，"
                               f"同时预留 ${csp['strike'] * 100:,.0f} 现金以备被行权"),
                    "reason": ("上涨或横盘 → 权利金白赚；下跌 → 按低于现价的成本接货。"
                               "缠论一/二类买点本就带有接货意愿，于是『等回调』这件事本身开始产生收益。"
                               f"实际接货成本为 {csp['strike'] - csp['last']:.2f}，"
                               f"比现价低 {(1 - (csp['strike'] - csp['last']) / S) * 100:.1f}%"),
                    "legs": [_mk_leg("SELL", "PUT", csp)],
                    "premium": round(csp["last"] * 100, 2),
                    "max_profit": round(csp["last"] * 100, 2),
                    "max_loss": round((csp["strike"] - csp["last"]) * 100, 2),
                    "breakeven": round(csp["strike"] - csp["last"], 2),
                    "capital": round(csp["strike"] * 100, 2),
                    "risk": "标的大跌时仍须按行权价买入，需预留全额资金",
                })

            pb, ps = _spread_legs(puts, -0.5, -0.25, "PUT", higher_strike_is_farther=False)
            if pb and ps:
                net = (pb["last"] - ps["last"]) * 100
                width = (pb["strike"] - ps["strike"]) * 100
                block["put"].append({
                    "name": "熊市看跌价差", "en": "Bear Put Spread",
                    "option_type": "PUT", "role": "价差", "stance": "bearish",
                    "score": round(_score(56, "bearish", ctx, iv_level, has_position, "价差"), 0),
                    "suit": "看空且 IV 偏高，想降低买 Put 的成本",
                    "advice": (f"买入 1 张 {pb['strike']:.0f} Put，同时卖出 1 张同到期 "
                               f"{ps['strike']:.0f} Put，净成本约 ${net:.0f}"),
                    "reason": ("IV 偏高时单腿买 Put 太贵，加一条卖腿可显著降低成本与时间损耗，"
                               "且最大亏损在建仓时就已经确定。代价是收益封顶——"
                               "适合『看空但认为跌幅有限』的判断"),
                    "legs": [_mk_leg("BUY", "PUT", pb), _mk_leg("SELL", "PUT", ps)],
                    "premium": round(net, 2),
                    "max_profit": round(width - net, 2),
                    "max_loss": round(net, 2),
                    "breakeven": round(pb["strike"] - net / 100, 2),
                    "capital": round(net, 2),
                    "risk": "收益封顶；若跌幅超预期，超额部分与你无关",
                })

        else:  # ---------------- 中长期 ----------------
            c = pick_contract(calls, target_delta=0.8)
            if c:
                intrinsic = max(S - c["strike"], 0)
                block["call"].append({
                    "name": "长期看涨期权（股票替代）", "en": "LEAPS Long Call",
                    "option_type": "CALL", "role": "买方", "stance": "bullish",
                    "score": round(_score(55, "bullish", ctx, iv_level, has_position, "买方"), 0),
                    "suit": "周线级别方向向上，希望用更小资金获得长期上涨敞口",
                    "advice": (f"买入 1 张约 {dte} 天（{(dte / 365):.1f} 年）、行权价 {c['strike']:.0f} "
                               f"的深度实值 Call（Delta {c.get('delta'):.2f}）"),
                    "reason": (f"Delta 接近 1，涨跌几乎同步跟随正股，但资金占用通常只有直接持股的 30–50%"
                               f"（本合约价格 {c['last']:.2f}，其中内在价值 {intrinsic:.2f}）。"
                               "Theta 极小，不必押注『马上就涨』，"
                               "适合周线方向明确、但短期节奏不确定的长期布局"),
                    "legs": [_mk_leg("BUY", "CALL", c)],
                    "premium": round(c["last"] * 100, 2),
                    "max_profit": "不限",
                    "max_loss": round(c["last"] * 100, 2),
                    "breakeven": round(c["strike"] + c["last"], 2),
                    "capital": round(c["last"] * 100, 2),
                    "risk": "无股息、无投票权；到期前若未上涨仍会损失时间价值部分",
                })

            pmcc_long = pick_contract(calls, target_delta=0.85)
            pmcc_short = pick_contract(calls, target_delta=0.3)
            if pmcc_long and pmcc_short and pmcc_short["strike"] > pmcc_long["strike"]:
                block["call"].append({
                    "name": "长期看涨 + 卖近月 Call", "en": "Poor Man's Covered Call",
                    "option_type": "CALL", "role": "组合", "stance": "bullish",
                    "score": round(_score(53, "bullish", ctx, iv_level, has_position, "组合"), 0),
                    "suit": "长期看多，但判断中间会反复震荡",
                    "advice": (f"买入 1 张 {dte} 天的深度实值 Call（行权价 {pmcc_long['strike']:.0f}），"
                               f"再逐月卖出约 30–45 天、Delta 0.3 左右的虚值 Call"),
                    "reason": ("用长期 Call 替代正股来做备兑，资金效率高于直接持股——"
                               "同样的看多敞口，占用的资金少得多，且下行最多亏掉权利金。"
                               "靠反复卖近月 Call 持续摊薄长期 Call 的成本。"
                               "风险是正股大跌时，长期 Call 同样承受亏损"),
                    "legs": [_mk_leg("BUY", "CALL", pmcc_long)],
                    "premium": round(pmcc_long["last"] * 100, 2),
                    "max_profit": "长期 Call 上涨空间 - 净成本（卖 Call 会封顶）",
                    "max_loss": round(pmcc_long["last"] * 100, 2),
                    "breakeven": round(pmcc_long["strike"] + pmcc_long["last"], 2),
                    "capital": round(pmcc_long["last"] * 100, 2),
                    "risk": "结构复杂；股价大涨时被卖腿封顶，需滚动管理",
                })

            cc = pick_contract(calls, target_delta=0.2)
            if cc:
                block["call"].append({
                    "name": "长期备兑看涨", "en": "Long-Term Covered Call",
                    "option_type": "CALL", "role": "卖方", "stance": "neutral",
                    "score": round(_score(50, "neutral", ctx, iv_level, has_position, "卖方",
                                          need_position=True), 0),
                    "suit": "长期持有正股、不急于卖出，希望在震荡期增厚收益",
                    "advice": (f"每 100 股卖出 1 张 {dte} 天、行权价 {cc['strike']:.0f} "
                               f"（Delta {cc.get('delta'):.2f}）的 Call"),
                    "reason": ("相比按月备兑，长期备兑滚动次数少、交易成本低、也不用频繁盯盘。"
                               "Delta 0.2 左右的虚值 Call 保留了相当的上涨空间，"
                               "适合『长期看好但短期不指望大涨』的仓位"),
                    "legs": [_mk_leg("SELL", "CALL", cc)],
                    "premium": round(cc["last"] * 100, 2),
                    "max_profit": round((cc["strike"] - S) * 100 + cc["last"] * 100, 2),
                    "max_loss": "正股下跌风险（权利金可部分对冲）",
                    "breakeven": round(S - cc["last"], 2),
                    "capital": "需持有 100 股正股",
                    "risk": "上涨收益封顶；中途想调整成本高",
                })

            # ---------------- PUT（中长期） ----------------
            p = pick_contract(puts, target_delta=-0.35)
            if p:
                block["put"].append({
                    "name": "保护性看跌", "en": "Protective Put",
                    "option_type": "PUT", "role": "买方", "stance": "defensive",
                    "score": round(_score(54, "neutral", ctx, iv_level, has_position, "买方",
                                          need_position=True), 0),
                    "suit": "长期持有正股，担心大幅回撤又不想卖出（避免踏空或税费）",
                    "advice": (f"每持有 100 股，买入 1 张 {dte} 天、行权价 {p['strike']:.0f} "
                               f"（Delta {p.get('delta'):.2f}）的 Put"),
                    "reason": (f"相当于给持仓设了一个『最坏情况』：无论跌到多少，"
                               f"都能按 {p['strike']:.0f} 卖出，而上涨空间仍然全部保留。"
                               "相比反复买 1 个月 Put，长期 Put 的单次成本低、不用频繁滚动，"
                               "也不会因为某次忘了续保而裸奔"),
                    "legs": [_mk_leg("BUY", "PUT", p)],
                    "premium": round(p["last"] * 100, 2),
                    "max_profit": "正股上涨空间 - 权利金",
                    "max_loss": round((S - p["strike"]) * 100 + p["last"] * 100, 2),
                    "breakeven": round(S + p["last"], 2),
                    "capital": round(p["last"] * 100, 2),
                    "risk": "持续支出权利金；若不跌则这笔钱沉没",
                })

            csp = pick_contract(puts, target_delta=-0.25)
            if csp:
                block["put"].append({
                    "name": "长期卖出看跌", "en": "Long-Term Cash-Secured Put",
                    "option_type": "PUT", "role": "卖方", "stance": "bullish",
                    "score": round(_score(52, "bullish", ctx, iv_level, has_position, "卖方"), 0),
                    "suit": "认可长期价值，想在更低价格分批建仓",
                    "advice": (f"卖出 1 张 {dte} 天、行权价 {csp['strike']:.0f} 的 Put，"
                               f"预留 ${csp['strike'] * 100:,.0f} 现金"),
                    "reason": ("把『挂单等回调』变成有收益的事——等到了按更低成本接货，"
                               "等不到就白收权利金。期限越长，容错空间越大："
                               "即便短期被套，也有足够时间等缠论结构修复，"
                               "而不像短周期那样被迫在最低点附近做决定"),
                    "legs": [_mk_leg("SELL", "PUT", csp)],
                    "premium": round(csp["last"] * 100, 2),
                    "max_profit": round(csp["last"] * 100, 2),
                    "max_loss": round((csp["strike"] - csp["last"]) * 100, 2),
                    "breakeven": round(csp["strike"] - csp["last"], 2),
                    "capital": round(csp["strike"] * 100, 2),
                    "risk": "资金长期被占用；标的大跌时接货成本仍高于市价",
                })

            pb = pick_contract(puts, target_delta=-0.3)
            cb = pick_contract(calls, target_delta=0.3)
            if pb and cb:
                net = (pb["last"] - cb["last"]) * 100
                block["put"].append({
                    "name": "领口策略", "en": "Collar",
                    "option_type": "PUT", "role": "组合", "stance": "defensive",
                    "score": round(_score(52, "neutral", ctx, iv_level, has_position, "组合",
                                          need_position=True), 0),
                    "suit": "长期看好但短期不确定，且不能承受大回撤",
                    "advice": (f"持有正股 + 买入 1 张行权价 {pb['strike']:.0f} 的 Put "
                               f"+ 卖出 1 张同到期行权价 {cb['strike']:.0f} 的 Call，"
                               f"净成本约 ${net:.0f}"),
                    "reason": ("卖 Call 收的权利金抵消买 Put 的成本，通常能做到接近零成本地"
                               "把下行风险锁死在区间内；上涨仍能参与，只是超出 Call 行权价的部分让渡出去。"
                               "适合『还想拿着，但接下来这几个月经不起折腾』的情形"),
                    "legs": [_mk_leg("BUY", "PUT", pb), _mk_leg("SELL", "CALL", cb)],
                    "premium": round(net, 2),
                    "max_profit": round((cb["strike"] - S) * 100 - net, 2),
                    "max_loss": round((S - pb["strike"]) * 100 + net, 2),
                    "breakeven": round(S + net / 100, 2),
                    "capital": "需持有 100 股正股",
                    "risk": "上涨封顶；若正股大涨会明显跑输",
                })

        for side in ("call", "put"):
            block[side].sort(key=lambda x: -x.get("score", 0))
        result["horizons"][hk] = block

    return result
