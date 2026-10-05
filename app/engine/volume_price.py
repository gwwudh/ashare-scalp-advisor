from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models import Bar

SwingType = Literal["peak", "trough", ""]
VpTag = Literal[
    "滞涨",
    "上涨无力",
    "放量滞涨",
    "下跌减弱",
    "买盘增强",
    "缩量下跌",
    "放量下跌",
    "量价配合",
    "",
]


@dataclass
class VolumePriceSignal:
    """波峰/波谷处的量价结构判断，用于通知流买卖参考。"""

    swing: SwingType = ""
    swing_index: int = -1
    swing_price: float = 0.0
    tags: list[str] = field(default_factory=list)
    vol_ratio: float = 1.0  # 相对均量
    price_change_pct: float = 0.0
    buy_score: float = 0.0  # >0 偏多参考
    sell_score: float = 0.0  # >0 偏空参考
    summary: str = ""
    actionable: bool = False  # 是否足以作为买卖参考


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def detect_confirmed_swing(
    bars: list[Bar],
    left: int = 2,
    right: int = 2,
) -> tuple[SwingType, int]:
    """
    检测最近一根「已确认」的波峰/波谷（右侧 right 根已走出）。
    返回 (类型, bar 下标)；无则 ("", -1)。
    """
    n = len(bars)
    if n < left + right + 1:
        return "", -1
    i = n - right - 1
    if i < left:
        return "", -1
    window = bars[i - left : i + right + 1]
    h, l = bars[i].high, bars[i].low
    is_peak = all(h >= b.high for b in window)
    is_trough = all(l <= b.low for b in window)
    if is_peak and not is_trough:
        return "peak", i
    if is_trough and not is_peak:
        return "trough", i
    return "", -1


def analyze_volume_price(
    bars: list[Bar],
    swing: SwingType | None = None,
    swing_index: int | None = None,
    vol_ma: int = 10,
) -> VolumePriceSignal:
    """
    在波峰/波谷结合量价、影线、近端动能，判断：
    - 滞涨 / 上涨无力 → 卖出参考
    - 下跌减弱 / 买盘增强 → 买入参考
    """
    sig = VolumePriceSignal()
    if len(bars) < 8:
        return sig

    if swing is None or swing_index is None:
        swing, swing_index = detect_confirmed_swing(bars)
    if not swing or swing_index < 0:
        return sig

    sig.swing = swing
    sig.swing_index = swing_index
    pivot = bars[swing_index]
    sig.swing_price = pivot.high if swing == "peak" else pivot.low

    # 均量（不含当前确认段末尾，用 pivot 及之前）
    hist = bars[max(0, swing_index - vol_ma) : swing_index + 1]
    vols = [b.volume for b in hist if b.volume > 0]
    avg_vol = _avg(vols) or 1.0
    sig.vol_ratio = round(pivot.volume / avg_vol, 2) if avg_vol else 1.0

    # 波段涨跌：相对前一相反枢轴或 N 根前
    look = bars[max(0, swing_index - 8) : swing_index + 1]
    if swing == "peak":
        base = min(b.low for b in look)
        sig.price_change_pct = round((pivot.high - base) / max(base, 1e-9) * 100, 2)
    else:
        base = max(b.high for b in look)
        sig.price_change_pct = round((pivot.low - base) / max(base, 1e-9) * 100, 2)

    body = abs(pivot.close - pivot.open)
    rng = max(pivot.high - pivot.low, 1e-9)
    upper_shadow = pivot.high - max(pivot.open, pivot.close)
    lower_shadow = min(pivot.open, pivot.close) - pivot.low
    upper_ratio = upper_shadow / rng
    lower_ratio = lower_shadow / rng

    # 近端动能：pivot 后 right 根的方向
    after = bars[swing_index + 1 :]
    after_chg = 0.0
    if after:
        after_chg = (after[-1].close - pivot.close) / max(pivot.close, 1e-9) * 100

    tags: list[str] = []
    buy = 0.0
    sell = 0.0

    if swing == "peak":
        # 上涨末端量价背离 / 长上影 / 冲高回落
        if sig.vol_ratio >= 1.4 and abs(pivot.close - pivot.open) / rng < 0.35:
            tags.append("放量滞涨")
            sell += 1.4
        elif sig.vol_ratio < 0.85 and sig.price_change_pct > 0.8:
            tags.append("上涨无力")
            sell += 1.2
        if upper_ratio >= 0.45 and pivot.close < pivot.open:
            tags.append("滞涨")
            sell += 1.0
        if after_chg < -0.3:
            tags.append("冲高回落")
            sell += 0.8
        if sig.vol_ratio >= 1.2 and pivot.close > pivot.open and upper_ratio < 0.25 and after_chg >= 0:
            tags.append("量价配合")
            buy += 0.4  # 强势峰不一定立刻卖
        # 默认峰位偏谨慎
        if not tags:
            tags.append("波峰观察")
            sell += 0.3

    elif swing == "trough":
        if sig.vol_ratio >= 1.2 and lower_ratio >= 0.4 and pivot.close >= pivot.open:
            tags.append("买盘增强")
            buy += 1.5
        if sig.vol_ratio < 0.9 and sig.price_change_pct < -0.5:
            tags.append("缩量下跌")
            buy += 0.9
            tags.append("下跌减弱")
            buy += 0.6
        if after_chg > 0.3:
            tags.append("下跌减弱")
            buy += 0.8
        if sig.vol_ratio >= 1.5 and pivot.close < pivot.open and lower_ratio < 0.2:
            tags.append("放量下跌")
            sell += 1.2
        if not tags:
            tags.append("波谷观察")
            buy += 0.3

    sig.tags = tags
    sig.buy_score = round(buy, 2)
    sig.sell_score = round(sell, 2)
    sig.actionable = max(buy, sell) >= 0.9

    side = "波峰" if swing == "peak" else "波谷"
    tag_txt = "、".join(tags) if tags else "中性"
    if buy > sell and buy >= 0.9:
        verdict = "可考虑买入/回补"
    elif sell > buy and sell >= 0.9:
        verdict = "可考虑减仓/卖出"
    else:
        verdict = "暂作观察"
    sig.summary = (
        f"{side}确认 {sig.swing_price:.2f}，量比{sig.vol_ratio:.2f}，"
        f"{tag_txt}；{verdict}"
    )
    return sig


def multi_tf_volume_price(
    bar_sets: dict[str, list[Bar]],
) -> VolumePriceSignal:
    """
    多周期：任一周期刚确认波峰/波谷即评估；
    取 actionable 分数最高者作为主信号。
    """
    best = VolumePriceSignal()
    best_score = -1.0
    for _tf, bars in bar_sets.items():
        if not bars or len(bars) < 8:
            continue
        sig = analyze_volume_price(bars)
        score = max(sig.buy_score, sig.sell_score)
        if sig.swing and score > best_score:
            best = sig
            best_score = score
            if _tf:
                best.summary = f"[{_tf}] {best.summary}"
    return best
