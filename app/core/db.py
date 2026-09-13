"""
SQLite 持久层：股票池、扫描结果、信号历史、每日行情快照。
线程安全：每个调用创建独立连接（WAL 模式）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "system.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL UNIQUE,
    name          TEXT,
    sector        TEXT,
    added_at      TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL DEFAULT 'manual',
    tags          TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    position_qty  REAL DEFAULT 0,
    position_cost REAL,
    target_price  REAL,
    stop_price    REAL
);

CREATE TABLE IF NOT EXISTS scan_result (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date  TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    strategy   TEXT NOT NULL,
    score      REAL NOT NULL DEFAULT 0,
    price      REAL,
    change_pct REAL,
    signals    TEXT DEFAULT '[]',
    reasons    TEXT DEFAULT '[]',
    metrics    TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(scan_date, symbol, strategy)
);

CREATE TABLE IF NOT EXISTS signal_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    sig_date   TEXT NOT NULL,
    code       TEXT NOT NULL,
    side       TEXT NOT NULL,
    price      REAL,
    confidence REAL,
    reason     TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE(symbol, sig_date, code)
);

CREATE TABLE IF NOT EXISTS daily_quote (
    symbol     TEXT NOT NULL,
    quote_date TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     REAL,
    PRIMARY KEY (symbol, quote_date)
);

CREATE TABLE IF NOT EXISTS scan_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    scan_type  TEXT NOT NULL,
    total      INTEGER DEFAULT 0,
    hits       INTEGER DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'running',
    message    TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_result(scan_date);
CREATE INDEX IF NOT EXISTS idx_signal_symbol ON signal_history(symbol);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate()


def _migrate() -> None:
    """给旧库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    with tx() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        if "market" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")
        # 已有数据按代码形态回填市场
        rows = conn.execute("SELECT symbol, market FROM watchlist").fetchall()
        for r in rows:
            sym, mkt = r[0], r[1]
            if mkt in ("US", "HK"):
                continue
            guess = "HK" if str(sym).strip().isdigit() else "US"
            conn.execute("UPDATE watchlist SET market=? WHERE symbol=?", (guess, sym))


@contextmanager
def tx():
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------
# 股票池
# ----------------------------------------------------------------------------
def add_to_watchlist(
    symbol: str,
    name: str = "",
    sector: str = "",
    source: str = "manual",
    tags: str = "",
    notes: str = "",
    market: str = "",
) -> Dict[str, Any]:
    from app.core.market import normalize
    mkt, symbol = normalize(symbol, market or None)
    if not symbol:
        raise ValueError("symbol 不能为空")
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE watchlist SET enabled=1, market=?, "
                "name=COALESCE(NULLIF(?,''), name), "
                "sector=COALESCE(NULLIF(?,''), sector), tags=?, notes=COALESCE(NULLIF(?,''), notes) "
                "WHERE symbol=?",
                (mkt, name, sector, tags, notes, symbol),
            )
            action = "updated"
        else:
            conn.execute(
                "INSERT INTO watchlist (symbol,name,sector,added_at,enabled,source,tags,notes,market)"
                " VALUES (?,?,?,?,1,?,?,?,?)",
                (symbol, name, sector, _now(), source, tags, notes, mkt),
            )
            action = "created"
    return {"symbol": symbol, "market": mkt, "action": action}


def remove_from_watchlist(symbol: str) -> None:
    from app.core.market import normalize
    _, sym = normalize(symbol)
    with tx() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))


def update_watchlist_item(symbol: str, **fields) -> None:
    allowed = {
        "name", "sector", "enabled", "tags", "notes",
        "position_qty", "position_cost", "target_price", "stop_price",
        "market",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    from app.core.market import normalize
    _, sym = normalize(symbol)
    vals.append(sym)
    with tx() as conn:
        conn.execute(f"UPDATE watchlist SET {','.join(sets)} WHERE symbol=?", vals)


def get_watchlist(only_enabled: bool = True, market: str = "") -> List[Dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM watchlist WHERE 1=1"
    vals: list = []
    if only_enabled:
        sql += " AND enabled=1"
    if market:
        sql += " AND market=?"
        vals.append(market)
    sql += " ORDER BY market, added_at DESC"
    return [dict(r) for r in _conn().execute(sql, vals).fetchall()]


def get_watch_item(symbol: str) -> Optional[Dict[str, Any]]:
    init_db()
    from app.core.market import normalize
    _, sym = normalize(symbol)
    row = _conn().execute(
        "SELECT * FROM watchlist WHERE symbol=?", (sym,)
    ).fetchone()
    return dict(row) if row else None


def watchlist_symbols(market: str = "") -> List[str]:
    return [r["symbol"] for r in get_watchlist(market=market)]


# ----------------------------------------------------------------------------
# 扫描结果
# ----------------------------------------------------------------------------
def save_scan_result(
    scan_date: str,
    symbol: str,
    strategy: str,
    score: float,
    price: Optional[float],
    change_pct: Optional[float],
    signals: List[Dict],
    reasons: List[str],
    metrics: Dict,
) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scan_result "
            "(scan_date,symbol,strategy,score,price,change_pct,signals,reasons,metrics,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                scan_date, symbol.upper(), strategy, float(score), price, change_pct,
                json.dumps(signals, ensure_ascii=False),
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False, default=str),
                _now(),
            ),
        )


def get_scan_results(scan_date: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    init_db()
    date = scan_date or latest_scan_date() or _today()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM scan_result WHERE scan_date=? ORDER BY score DESC LIMIT ?",
        (date, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("signals", "reasons", "metrics"):
            try:
                d[k] = json.loads(d[k]) if d[k] else ([] if k != "metrics" else {})
            except Exception:
                d[k] = [] if k != "metrics" else {}
        out.append(d)
    return out


def latest_scan_date() -> Optional[str]:
    init_db()
    row = _conn().execute(
        "SELECT scan_date FROM scan_result ORDER BY scan_date DESC LIMIT 1"
    ).fetchone()
    return row["scan_date"] if row else None


def clear_scan_date(scan_date: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM scan_result WHERE scan_date=?", (scan_date,))


# ----------------------------------------------------------------------------
# 信号历史
# ----------------------------------------------------------------------------
def save_signal(symbol: str, sig: Dict[str, Any]) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO signal_history "
            "(symbol,sig_date,code,side,price,confidence,reason,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,'active',?)",
            (
                symbol.upper(), sig.get("date", _today()), sig.get("code"),
                sig.get("side"), sig.get("price"), sig.get("confidence"),
                sig.get("reason"), _now(),
            ),
        )


def get_signal_history(symbol: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    init_db()
    conn = _conn()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM signal_history WHERE symbol=? ORDER BY sig_date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM signal_history ORDER BY sig_date DESC, confidence DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------
def start_scan_log(scan_type: str) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO scan_log (started_at, scan_type, status) VALUES (?,?,'running')",
            (_now(), scan_type),
        )
        return int(cur.lastrowid)


def finish_scan_log(log_id: int, total: int, hits: int, status: str = "done", message: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE scan_log SET finished_at=?, total=?, hits=?, status=?, message=? WHERE id=?",
            (_now(), total, hits, status, message, log_id),
        )


def get_scan_logs(limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    rows = _conn().execute(
        "SELECT * FROM scan_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
