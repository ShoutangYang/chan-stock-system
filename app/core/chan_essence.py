"""
缠论精髓层（Essence Layer）
===========================

把《缠论原理.md》中"最精髓、但此前未工程化"的部分落成代码：

| 原理（见 缠论原理.md）         | 本模块实现                       | 函数 |
|---|---|---|
| 第一章 完全分类               | 走势类型判定 上涨/下跌/盘整       | `classify_trend_type()` |
| 第四章 走势必完美             | 走势是否完成 + 接下来必然发生什么 | `analyze_perfection()` |
| 第三章 背驰（趋势 vs 盘整）    | 背驰分类与"必然回中枢"保证        | `classify_divergence()` |
| 第七章 中枢演化               | 延伸 / 扩展 / 扩张 / 新生         | `detect_center_evolution()` |
| 第六章 级别与区间套           | 日线×周线多级别联立              | `analyze_nested()` |
| 第五章 三类买卖点             | 带理由的操作提示                 | `build_advices()` |

设计原则（与整个项目一致）：
  - **只做结构推断，不做价格预测**——输出的是"按缠论，接下来必然发生什么"，不是目标价。
  - **每条结论都必须给出依据**，可追溯到具体中枢区间 / 力度数值 / 周线状态。
  - **不修改 `chanlun.py`**——本模块只读它的输出，保证现有逻辑与回测结果不受影响。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def _f(x: Any, nd: int = 2) -> Optional[float]:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(v, nd)
    except Exception:
        return None


def _centers(chan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((chan or {}).get("centers") or [])


def _signals(chan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((chan or {}).get("signals") or [])


def _divs(chan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((chan or {}).get("divergences") or [])


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """两个中枢区间是否有重叠（扩张 / 级别升级的判据）。"""
    try:
        return min(float(a["zg"]), float(b["zg"])) > max(float(a["zd"]), float(b["zd"]))
    except Exception:
        return False


# ----------------------------------------------------------------------------
# 一、走势类型（完全分类）
# ----------------------------------------------------------------------------
def classify_trend_type(chan: Dict[str, Any]) -> Dict[str, Any]:
    """
    按中枢的数量与相对位置，把走势完全分类为 上涨 / 下跌 / 盘整。

    上涨：至少两个依次向上的中枢（后中枢下沿 > 前中枢上沿）
    下跌：至少两个依次向下的中枢（后中枢上沿 < 前中枢下沿）
    盘整：只有一个中枢，或相邻中枢区间重叠（级别升级）
    """
    cs = _centers(chan)
    n = len(cs)

    if n == 0:
        return {
            "type": "未定", "code": "unknown", "center_count": 0,
            "desc": "尚未形成中枢——按缠论，走势未完成，无法分类。",
            "basis": "中枢是走势分类的唯一依据，无中枢即无走势类型。",
        }

    if n == 1:
        c = cs[0]
        return {
            "type": "盘整", "code": "consolidation", "center_count": 1,
            "desc": f"只有一个中枢 [{_f(c['zd'])} , {_f(c['zg'])}]，走势类型为盘整。",
            "basis": "盘整的定义就是仅包含一个同级别中枢。",
            "center": {"zg": _f(c["zg"]), "zd": _f(c["zd"])},
        }

    prev, last = cs[-2], cs[-1]
    if float(last["zd"]) > float(prev["zg"]):
        return {
            "type": "上涨", "code": "up", "center_count": n,
            "desc": f"最近两个中枢依次抬高（前中枢上沿 {_f(prev['zg'])} → 后中枢下沿 {_f(last['zd'])}），走势类型为上涨。",
            "basis": "上涨 = 至少两个依次向上的同级别中枢。",
            "center": {"zg": _f(last["zg"]), "zd": _f(last["zd"])},
            "prev_center": {"zg": _f(prev["zg"]), "zd": _f(prev["zd"])},
        }
    if float(last["zg"]) < float(prev["zd"]):
        return {
            "type": "下跌", "code": "down", "center_count": n,
            "desc": f"最近两个中枢依次降低（前中枢下沿 {_f(prev['zd'])} → 后中枢上沿 {_f(last['zg'])}），走势类型为下跌。",
            "basis": "下跌 = 至少两个依次向下的同级别中枢。",
            "center": {"zg": _f(last["zg"]), "zd": _f(last["zd"])},
            "prev_center": {"zg": _f(prev["zg"]), "zd": _f(prev["zd"])},
        }

    return {
        "type": "盘整", "code": "consolidation", "center_count": n,
        "desc": f"相邻两个中枢区间重叠（[{_f(prev['zd'])} , {_f(prev['zg'])}] 与 [{_f(last['zd'])} , {_f(last['zg'])}]），"
                f"属中枢扩张——震荡级别升级，仍归为盘整。",
        "basis": "中枢区间重叠意味着级别升级，形成更大级别的中枢，走势类型为盘整。",
        "center": {"zg": _f(last["zg"]), "zd": _f(last["zd"])},
    }


# ----------------------------------------------------------------------------
# 二、走势必完美
# ----------------------------------------------------------------------------
def analyze_perfection(chan: Dict[str, Any], ttype: Dict[str, Any],
                       dcls: Dict[str, Any]) -> Dict[str, Any]:
    """
    走势必完美：任何级别的任何走势终将完成。

    工程化判定：
      上涨 + 顶背驰  -> 上涨走势完成，必然至少回到最后一个中枢
      下跌 + 底背驰  -> 下跌走势完成，必然至少回到最后一个中枢
      盘整 + 三类买卖点 -> 盘整完成，转化为趋势
      否则 -> 走势未完成，按原方向延续
    """
    code = ttype.get("code")
    center = ttype.get("center") or {}
    sigs = {s.get("code") for s in _signals(chan)}
    dkind = dcls.get("kind", "无")
    dside = dcls.get("side")

    completed = False
    evidence = ""
    expectation = ""
    certainty = "中"

    if code == "up" and dside == "bear":
        completed = True
        certainty = "高" if dkind == "趋势背驰" else "中"
        evidence = f"上涨走势中出现{dkind}（顶背驰），力度衰竭——按走势必完美，该上涨必须完成。"
        expectation = f"必然至少回到最后一个中枢（下沿 ZD = {_f(center.get('zd'))}）；" \
                      f"趋势背驰的回抽是结构性必然，不是概率。"
    elif code == "down" and dside == "bull":
        completed = True
        certainty = "高" if dkind == "趋势背驰" else "中"
        evidence = f"下跌走势中出现{dkind}（底背驰），力度衰竭——按走势必完美，该下跌必须完成。"
        expectation = f"必然至少回到最后一个中枢（上沿 ZG = {_f(center.get('zg'))}）；" \
                      f"趋势背驰的回抽是结构性必然，不是概率。"
    elif code == "consolidation" and ("B3" in sigs or "S3" in sigs):
        completed = True
        certainty = "中"
        which = "第三类买点" if "B3" in sigs else "第三类卖点"
        evidence = f"盘整中出现{which}，中枢被有效突破——盘整完成。"
        expectation = "盘整结束，将转化为上涨或下跌趋势；若随后回中枢，则突破失败、盘整延续。"
    else:
        if code == "up":
            expectation = "上涨走势尚未出现顶背驰，按走势必完美，走势未完成，仍将延续——至少还要形成新的中枢。"
        elif code == "down":
            expectation = "下跌走势尚未出现底背驰，按走势必完美，走势未完成，仍将延续——至少还要形成新的中枢。"
        elif code == "consolidation":
            expectation = "盘整未出现第三类买卖点，方向未定，继续围绕中枢震荡。"
        else:
            expectation = "尚无中枢，走势未完成。"

    return {
        "principle": "任何级别的任何走势，终将完成",
        "type": ttype.get("type"),
        "completed": completed,
        "evidence": evidence or "未出现背驰或第三类买卖点，走势仍在延续中。",
        "expectation": expectation,
        "certainty": certainty,
        "note": "走势必完美是缠论唯一的'预测'来源：它保证的是'走势必须完成'，"
                "不是'会涨到多少'。本模块据此给出必然性，不给出目标价。",
    }


# ----------------------------------------------------------------------------
# 三、背驰分类（趋势背驰 / 盘整背驰）
# ----------------------------------------------------------------------------
def classify_divergence(chan: Dict[str, Any], ttype: Dict[str, Any]) -> Dict[str, Any]:
    """
    区分趋势背驰与盘整背驰。

    这是《缠论原理.md》3.2 的关键区别：
      趋势背驰（趋势中，≥2 个同向中枢）-> 背驰后**必然**回中枢，可下注
      盘整背驰（盘整中，仅 1 个中枢）  -> **不保证**回中枢，只能降权看待
    """
    divs = _divs(chan)
    if not divs:
        return {
            "kind": "无", "side": None, "ratio": None,
            "guarantee": "—", "implication": "当前无背驰，走势力度未衰竭。",
            "tradeable": False,
        }

    d = divs[-1]
    side = d.get("kind")              # bull / bear
    ratio = _f(d.get("ratio"))
    is_trend = ttype.get("code") in ("up", "down")
    kind = "趋势背驰" if is_trend else "盘整背驰"

    if is_trend:
        c = ttype.get("center") or {}
        target = c.get("zg") if side == "bull" else c.get("zd")
        guarantee = f"必然至少回到最后一个中枢（{_f(target)}）"
        implication = (f"{kind}：力度比值 {ratio}，价格创更极端点位但推动力量衰竭。"
                       f"按走势必完美，回抽 {_f(target)} 是结构性必然——这是可以下注的背驰。")
        tradeable = True
    else:
        guarantee = "不保证回中枢"
        implication = (f"{kind}：力度比值 {ratio}。盘整背驰只说明震荡力度减弱，"
                       f"不保证回中枢，可能出现'背驰后再背驰'。仅作提醒，不宜独立下注。")
        tradeable = False

    return {
        "kind": kind,
        "side": side,
        "ratio": ratio,
        "prev_power": _f(d.get("prev_power"), 3),
        "curr_power": _f(d.get("curr_power"), 3),
        "guarantee": guarantee,
        "implication": implication,
        "tradeable": tradeable,
    }


# ----------------------------------------------------------------------------
# 四、中枢演化
# ----------------------------------------------------------------------------
def detect_center_evolution(chan: Dict[str, Any]) -> Dict[str, Any]:
    """
    中枢的四种演化：延伸 / 扩展 / 扩张 / 新生。

    延伸：同一中枢并入更多笔（笔数 > 3），多空胶着、方向未定
    扩张：相邻两个同级别中枢区间重叠 -> **级别升级**
    新生：相邻中枢完全分离，在更高/更低位置形成 -> **趋势延续**
    """
    cs = _centers(chan)
    if not cs:
        return {"current": "无", "desc": "尚未形成中枢。", "trend_upgrade": False}

    last = cs[-1]
    # 中枢包含的笔数（build_centers 已把重叠笔并入）
    try:
        stroke_span = int(last.get("end_stroke", 0)) - int(last.get("start_stroke", 0)) + 1
    except Exception:
        stroke_span = 3

    current, desc, upgrade = "新形成", "中枢刚形成，尚未出现明显演化。", False

    if len(cs) >= 2:
        prev = cs[-2]
        if _overlap(prev, last):
            current = "扩张"
            upgrade = True
            desc = (f"相邻两中枢区间重叠（[{_f(prev['zd'])} , {_f(prev['zg'])}] 与 "
                    f"[{_f(last['zd'])} , {_f(last['zg'])}]），形成更大级别中枢——"
                    f"**级别升级**，震荡从次级别升为本级别。")
        elif float(last["zd"]) > float(prev["zg"]):
            current = "新生"
            desc = (f"新中枢在更高位置形成（前中枢上沿 {_f(prev['zg'])} → 新中枢下沿 {_f(last['zd'])}），"
                    f"**趋势延续**——上涨就是'一个又一个更高的中枢'。")
        elif float(last["zg"]) < float(prev["zd"]):
            current = "新生"
            desc = (f"新中枢在更低位置形成（前中枢下沿 {_f(prev['zd'])} → 新中枢上沿 {_f(last['zg'])}），"
                    f"**趋势延续**——下跌就是'一个又一个更低的中枢'。")
        else:
            current = "新生"
            desc = "新中枢形成，位置与前一中枢接近。"
    elif stroke_span > 3:
        current = "延伸"
        desc = (f"该中枢已并入 {stroke_span} 笔（超过构成中枢所需的最少 3 笔），价格反复穿越中枢区间——"
                f"**中枢延伸**，多空胶着、方向未定。")

    return {
        "current": current,
        "desc": desc,
        "trend_upgrade": upgrade,
        "stroke_span": stroke_span,
        "center": {"zg": _f(last["zg"]), "zd": _f(last["zd"])},
        "meaning": {
            "延伸": "多空胶着，方向未定，不宜重仓",
            "扩张": "级别升级，需按更大级别重新判断",
            "新生": "趋势延续，顺势持仓为主",
            "新形成": "结构初成，等待方向选择",
            "无": "—",
        }.get(current, "—"),
    }


# ----------------------------------------------------------------------------
# 五、区间套（多级别联立）
# ----------------------------------------------------------------------------
def analyze_nested(day_chan: Dict[str, Any],
                   week_chan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    《缠论原理.md》第六章：大级别定方向，小级别找入场。

    本系统没有完整的级别递归，但可以做**周线 × 日线**两级别联立——
    这是区间套思想在当前数据条件下的可用近似。

    共振判定：日线买卖点方向 与 周线走势方向 一致 → 共振（加分）
              相反 → 矛盾（减分，提示降低仓位）
    """
    if not week_chan:
        return {
            "available": False,
            "desc": "周线数据不足，无法做多级别联立。区间套需要至少两个级别的结构。",
            "alignment": "中性", "score_delta": 0,
        }

    wtype = classify_trend_type(week_chan)
    wtrend = week_chan.get("trend") or "oscillate"
    dtype = classify_trend_type(day_chan)
    dtrend = day_chan.get("trend") or "oscillate"

    def _dir(trend: str) -> int:
        return 1 if trend == "up" else (-1 if trend == "down" else 0)

    wd, dd = _dir(wtrend), _dir(dtrend)
    if wd != 0 and dd != 0 and wd == dd:
        alignment, delta = "共振", 12
    elif wd != 0 and dd != 0 and wd != dd:
        alignment, delta = "矛盾", -15
    else:
        alignment, delta = "中性", 0

    desc = (f"周线走势类型：**{wtype.get('type')}**（趋势状态 {wtrend}），"
            f"日线走势类型：**{dtype.get('type')}**（趋势状态 {dtrend}）——两者{alignment}。")
    if alignment == "共振":
        desc += "大级别与小级别方向一致，按区间套思想，日线信号可靠性提升。"
    elif alignment == "矛盾":
        desc += "大级别与小级别方向相反：日线的买点可能只是更大级别下跌中的一次反弹，建议降低仓位。"

    return {
        "available": True,
        "higher": {"level": "周线", "type": wtype.get("type"), "trend": wtrend},
        "lower": {"level": "日线", "type": dtype.get("type"), "trend": dtrend},
        "alignment": alignment,
        "score_delta": delta,
        "desc": desc,
        "note": "完整的区间套要求'级别'作为可计算变量并逐级递归定位到单根K线；"
                "本系统目前只有日线/周线两档，属区间套思想的近似实现。",
    }


# ----------------------------------------------------------------------------
# 六、买卖点提示（带理由）
# ----------------------------------------------------------------------------
_ACTION = {
    "B1": "一类买点 = 趋势的转折点。可小仓位试探，待二类/三类买点确认后再加仓。",
    "B2": "二类买点 = 确认点。风险低于一类，是本系统最稳健的入场位，可作为主力建仓位。",
    "B3": "三类买点 = 趋势延续点。最顺势、最快，适合顺势加仓；回中枢即离场。",
    "S1": "一类卖点 = 趋势顶部转折。持股者应减仓，空仓者不宜追高。",
    "S2": "二类卖点 = 反抽确认。反弹不创新高，是较好的减仓/离场位。",
    "S3": "三类卖点 = 中枢下方受阻。反抽不回中枢，趋势向下延续，应果断离场或做空。",
}

_INVALIDATE = {
    "B1": "跌破该买点即证伪（买点即止损）",
    "B2": "跌破一类买点即证伪",
    "B3": "跌回中枢上沿 ZG 以内即证伪",
    "S1": "升破该卖点即证伪",
    "S2": "升破一类卖点即证伪",
    "S3": "升回中枢下沿 ZD 以上即证伪",
}


def build_advices(chan: Dict[str, Any], ttype: Dict[str, Any],
                  dcls: Dict[str, Any], perf: Dict[str, Any],
                  evo: Dict[str, Any], nested: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    把缠论买卖点升级为**带理由的操作提示**。

    每条提示包含：动作、确定性、逐条依据（几何 / 动力学 / 走势必完美 /
    中枢演化 / 区间套 / 证伪条件）。
    """
    out: List[Dict[str, Any]] = []
    sigs = _signals(chan)
    if not sigs:
        return out

    center = ttype.get("center") or {}
    zg, zd = center.get("zg"), center.get("zd")
    delta = nested.get("score_delta", 0) or 0
    alignment = nested.get("alignment", "中性")

    for s in sigs:
        code = str(s.get("code", "")).upper()
        side = s.get("side")
        reasons: List[str] = []

        # 1. 原始几何依据（来自 chanlun.py）
        if s.get("reason"):
            reasons.append(f"**几何**：{s['reason']}")

        # 2. 动力学 + 背驰性质 + 走势必完美
        if code in ("B1", "S1"):
            if dcls.get("kind") != "无":
                reasons.append(
                    f"**动力学**：{dcls['kind']}，MACD 柱面积 {dcls.get('prev_power')} → "
                    f"{dcls.get('curr_power')}（力度比值 {dcls.get('ratio')}），力度衰竭。"
                )
                reasons.append(f"**走势必完美**：{dcls.get('guarantee')}。"
                               f"{'这是结构性必然，可下注。' if dcls.get('tradeable') else '不保证，仅作提醒。'}")
            else:
                reasons.append("**动力学**：本级别未见明确背驰，该买点依据的是结构位置而非力度衰竭，确定性打折。")
        elif code in ("B2", "S2"):
            reasons.append(f"**确认**：{_ACTION.get(code, '')[:0]}"
                           f"该点是对一类买卖点的次级别确认——回调未创新低/反抽未创新高，"
                           f"说明转折已被市场接受，风险低于一类。")
        elif code in ("B3", "S3"):
            reasons.append(
                f"**中枢突破**：价格离开中枢 [{_f(zd)} , {_f(zg)}] 后回抽未回到中枢，"
                f"说明中枢被有效突破，趋势即将延续。"
            )
            if evo.get("current") == "新生":
                reasons.append(f"**中枢演化**：{evo.get('desc')}")

        # 3. 走势类型与完成状态（只讲分类，必然性由"走势必完美"那条承担，避免重复）
        reasons.append(f"**走势分类**：当前为{ttype.get('type')}"
                       f"（{'已完成' if perf.get('completed') else '未完成'}）；{ttype.get('basis')}")

        # 4. 区间套
        if nested.get("available"):
            reasons.append(f"**区间套（周线×日线）**：{nested.get('desc')}")

        # 5. 中枢演化
        if evo.get("current") not in ("无", "新形成"):
            reasons.append(f"**中枢演化**：{evo.get('current')}——{evo.get('meaning')}")

        # 6. 证伪条件（买点即止损）
        reasons.append(f"**证伪与止损**：{_INVALIDATE.get(code, '跌破买点即证伪')}；"
                       f"参考止损 {_f(s.get('stop_loss'))}。")

        # ---- 确定性打分 ----
        score = 0.0
        if code in ("B1", "S1"):
            score += 25 if dcls.get("kind") == "趋势背驰" else (10 if dcls.get("kind") == "盘整背驰" else 0)
        elif code in ("B2", "S2"):
            score += 15
        elif code in ("B3", "S3"):
            score += 18
        if evo.get("current") == "新生" and code in ("B3", "S3"):
            score += 8
        if evo.get("trend_upgrade"):
            score -= 6
        score += delta
        score += float(s.get("confidence") or 0) * 20

        certainty = "高" if score >= 55 else ("中" if score >= 38 else "低")

        out.append({
            "code": code,
            "side": side,
            "name": s.get("name"),
            "date": s.get("date"),
            "price": _f(s.get("price")),
            "stop": _f(s.get("stop_loss")),
            "confidence": _f(s.get("confidence"), 2),
            "action": _ACTION.get(code, ""),
            "certainty": certainty,
            "essence_score": round(score, 1),
            "nested_alignment": alignment,
            "invalidate": _INVALIDATE.get(code, ""),
            "reasons": reasons,
        })

    out.sort(key=lambda x: -x["essence_score"])
    return out


# ----------------------------------------------------------------------------
# 七、观察提示（无买卖点时，按走势必完美告诉用户"该等什么"）
# ----------------------------------------------------------------------------
def build_watchpoints(ttype: Dict[str, Any], dcls: Dict[str, Any],
                      perf: Dict[str, Any], evo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    当 45 日时效窗口内没有明确买卖点时，缠论仍然有话可说：
    走势处于什么类型、按走势必完美接下来必然发生什么、应该等哪个确认信号。

    这不是"硬凑信号"，而是把当前结构状态讲清楚——无信号即观望，但要知道在等什么。
    """
    out: List[Dict[str, Any]] = []
    center = ttype.get("center") or {}
    zg, zd = center.get("zg"), center.get("zd")
    code = ttype.get("code")

    if perf.get("completed") and dcls.get("tradeable"):
        if dcls.get("side") == "bull":
            out.append({
                "tag": "下跌趋势背驰成立",
                "tone": "buy",
                "desc": f"下跌走势已按走势必完美完成：{dcls.get('guarantee')}。",
                "wait": [f"回调不创新低 → 形成**二类买点**（最稳健的入场位）",
                         f"向上突破中枢并回抽不跌回 ZG={_f(zg)} → 形成**三类买点**",
                         "在二类/三类买点出现前，不宜仅凭背驰重仓抄底"],
                "basis": dcls.get("implication", ""),
            })
        elif dcls.get("side") == "bear":
            out.append({
                "tag": "上涨趋势背驰成立",
                "tone": "sell",
                "desc": f"上涨走势已按走势必完美完成：{dcls.get('guarantee')}。",
                "wait": [f"反抽不创新高 → 形成**二类卖点**（较好的减仓位）",
                         f"跌破中枢并反抽不升回 ZD={_f(zd)} → 形成**三类卖点**",
                         "持股者应在此阶段逐步减仓，不宜继续追高"],
                "basis": dcls.get("implication", ""),
            })
    elif perf.get("completed") and not dcls.get("tradeable") and dcls.get("kind") == "盘整背驰":
        out.append({
            "tag": "盘整背驰（不保证回中枢）",
            "tone": "neutral",
            "desc": dcls.get("implication", ""),
            "wait": ["盘整背驰只说明震荡力度减弱，不保证回中枢，可能反复背驰",
                     "等待**第三类买卖点**确认中枢被有效突破，方向才明确"],
            "basis": "《缠论原理.md》3.2：趋势背驰必回中枢，盘整背驰不保证。",
        })
    else:
        if code == "up":
            out.append({
                "tag": "上涨走势未完成",
                "tone": "buy",
                "desc": "尚未出现顶背驰，按走势必完美，上涨走势未完成，仍将延续（至少再形成一个新中枢）。",
                "wait": ["等待出现**顶背驰** → 一类卖点，那才是可以减仓的位置",
                         "中途回调若形成**三类买点**（不跌回 ZG），属趋势延续的加仓机会"],
                "basis": "走势必完美：未完成就不会转折。",
            })
        elif code == "down":
            out.append({
                "tag": "下跌走势未完成",
                "tone": "sell",
                "desc": "尚未出现底背驰，按走势必完美，下跌走势未完成，仍将延续（至少再形成一个新中枢）。",
                "wait": ["等待出现**底背驰** → 一类买点，那才是可以试探的位置",
                         "中途反弹若形成**三类卖点**（不升回 ZD），属下跌延续，不宜抢反弹"],
                "basis": "走势必完美：未完成就不会转折。",
            })
        elif code == "consolidation":
            out.append({
                "tag": "盘整中，方向未定",
                "tone": "neutral",
                "desc": f"价格围绕中枢 [{_f(zd)} , {_f(zg)}] 震荡，未出现第三类买卖点，方向未定。",
                "wait": [f"向上突破并回抽不跌回 ZG={_f(zg)} → **三类买点**，转上涨",
                         f"向下跌破并反抽不升回 ZD={_f(zd)} → **三类卖点**，转下跌",
                         "中枢内是平衡区，不宜下注方向"],
                "basis": "中枢 = 多空平衡区；离开中枢才是失衡，才有方向。",
            })
        else:
            out.append({
                "tag": "结构尚未成型",
                "tone": "neutral",
                "desc": "尚未形成中枢，走势无法分类，也就无法给出买卖点。",
                "wait": ["等待至少形成一个中枢后再做判断"],
                "basis": "中枢是走势分类的唯一依据。",
            })

    if evo.get("trend_upgrade"):
        out.append({
            "tag": "中枢扩张：级别升级",
            "tone": "neutral",
            "desc": evo.get("desc", ""),
            "wait": ["震荡级别已升级，需按更大级别重新判断，原级别的买卖点会失效"],
            "basis": "《缠论原理.md》第七章：中枢扩张 = 级别升级。",
        })

    return out


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def analyze_essence(day_chan: Dict[str, Any],
                    week_chan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    缠论精髓分析主入口。

    day_chan  : 当前级别（通常日线）的 ChanEngine.analyze() 结果
    week_chan : 高一个级别（周线）的结果，用于区间套；可为空
    """
    if not day_chan:
        return {"ok": False, "error": "无缠论结构数据"}

    ttype = classify_trend_type(day_chan)
    dcls = classify_divergence(day_chan, ttype)
    perf = analyze_perfection(day_chan, ttype, dcls)
    evo = detect_center_evolution(day_chan)
    nested = analyze_nested(day_chan, week_chan)
    advices = build_advices(day_chan, ttype, dcls, perf, evo, nested)
    watchpoints = build_watchpoints(ttype, dcls, perf, evo) if not advices else []

    # 一句话结论
    if advices:
        top = advices[0]
        verdict = (f"{ttype.get('type')}走势"
                   f"{'已完成' if perf.get('completed') else '未完成'}；"
                   f"当前最强信号为 {top['code']} {top['name']}（确定性 {top['certainty']}）。")
    else:
        wp = watchpoints[0]["tag"] if watchpoints else "无明确买卖点"
        verdict = (f"{ttype.get('type')}走势"
                   f"{'已完成' if perf.get('completed') else '未完成'}；"
                   f"当前无明确买卖点（{wp}）——缠论讲究「不测而测」，无信号即观望。")

    return {
        "ok": True,
        "verdict": verdict,
        "trend_type": ttype,
        "perfection": perf,
        "divergence": dcls,
        "center_evolution": evo,
        "nested": nested,
        "advices": advices,
        "watchpoints": watchpoints,
        "principle_ref": "依据见 缠论原理.md：第四章 走势必完美 / 第六章 区间套 / 第七章 中枢演化",
    }
