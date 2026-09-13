"""
美股交易系统 - FastAPI 主程序。

启动： python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.core import config, data as data_mod, db, options as opt_mod, review
from app.core import indicators as ind
from app.core import scanner, strategies as strat
from app.core import backtest as bt, optimizer as opt_engine
from app.core import btsource as bts
from app.core import volprice as vp_mod
from app.core import hotspot as hs_mod
from app.core import fundamental as fund_mod
from app.core.backtest import ExitRule, backtest_universe, result_to_dict
from app.core.chanlun import ChanEngine

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "app", "web")

app = FastAPI(title="美股缠论交易系统", version="1.0.0")

db.init_db()


# ----------------------------------------------------------------------------
# 请求模型
# ----------------------------------------------------------------------------
class WatchItemIn(BaseModel):
    symbol: str
    name: str = ""
    tags: str = ""
    notes: str = ""
    market: str = ""        # 留空则按代码形态自动判断（5 位数字 = 港股）


class WatchUpdateIn(BaseModel):
    name: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    enabled: Optional[int] = None
    position_qty: Optional[float] = None
    position_cost: Optional[float] = None
    target_price: Optional[float] = None
    stop_price: Optional[float] = None


class ScanIn(BaseModel):
    symbols: Optional[List[str]] = None
    strategies: Optional[List[str]] = None
    max_workers: int = 8
    market: str = ""            # US / HK / 空=全部


class MarketScanIn(BaseModel):
    universe: Optional[List[str]] = None
    strategies: Optional[List[str]] = None
    limit: int = 120
    max_workers: int = 10
    market: str = "US"          # US / HK


class HotspotIn(BaseModel):
    window: int = 5
    mode: str = "full"          # full(日线累计) | fast(仅当日实时报价)
    market: str = "US"          # US / HK
    top_sector: int = 10
    top_flow_sector: int = 5
    top_stock: int = 10


class ConfigIn(BaseModel):
    patch: Dict[str, Any]


class BacktestIn(BaseModel):
    symbols: Optional[List[str]] = None
    period: str = "3y"
    chan: Optional[Dict[str, Any]] = None
    exit: Optional[Dict[str, Any]] = None
    mode: str = "long_only"
    entry_from_ratio: float = 0.0


class OptimizeIn(BaseModel):
    symbols: Optional[List[str]] = None
    period: str = "3y"
    n_samples: int = 40
    objective: str = "risk_adjusted"
    split: float = 0.7
    min_trades: int = 25
    top_k: int = 5
    mode: str = "long_only"


# ----------------------------------------------------------------------------
# 静态资源
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ----------------------------------------------------------------------------
# 股票池
# ----------------------------------------------------------------------------
@app.get("/api/watchlist")
def api_watchlist(market: str = ""):
    items = db.get_watchlist(only_enabled=False, market=market)
    return {"ok": True, "items": items, "market": market or "ALL"}


@app.post("/api/watchlist")
def api_watchlist_add(item: WatchItemIn):
    from app.core.market import normalize, hk_meta, MARKET_META
    mkt, symbol = normalize(item.symbol, item.market or None)
    if not symbol:
        raise HTTPException(400, "代码不能为空")

    # 校验代码有效性并补全名称
    name = item.name
    sector = ""
    if mkt == "HK":
        meta = hk_meta().get(symbol)
        if meta:
            name = name or meta[0]
            sector = meta[1]
        if not name:
            name = data_mod.get_name(symbol) or symbol
    else:
        try:
            info = data_mod.get_info(symbol)
            if not name:
                name = info.get("shortName") or symbol
            sector = info.get("sector") or ""
        except Exception:
            name = name or symbol

    res = db.add_to_watchlist(symbol, name=name, sector=sector,
                              tags=item.tags, notes=item.notes, market=mkt)
    return {"ok": True, **res, "name": name,
            "currency": MARKET_META[mkt]["currency_symbol"]}


@app.patch("/api/watchlist/{symbol}")
def api_watchlist_update(symbol: str, body: WatchUpdateIn):
    fields = {k: v for k, v in body.dict().items() if v is not None}
    db.update_watchlist_item(symbol, **fields)
    return {"ok": True, "item": db.get_watch_item(symbol)}


@app.delete("/api/watchlist/{symbol}")
def api_watchlist_delete(symbol: str):
    db.remove_from_watchlist(symbol)
    return {"ok": True}


# ----------------------------------------------------------------------------
# 扫描
# ----------------------------------------------------------------------------
@app.post("/api/scan/run")
def api_scan_run(body: ScanIn):
    state = scanner.get_scan_state()
    if state["running"]:
        return {"ok": False, "error": "已有扫描任务在运行中"}

    syms = body.symbols or db.watchlist_symbols(market=body.market or "")
    if not syms:
        return {"ok": False, "error": "股票池为空，请先添加标的"}
    strategies = body.strategies or _enabled_strategies()

    result = scanner.scan(syms, strategies=strategies, max_workers=body.max_workers,
                          scan_type="watchlist")
    result["market"] = body.market or "ALL"
    return result


@app.post("/api/scan/market")
def api_scan_market(body: MarketScanIn):
    state = scanner.get_scan_state()
    if state["running"]:
        return {"ok": False, "error": "已有扫描任务在运行中"}
    strategies = body.strategies or _enabled_strategies()
    universe = body.universe
    if not universe and body.market:
        from app.core.market import universe_of
        universe = universe_of(body.market)
    res = scanner.scan_market(universe, strategies=strategies,
                              limit=body.limit, max_workers=body.max_workers)
    res["market"] = body.market or "US"
    return res


@app.get("/api/scan/status")
def api_scan_status():
    return {"ok": True, **scanner.get_scan_state()}


@app.get("/api/scan/results")
def api_scan_results(date: Optional[str] = None):
    results = db.get_scan_results(date)
    return {"ok": True, "date": date or db.latest_scan_date(), "items": results}


@app.get("/api/scan/logs")
def api_scan_logs():
    return {"ok": True, "items": db.get_scan_logs()}


# ----------------------------------------------------------------------------
# 单只股票详情
# ----------------------------------------------------------------------------
def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线聚合为周线（周五结束）。"""
    d = df.copy()
    d["dt"] = pd.to_datetime(d["date"])
    w = (
        d.set_index("dt")
        .resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    if w.empty:
        return df
    w["date"] = w["dt"].dt.strftime("%Y-%m-%d")
    return w[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


@app.get("/api/symbol/{symbol}")
def api_symbol_detail(symbol: str, period: str = "2y", refresh: str = "0",
                      level: str = "day"):
    symbol = symbol.upper().strip()
    try:
        df = data_mod.get_history(symbol, period=period, interval="1d",
                                  force_refresh=(refresh == "1"))
    except Exception as e:
        raise HTTPException(404, f"获取 {symbol} 数据失败: {e}")

    # 周线级别：缠论强调多级别联立，周线用于确认日线信号的大级别位置
    if level == "week":
        df = to_weekly(df)
        if len(df) < 60:
            raise HTTPException(400, "周线数据不足，请改用更长的周期（如 5y/10y）")

    cfg = config.load()
    res = strat.analyze_symbol(df, symbol, strategies=_enabled_strategies(),
                               chan_kwargs=cfg.get("chan", {}))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "分析失败"))

    # K线数据（给前端绘图，最近 260 根）
    tail = df.tail(260).reset_index(drop=True)
    offset = len(df) - len(tail)

    ma20_all = ind.sma(df["close"], 20).reset_index(drop=True)
    ma60_all = ind.sma(df["close"], 60).reset_index(drop=True)

    klines = []
    for i in range(len(tail)):
        gi = offset + i
        m20 = ma20_all.iloc[gi] if gi < len(ma20_all) else float("nan")
        m60 = ma60_all.iloc[gi] if gi < len(ma60_all) else float("nan")
        klines.append(
            {
                "i": gi,
                "d": str(tail["date"].iloc[i]),
                "o": round(float(tail["open"].iloc[i]), 4),
                "h": round(float(tail["high"].iloc[i]), 4),
                "l": round(float(tail["low"].iloc[i]), 4),
                "c": round(float(tail["close"].iloc[i]), 4),
                "v": float(tail["volume"].iloc[i]),
                "ma20": None if pd.isna(m20) else round(float(m20), 4),
                "ma60": None if pd.isna(m60) else round(float(m60), 4),
            }
        )

    # 缠论结构索引需要偏移到 tail 坐标系
    chan = res["chan"]
    chan["offset"] = offset
    for key in ("fractals", "strokes", "centers"):
        for item in chan.get(key, []):
            for idx_key in ("raw_idx", "start_raw", "end_raw"):
                if idx_key in item and item[idx_key] is not None:
                    item[idx_key] = item[idx_key] - offset
    for s in chan.get("signals", []):
        s["raw_idx"] = s["raw_idx"] - offset

    item = db.get_watch_item(symbol) or {}
    from app.core.market import normalize, MARKET_META
    mkt, sym = normalize(symbol)
    meta = MARKET_META[mkt]

    name = item.get("name") or res.get("name")
    if not name:
        try:
            name = data_mod.get_name(sym)
        except Exception:
            name = ""
        if not name or name == sym:
            name = sym

    return {
        "ok": True,
        "symbol": sym,
        "market": mkt,
        "market_name": meta["name"],
        "currency": meta["currency_symbol"],
        "name": name,
        "watch": item,
        "price": res["price"],
        "change_pct": res["change_pct"],
        "date": res["date"],
        "indicators": res["indicators"],
        "chan": chan,
        "hits": res["hits"],
        "klines": klines,
        "period": period,
        "level": level,
    }


@app.get("/api/symbol/{symbol}/fundamentals")
def api_symbol_fundamentals(symbol: str, years: int = 3, refresh: str = "0"):
    """
    基本面分析：估值快照（PE/EPS/市值/52周位置）、
    历史估值对比（PE 带 + 价格分位）、重要事件（回购/分红/异动日）。
    """
    try:
        years = max(1, min(int(years), 5))
        return fund_mod.analyze_fundamentals(symbol, years=years)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"基本面数据获取失败：{e}"}


@app.get("/api/symbol/{symbol}/options")
def api_symbol_options(symbol: str, expiration: Optional[str] = None):
    symbol = symbol.upper().strip()
    try:
        df = data_mod.get_history(symbol, period="1y", interval="1d")
        cfg = config.load()
        res = strat.analyze_symbol(df, symbol, chan_kwargs=cfg.get("chan", {}))
        signals = res.get("chan", {}).get("signals", []) if res.get("ok") else []
        price = res.get("price") if res.get("ok") else None
        hint = None
        if res.get("ok") and res.get("hits"):
            hint = res["hits"][0]["strategy"]
    except Exception:
        signals, price, hint = [], None, None

    item = db.get_watch_item(symbol) or {}
    has_position = float(item.get("position_qty") or 0) > 0

    out = opt_mod.build_option_strategies(
        symbol, price, signals, strategy_hint=hint, has_position=has_position
    )

    # 附带期权链原始数据供展示
    chain = opt_mod.get_chain(symbol, expiration=expiration)
    if chain.get("ok"):
        S = price or chain.get("underlying") or 0
        calls = [c for c in chain.get("calls", []) if abs((c.get("moneyness") or 0)) < 0.25]
        puts = [c for c in chain.get("puts", []) if abs((c.get("moneyness") or 0)) < 0.25]
        out["chain"] = {
            "expirations": chain.get("expirations", [])[:12],
            "selected": chain.get("selected_expiration"),
            "dte": chain.get("dte"),
            "underlying": chain.get("underlying"),
            "calls": calls[:40],
            "puts": puts[:40],
        }
    return out


@app.get("/api/symbol/{symbol}/signals")
def api_symbol_signals(symbol: str):
    return {"ok": True, "items": db.get_signal_history(symbol)}


# ----------------------------------------------------------------------------
# 信号与配置
# ----------------------------------------------------------------------------
@app.get("/api/signals")
def api_signals(limit: int = 200):
    return {"ok": True, "items": db.get_signal_history(limit=limit)}


# ----------------------------------------------------------------------------
# 量价关系提醒
# ----------------------------------------------------------------------------
@app.get("/api/markets")
def api_markets():
    """市场元信息：货币、交易时段、代码规则、可选池规模。"""
    from app.core import market as mk
    return {
        "ok": True,
        "markets": [
            {**mk.MARKET_META[m], "universe_size": len(mk.universe_of(m))}
            for m in (mk.US, mk.HK)
        ],
    }


@app.get("/api/universe")
def api_universe(market: str = "US"):
    """某市场的默认扫描池（带名称）。"""
    from app.core import market as mk
    mkt = "HK" if str(market).upper() == "HK" else mk.US
    if mkt == mk.HK:
        items = [{"symbol": c, "name": n, "industry": ind} for c, n, ind in mk.HK_UNIVERSE]
        etfs = [{"symbol": c, "name": n, "kind": k, "desc": d} for c, n, k, d in mk.HK_ETFS]
    else:
        items = [{"symbol": s, "name": "", "industry": ""} for s in data_mod.get_universe()]
        etfs = []
    return {"ok": True, "market": mkt, "items": items, "etfs": etfs,
            "industries": mk.hk_industries() if mkt == mk.HK else []}


@app.get("/api/hotspots")
def api_hotspots(window: int = 5, mode: str = "full", market: str = "US",
                 top_sector: int = 10, top_flow_sector: int = 5, top_stock: int = 10):
    """
    市场热点板块：
      · 资金最活跃的 TOP10 板块
      · 资金流入 TOP5 板块 + TOP10 股票
      · 资金流出 TOP5 板块 + TOP10 股票
    mode=fast 时只走实时报价（1-2 秒），full 走日线累计（首次 20-30 秒，5 分钟内走缓存）。
    market=US 用美股行业/主题 ETF；market=HK 用港股 ETF + 港股通池。
    """
    try:
        window = max(1, min(int(window), 60))
        mode = "fast" if str(mode).lower() == "fast" else "full"
        market = "HK" if str(market).upper() == "HK" else "US"
        return hs_mod.build_hotspots(
            window=window, mode=mode, market=market,
            top_sector=max(3, min(int(top_sector), 40)),
            top_flow_sector=max(3, min(int(top_flow_sector), 20)),
            top_stock=max(3, min(int(top_stock), 50)),
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"热点数据获取失败：{e}"}


@app.post("/api/hotspots")
def api_hotspots_post(body: HotspotIn):
    return api_hotspots(body.window, body.mode, body.market, body.top_sector,
                        body.top_flow_sector, body.top_stock)


@app.get("/api/hotspots/meta")
def api_hotspots_meta():
    """板块篮子与口径说明。"""
    return {
        "ok": True,
        "sectors": [
            {"symbol": c, "name": n, "kind": k, "desc": d} for c, n, k, d in hs_mod.SECTOR_ETFS
        ],
        "stock_count": len(hs_mod.STOCK_UNIVERSE),
        "industries": hs_mod.INDUSTRY_ORDER,
        "method": {
            "model": "Chaikin A/D（累积派发线）",
            "formula": "MFM = ((C-L)-(H-C))/(H-L)；净流入 = Σ(MFM × Volume × Close)",
            "activity": "活跃度 = 0.62 × 成交额分位 + 0.38 × 量比分位",
            "vol_ratio": "量比 = 当前成交量 / 近 10 日均量（无则 5 日均量 / 20 日均量）",
            "caveat": "基于公开 OHLCV 的估算值，非交易所真实资金流；适合横向比较，不代表绝对金额",
        },
    }


@app.get("/api/volprice/meta")
def api_volprice_meta():
    """信号类型说明（供前端渲染图例）。"""
    return {
        "ok": True,
        "buy_codes": vp_mod.BUY_CODES,
        "sell_codes": vp_mod.SELL_CODES,
        "meta": vp_mod.SIGNAL_META,
        "params": config.load().get("volprice", {}),
    }


@app.get("/api/volprice/history")
def api_volprice_history(limit: int = 300):
    """历史量价提醒（来自 signal_history 中 VP_ 前缀的记录）。"""
    rows = db.get_signal_history(limit=limit * 2)
    items = [r for r in rows if str(r.get("code", "")).startswith("VP_")]
    return {"ok": True, "items": items[:limit]}


_VP_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_VP_CACHE_TTL = 300.0


def _volprice_one(symbol: str, period: str = "1y"):
    """分析单只股票的量价状态。"""
    import time as _t
    cfg = config.load()
    try:
        df = data_mod.get_history(symbol, period=period, interval="1d")
        res = strat.analyze_symbol(df, symbol,
                                   chan_kwargs=cfg.get("chan", {}),
                                   vp_kwargs=cfg.get("volprice", {}))
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "error": str(e)}

    if not res.get("ok"):
        return {"symbol": symbol, "ok": False, "error": res.get("error", "分析失败")}

    item = db.get_watch_item(symbol) or {}
    vpr = res.get("volprice") or {}
    return {
        "symbol": symbol,
        "name": item.get("name") or symbol,
        "ok": True,
        "price": res.get("price"),
        "change_pct": round(float(res.get("change_pct") or 0), 2),
        "date": res.get("date"),
        "relation": vpr.get("relation", {}),
        "state": vpr.get("state", {}),
        "signals": vpr.get("signals", []),
        "has_position": float(item.get("position_qty") or 0) > 0,
    }


@app.get("/api/volprice")
def api_volprice(symbols: Optional[str] = None, period: str = "1y",
                 refresh: str = "0", min_confidence: Optional[float] = None):
    """
    批量生成量价买卖提醒。默认扫描整个股票池。
    symbols: 逗号分隔，为空则用股票池。
    """
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    key = f"{symbols}|{period}"
    now = _t.time()
    if refresh != "1" and _VP_CACHE["data"] and _VP_CACHE["at"] + _VP_CACHE_TTL > now \
            and _VP_CACHE["data"].get("key") == key:
        cached = dict(_VP_CACHE["data"]["payload"])
        cached["cached"] = True
        return cached

    cfg = config.load()
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        syms = db.watchlist_symbols()
    syms = list(dict.fromkeys(syms))

    if not syms:
        return {"ok": False, "error": "股票池为空", "buys": [], "sells": []}

    workers = min(int(cfg.get("max_workers", 8)), max(1, len(syms)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda s: _volprice_one(s, period), syms))

    thr = float(min_confidence if min_confidence is not None
                else cfg.get("volprice", {}).get("min_confidence", 55))

    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    errors: List[str] = []
    scanned = 0

    for r in results:
        if not r.get("ok"):
            errors.append(f"{r['symbol']}: {r.get('error', '')}")
            continue
        scanned += 1
        for sig in r.get("signals", []):
            if float(sig.get("confidence", 0)) < thr:
                continue
            alert = {
                # name 保留为股票名，信号名另存 signal_name，避免被信号字段覆盖
                "symbol": r["symbol"], "name": r["name"],
                "price": r["price"], "change_pct": r["change_pct"],
                "date": r["date"], "has_position": r["has_position"],
                "relation": r.get("relation", {}),
                "signal_name": sig.get("name"),
                "code": sig.get("code"),
                "side": sig.get("side"),
                "confidence": sig.get("confidence"),
                "level": sig.get("level"),
                "stop": sig.get("stop"),
                "target": sig.get("target"),
                "reasons": sig.get("reasons", []),
                "metrics": sig.get("metrics", {}),
                "resonance": sig.get("resonance", False),
                "desc": sig.get("desc", ""),
            }
            (buys if sig["side"] == "buy" else sells).append(alert)

    buys.sort(key=lambda x: -x["confidence"])
    sells.sort(key=lambda x: -x["confidence"])

    payload = {
        "ok": True,
        "key": key,
        "scanned": scanned,
        "date": results[0].get("date") if results else "",
        "buys": buys, "sells": sells,
        "errors": errors[:10],
        "threshold": thr,
        "params": cfg.get("volprice", {}),
        "cached": False,
    }
    _VP_CACHE["at"] = now
    _VP_CACHE["data"] = {"key": key, "payload": payload}
    return payload


@app.get("/api/volprice/{symbol}")
def api_volprice_detail(symbol: str, period: str = "1y"):
    """单只股票的量价详情（含量能柱图与历史信号）。"""
    r = _volprice_one(symbol.upper(), period)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "分析失败"))
    try:
        df = data_mod.get_history(symbol.upper(), period=period, interval="1d")
        r["bars"] = vp_mod.volume_bars(df, 120)
        hist = vp_mod.scan_history(df, params=config.load().get("volprice", {}))
        r["history"] = hist[-40:]
    except Exception:  # noqa: BLE001
        r["bars"], r["history"] = [], []
    return r


# ----------------------------------------------------------------------------
# 信号复盘
# ----------------------------------------------------------------------------
@app.get("/api/review")
def api_review(limit: int = 400):
    return review.run_review(limit=limit)


@app.post("/api/review/refresh")
def api_review_refresh(limit: int = 400):
    return review.run_review(limit=limit, force=True)


# ----------------------------------------------------------------------------
# 回测与参数自迭代
# ----------------------------------------------------------------------------
_OPT_STATE: Dict[str, Any] = {
    "running": False, "progress": 0, "total": 0, "message": "", "result": None,
}


def _load_frames(symbols: List[str], period: str = "3y") -> Dict[str, pd.DataFrame]:
    """并发加载多只股票历史数据。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(sym: str):
        try:
            df = data_mod.get_history(sym, period=period, interval="1d")
            return sym, (df if df is not None and len(df) > 200 else None)
        except Exception:
            return sym, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        pairs = list(ex.map(one, symbols))
    return {s: d for s, d in pairs if d is not None}


def _current_exit_rule() -> ExitRule:
    cfg = config.load().get("exit", {})
    return ExitRule(
        stop_pct=float(cfg.get("stop_pct", 0.06)),
        take_pct=float(cfg.get("take_pct", 0.12)),
        max_hold=int(cfg.get("max_hold", 20)),
        use_chan_stop=bool(cfg.get("use_chan_stop", True)),
        exit_on_reverse=bool(cfg.get("exit_on_reverse", True)),
    )


@app.post("/api/backtest")
def api_backtest(body: BacktestIn):
    syms = body.symbols or db.watchlist_symbols()
    if not syms:
        return {"ok": False, "error": "股票池为空"}
    frames = _load_frames(syms, body.period)
    if not frames:
        return {"ok": False, "error": "未获取到有效行情数据"}

    chan = body.chan or config.load().get("chan", {})
    rule = ExitRule(**body.exit) if body.exit else _current_exit_rule()

    r = backtest_universe(frames, chan_params=chan, rule=rule,
                          mode=body.mode, entry_from_ratio=body.entry_from_ratio)
    out = result_to_dict(r)
    out["symbols"] = sorted(frames.keys())
    out["bars_per_symbol"] = int(sum(len(d) for d in frames.values()) / len(frames))
    return out


class SourceCompareIn(BaseModel):
    symbols: Optional[List[str]] = None
    period: str = "3y"
    sources: Optional[List[str]] = None
    mode: str = "long_only"
    oos_ratio: float = 0.7
    exit: Optional[Dict[str, Any]] = None
    min_confidence: Optional[float] = None


@app.post("/api/backtest/sources")
def api_backtest_sources(body: SourceCompareIn):
    """
    对比「缠论 / 量价 / 两者共振」三种信号源，含样本外检验。
    用于判断量价维度是否提供了增量价值。
    """
    syms = body.symbols or db.watchlist_symbols()
    if not syms:
        return {"ok": False, "error": "股票池为空"}
    frames = _load_frames(syms, body.period)
    if not frames:
        return {"ok": False, "error": "未获取到有效行情数据"}

    cfg = config.load()
    rule = ExitRule(**body.exit) if body.exit else _current_exit_rule()
    sources = [s for s in (body.sources or bts.SOURCES) if s in bts.SOURCES]

    try:
        r = bts.compare_sources(
            frames, sources=sources, chan_params=cfg.get("chan", {}),
            vp_params=cfg.get("volprice", {}), rule=rule, mode=body.mode,
            oos_ratio=body.oos_ratio, min_confidence=body.min_confidence,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"对比回测失败: {e}"}

    r["symbols"] = sorted(frames.keys())
    r["exit"] = {
        "stop_pct": rule.stop_pct, "take_pct": rule.take_pct,
        "max_hold": rule.max_hold, "use_chan_stop": rule.use_chan_stop,
        "exit_on_reverse": rule.exit_on_reverse,
    }
    return r


@app.get("/api/optimize/status")
def api_optimize_status():
    return {"ok": True, **_OPT_STATE}


@app.post("/api/optimize")
def api_optimize(body: OptimizeIn):
    if _OPT_STATE["running"]:
        return {"ok": False, "error": "已有优化任务在运行"}

    syms = body.symbols or db.watchlist_symbols()
    if not syms:
        return {"ok": False, "error": "股票池为空"}

    def run():
        try:
            frames = _load_frames(syms, body.period)
            if not frames:
                _OPT_STATE.update(running=False, message="未获取到有效行情数据")
                return

            def cb(i, total, msg):
                _OPT_STATE.update(progress=i, total=total, message=msg)

            res = opt_engine.optimize(
                frames, n_samples=body.n_samples, objective=body.objective,
                split=body.split, min_trades=body.min_trades,
                top_k=body.top_k, mode=body.mode, progress_cb=cb,
            )
            res["symbols"] = sorted(frames.keys())
            _OPT_STATE.update(running=False, result=res, message="完成",
                              progress=_OPT_STATE["total"])
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _OPT_STATE.update(running=False, message=f"失败: {e}")

    _OPT_STATE.update(running=True, progress=0, total=body.n_samples + body.top_k + 1,
                      message="正在加载行情…", result=None)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "message": "优化任务已启动"}


@app.post("/api/optimize/apply")
def api_optimize_apply():
    res = _OPT_STATE.get("result")
    if not res or not res.get("ok"):
        return {"ok": False, "error": "没有可用的优化结果"}
    if not res.get("accepted"):
        return {"ok": False, "error": "样本外未超越默认参数，拒绝应用以避免过拟合"}

    patch = opt_engine.apply_best_params(res["best"])
    patch["exit_source"] = "optimized"
    cfg = config.update(patch)
    return {"ok": True, "config": cfg, "applied": patch}


@app.post("/api/optimize/reset")
def api_optimize_reset():
    """恢复默认参数。"""
    cfg = config.update({
        "chan": {"min_stroke_bars": 4, "power_ratio": 1.0},
        "exit": {"stop_pct": 0.06, "take_pct": 0.12, "max_hold": 20,
                 "use_chan_stop": True, "exit_on_reverse": True},
        "exit_source": "default",
    })
    return {"ok": True, "config": cfg}


# ----------------------------------------------------------------------------
# 批量报价 / 持仓市值
# ----------------------------------------------------------------------------
@app.get("/api/quotes")
def api_quotes(symbols: str):
    """批量获取报价，用于股票池展示持仓盈亏。"""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    from concurrent.futures import ThreadPoolExecutor

    def one(sym: str):
        try:
            return data_mod.get_quote(sym)
        except Exception as e:  # noqa: BLE001
            return {"symbol": sym, "price": None, "error": str(e)}

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(one, syms))
    return {"ok": True, "quotes": out}


@app.get("/api/positions")
def api_positions():
    """持仓汇总：含现价、盈亏、以及当前缠论信号给出的操作建议。"""
    items = [w for w in db.get_watchlist(only_enabled=False)
             if float(w.get("position_qty") or 0) > 0]
    if not items:
        return {"ok": True, "items": [], "summary": None}

    from concurrent.futures import ThreadPoolExecutor

    def one(w: Dict[str, Any]):
        sym = w["symbol"]
        q = {}
        try:
            q = data_mod.get_quote(sym) or {}
        except Exception:
            pass
        price = q.get("price")
        cost = float(w.get("position_cost") or 0)
        qty = float(w.get("position_qty") or 0)
        pnl = pnl_pct = None
        if price and cost:
            pnl = (float(price) - cost) * qty
            pnl_pct = (float(price) / cost - 1) * 100

        # 当前缠论信号
        sigs: List[Dict[str, Any]] = []
        try:
            df = data_mod.get_history(sym, period="1y", interval="1d")
            cfg = config.load()
            res = strat.analyze_symbol(df, sym, chan_kwargs=cfg.get("chan", {}))
            if res.get("ok"):
                sigs = res["chan"].get("signals", [])
        except Exception:
            pass

        advice = "持有"
        if any(s["side"] == "sell" for s in sigs):
            advice = "缠论出现卖点，考虑减仓或备兑看涨"
        elif any(s["code"] in ("B1", "B2") for s in sigs):
            advice = "缠论出现一类/二类买点，可考虑补仓或卖Put接货"

        return {
            "symbol": sym, "name": w.get("name") or sym,
            "qty": qty, "cost": cost, "price": price,
            "market_value": round(float(price) * qty, 2) if price else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "target_price": w.get("target_price"),
            "stop_price": w.get("stop_price"),
            "signals": sigs,
            "advice": advice,
        }

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(one, items))

    total_cost = sum(r["cost"] * r["qty"] for r in rows if r["cost"])
    total_mv = sum(r["market_value"] or 0 for r in rows)
    total_pnl = sum(r["pnl"] or 0 for r in rows)

    return {
        "ok": True,
        "items": rows,
        "summary": {
            "count": len(rows),
            "cost": round(total_cost, 2),
            "market_value": round(total_mv, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost else None,
        },
    }


@app.get("/api/config")
def api_config():
    return {"ok": True, "config": config.load()}


@app.post("/api/config")
def api_config_update(body: ConfigIn):
    cfg = config.update(body.patch)
    _reschedule()
    return {"ok": True, "config": cfg}


@app.get("/api/strategies")
def api_strategies():
    return {"ok": True, "items": strat.STRATEGY_META}


def _app_version() -> str:
    """读取仓库根目录 VERSION 文件（缺失时返回 dev，不阻塞启动）。"""
    try:
        with open(os.path.join(BASE_DIR, "VERSION"), "r", encoding="utf-8") as f:
            v = f.read().strip()
            return ("v" + v) if v else "dev"
    except Exception:
        return "dev"


@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": _app_version(),
        "scan": scanner.get_scan_state(),
    }


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def _enabled_strategies() -> List[str]:
    cfg = config.load().get("strategies", {})
    return [k for k, v in cfg.items() if v] or ["oversold", "breakout", "reversal"]


# ----------------------------------------------------------------------------
# 定时扫描
# ----------------------------------------------------------------------------
_scheduler = None


def _run_scheduled_scan():
    try:
        cfg = config.load().get("auto_scan", {})
        strategies = _enabled_strategies()
        if cfg.get("scan_type") == "market":
            scanner.scan_market(None, strategies=strategies, limit=int(cfg.get("market_limit", 120)))
        else:
            syms = db.watchlist_symbols()
            if syms:
                scanner.scan(syms, strategies=strategies)
    except Exception:
        traceback.print_exc()


def _reschedule():
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        import apscheduler.triggers.cron as cron_trigger
    except Exception:
        return

    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=None)

    _scheduler.remove_all_jobs()
    cfg = config.load().get("auto_scan", {})
    if cfg.get("enabled", True):
        _scheduler.add_job(
            _run_scheduled_scan,
            "cron",
            hour=int(cfg.get("hour", 5)),
            minute=int(cfg.get("minute", 30)),
            id="daily_scan",
            replace_existing=True,
        )
    if not _scheduler.running:
        _scheduler.start()


@app.on_event("startup")
def on_startup():
    _reschedule()


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
    )
