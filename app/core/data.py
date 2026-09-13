"""
行情数据层（多数据源容错，针对国内网络优化）。

数据源优先级：
  1. 新浪财经 usstock 日K — 国内直连稳定，返回全历史日线；同时提供实时报价（含52周高低/市值/PE）。
  2. 东方财富 — 用于代码解析与中文名称（searchapi 可用）。
  3. Yahoo Finance (yfinance) — 海外环境或需要期权链时备用。

所有请求带重试与本地磁盘缓存；日线自动检测拆股并做前复权修正。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CACHE_TTL_SECONDS = 4 * 3600

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


class DataError(Exception):
    pass


def _safe_symbol(symbol: str) -> str:
    return "".join(c for c in symbol.upper() if c.isalnum() or c in ".-")


def _split(symbol: str):
    """拆成 (market, code)。港股统一成 5 位数字。"""
    from app.core import market as mk
    return mk.normalize(symbol)


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _http_get(url: str, headers: Optional[Dict] = None, timeout: int = 25, retries: int = 3,
              encoding: Optional[str] = None) -> str:
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        hdrs.update(headers)

    last_err: Optional[Exception] = None
    for attempt in range(retries):
        for backend in ("curl_cffi", "requests"):
            try:
                if backend == "curl_cffi":
                    from curl_cffi import requests as creq
                    r = creq.get(url, headers=hdrs, timeout=timeout, impersonate="chrome")
                    r.encoding = encoding or "utf-8"
                    text = r.text
                else:
                    import requests as req
                    r = req.get(url, headers=hdrs, timeout=timeout)
                    r.encoding = encoding or "utf-8"
                    text = r.text
                if text and len(text) > 10:
                    return text
                last_err = RuntimeError("响应为空")
            except Exception as e:  # noqa: BLE001
                last_err = e
        if attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.4)

    raise DataError(f"请求失败: {last_err}")


# ----------------------------------------------------------------------------
# 新浪财经
# ----------------------------------------------------------------------------
SINA_DAILY = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php"
              "/var%20_{sym}/US_MinKService.getDailyK?symbol={sym}")
SINA_QUOTE = "https://hq.sinajs.cn/list=gb_{sym}"


def sina_history(symbol: str, bars: int = 600) -> pd.DataFrame:
    """新浪美股日K（全历史，取最近 bars 根）。"""
    sym = _safe_symbol(symbol)
    url = SINA_DAILY.format(sym=sym)
    text = _http_get(url, headers={"Referer": "https://finance.sina.com.cn/"}, retries=3)

    # jsonp: /*<script>...</script>*/ var _AAPL([...]);
    i = text.find("(")
    j = text.rfind(")")
    if i < 0 or j <= i:
        raise DataError("新浪K线格式异常")
    try:
        arr = json.loads(text[i + 1:j])
    except Exception as e:
        raise DataError(f"新浪K线解析失败: {e}")

    if not isinstance(arr, list) or not arr:
        raise DataError("新浪K线为空")

    rows = []
    for x in arr:
        try:
            rows.append(
                {
                    "date": x["d"],
                    "open": float(x["o"]),
                    "high": float(x["h"]),
                    "low": float(x["l"]),
                    "close": float(x["c"]),
                    "volume": float(x.get("v") or 0),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue

    if not rows:
        raise DataError("新浪K线无有效行")

    df = pd.DataFrame(rows)
    df = df[df["close"] > 0].reset_index(drop=True)
    df = _adjust_splits(df)
    return df.tail(bars).reset_index(drop=True)


def _adjust_splits(df: pd.DataFrame, low: float = 0.6, high: float = 1.6) -> pd.DataFrame:
    """
    检测拆股/合股造成的价格跳空，对历史数据做前复权修正。
    单日收盘价比值 < low（如 1拆2 的 0.5）或 > high（合股）视为分割。
    """
    df = df.reset_index(drop=True).copy()
    n = len(df)
    if n < 3:
        return df

    c = df["close"].to_numpy(dtype=float)
    factor = np.ones(n)
    cum = 1.0
    for i in range(n - 1, 0, -1):
        if c[i - 1] > 0:
            r = c[i] / c[i - 1]
            if r < low or r > high:
                cum *= r
        factor[i - 1] = cum

    if np.allclose(factor, 1.0):
        return df

    for col in ("open", "high", "low", "close"):
        df[col] = df[col].to_numpy(dtype=float) * factor
    return df


def sina_quote(symbol: str) -> Optional[Dict]:
    """新浪实时/延迟报价（含中文名、52周高低、市值、PE）。"""
    sym = _safe_symbol(symbol)
    try:
        # 新浪行情接口返回 GBK 编码
        text = _http_get(
            SINA_QUOTE.format(sym=sym.lower()),
            headers={"Referer": "https://finance.sina.com.cn/"},
            retries=2, timeout=15, encoding="gbk",
        )
    except Exception:
        return None

    m = re.search(r'"([^"]*)"', text)
    if not m:
        return None
    p = m.group(1).split(",")
    if len(p) < 13:
        return None

    def f(idx, default=None):
        try:
            v = float(p[idx])
            return v if np.isfinite(v) else default
        except (ValueError, IndexError):
            return default

    return {
        "symbol": sym,
        "name": p[0] or sym,
        "price": f(1),
        "change_pct": f(2),
        "time": p[3] if len(p) > 3 else "",
        "change": f(4),
        "open": f(5),
        "high": f(6),
        "low": f(7),
        "year_high": f(8),
        "year_low": f(9),
        "volume": f(10),
        "avg_volume_10d": f(11),
        "market_cap": f(12),
        "pe": f(13),
        "eps": f(14),
    }


# ----------------------------------------------------------------------------
# 东方财富（代码解析 / 名称）
# ----------------------------------------------------------------------------
_SEC_ID_CACHE: Dict[str, Tuple[str, str]] = {}
_SEC_ID_FILE = os.path.join(CACHE_DIR, "_em_secid.json")


def _load_secid_cache() -> None:
    global _SEC_ID_CACHE
    if _SEC_ID_CACHE:
        return
    try:
        if os.path.exists(_SEC_ID_FILE):
            with open(_SEC_ID_FILE, "r", encoding="utf-8") as f:
                _SEC_ID_CACHE.update(json.load(f))
    except Exception:
        pass


def em_resolve(symbol: str) -> Optional[Tuple[str, str]]:
    """symbol -> (secid, 中文名)。"""
    symbol = _safe_symbol(symbol)
    _load_secid_cache()
    if symbol in _SEC_ID_CACHE:
        return _SEC_ID_CACHE[symbol]

    url = ("https://searchapi.eastmoney.com/api/suggest/get"
           f"?input={symbol}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=8")
    try:
        text = _http_get(url, headers={"Referer": "https://quote.eastmoney.com/"}, retries=2, timeout=15)
        items = ((json.loads(text).get("QuotationCodeTable") or {}).get("Data")) or []
        for it in items:
            if (it.get("Code") or "").upper() == symbol and it.get("SecurityTypeName") == "美股":
                _SEC_ID_CACHE[symbol] = (it["QuoteID"], it.get("Name") or symbol)
                try:
                    with open(_SEC_ID_FILE, "w", encoding="utf-8") as f:
                        json.dump(_SEC_ID_CACHE, f, ensure_ascii=False)
                except Exception:
                    pass
                return _SEC_ID_CACHE[symbol]
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------------
# yfinance
# ----------------------------------------------------------------------------
_yf_mod = None


def _yf():
    global _yf_mod
    if _yf_mod is None:
        try:
            import yfinance as yf
        except ImportError as e:  # pragma: no cover
            raise DataError("yfinance 未安装") from e
        _yf_mod = yf
    return _yf_mod


def yf_history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    yf = _yf()
    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise DataError("yfinance 返回空数据")
    df = df.reset_index()
    cm = {c.lower(): c for c in df.columns}
    date_col = cm.get("date") or cm.get("datetime") or df.columns[0]
    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df[cm["open"]], errors="coerce"),
        "high": pd.to_numeric(df[cm["high"]], errors="coerce"),
        "low": pd.to_numeric(df[cm["low"]], errors="coerce"),
        "close": pd.to_numeric(df[cm["close"]], errors="coerce"),
        "volume": pd.to_numeric(df[cm["volume"]], errors="coerce").fillna(0),
    }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if out.empty:
        raise DataError("yfinance 数据为空")
    return out


# ----------------------------------------------------------------------------
# 缓存
# ----------------------------------------------------------------------------
def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{_safe_symbol(key)}.csv")


def _meta_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{_safe_symbol(key)}.meta.json")


def _cache_valid(key: str, ttl: int = CACHE_TTL_SECONDS) -> bool:
    p = _cache_path(key)
    if not os.path.exists(p):
        return False
    try:
        with open(_meta_path(key), "r", encoding="utf-8") as f:
            return (time.time() - json.load(f).get("ts", 0)) < ttl
    except Exception:
        return os.path.getmtime(p) > time.time() - ttl


def _write_cache(key: str, df: pd.DataFrame, symbol: str) -> None:
    try:
        df.to_csv(_cache_path(key), index=False, encoding="utf-8")
        with open(_meta_path(key), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "symbol": symbol}, f)
    except Exception:
        pass


_PERIOD_BARS = {"1mo": 25, "3mo": 65, "6mo": 130, "1y": 250,
                "2y": 500, "3y": 750, "5y": 1250, "10y": 2500}


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def get_history(
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
    force_refresh: bool = False,
    retries: int = 2,
) -> pd.DataFrame:
    """返回列：date, open, high, low, close, volume（升序）。"""
    mkt, symbol = _split(symbol)
    key = f"{mkt}_{symbol}_{interval}_{period}"

    if mkt == "HK":
        if not force_refresh and _cache_valid(key):
            try:
                df = pd.read_csv(_cache_path(key))
                if len(df) > 0:
                    return df
            except Exception:
                pass
        try:
            df = hk_history(symbol, bars=_PERIOD_BARS.get(period, 500))
            try:
                _write_cache(key, df, "HK" + symbol)
            except Exception:
                pass
            return df
        except Exception:
            try:                      # 兜底：过期缓存
                df = pd.read_csv(_cache_path(key))
                if len(df) > 0:
                    return df
            except Exception:
                pass
            raise


    if not force_refresh and _cache_valid(key):
        try:
            df = pd.read_csv(_cache_path(key))
            if len(df) > 0:
                return df
        except Exception:
            pass

    bars = _PERIOD_BARS.get(period, 500)
    errors: List[str] = []

    # 源1：新浪
    try:
        df = sina_history(symbol, bars=bars)
        if len(df) >= 20:
            _write_cache(key, df, symbol)
            return df
        errors.append(f"SINA: 仅 {len(df)} 行")
    except Exception as e:  # noqa: BLE001
        errors.append(f"SINA: {e}")

    # 源2：yfinance
    try:
        df = yf_history(symbol, period=period, interval=interval)
        if len(df) >= 20:
            _write_cache(key, df, symbol)
            return df
    except Exception as e:  # noqa: BLE001
        errors.append(f"YF: {e}")

    # 兜底：过期缓存
    try:
        df = pd.read_csv(_cache_path(key))
        if len(df) > 0:
            return df
    except Exception:
        pass

    raise DataError(f"获取 {symbol} 数据失败: " + " | ".join(errors[:2]))


def get_name(symbol: str) -> str:
    mkt, symbol = _split(symbol)
    if mkt == "HK":
        from app.core import market as mk
        meta = mk.hk_meta()
        if symbol in meta:
            return meta[symbol][0]
        q = hk_quote(symbol)
        if q and q.get("name"):
            return q["name"]
        return symbol
    q = sina_quote(symbol)
    if q and q.get("name"):
        return q["name"]
    r = em_resolve(symbol)
    if r and r[1]:
        return r[1]
    try:
        return get_info(symbol).get("shortName") or symbol
    except Exception:
        return symbol


def get_quote(symbol: str) -> Dict:
    """实时/延迟报价（美股优先新浪，港股走新浪 rt_hk）。"""
    mkt, symbol = _split(symbol)
    if mkt == "HK":
        q = hk_quote(symbol)
        if q and q.get("price"):
            return q
        return {"symbol": symbol, "market": "HK", "price": None,
                "error": f"港股 {symbol} 报价获取失败"}
    q = sina_quote(symbol)
    if q and q.get("price"):
        return q
    try:
        df = get_history(symbol, period="3mo")
        last, prev = df.iloc[-1], df.iloc[-2]
        return {
            "symbol": symbol, "name": get_name(symbol),
            "price": float(last["close"]), "date": str(last["date"]),
            "previous_close": float(prev["close"]),
            "change_pct": (float(last["close"]) / float(prev["close"]) - 1) * 100,
            "high": float(last["high"]), "low": float(last["low"]),
            "volume": float(last["volume"]),
            "year_high": float(df["high"].tail(250).max()),
            "year_low": float(df["low"].tail(250).min()),
        }
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "price": None, "error": str(e)}


def get_info(symbol: str) -> Dict:
    symbol = _safe_symbol(symbol)
    out = {"symbol": symbol, "shortName": symbol}
    q = sina_quote(symbol)
    if q:
        out["shortName"] = q.get("name") or symbol
        out["nameCN"] = q.get("name")
        if q.get("pe"):
            out["trailingPE"] = q["pe"]
        if q.get("market_cap"):
            out["marketCap"] = q["market_cap"]
    else:
        r = em_resolve(symbol)
        if r:
            out["shortName"] = r[1] or symbol
            out["nameCN"] = r[1]

    try:
        yf = _yf()
        info = yf.Ticker(symbol).info or {}
        for k in ("sector", "industry", "marketCap", "trailingPE", "beta"):
            if info.get(k):
                out[k] = info[k]
    except Exception:
        pass
    out.setdefault("shortName", symbol)
    return out


SINA_HK_QUOTE = "https://hq.sinajs.cn/list=rt_hk{sym}"
TX_HK_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
               "?param=hk{sym},day,,,{n},{adj}")


# ----------------------------------------------------------------------------
# 港股
# ----------------------------------------------------------------------------
def _parse_hk_quote(code: str, body: str) -> Optional[Dict]:
    """
    解析新浪港股报价行。

    字段顺序：英文名, 中文名, 开盘, 昨收, 最高, 最低, 现价, 涨跌额, 涨跌幅,
              买价, 卖价, 成交额, 成交量, PE, 周息率, 52周高, 52周低, 日期, 时间, ...
    """
    p = (body or "").split(",")
    if len(p) < 10:
        return None

    def f(idx, default=None):
        try:
            v = float(p[idx])
            return v if np.isfinite(v) else default
        except (ValueError, IndexError):
            return default

    price = f(6) or f(2)
    prev = f(3)
    pct = f(8)
    if pct is None and price and prev:
        pct = (price / prev - 1) * 100
    return {
        "symbol": code,
        "name": p[1] or p[0] or code,
        "en_name": p[0],
        "price": price,
        "previous_close": prev,
        "open": f(2), "high": f(4), "low": f(5),
        "change": f(7), "change_pct": pct,
        "amount": f(11),          # 成交额（港元）
        "volume": f(12),          # 成交量（股）
        "pe": f(13),
        "year_high": f(15), "year_low": f(16),
        "date": (p[17] or "").replace("/", "-") if len(p) > 17 else "",
        # 新浪把日期和时间分成两个字段，界面需要完整时间戳
        "time": (f"{p[17]} {p[18]}" if len(p) > 18 else (p[17] or "")).replace("/", "-"),
        "market": "HK",
    }


def hk_quote(symbol: str) -> Optional[Dict]:
    """新浪港股实时/延迟报价（单只）。"""
    from app.core import market as mk
    _, code = mk.normalize(symbol)
    if not code:
        return None
    try:
        text = _http_get(
            SINA_HK_QUOTE.format(sym=code),
            headers={"Referer": "https://finance.sina.com.cn/"},
            retries=2, timeout=15, encoding="gbk",
        )
    except Exception:
        return None
    m = re.search(r'"([^"]*)"', text)
    return _parse_hk_quote(code, m.group(1)) if m else None


def hk_batch_quotes(symbols, batch_size: int = 80) -> Dict[str, Dict]:
    """
    新浪港股批量报价。

    注意：必须在批量响应里直接解析每行，不能对每只再调 hk_quote，
    否则等于退化成 N 次单只请求（实测 106 只会从 1 秒变成 16 秒）。
    """
    from app.core import market as mk
    codes = [mk.normalize(s)[1] for s in symbols]
    codes = [c for c in codes if c]
    out: Dict[str, Dict] = {}
    for i in range(0, len(codes), batch_size):
        part = codes[i:i + batch_size]
        q = ",".join("rt_hk" + c for c in part)
        try:
            text = _http_get(
                "https://hq.sinajs.cn/list=" + q,
                headers={"Referer": "https://finance.sina.com.cn/"},
                retries=2, timeout=20, encoding="gbk",
            )
        except Exception:
            continue
        for m in re.finditer(r'hq_str_rt_hk(\d+)="([^"]*)"', text):
            r = _parse_hk_quote(m.group(1), m.group(2))
            if r:
                out[r["symbol"]] = r
        time.sleep(0.05)
    return out


def hk_history(symbol: str, bars: int = 500, adj: str = "qfq") -> pd.DataFrame:
    """
    腾讯港股日K（前复权）。

    返回行：[日期, 开, 收, 高, 低, 成交量, {...}, 换手率, 成交额(万元)]
    """
    from app.core import market as mk
    _, code = mk.normalize(symbol)
    if not code:
        raise DataError(f"港股代码无效: {symbol}")
    n = max(20, min(int(bars), 1000))
    text = _http_get(TX_HK_KLINE.format(sym=code, n=n, adj=adj),
                     retries=2, timeout=25)
    try:
        j = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise DataError(f"港股日K解析失败: {e}")

    d = (j.get("data") or {}).get("hk" + code) or {}
    kl = d.get(adj + "day") or d.get("day") or []
    if len(kl) < 5:
        raise DataError(f"港股 {code} 日K为空（{len(kl)} 行）")

    rows = []
    for k in kl:
        try:
            rows.append({
                "date": str(k[0]),
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            })
        except (IndexError, TypeError, ValueError):
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        raise DataError(f"港股 {code} 日K解析后为空")
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


# ----------------------------------------------------------------------------
# 扫描池
# ----------------------------------------------------------------------------
DEFAULT_UNIVERSE: List[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "LLY", "JPM",
    "V", "UNH", "XOM", "MA", "JNJ", "PG", "COST", "HD", "MRK", "ABBV",
    "CVX", "CRM", "AMD", "ADBE", "PEP", "KO", "BAC", "PFE", "TMO", "CSCO",
    "MCD", "WMT", "DIS", "ABT", "DHR", "LIN", "WFC", "INTC", "VZ", "CMCSA",
    "NKE", "NFLX", "QCOM", "TXN", "AMGN", "HON", "IBM", "GE", "CAT", "BA",
    "GS", "MS", "SPGI", "BLK", "SCHW", "AXP", "CVS", "GILD", "ADI", "SBUX",
    "LMT", "DE", "PLD", "AMT", "MDT", "BKNG", "NOW", "ISRG", "TJX", "C",
    "MO", "BMY", "LRCX", "KLAC", "MU", "SNPS", "CDNS", "PANW", "CRWD", "ORCL",
    "ACN", "UBER", "SHOP", "PYPL", "ROKU", "COIN", "PLTR", "ARM", "MRVL", "ASML",
    "TSM", "BABA", "JD", "PDD", "NIO", "LI", "XPEV", "BIDU", "NTES", "TCOM",
    "BEKE", "FUTU", "TME", "SQ", "ZM", "SPOT", "SNOW", "DDOG", "MDB", "NET",
    "ZS", "OKTA", "TWLO", "DOCU", "PINS", "SNAP", "MRNA", "BNTX", "REGN", "VRTX",
    "BIIB", "ILMN", "DXCM", "EW", "BSX", "SOFI", "HOOD", "RBLX", "DASH", "ABNB",
    "RIVN", "LCID", "ELV", "MMM", "GD", "NOC", "RTX", "COP", "SLB",
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "ARKK", "TLT", "GLD", "SLV",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE",
    "SMH", "SOXX", "SOXL", "TQQQ", "SQQQ", "SPXL", "UVXY", "HYG", "LQD", "KWEB",
]


def get_universe() -> List[str]:
    seen, out = set(), []
    for s in DEFAULT_UNIVERSE:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def sp500_symbols() -> List[str]:
    cache_file = os.path.join(CACHE_DIR, "_sp500.json")
    try:
        if os.path.exists(cache_file):
            if time.time() - os.path.getmtime(cache_file) < 7 * 24 * 3600:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        syms = tables[0]["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        syms = [s.strip() for s in syms if s.strip()]
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(syms, f)
        return syms
    except Exception:
        return get_universe()
