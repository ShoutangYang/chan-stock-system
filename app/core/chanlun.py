"""
实用简化版缠论引擎。

实现链路：
  原始K线 -> 包含处理(合并K线) -> 分型 -> 笔 -> 线段 -> 中枢 -> 背驰 -> 一/二/三类买卖点

设计取舍：
  - 采用「笔」作为中枢的次级别构件（连续3笔重叠构成中枢），这是工程上最稳定的简化方案。
  - 背驰用 MACD 柱面积（同色累加）+ 幅度双重确认，避免单指标误判。
  - 不做多级别递归，日线单级别为主；级别可通过传入不同周期的 df 实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .indicators import macd


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class MergedK:
    """包含处理后的合并K线。"""

    idx: int          # 在合并序列中的位置
    high: float
    low: float
    start: int        # 对应原始K线起始索引
    end: int          # 对应原始K线结束索引（含）
    direction: int = 1  # 相对前一根合并K线的方向：1 向上 / -1 向下


@dataclass
class Fractal:
    """分型。"""

    kind: str        # 'top' | 'bottom'
    mk_idx: int      # 合并K线索引
    raw_idx: int     # 原始K线索引（分型中间那根）
    price: float     # 顶分型取 high，底分型取 low

    @property
    def is_top(self) -> bool:
        return self.kind == "top"


@dataclass
class Stroke:
    """笔：从一个分型到另一个反向分型。"""

    direction: int   # 1 向上（底->顶）, -1 向下（顶->底）
    start_fx: Fractal
    end_fx: Fractal

    @property
    def high(self) -> float:
        return max(self.start_fx.price, self.end_fx.price)

    @property
    def low(self) -> float:
        return min(self.start_fx.price, self.end_fx.price)

    @property
    def start_raw(self) -> int:
        return self.start_fx.raw_idx

    @property
    def end_raw(self) -> int:
        return self.end_fx.raw_idx

    @property
    def amplitude(self) -> float:
        """幅度比例。"""
        base = self.low if self.low != 0 else 1.0
        return (self.high - self.low) / abs(base)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "high": self.high,
            "low": self.low,
            "start_raw": self.start_raw,
            "end_raw": self.end_raw,
            "start_price": self.start_fx.price,
            "end_price": self.end_fx.price,
        }


@dataclass
class Segment:
    """线段：由至少3笔构成的方向性走势。"""

    direction: int
    start_raw: int
    end_raw: int
    high: float
    low: float
    stroke_count: int


@dataclass
class Center:
    """中枢（震荡区间）。"""

    zg: float                 # 中枢上沿 ZG
    zd: float                 # 中枢下沿 ZD
    direction: int            # 进入中枢的第一笔方向
    start_stroke: int         # 起始笔索引
    end_stroke: int           # 结束笔索引（含）
    start_raw: int
    end_raw: int

    @property
    def height(self) -> float:
        return self.zg - self.zd

    def contains(self, price: float) -> bool:
        return self.zd <= price <= self.zg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zg": self.zg,
            "zd": self.zd,
            "direction": self.direction,
            "start_raw": self.start_raw,
            "end_raw": self.end_raw,
            "start_stroke": self.start_stroke,
            "end_stroke": self.end_stroke,
        }


@dataclass
class Divergence:
    """背驰记录。"""

    kind: str        # 'bull' 底背驰 | 'bear' 顶背驰
    prev_stroke: Stroke
    curr_stroke: Stroke
    prev_power: float
    curr_power: float
    ratio: float     # curr_power / prev_power，<1 表示力度衰竭

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "prev_power": self.prev_power,
            "curr_power": self.curr_power,
            "ratio": self.ratio,
        }


@dataclass
class ChanSignal:
    """缠论买卖点信号。"""

    code: str          # B1 / B2 / B3 / S1 / S2 / S3
    side: str          # 'buy' | 'sell'
    name: str
    price: float
    raw_idx: int
    date: str
    confidence: float
    reason: str
    stop_loss: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "side": self.side,
            "name": self.name,
            "price": self.price,
            "raw_idx": self.raw_idx,
            "date": self.date,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "stop_loss": None if self.stop_loss is None else round(self.stop_loss, 4),
        }


# ----------------------------------------------------------------------------
# 引擎
# ----------------------------------------------------------------------------
class ChanEngine:
    """
    参数:
      min_stroke_bars: 分型之间至少间隔的合并K线数量（默认4，即经典「5根K线成笔」）。
      power_ratio:     背驰判定阈值，当前段力度 / 前段力度 < 该值视为衰竭。
      macd_fast/slow/signal: MACD 参数，用于力度计算。
    """

    def __init__(
        self,
        df: pd.DataFrame,
        min_stroke_bars: int = 4,
        power_ratio: float = 1.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ) -> None:
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            raise ValueError("df 必须包含 open/high/low/close 列")

        self.df = df.reset_index(drop=True).copy()
        self.min_stroke_bars = max(2, int(min_stroke_bars))
        self.power_ratio = float(power_ratio)

        if "date" in self.df.columns:
            self.dates = [str(x)[:10] for x in self.df["date"].tolist()]
        elif isinstance(self.df.index, pd.DatetimeIndex):
            self.dates = [d.strftime("%Y-%m-%d") for d in self.df.index]
        else:
            self.dates = [str(i) for i in range(len(self.df))]

        self.close = self.df["close"].astype(float)
        self.high = self.df["high"].astype(float)
        self.low = self.df["low"].astype(float)

        dif, dea, hist = macd(self.close, macd_fast, macd_slow, macd_signal)
        self.dif = dif
        self.dea = dea
        self.hist = hist

        self.merged: List[MergedK] = []
        self.fractals: List[Fractal] = []
        self.strokes: List[Stroke] = []
        self.segments: List[Segment] = []
        self.centers: List[Center] = []
        self.divergences: List[Divergence] = []
        self.signals: List[ChanSignal] = []

    # -------------------------------------------------- 1. 包含处理
    def merge_klines(self) -> List[MergedK]:
        """处理K线包含关系，输出标准化K线序列。"""
        n = len(self.df)
        if n == 0:
            return []

        highs = self.high.to_numpy()
        lows = self.low.to_numpy()

        merged: List[MergedK] = []
        cur = MergedK(idx=0, high=float(highs[0]), low=float(lows[0]), start=0, end=0)
        direction = 1

        for i in range(1, n):
            h, l = float(highs[i]), float(lows[i])
            ch, cl = cur.high, cur.low

            is_contain = (h <= ch and l >= cl) or (h >= ch and l <= cl)

            if is_contain:
                if direction == 1:
                    # 向上：取高高、低高
                    cur.high = max(ch, h)
                    cur.low = max(cl, l)
                else:
                    # 向下：取低低、高低
                    cur.high = min(ch, h)
                    cur.low = min(cl, l)
                cur.end = i
            else:
                merged.append(cur)
                direction = 1 if h > ch else -1
                cur = MergedK(
                    idx=len(merged), high=h, low=l, start=i, end=i, direction=direction
                )

        merged.append(cur)

        # 方向回填
        for i, mk in enumerate(merged):
            if i == 0:
                mk.direction = 1
            else:
                mk.direction = 1 if mk.high > merged[i - 1].high else -1

        self.merged = merged
        return merged

    # -------------------------------------------------- 2. 分型
    def find_fractals(self) -> List[Fractal]:
        """在合并K线序列上识别顶/底分型。"""
        if not self.merged:
            self.merge_klines()
        if not self.merged:
            return []

        m = self.merged
        out: List[Fractal] = []

        for i in range(1, len(m) - 1):
            prev, cur, nxt = m[i - 1], m[i], m[i + 1]
            # 顶分型：中间K线高低点均为三者最高
            if cur.high > prev.high and cur.high > nxt.high and cur.low > prev.low and cur.low > nxt.low:
                out.append(Fractal("top", i, cur.end, cur.high))
            # 底分型：中间K线高低点均为三者最低
            elif cur.high < prev.high and cur.high < nxt.high and cur.low < prev.low and cur.low < nxt.low:
                out.append(Fractal("bottom", i, cur.end, cur.low))

        self.fractals = out
        return out

    # -------------------------------------------------- 3. 笔
    def build_strokes(self) -> List[Stroke]:
        """连接交替的顶底分型形成笔。"""
        if not self.fractals:
            self.find_fractals()
        fx_list = self.fractals
        if len(fx_list) < 2:
            return []

        strokes: List[Stroke] = []
        last: Optional[Fractal] = None

        for fx in fx_list:
            if last is None:
                last = fx
                continue

            if fx.kind == last.kind:
                # 同类分型：保留更极端的那个
                if (fx.is_top and fx.price > last.price) or (
                    (not fx.is_top) and fx.price < last.price
                ):
                    last = fx
                continue

            gap = fx.mk_idx - last.mk_idx
            if gap >= self.min_stroke_bars:
                direction = 1 if fx.is_top else -1
                # 向上笔要求终点高于起点
                if direction == 1 and fx.price <= last.price:
                    continue
                if direction == -1 and fx.price >= last.price:
                    continue
                strokes.append(Stroke(direction=direction, start_fx=last, end_fx=fx))
                last = fx
            else:
                # 间距不足：若新分型反向突破（更极端），说明原分型不成立，替换
                if (last.is_top and not fx.is_top and fx.price < last.price) or (
                    (not last.is_top) and fx.is_top and fx.price > last.price
                ):
                    last = fx

        self.strokes = strokes
        return strokes

    # -------------------------------------------------- 4. 线段
    def build_segments(self) -> List[Segment]:
        """
        线段：至少3笔构成，方向由首笔决定。
        简化规则——当新笔的高点(低点)突破当前线段端点，线段延续；
        否则当出现反向三笔结构时线段结束。
        """
        strokes = self.strokes or self.build_strokes()
        if len(strokes) < 3:
            return []

        segments: List[Segment] = []
        i = 0
        while i + 2 < len(strokes):
            s1 = strokes[i]
            direction = s1.direction
            j = i + 1
            extreme_high = s1.high
            extreme_low = s1.low

            while j < len(strokes):
                s = strokes[j]
                if s.direction == direction:
                    # 同向笔：创新高/新低则线段延伸
                    if direction == 1:
                        extreme_high = max(extreme_high, s.high)
                    else:
                        extreme_low = min(extreme_low, s.low)
                    j += 1
                else:
                    # 反向笔：破坏判定
                    if direction == 1 and s.low < extreme_low:
                        break
                    if direction == -1 and s.high > extreme_high:
                        break
                    extreme_high = max(extreme_high, s.high)
                    extreme_low = min(extreme_low, s.low)
                    j += 1

            if j - i >= 3:
                seg_strokes = strokes[i:j]
                segments.append(
                    Segment(
                        direction=direction,
                        start_raw=seg_strokes[0].start_raw,
                        end_raw=seg_strokes[-1].end_raw,
                        high=max(s.high for s in seg_strokes),
                        low=min(s.low for s in seg_strokes),
                        stroke_count=len(seg_strokes),
                    )
                )
                i = j
            else:
                i += 1

        self.segments = segments
        return segments

    # -------------------------------------------------- 5. 中枢
    def build_centers(self, strokes: Optional[List["Stroke"]] = None) -> List[Center]:
        """
        连续3笔（及以上）的重叠区间构成中枢。
        传入 strokes 时按该笔序列计算且不覆盖 self.centers（回测截断视图用）。
        """
        owns = strokes is None
        if owns:
            strokes = self.strokes or self.build_strokes()
        if len(strokes) < 3:
            return []

        centers: List[Center] = []
        i = 0
        while i + 2 < len(strokes):
            s1, s2, s3 = strokes[i], strokes[i + 1], strokes[i + 2]
            zg = min(s1.high, s2.high, s3.high)
            zd = max(s1.low, s2.low, s3.low)

            if zg <= zd:
                i += 1
                continue

            direction = s1.direction
            j = i + 3
            # 中枢延伸：后续笔与中枢有重叠则并入
            while j < len(strokes):
                s = strokes[j]
                nzg = min(zg, s.high)
                nzd = max(zd, s.low)
                if nzg > nzd:
                    zg, zd = nzg, nzd
                    j += 1
                else:
                    break

            centers.append(
                Center(
                    zg=float(zg),
                    zd=float(zd),
                    direction=direction,
                    start_stroke=i,
                    end_stroke=j - 1,
                    start_raw=strokes[i].start_raw,
                    end_raw=strokes[j - 1].end_raw,
                )
            )
            i = j

        if owns:
            self.centers = centers
        return centers

    # -------------------------------------------------- 6. 力度与背驰
    def stroke_power(self, s: Stroke) -> float:
        """笔的力度 = MACD 柱同色面积。"""
        a, b = max(0, s.start_raw), min(len(self.hist) - 1, s.end_raw)
        if b <= a:
            return 0.0
        seg = self.hist.iloc[a : b + 1]
        if len(seg) == 0:
            return 0.0
        if s.direction == 1:
            return float(seg[seg > 0].sum())
        return float(abs(seg[seg < 0].sum()))

    def detect_divergence(self) -> List[Divergence]:
        """
        背驰：相邻两个同向笔，后段价格更极端但 MACD 柱面积更小。
        """
        strokes = self.strokes or self.build_strokes()
        out: List[Divergence] = []

        for i in range(len(strokes) - 2):
            a, b = strokes[i], strokes[i + 2]
            if a.direction != b.direction:
                continue

            pa, pb = self.stroke_power(a), self.stroke_power(b)
            if pa <= 0 or pb <= 0:
                continue

            if b.direction == 1 and b.high > a.high and pb < pa * self.power_ratio:
                out.append(Divergence("bear", a, b, pa, pb, pb / pa))
            elif b.direction == -1 and b.low < a.low and pb < pa * self.power_ratio:
                out.append(Divergence("bull", a, b, pa, pb, pb / pa))

        self.divergences = out
        return out

    # -------------------------------------------------- 7. 买卖点
    def _date_of(self, idx: int) -> str:
        if 0 <= idx < len(self.dates):
            return self.dates[idx]
        return ""

    def _mk_signal(
        self,
        code: str,
        side: str,
        name: str,
        price: float,
        raw_idx: int,
        confidence: float,
        reason: str,
        stop: Optional[float] = None,
    ) -> ChanSignal:
        return ChanSignal(
            code=code,
            side=side,
            name=name,
            price=float(price),
            raw_idx=int(raw_idx),
            date=self._date_of(raw_idx),
            confidence=min(1.0, max(0.0, confidence)),
            reason=reason,
            stop_loss=stop,
        )

    def find_signals(
        self,
        lookback_strokes: int = 6,
        staleness_bars: int = 45,
        visible_until: Optional[int] = None,
        confirm_delay: int = 0,
    ) -> List[ChanSignal]:
        """
        识别一/二/三类买卖点。

        B1: 下跌中枢后向下离开段底背驰 -> 一买
        B2: 一买后首次回调不破新低 -> 二买
        B3: 向上离开中枢后回调不回中枢（低点 > ZG） -> 三买
        S1: 上涨中枢后向上离开段顶背驰 -> 一卖
        S2: 一卖后首次反抽不创新高 -> 二卖
        S3: 向下离开中枢后反抽不回中枢（高点 < ZD） -> 三卖

        staleness_bars: 信号新鲜度窗口。只有在该窗口内形成的买卖点才有效——
        缠论买卖点具有时效性，数月前的点位已无交易意义。

        visible_until / confirm_delay: 回测专用时间可见性控制。
        只采用 end_raw + confirm_delay <= visible_until 的笔，
        模拟"当时只能看到已确认结构"，严格避免未来函数。
        回测与实盘共用同一套判定逻辑，保证结果可比。
        """
        strokes = self.strokes or self.build_strokes()

        if visible_until is not None:
            strokes = [s for s in strokes if s.end_raw + confirm_delay <= visible_until]
            if len(strokes) < 3:
                return []
            centers = self.build_centers(strokes)
        else:
            centers = self.centers or self.build_centers()

        # 背驰只需计算一次（截断笔与完整笔共享同一批 Stroke 对象，id 映射仍然有效）
        if not self.divergences:
            self.detect_divergence()

        if len(strokes) < 3:
            return []

        signals: List[ChanSignal] = []
        div_map: Dict[int, Divergence] = {}
        for d in self.divergences:
            div_map[id(d.curr_stroke)] = d

        last_c = centers[-1] if centers else None
        recent_from = max(0, len(strokes) - lookback_strokes)
        recent = strokes[recent_from:]
        last_price = (
            float(self.close.iloc[visible_until])
            if visible_until is not None
            else float(self.close.iloc[-1])
        )

        # ---- 基于最后一个中枢的三类买卖点 ----
        if last_c is not None:
            end_si = last_c.end_stroke
            after_all = strokes[end_si + 1 :]
            # 只取中枢之后最近的 3 笔：古老的离开段没有交易意义
            leaving = after_all[-3:] if len(after_all) > 3 else after_all

            for s in leaving:
                div = div_map.get(id(s))

                # 一买：下跌中枢 + 向下离开段底背驰
                if (
                    last_c.direction == -1
                    and s.direction == -1
                    and div is not None
                    and div.kind == "bull"
                ):
                    conf = 0.55 + 0.25 * (1 - div.ratio) + 0.1
                    signals.append(
                        self._mk_signal(
                            "B1", "buy", "一类买点（底背驰）",
                            s.low, s.end_raw, conf,
                            f"下跌中枢[{last_c.zd:.2f},{last_c.zg:.2f}]后向下离开段出现底背驰，"
                            f"MACD柱面积由{div.prev_power:.3f}衰减至{div.curr_power:.3f}（比值{div.ratio:.2f}）",
                            stop=s.low * 0.97,
                        )
                    )

                # 一卖：上涨中枢 + 向上离开段顶背驰
                if (
                    last_c.direction == 1
                    and s.direction == 1
                    and div is not None
                    and div.kind == "bear"
                ):
                    conf = 0.55 + 0.25 * (1 - div.ratio) + 0.1
                    signals.append(
                        self._mk_signal(
                            "S1", "sell", "一类卖点（顶背驰）",
                            s.high, s.end_raw, conf,
                            f"上涨中枢[{last_c.zd:.2f},{last_c.zg:.2f}]后向上离开段出现顶背驰，"
                            f"MACD柱面积由{div.prev_power:.3f}衰减至{div.curr_power:.3f}（比值{div.ratio:.2f}）",
                            stop=s.high * 1.03,
                        )
                    )

            # 三买：向上离开中枢后的回调笔，低点不回中枢
            for s in leaving:
                if s.direction == -1 and s.low > last_c.zg:
                    conf = 0.6 + 0.15 * (1 if last_c.direction == 1 else 0)
                    signals.append(
                        self._mk_signal(
                            "B3", "buy", "三类买点（中枢上方企稳）",
                            s.low, s.end_raw, conf,
                            f"向上离开中枢[{last_c.zd:.2f},{last_c.zg:.2f}]后回调至{s.low:.2f}，"
                            f"未跌回中枢上沿{last_c.zg:.2f}，中枢上沿形成支撑",
                            stop=last_c.zg * 0.99,
                        )
                    )
                    break

            # 三卖：向下离开中枢后的反抽笔，高点不回中枢
            for s in leaving:
                if s.direction == 1 and s.high < last_c.zd:
                    conf = 0.6 + 0.15 * (1 if last_c.direction == -1 else 0)
                    signals.append(
                        self._mk_signal(
                            "S3", "sell", "三类卖点（中枢下方受阻）",
                            s.high, s.end_raw, conf,
                            f"向下离开中枢[{last_c.zd:.2f},{last_c.zg:.2f}]后反抽至{s.high:.2f}，"
                            f"未升回中枢下沿{last_c.zd:.2f}，中枢下沿形成压力",
                            stop=last_c.zd * 1.01,
                        )
                    )
                    break

        # ---- 二买 / 二卖：一买一卖之后的次级别确认 ----
        b1s = [s for s in signals if s.code == "B1"]
        s1s = [s for s in signals if s.code == "S1"]

        for b1 in b1s:
            after = [s for s in strokes if s.start_raw >= b1.raw_idx]
            if len(after) >= 2:
                up, back = after[0], after[1]
                if up.direction == 1 and back.direction == -1 and back.low >= b1.price * 0.995:
                    conf = 0.65 if back.low > b1.price else 0.5
                    signals.append(
                        self._mk_signal(
                            "B2", "buy", "二类买点（回调不破前低）",
                            back.low, back.end_raw, conf,
                            f"一类买点{b1.price:.2f}后反弹至{up.high:.2f}，回调至{back.low:.2f}未创新低，"
                            f"次级别回调结束",
                            stop=min(b1.price, back.low) * 0.97,
                        )
                    )
                    break

        for s1 in s1s:
            after = [s for s in strokes if s.start_raw >= s1.raw_idx]
            if len(after) >= 2:
                dn, back = after[0], after[1]
                if dn.direction == -1 and back.direction == 1 and back.high <= s1.price * 1.005:
                    conf = 0.65 if back.high < s1.price else 0.5
                    signals.append(
                        self._mk_signal(
                            "S2", "sell", "二类卖点（反抽不创新高）",
                            back.high, back.end_raw, conf,
                            f"一类卖点{s1.price:.2f}后回落至{dn.low:.2f}，反抽至{back.high:.2f}未创新高，"
                            f"次级别反抽结束",
                            stop=max(s1.price, back.high) * 1.03,
                        )
                    )
                    break

        # ---- 无中枢时的兜底：最近一笔 + 背驰 ----
        if not centers and len(recent) >= 3:
            last = recent[-1]
            div = div_map.get(id(last))
            if div is not None:
                if div.kind == "bull" and last.direction == -1:
                    signals.append(
                        self._mk_signal(
                            "B1", "buy", "一类买点（趋势底背驰）",
                            last.low, last.end_raw, 0.5,
                            f"下跌段 MACD 柱面积由{div.prev_power:.3f}衰减至{div.curr_power:.3f}，"
                            f"价格创新低但动能衰竭",
                            stop=last.low * 0.97,
                        )
                    )
                if div.kind == "bear" and last.direction == 1:
                    signals.append(
                        self._mk_signal(
                            "S1", "sell", "一类卖点（趋势顶背驰）",
                            last.high, last.end_raw, 0.5,
                            f"上涨段 MACD 柱面积由{div.prev_power:.3f}衰减至{div.curr_power:.3f}，"
                            f"价格创新高但动能衰竭",
                            stop=last.high * 1.03,
                        )
                    )

        # ---- 过滤 1：价格不能偏离当前价过远 ----
        lo, hi = last_price * 0.75, last_price * 1.35
        signals = [s for s in signals if lo <= s.price <= hi]

        # ---- 过滤 2：新鲜度。缠论买卖点有强时效性，过期信号无交易价值 ----
        n_bars = (visible_until + 1) if visible_until is not None else len(self.close)
        fresh_cut = n_bars - max(5, int(staleness_bars))
        signals = [s for s in signals if s.raw_idx >= fresh_cut]

        # 去重：同一 code 只保留最近一次
        best: Dict[str, ChanSignal] = {}
        for s in signals:
            if s.code not in best or s.raw_idx > best[s.code].raw_idx:
                best[s.code] = s

        result = sorted(best.values(), key=lambda x: -x.raw_idx)
        # 回测模式不污染引擎状态
        if visible_until is None:
            self.signals = result
        return result

    # -------------------------------------------------- 8. 汇总
    def trend_state(self) -> str:
        """当前趋势状态：up / down / oscillate。"""
        centers = self.centers or self.build_centers()
        last_price = float(self.close.iloc[-1])
        if not centers:
            return "oscillate" if len(self.strokes) < 3 else (
                "up" if self.strokes[-1].direction == 1 else "down"
            )
        c = centers[-1]
        if last_price > c.zg:
            return "up"
        if last_price < c.zd:
            return "down"
        return "oscillate"

    def analyze(self) -> Dict[str, Any]:
        """执行完整分析链并返回结构化结果。"""
        self.merge_klines()
        self.find_fractals()
        self.build_strokes()
        self.build_segments()
        self.build_centers()
        self.detect_divergence()
        self.find_signals()

        last_price = float(self.close.iloc[-1])
        last_c = self.centers[-1] if self.centers else None

        return {
            "last_price": last_price,
            "trend": self.trend_state(),
            "merged": [
                {"idx": m.idx, "high": m.high, "low": m.low, "start": m.start, "end": m.end}
                for m in self.merged
            ],
            "fractals": [
                {"kind": f.kind, "raw_idx": f.raw_idx, "price": f.price, "date": self._date_of(f.raw_idx)}
                for f in self.fractals
            ],
            "strokes": [s.to_dict() for s in self.strokes],
            "segments": [
                {
                    "direction": s.direction,
                    "start_raw": s.start_raw,
                    "end_raw": s.end_raw,
                    "high": s.high,
                    "low": s.low,
                }
                for s in self.segments
            ],
            "centers": [c.to_dict() for c in self.centers],
            "divergences": [d.to_dict() for d in self.divergences],
            "signals": [s.to_dict() for s in self.signals],
            "last_center": last_c.to_dict() if last_c else None,
            "stroke_count": len(self.strokes),
            "center_count": len(self.centers),
        }


# ----------------------------------------------------------------------------
# 便捷入口
# ----------------------------------------------------------------------------
def analyze_chan(df: pd.DataFrame, **kwargs) -> Dict[str, Any]:
    return ChanEngine(df, **kwargs).analyze()


def latest_signals(df: pd.DataFrame, **kwargs) -> List[Dict[str, Any]]:
    return analyze_chan(df, **kwargs)["signals"]
