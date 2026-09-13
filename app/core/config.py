"""
全局配置：持久化到 data/config.json，支持运行时修改。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.json")

# 可重入锁：save() 内部会调用 load()，普通 Lock 会在此处死锁
_lock = threading.RLock()

DEFAULTS: Dict[str, Any] = {
    # 缠论参数
    "chan": {
        "min_stroke_bars": 4,      # 成笔所需最少合并K线数
        "power_ratio": 1.0,        # 背驰力度比阈值
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    },
    # 回测/实盘共用的出场规则（由参数优化自动迭代产生）
    "exit": {
        "stop_pct": 0.06,
        "take_pct": 0.12,
        "max_hold": 20,
        "use_chan_stop": True,
        "exit_on_reverse": True,
    },
    "exit_source": "default",   # default | optimized
    # 量价关系参数（见 volprice.DEFAULT_PARAMS）
    "volprice": {
        "breakout_vol": 1.8,      # 突破放量倍数
        "dryup_vol": 0.7,         # 缩量回踩上限
        "lowvol_vol": 0.5,        # 地量阈值
        "heavy_vol": 3.0,         # 天量倍数
        "pivot_w": 5,             # 波段枢轴点窗口（背离检测）
        "risk_reward": 2.0,       # 目标盈亏比
        "atr_stop_mult": 1.5,     # ATR 止损倍数
        "max_risk_pct": 0.15,     # 单笔最大风险
        "min_confidence": 55.0,   # 提醒门槛
    },
    # 策略开关
    "strategies": {
        "oversold": True,
        "breakout": True,
        "reversal": True,
        "volprice": True,
    },
    # 每日自动扫描（服务器本地时间，即北京时间）
    "auto_scan": {
        "enabled": True,
        "hour": 5,
        "minute": 30,
        "scan_type": "watchlist",  # watchlist | market
        "market_limit": 120,
    },
    "history_period": "2y",
    "max_workers": 8,
}


def _deep_merge(base: Dict, upd: Dict) -> Dict:
    out = dict(base)
    for k, v in (upd or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    with _lock:
        cfg = json.loads(json.dumps(DEFAULTS))
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = _deep_merge(cfg, json.load(f))
        except Exception:
            pass
        return cfg


def save(cfg: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        merged = _deep_merge(load(), cfg)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        return merged


def get(path: str, default: Any = None) -> Any:
    """点号路径取值，如 'chan.min_stroke_bars'。"""
    cfg = load()
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    return save(patch)
