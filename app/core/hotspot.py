"""
市场热点板块引擎。

回答三个问题：
  1. 市场里「资金交易最集中、最活跃」的 TOP10 行业 / 板块是谁？
  2. 当下资金**流入**最多的 5 个板块 + 10 只股票是谁？
  3. 当下资金**流出**最多的 5 个板块 + 10 只股票是谁？

------------------------------------------------------------------------------
一、口径说明（重要，务必先读）
------------------------------------------------------------------------------
美股**没有免费的交易所级真实资金流数据**（NYSE/NASDAQ 的实时逐笔资金流是付费行情，
且"主力/超大单"分类本身也是加工的）。所以本模块用的是**公开 OHLCV 推算的资金流模型**：

    Chaikin A/D（Accumulation / Distribution，累积派发线）

        MFM = ((C - L) - (H - C)) / (H - L)        ∈ [-1, +1]，收盘在当日区间的位置
        MFV = MFM × Volume                         带方向的成交量
        净流入金额(N日) = Σ MFV_i × Close_i

    直觉：收盘收在当日最高附近 → 买方主导 → 计为流入；收在最低附近 → 卖方主导 → 计为流出。

这是**估算值，不是真实资金流**。它的价值在于横向比较（谁相对更强），
不在于绝对值（不要指望它等于券商软件里的"主力净流入"）。
页面上会明确标注这一点。

------------------------------------------------------------------------------
二、数据源
------------------------------------------------------------------------------
新浪财经：
  - 批量实时报价 `hq.sinajs.cn/list=gb_xxx,...`：一次请求可拿上百只，
    含 open/high/low/price/volume/10日均量，不需要逐只请求。
  - 日线历史（走 data.get_history，带 4 小时磁盘缓存）。
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.core import data


# ----------------------------------------------------------------------------
# 一、板块篮子：11 大行业 + 主题板块
# ----------------------------------------------------------------------------
# (代码, 中文名, 类别 industry/theme, 说明)
SECTOR_ETFS: List[Tuple[str, str, str, str]] = [
    # ---------------- 11 大行业（GICS 一级） ----------------
    ("XLK",  "科技",     "industry", "半导体 + 软件 + 硬件"),
    ("XLF",  "金融",     "industry", "银行 + 保险 + 券商"),
    ("XLV",  "医疗",     "industry", "制药 + 器械 + 服务"),
    ("XLE",  "能源",     "industry", "油气开采 + 油服"),
    ("XLI",  "工业",     "industry", "制造 + 国防 + 运输"),
    ("XLY",  "可选消费", "industry", "零售 + 汽车 + 酒店"),
    ("XLP",  "必选消费", "industry", "食品 + 日化 + 商超"),
    ("XLB",  "材料",     "industry", "化工 + 金属 + 包装"),
    ("XLU",  "公用事业", "industry", "电力 + 水务（防御）"),
    ("XLRE", "房地产",   "industry", "REITs + 地产管理"),
    ("XLC",  "通信服务", "industry", "媒体 + 电信 + 互动娱乐"),
    # ---------------- 主题板块（资金最容易聚集的地方） ----------------
    ("SMH",  "半导体",   "theme", "芯片全产业链"),
    ("SOXX", "半导体(ICE)", "theme", "半导体指数 ETF"),
    ("IBB",  "生物科技", "theme", "大市值生科"),
    ("XBI",  "生物科技(小盘)", "theme", "等权生科，弹性大"),
    ("ARKK", "颠覆创新", "theme", "高贝塔成长股集合"),
    ("KWEB", "中国互联网", "theme", "中概互联网"),
    ("KRE",  "区域银行", "theme", "中小银行，利率敏感"),
    ("KBE",  "银行",     "theme", "全银行板块"),
    ("TAN",  "太阳能",   "theme", "光伏产业链"),
    ("ICLN", "清洁能源", "theme", "风光 + 储能"),
    ("GDX",  "金矿",     "theme", "黄金矿业股"),
    ("XOP",  "油气勘探", "theme", "油气 E&P，高波动"),
    ("OIH",  "油田服务", "theme", "油服设备"),
    ("URA",  "铀矿",     "theme", "核燃料 + 铀矿"),
    ("REMX", "稀土",     "theme", "稀土与战略金属"),
    ("LIT",  "锂电",     "theme", "锂矿 + 电池"),
    ("BOTZ", "机器人AI", "theme", "机器人与自动化"),
    ("HACK", "网络安全", "theme", "网络安全"),
    ("CIBR", "网络安全(小盘)", "theme", "网络安全细分"),
    ("WCLD", "云软件",   "theme", "SaaS 中小盘"),
    ("SKYY", "云计算",   "theme", "云基础设施"),
    ("FDN",  "互联网",   "theme", "互联网龙头"),
    ("JETS", "航空",     "theme", "航空运输"),
    ("ITA",  "国防军工", "theme", "航空航天与国防"),
    ("PAVE", "基建",     "theme", "基建建设"),
    ("XRT",  "零售",     "theme", "等权零售"),
    ("VNQ",  "房地产(全部)", "theme", "全市场 REITs"),
]

INDUSTRY_ORDER = ["科技", "金融", "医疗", "能源", "工业", "可选消费",
                  "必选消费", "材料", "公用事业", "房地产", "通信服务"]


# ----------------------------------------------------------------------------
# 二、股票池（按 GICS 一级行业标注）
# ----------------------------------------------------------------------------
# (代码, 中文名, 行业)  —— 中文名仅用于展示，取不到也不影响计算
STOCK_UNIVERSE: List[Tuple[str, str, str]] = [
    # 科技
    ("AAPL", "苹果", "科技"), ("MSFT", "微软", "科技"), ("NVDA", "英伟达", "科技"),
    ("AVGO", "博通", "科技"), ("AMD", "超微半导体", "科技"), ("ORCL", "甲骨文", "科技"),
    ("CRM", "Salesforce", "科技"), ("ADBE", "Adobe", "科技"), ("CSCO", "思科", "科技"),
    ("IBM", "IBM", "科技"), ("NOW", "ServiceNow", "科技"), ("TXN", "德州仪器", "科技"),
    ("QCOM", "高通", "科技"), ("INTC", "英特尔", "科技"), ("MU", "美光", "科技"),
    ("AMAT", "应用材料", "科技"), ("LRCX", "泛林集团", "科技"), ("KLAC", "科磊", "科技"),
    ("SNPS", "新思科技", "科技"), ("CDNS", "铿腾电子", "科技"), ("ARM", "ARM", "科技"),
    ("MRVL", "迈威尔", "科技"), ("TSM", "台积电", "科技"), ("ASML", "阿斯麦", "科技"),
    ("PANW", "Palo Alto", "科技"), ("CRWD", "CrowdStrike", "科技"), ("ZS", "Zscaler", "科技"),
    ("NET", "Cloudflare", "科技"), ("DDOG", "Datadog", "科技"), ("SNOW", "Snowflake", "科技"),
    ("MDB", "MongoDB", "科技"), ("OKTA", "Okta", "科技"), ("TWLO", "Twilio", "科技"),
    ("DOCU", "DocuSign", "科技"), ("PLTR", "Palantir", "科技"), ("SHOP", "Shopify", "科技"),
    ("ACN", "埃森哲", "科技"), ("DELL", "戴尔", "科技"), ("SMCI", "超微电脑", "科技"),
    ("ANET", "Arista", "科技"), ("VRT", "Vertiv", "科技"),
    # 通信服务
    ("GOOGL", "谷歌", "通信服务"), ("META", "Meta", "通信服务"), ("NFLX", "奈飞", "通信服务"),
    ("DIS", "迪士尼", "通信服务"), ("VZ", "威瑞森", "通信服务"), ("T", "AT&T", "通信服务"),
    ("TMUS", "T-Mobile", "通信服务"), ("CMCSA", "康卡斯特", "通信服务"),
    ("SPOT", "Spotify", "通信服务"), ("RBLX", "Roblox", "通信服务"),
    ("ZM", "Zoom", "通信服务"), ("PINS", "Pinterest", "通信服务"), ("SNAP", "Snap", "通信服务"),
    ("BIDU", "百度", "通信服务"), ("NTES", "网易", "通信服务"), ("TME", "腾讯音乐", "通信服务"),
    # 金融
    ("JPM", "摩根大通", "金融"), ("V", "Visa", "金融"), ("MA", "万事达", "金融"),
    ("BAC", "美国银行", "金融"), ("WFC", "富国银行", "金融"), ("GS", "高盛", "金融"),
    ("MS", "摩根士丹利", "金融"), ("C", "花旗", "金融"), ("SCHW", "嘉信理财", "金融"),
    ("BLK", "贝莱德", "金融"), ("SPGI", "标普全球", "金融"), ("AXP", "美国运通", "金融"),
    ("PYPL", "PayPal", "金融"), ("SOFI", "SoFi", "金融"), ("HOOD", "Robinhood", "金融"),
    ("COIN", "Coinbase", "金融"), ("FUTU", "富途", "金融"), ("SQ", "Block", "金融"),
    ("BX", "黑石", "金融"), ("KKR", "KKR", "金融"),
    # 医疗
    ("LLY", "礼来", "医疗"), ("UNH", "联合健康", "医疗"), ("JNJ", "强生", "医疗"),
    ("MRK", "默沙东", "医疗"), ("ABBV", "艾伯维", "医疗"), ("PFE", "辉瑞", "医疗"),
    ("TMO", "赛默飞", "医疗"), ("ABT", "雅培", "医疗"), ("DHR", "丹纳赫", "医疗"),
    ("AMGN", "安进", "医疗"), ("GILD", "吉利德", "医疗"), ("CVS", "CVS健康", "医疗"),
    ("MDT", "美敦力", "医疗"), ("ISRG", "直觉外科", "医疗"), ("BMY", "百时美施贵宝", "医疗"),
    ("VRTX", "福泰制药", "医疗"), ("REGN", "再生元", "医疗"), ("MRNA", "Moderna", "医疗"),
    ("BNTX", "BioNTech", "医疗"), ("BIIB", "渤健", "医疗"), ("ILMN", "因美纳", "医疗"),
    ("DXCM", "德康医疗", "医疗"), ("EW", "爱德华兹", "医疗"), ("BSX", "波士顿科学", "医疗"),
    ("ELV", "Elevance", "医疗"), ("NVO", "诺和诺德", "医疗"),
    # 可选消费
    ("AMZN", "亚马逊", "可选消费"), ("TSLA", "特斯拉", "可选消费"), ("HD", "家得宝", "可选消费"),
    ("MCD", "麦当劳", "可选消费"), ("NKE", "耐克", "可选消费"), ("SBUX", "星巴克", "可选消费"),
    ("TJX", "TJX", "可选消费"), ("BKNG", "Booking", "可选消费"), ("ABNB", "爱彼迎", "可选消费"),
    ("DASH", "DoorDash", "可选消费"), ("UBER", "优步", "可选消费"), ("ROKU", "Roku", "可选消费"),
    ("RIVN", "Rivian", "可选消费"), ("LCID", "Lucid", "可选消费"),
    ("BABA", "阿里巴巴", "可选消费"), ("JD", "京东", "可选消费"), ("PDD", "拼多多", "可选消费"),
    ("NIO", "蔚来", "可选消费"), ("LI", "理想", "可选消费"), ("XPEV", "小鹏", "可选消费"),
    ("TCOM", "携程", "可选消费"), ("BEKE", "贝壳", "可选消费"), ("GM", "通用汽车", "可选消费"),
    ("F", "福特", "可选消费"),
    # 必选消费
    ("PG", "宝洁", "必选消费"), ("COST", "好市多", "必选消费"), ("PEP", "百事", "必选消费"),
    ("KO", "可口可乐", "必选消费"), ("WMT", "沃尔玛", "必选消费"), ("MO", "奥驰亚", "必选消费"),
    ("PM", "菲利普莫里斯", "必选消费"), ("MDLZ", "亿滋", "必选消费"), ("GIS", "通用磨坊", "必选消费"),
    # 能源
    ("XOM", "埃克森美孚", "能源"), ("CVX", "雪佛龙", "能源"), ("COP", "康菲石油", "能源"),
    ("SLB", "斯伦贝谢", "能源"), ("EOG", "EOG资源", "能源"), ("MPC", "马拉松石油", "能源"),
    ("PSX", "Phillips 66", "能源"), ("OXY", "西方石油", "能源"),
    # 工业
    ("CAT", "卡特彼勒", "工业"), ("BA", "波音", "工业"), ("HON", "霍尼韦尔", "工业"),
    ("GE", "GE航空航天", "工业"), ("LMT", "洛克希德马丁", "工业"), ("DE", "迪尔", "工业"),
    ("RTX", "雷神", "工业"), ("GD", "通用动力", "工业"), ("NOC", "诺格", "工业"),
    ("MMM", "3M", "工业"), ("UPS", "联合包裹", "工业"), ("UNP", "联合太平洋", "工业"),
    # 材料
    ("LIN", "林德", "材料"), ("SHW", "宣伟", "材料"), ("ECL", "艺康", "材料"),
    ("FCX", "自由港铜业", "材料"), ("NEM", "纽蒙特", "材料"), ("DOW", "陶氏", "材料"),
    # 公用事业
    ("NEE", "新纪元能源", "公用事业"), ("DUK", "杜克能源", "公用事业"),
    ("SO", "南方公司", "公用事业"), ("AEP", "美国电力", "公用事业"), ("D", "道明尼", "公用事业"),
    ("CEG", "星座能源", "公用事业"), ("VST", "Vistra", "公用事业"),
    # 房地产
    ("PLD", "安博", "房地产"), ("AMT", "美国电塔", "房地产"),
    ("O", "Realty Income", "房地产"), ("SPG", "西蒙地产", "房地产"),
]

SECTOR_NAME_TO_ETF = {n: c for c, n, k, _ in SECTOR_ETFS if k == "industry"}


def sector_etf_symbols() -> List[str]:
    return [c for c, *_ in SECTOR_ETFS]


def stock_symbols() -> List[str]:
    return [c for c, *_ in STOCK_UNIVERSE]


def _stock_meta() -> Dict[str, Tuple[str, str]]:
    return {c: (n, ind) for c, n, ind in STOCK_UNIVERSE}


# ----------------------------------------------------------------------------
# 三、批量实时报价
# ----------------------------------------------------------------------------
def batch_quotes(symbols: Sequence[str], batch_size: int = 60,
                 market: str = "US") -> Dict[str, Dict]:
    """
    一次请求拿多只标的的实时报价。

    新浪美股字段：name, price, change_pct, time, change, open, high, low,
                 year_high, year_low, volume, avg_volume_10d, market_cap, ...
    """
    if market == "HK":
        return data.hk_batch_quotes(symbols, batch_size=batch_size)

    out: Dict[str, Dict] = {}
    syms = [data._safe_symbol(s) for s in symbols]
    for i in range(0, len(syms), batch_size):
        part = syms[i:i + batch_size]
        q = ",".join("gb_" + s.lower() for s in part)
        try:
            text = data._http_get(
                "https://hq.sinajs.cn/list=" + q,
                headers={"Referer": "https://finance.sina.com.cn/"},
                retries=2, timeout=20, encoding="gbk",
            )
        except Exception:
            continue
        for m in re.finditer(r'hq_str_gb_(\w+)="([^"]*)"', text):
            code = m.group(1).upper()
            p = m.group(2).split(",")
            if len(p) < 13:
                continue

            def f(idx, default=None):
                try:
                    v = float(p[idx])
                    return v if np.isfinite(v) else default
                except (ValueError, IndexError):
                    return default

            out[code] = {
                "symbol": code,
                "name": p[0] or code,
                "price": f(1),
                "change_pct": f(2),
                "time": p[3] if len(p) > 3 else "",
                "change": f(4),
                "open": f(5), "high": f(6), "low": f(7),
                "year_high": f(8), "year_low": f(9),
                "volume": f(10),
                "avg_volume_10d": f(11),
                "market_cap": f(12),
            }
        time.sleep(0.05)
    return out


# ----------------------------------------------------------------------------
# 四、资金流模型（Chaikin A/D）
# ----------------------------------------------------------------------------
def money_flow_series(df: pd.DataFrame) -> pd.Series:
    """
    每日带方向的资金额（美元）：MFV = MFM × Volume × Close
        MFM = ((C-L) - (H-C)) / (H-L)
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    rng = (h - lo).replace(0, np.nan)
    mfm = ((c - lo) - (h - c)) / rng
    mfm = mfm.clip(-1, 1).fillna(0.0)
    return mfm * v * c


def _flow_from_df(df: pd.DataFrame, window: int) -> Dict[str, Optional[float]]:
    """从日线算出 N 日净流入金额、成交额、涨跌幅。"""
    zero = {"net": None, "turnover": None, "chg": None, "days": 0}
    if df is None or len(df) < 2:
        return zero
    mf = money_flow_series(df)
    if len(mf) == 0:
        return zero
    n = min(int(window), len(mf))
    tail = mf.tail(n)
    net = float(tail.sum())
    turn = float((df["volume"].astype(float).tail(n) * df["close"].astype(float).tail(n)).sum())
    chg = None
    if len(df) >= n + 1:
        try:
            base = float(df["close"].iloc[-(n + 1)])
            if base > 0:
                chg = (float(df["close"].iloc[-1]) / base - 1) * 100
        except Exception:
            pass
    return {"net": net, "turnover": turn, "chg": chg, "days": n}


def _flow_from_quote(q: Dict) -> Optional[float]:
    """只有实时报价（当日 OHLCV）时的单日净流入。"""
    try:
        h, lo, c, v = float(q["high"]), float(q["low"]), float(q["price"]), float(q["volume"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (h > 0 and v > 0):
        return None
    rng = h - lo
    mfm = 0.0 if rng <= 0 else ((c - lo) - (h - c)) / rng
    mfm = max(-1.0, min(1.0, mfm))
    return mfm * v * c


def _fmt_money(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 2)


# ----------------------------------------------------------------------------
# 五、并发取历史
# ----------------------------------------------------------------------------
def _parallel_history(symbols: Sequence[str], period: str = "3mo",
                      workers: int = 24) -> Dict[str, Optional[pd.DataFrame]]:
    out: Dict[str, Optional[pd.DataFrame]] = {}
    if not symbols:
        return out
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        fut = {ex.submit(data.get_history, s, period): s for s in symbols}
        for f in cf.as_completed(fut):
            s = fut[f]
            try:
                df = f.result()
                out[s] = df if df is not None and len(df) > 5 else None
            except Exception:
                out[s] = None
    return out


# ----------------------------------------------------------------------------
# 结果缓存：全市场扫描代价高，短时间内重复请求直接复用
# ----------------------------------------------------------------------------
_CACHE: Dict[str, Tuple[float, Dict]] = {}
CACHE_TTL = 300  # 5 分钟


def _cache_key(window: int, mode: str) -> str:
    return f"{window}|{mode}"


def get_cached(window: int, mode: str) -> Optional[Dict]:
    hit = _CACHE.get(_cache_key(window, mode))
    if not hit:
        return None
    ts, payload = hit
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(_cache_key(window, mode), None)
        return None
    return payload


# ----------------------------------------------------------------------------
# 六、主入口
# ----------------------------------------------------------------------------
def build_hotspots(window: int = 5, top_sector: int = 10, top_flow_sector: int = 5,
                   top_stock: int = 10, mode: str = "full", market: str = "US") -> Dict:
    """
    生成市场热点板块数据。

    window  资金流统计窗口（交易日）；mode="fast" 时固定为当日
    mode    "full" —— 拉日线算 N 日累计资金流（准，首次约 20-30 秒，之后走缓存）
            "fast" —— 只用实时报价算当日资金流（1-2 秒）
    market  "US" 美股板块 ETF + 美股池；"HK" 港股 ETF + 港股通池
    """
    from app.core import market as mk
    mode = "fast" if mode == "fast" else "full"
    market = "HK" if str(market).upper() == "HK" else "US"
    cached = get_cached(window, f"{mode}|{market}")
    if cached:
        out = dict(cached)
        out["cached"] = True
        return out

    t0 = time.time()
    if market == "HK":
        sec_syms = mk.hk_etf_symbols()
        stk_syms = mk.hk_stock_symbols()
        sector_meta = {c: (n, k, d) for c, n, k, d in mk.HK_ETFS}
        meta = {c: (n, ind) for c, n, ind in mk.HK_UNIVERSE}
    else:
        sec_syms = sector_etf_symbols()
        stk_syms = stock_symbols()
        sector_meta = {c: (n, k, d) for c, n, k, d in SECTOR_ETFS}
        meta = _stock_meta()

    quotes = batch_quotes(sec_syms + stk_syms, market=market)

    hist: Dict[str, Optional[pd.DataFrame]] = {}
    if mode == "full":
        hist = _parallel_history(sec_syms + stk_syms, period="3mo")

    def build(sym: str, name_hint: str = "", extra: Optional[Dict] = None) -> Optional[Dict]:
        q = quotes.get(sym)
        df = hist.get(sym) if mode == "full" else None

        if df is not None and len(df) > 5:
            fl = _flow_from_df(df, window)
            net, turn, chg = fl["net"], fl["turnover"], fl["chg"]
            src = "history"
        elif q:
            net = _flow_from_quote(q)
            turn = None
            try:
                turn = float(q["volume"]) * float(q["price"])
            except (KeyError, TypeError, ValueError):
                pass
            chg = q.get("change_pct")
            src = "quote"
        else:
            return None

        if net is None:
            return None

        price = (q or {}).get("price")
        if price is None and df is not None and len(df):
            price = float(df["close"].iloc[-1])
        if price is None:
            return None

        vol = (q or {}).get("volume")
        avg10 = (q or {}).get("avg_volume_10d")
        vol_ratio = None
        try:
            if vol and avg10 and float(avg10) > 0:
                vol_ratio = float(vol) / float(avg10)
        except (TypeError, ValueError):
            pass
        if vol_ratio is None and df is not None and len(df) >= 21:
            try:
                v20 = float(df["volume"].tail(20).mean())
                v5 = float(df["volume"].tail(5).mean())
                if v20 > 0:
                    vol_ratio = v5 / v20
            except Exception:
                pass

        row = {
            "symbol": sym,
            "name": name_hint or ((q or {}).get("name") or sym),
            "price": round(float(price), 2),
            "change_pct": None if chg is None else round(float(chg), 2),
            "net_flow": _fmt_money(net),
            "turnover": _fmt_money(turn),
            "flow_ratio": (round(net / turn * 100, 2)
                           if (turn and turn > 0) else None),
            "vol_ratio": None if vol_ratio is None else round(float(vol_ratio), 2),
            "source": src,
        }
        if extra:
            row.update(extra)
        return row

    # ---------------- 板块 ----------------
    sectors: List[Dict] = []
    for sym in sec_syms:
        n, kind, desc = sector_meta[sym]
        r = build(sym, n, {"kind": kind, "desc": desc})
        if r:
            sectors.append(r)

    # ---------------- 个股 ----------------
    stocks: List[Dict] = []
    for sym in stk_syms:
        nm, ind = meta.get(sym, (sym, ""))
        r = build(sym, nm, {"industry": ind, "kind": "stock"})
        if r:
            stocks.append(r)

    # ---------------- 活跃度：成交额 + 量比 ----------------
    def attach_activity(rows: List[Dict]) -> List[Dict]:
        if not rows:
            return rows
        tos = [r["turnover"] for r in rows if r.get("turnover")]
        vrs = [r["vol_ratio"] for r in rows if r.get("vol_ratio")]

        def pct_rank(vals, v):
            if v is None or not vals:
                return 0.5
            vals = sorted(vals)
            lo, hi = vals[0], vals[-1]
            if hi <= lo:
                return 0.5
            return (v - lo) / (hi - lo)

        for r in rows:
            r["act_score"] = round(
                100 * (0.62 * pct_rank(tos, r.get("turnover"))
                       + 0.38 * pct_rank(vrs, r.get("vol_ratio"))), 1)
        total = sum(r.get("turnover") or 0 for r in rows)
        for r in rows:
            r["turnover_share"] = (round((r.get("turnover") or 0) / total * 100, 2)
                                   if total > 0 else None)
        return sorted(rows, key=lambda x: -x.get("act_score", 0))

    sectors = attach_activity(sectors)
    stocks = attach_activity(stocks)

    def short(rows, n):
        return rows[:n]

    inflow_sector = short(sorted([r for r in sectors if r["net_flow"] is not None],
                                 key=lambda x: -x["net_flow"]), top_flow_sector)
    outflow_sector = short(sorted([r for r in sectors if r["net_flow"] is not None],
                                  key=lambda x: x["net_flow"]), top_flow_sector)
    inflow_stock = short(sorted([r for r in stocks if r["net_flow"] is not None],
                                key=lambda x: -x["net_flow"]), top_stock)
    outflow_stock = short(sorted([r for r in stocks if r["net_flow"] is not None],
                                 key=lambda x: x["net_flow"]), top_stock)

    # ---------------- 按行业聚合个股资金流（交叉验证板块方向） ----------------
    agg: Dict[str, Dict] = {}
    for r in stocks:
        ind = r.get("industry") or "其他"
        a = agg.setdefault(ind, {"industry": ind, "net_flow": 0.0, "turnover": 0.0,
                                 "count": 0, "up": 0, "down": 0})
        a["net_flow"] += r.get("net_flow") or 0
        a["turnover"] += r.get("turnover") or 0
        a["count"] += 1
        cp = r.get("change_pct")
        if cp is not None:
            if cp > 0:
                a["up"] += 1
            elif cp < 0:
                a["down"] += 1
    ind_rows = []
    for a in agg.values():
        a["net_flow"] = round(a["net_flow"], 2)
        a["turnover"] = round(a["turnover"], 2)
        a["breadth"] = round(a["up"] / a["count"] * 100, 1) if a["count"] else None
        a["etf"] = SECTOR_NAME_TO_ETF.get(a["industry"]) if market == "US" else None
        ind_rows.append(a)
    ind_rows.sort(key=lambda x: -x["net_flow"])

    q0 = next(iter(quotes.values()), None)
    as_of = str(q0.get("time") or "") if q0 else ""

    # ---------------- 市场宽度与方向（诚实标注榜单含义） ----------------
    all_rows = sectors + stocks
    up_cnt = sum(1 for r in all_rows if (r.get("change_pct") or 0) > 0)
    dn_cnt = sum(1 for r in all_rows if (r.get("change_pct") or 0) < 0)
    pos_flow = sum(1 for r in sectors if (r.get("net_flow") or 0) > 0)
    neg_flow = sum(1 for r in sectors if (r.get("net_flow") or 0) < 0)

    if pos_flow > neg_flow * 1.5:
        tone, tone_label = "risk_on", "资金整体进场"
    elif neg_flow > pos_flow * 1.5:
        tone, tone_label = "risk_off", "资金整体撤离"
    else:
        tone, tone_label = "mixed", "多空分歧"

    # 涨跌家数 vs 资金流家数是两回事（A/D 看的是收盘在当日区间的位置，不是涨跌），
    # 分开呈现，避免用户误读。
    price_breadth = {
        "up": up_cnt, "down": dn_cnt,
        "up_ratio": round(up_cnt / len(all_rows) * 100, 1) if all_rows else None,
        "label": "涨跌家数",
    }
    flow_breadth = {
        "inflow": pos_flow, "outflow": neg_flow,
        "inflow_ratio": round(pos_flow / len(sectors) * 100, 1) if sectors else None,
        "tone": tone, "label": tone_label,
    }

    note = None
    if inflow_sector and (inflow_sector[0].get("net_flow") or 0) <= 0:
        note = ("当前所有板块均为净流出，「流入 TOP5」实际是**流出最少**的板块；"
                "这时更该关注谁扛得住，而不是急着抄底。")
    elif outflow_sector and (outflow_sector[0].get("net_flow") or 0) >= 0:
        note = ("当前所有板块均为净流入，「流出 TOP5」实际是**流入最少**的板块。")
    else:
        neg_in_inflow = sum(1 for r in inflow_sector if (r.get("net_flow") or 0) <= 0)
        pos_in_outflow = sum(1 for r in outflow_sector if (r.get("net_flow") or 0) >= 0)
        if neg_in_inflow:
            note = (f"流入榜中有 {neg_in_inflow} 个板块净额仍为负，"
                    f"说明市场整体偏流出，它们是相对最抗跌的一批。")
        elif pos_in_outflow:
            note = (f"流出榜中有 {pos_in_outflow} 个板块净额仍为正，"
                    f"说明市场整体偏流入，它们只是流入较少（板块样本少时容易这样）。")

    # 强度榜（按净额占成交额比重排序）—— 与绝对金额榜互为交叉验证
    def by_strength(rows, n, reverse=True):
        return short(sorted([r for r in rows if r.get("flow_ratio") is not None],
                            key=lambda x: x["flow_ratio"], reverse=reverse), n)

    sector_strength = {
        "inflow": by_strength(sectors, top_flow_sector, True),
        "outflow": by_strength(sectors, top_flow_sector, False),
    }

    result = {
        "ok": True,
        "cached": False,
        "as_of": as_of,
        "window": 1 if mode == "fast" else window,
        "mode": mode,
        "market": market,
        "currency": ("HK$" if market == "HK" else "$"),
        "elapsed": round(time.time() - t0, 2),
        "counts": {"sectors": len(sectors), "stocks": len(stocks)},
        "price_breadth": price_breadth,
        "flow_breadth": flow_breadth,
        "note": note,
        "sectors": {
            "active": short(sectors, top_sector),
            "inflow": inflow_sector,
            "outflow": outflow_sector,
            "strength_inflow": sector_strength["inflow"],
            "strength_outflow": sector_strength["outflow"],
        },
        "stocks": {
            "inflow": inflow_stock,
            "outflow": outflow_stock,
        },
        "industry_agg": ind_rows,
        "method": {
            "model": "Chaikin A/D（累积派发线）",
            "formula": "MFM=((C-L)-(H-C))/(H-L)；净流入=Σ(MFM×Volume×Close)",
            "window": "当日" if mode == "fast" else f"近 {window} 个交易日",
            "source": "新浪财经（实时报价" + (" + 日线）" if mode == "full" else "）"),
            "activity": "活跃度 = 0.62 × 成交额分位 + 0.38 × 量比分位",
            "flow_ratio": "强度 = 净流入 / 成交额，剔除规模差异后横向可比的相对指标",
            "caveat": ("基于公开 OHLCV 的估算值，非交易所真实资金流。"
                       "A/D 看的是「收盘价在当日区间的位置」而非涨跌——"
                       "高开低走即使收涨也会计为流出，这是模型特性不是 bug。"
                       "建议同时看「强度」榜交叉验证"),
        },
    }
    _CACHE[_cache_key(window, f"{mode}|{market}")] = (time.time(), result)
    return result
