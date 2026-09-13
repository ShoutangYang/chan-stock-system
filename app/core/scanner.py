"""
扫描器：对股票池执行批量分析（并发抓取 + 缠论 + 三策略），结果入库。
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import config as config_mod
from . import data as data_mod
from . import db
from . import strategies as strat

_scan_lock = threading.Lock()
_scan_state = {"running": False, "progress": 0, "total": 0, "current": "", "log_id": None}


def get_scan_state() -> Dict[str, Any]:
    with _scan_lock:
        return dict(_scan_state)


def _set_state(**kw) -> None:
    with _scan_lock:
        _scan_state.update(kw)


def analyze_one(symbol: str, strategies: Optional[List[str]] = None, period: str = "2y") -> Dict[str, Any]:
    """分析单只股票（含数据获取），异常不会抛出。"""
    try:
        df = data_mod.get_history(symbol, period=period, interval="1d")
        cfg = config_mod.load()
        res = strat.analyze_symbol(
            df, symbol, strategies=strategies,
            chan_kwargs=cfg.get("chan", {}),
            vp_kwargs=cfg.get("volprice", {}),
        )
        if res.get("ok"):
            res["name"] = _lookup_name(symbol)
        return res
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "error": str(e)}


_name_cache: Dict[str, str] = {}


def _lookup_name(symbol: str) -> str:
    if symbol in _name_cache:
        return _name_cache[symbol]
    item = db.get_watch_item(symbol)
    name = (item or {}).get("name") or ""
    if not name:
        try:
            name = data_mod.get_info(symbol).get("shortName") or symbol
        except Exception:
            name = symbol
    _name_cache[symbol] = name
    return name


def scan(
    symbols: List[str],
    strategies: Optional[List[str]] = None,
    max_workers: int = 8,
    save: bool = True,
    scan_type: str = "watchlist",
) -> Dict[str, Any]:
    """
    批量扫描。返回本次命中的结果列表与统计信息。
    """
    db.init_db()
    if _scan_state["running"]:
        return {"ok": False, "error": "扫描任务正在运行中", "hits": []}

    symbols = list(dict.fromkeys([s.upper().strip() for s in symbols if s and s.strip()]))
    if not symbols:
        return {"ok": False, "error": "股票池为空", "hits": []}

    scan_date = datetime.now().strftime("%Y-%m-%d")
    log_id = db.start_scan_log(scan_type)

    _set_state(running=True, progress=0, total=len(symbols), current="", log_id=log_id)

    results: List[Dict[str, Any]] = []
    done = 0
    errors: List[str] = []

    try:
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(analyze_one, s, strategies): s for s in symbols}
            for fut in cf.as_completed(futures):
                sym = futures[fut]
                done += 1
                _set_state(progress=done, current=sym)
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{sym}: {e}")
                    continue

                if not res.get("ok"):
                    errors.append(f"{sym}: {res.get('error', '未知错误')}")
                    continue

                results.append(res)

                if save:
                    for hit in res.get("hits", []):
                        db.save_scan_result(
                            scan_date=scan_date,
                            symbol=sym,
                            strategy=hit["strategy"],
                            score=hit["score"],
                            price=hit["price"],
                            change_pct=hit["change_pct"],
                            signals=hit["signals"],
                            reasons=hit["reasons"],
                            metrics=hit["metrics"],
                        )
                    # 记录缠论信号历史
                    for sig in res.get("chan", {}).get("signals", []):
                        db.save_signal(sym, sig)
                    # 记录量价信号历史（买入/卖出提醒）
                    for sig in (res.get("volprice") or {}).get("signals", []):
                        db.save_signal(sym, {
                            "date": sig.get("date"),
                            "code": sig.get("code"),
                            "side": sig.get("side"),
                            "price": sig.get("price"),
                            "confidence": sig.get("confidence"),
                            "reason": "；".join(sig.get("reasons", [])[:3]),
                        })
                    # 记录行情快照
                    try:
                        db._conn().execute(
                            "INSERT OR REPLACE INTO daily_quote "
                            "(symbol,quote_date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                            (
                                sym,
                                res.get("date") or scan_date,
                                res.get("indicators", {}).get("ma20"),
                                None, None,
                                res.get("price"),
                                res.get("volume"),
                            ),
                        )
                        db._conn().commit()
                    except Exception:
                        pass

        hits = []
        for r in results:
            for h in r.get("hits", []):
                hits.append({**h, "name": r.get("name", ""), "date": r.get("date", "")})

        hits.sort(key=lambda x: -x["score"])

        db.finish_scan_log(log_id, total=len(symbols), hits=len(hits), status="done",
                           message=f"错误 {len(errors)} 条")
        _set_state(running=False, progress=done, current="")

        return {
            "ok": True,
            "scan_date": scan_date,
            "total": len(symbols),
            "scanned": len(results),
            "hit_count": len(hits),
            "hits": hits,
            "details": results,
            "errors": errors[:20],
            "log_id": log_id,
        }

    except Exception as e:  # noqa: BLE001
        db.finish_scan_log(log_id, total=len(symbols), hits=0, status="error", message=str(e))
        _set_state(running=False)
        return {"ok": False, "error": str(e), "hits": []}


def scan_market(
    universe: Optional[List[str]] = None,
    strategies: Optional[List[str]] = None,
    limit: Optional[int] = None,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """扫描市场（默认池或标普500）寻找符合条件的标的。"""
    syms = universe or data_mod.get_universe()
    if limit:
        syms = syms[:limit]
    return scan(syms, strategies=strategies, max_workers=max_workers, scan_type="market")
