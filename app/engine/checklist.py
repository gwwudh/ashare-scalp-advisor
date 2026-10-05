from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models import Action, Bar, Quote, Situation

Mode = Literal["低吸", "减仓", "观望扫描", ""]


@dataclass
class CheckDim:
    name: str
    standard: str
    current: str
    ok: bool
    note: str = ""


@dataclass
class TripleConfirm:
    mode: Mode
    dims: list[CheckDim] = field(default_factory=list)
    passed: int = 0
    total: int = 3
    action: Action = "观望"
    headline: str = ""
    near_level: bool = False
    dist_pct: float = 99.0

    @property
    def all_ok(self) -> bool:
        return self.passed >= self.total and self.total > 0


def _rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # index in closes aligns with gains index i-1
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def _kdj_series(bars: list[Bar], n: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ks: list[float | None] = [None] * len(bars)
    ds: list[float | None] = [None] * len(bars)
    js: list[float | None] = [None] * len(bars)
    if len(bars) < n:
        return ks, ds, js
    k, d = 50.0, 50.0
    for i in range(n - 1, len(bars)):
        window = bars[i - n + 1 : i + 1]
        hh = max(b.high for b in window)
        ll = min(b.low for b in window)
        rsv = 50.0 if hh == ll else (bars[i].close - ll) / (hh - ll) * 100
        k = (rsv + 2 * k) / 3
        d = (k + 2 * d) / 3
        j = 3 * k - 2 * d
        ks[i], ds[i], js[i] = k, d, j
    return ks, ds, js


def _vol_ratio(bars: list[Bar], lookback: int = 20) -> float:
    if not bars:
        return 1.0
    vols = [b.volume for b in bars[-lookback:] if b.volume > 0]
    if len(vols) < 2:
        return 1.0
    avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
    if avg <= 0:
        return 1.0
    return bars[-1].volume / avg


def _shadow_pct(bar: Bar) -> tuple[float, float]:
    """返回 (下影线占收盘%, 上影线占收盘%)."""
    if bar.close <= 0:
        return 0.0, 0.0
    body_top = max(bar.open, bar.close)
    body_bot = min(bar.open, bar.close)
    lower = max(0.0, body_bot - bar.low) / bar.close * 100
    upper = max(0.0, bar.high - body_top) / bar.close * 100
    return lower, upper


def _zone_label(levels: list[float], i: int) -> str:
    if i >= len(levels):
        return "-"
    p = levels[i]
    # 给出窄带描述，便于问财式阅读
    lo = p * 0.997
    hi = p * 1.003
    return f"{lo:.0f}-{hi:.0f}" if p >= 100 else f"{lo:.2f}-{hi:.2f}"


def _fmt_support_zones(supports: list[float]) -> str:
    parts = []
    for i, s in enumerate(supports[:3]):
        band = max(s * 0.004, 0.5)
        parts.append(f"{s - band:.0f}-{s + band * 0.3:.0f}" if s >= 50 else f"{s - band:.2f}-{s:.2f}")
    return " / ".join(parts) if parts else "-"


def _fmt_resist_zones(resistances: list[float]) -> str:
    parts = []
    for i, r in enumerate(resistances[:3]):
        band = max(r * 0.004, 0.5)
        parts.append(f"{r - band * 0.3:.0f}-{r + band:.0f}" if r >= 50 else f"{r:.2f}-{r + band:.2f}")
    return " / ".join(parts) if parts else "-"


def build_triple_confirm(
    sit: Situation,
    bars: list[Bar],
    quote: Quote | None = None,
    approach_pct: float = 1.2,
) -> TripleConfirm:
    """
    接近支撑 → 低吸三重确认；接近压力 → 减仓三重确认。
    维度：价位到位 / 量能配合 / 动量拐点。
    """
    price = sit.price
    supports = list(sit.supports or [])
    resistances = list(sit.resistances or [])

    dist_s = 99.0
    dist_r = 99.0
    if supports:
        nearest_s = max([s for s in supports if s <= price], default=supports[0])
        dist_s = abs(price - nearest_s) / max(price, 1e-9) * 100
    if resistances:
        nearest_r = min([r for r in resistances if r >= price], default=resistances[0])
        dist_r = abs(nearest_r - price) / max(price, 1e-9) * 100

    # 选模式：更靠近哪一侧
    if dist_s <= approach_pct or (supports and price <= supports[0] * 1.012):
        mode: Mode = "低吸"
        near = True
        dist = dist_s
    elif dist_r <= approach_pct or (resistances and price >= resistances[0] * 0.988):
        mode = "减仓"
        near = True
        dist = dist_r
    else:
        mode = "观望扫描"
        near = False
        dist = min(dist_s, dist_r)

    closes = [b.close for b in bars] if bars else [price]
    rsi_s = _rsi_series(closes)
    _k, _d, j_s = _kdj_series(bars) if bars else ([], [], [])
    vol_r = _vol_ratio(bars) if bars else 1.0
    last_bar = bars[-1] if bars else None
    lower_sh, upper_sh = _shadow_pct(last_bar) if last_bar else (0.0, 0.0)

    rsi_now = next((x for x in reversed(rsi_s) if x is not None), None)
    rsi_prev = None
    for x in reversed(rsi_s[:-1]):
        if x is not None:
            rsi_prev = x
            break
    j_now = next((x for x in reversed(j_s) if x is not None), None)
    j_prev = None
    for x in reversed(j_s[:-1] if j_s else []):
        if x is not None:
            j_prev = x
            break

    amount = quote.amount if quote and quote.amount is not None else None
    # quote.amount 约定为万元；转亿元展示
    amount_yi = amount / 10000.0 if amount is not None else None

    dims: list[CheckDim] = []

    if mode == "低吸":
        # 1 价位
        zones = _fmt_support_zones(supports)
        s1 = supports[0] if supports else None
        gap = (price - s1) if s1 else None
        price_ok = bool(
            s1
            and (
                price <= s1 * 1.002
                or (last_bar is not None and last_bar.low <= s1 * 1.003)
            )
        )
        if s1 and gap is not None:
            cur = f"当前{price:.2f}，距第一档差{gap:.2f}元" + ("，已触及" if price_ok else "，未到")
        else:
            cur = f"当前{price:.2f}"
        dims.append(
            CheckDim("价位到位", f"触及支撑位：{zones}", cur, price_ok)
        )

        # 2 量能
        vol_ok = vol_r < 0.8
        amt_note = ""
        if amount_yi is not None:
            # 个股经验阈值：创业板/中军可按现价粗调；用相对判断
            amt_ok = amount_yi <= max(price * 0.08, 8.0)  # 软阈值
            amt_note = f"成交额约{amount_yi:.1f}亿"
            vol_ok = vol_ok and (amount_yi < max(price * 0.12, 15) or vol_r < 0.7)
        else:
            amt_ok = vol_r < 0.75
            amt_note = "无成交额，凭量比判断"
        cur_vol = f"量比{vol_r:.2f}" + (f"，{amt_note}" if amt_note else "")
        if vol_r < 0.8 and not price_ok:
            cur_vol += "，缩量雏形但未到极值" if vol_r >= 0.55 else "，缩量较充分"
        dims.append(
            CheckDim("量能配合", "缩量配合：量比<0.8（越低越好）", cur_vol, vol_ok)
        )

        # 3 动量
        rsi_turn = bool(rsi_now is not None and rsi_prev is not None and rsi_now < 35 and rsi_now > rsi_prev)
        j_turn = bool(j_now is not None and j_prev is not None and j_now < 20 and j_now > j_prev)
        j_deep = bool(j_now is not None and j_now < 10)
        shadow_ok = lower_sh >= 1.0
        mom_ok = (rsi_turn or (rsi_now is not None and rsi_now < 30)) and (j_deep or j_turn) and shadow_ok
        # 放宽一点：两项以上算部分通过，三项才全过
        mom_score = sum([rsi_turn or (rsi_now is not None and rsi_now < 32), j_deep or j_turn, shadow_ok])
        mom_ok = mom_score >= 3
        jtxt = f"J={j_now:.1f}" if j_now is not None else "J=-"
        rtxt = f"RSI={rsi_now:.1f}" if rsi_now is not None else "RSI=-"
        cur_m = f"{rtxt}，{jtxt}，下影{lower_sh:.2f}%"
        if j_now is not None and 10 <= j_now < 20:
            cur_m += "，J接近但未到<10"
        if lower_sh < 1.0:
            cur_m += f"（无长下影，需≥1%）"
        dims.append(
            CheckDim(
                "动量拐点",
                "RSI<30拐头向上 + KDJ的J<10拐头 + 当日长下影≥1%",
                cur_m,
                mom_ok,
            )
        )

    elif mode == "减仓":
        zones = _fmt_resist_zones(resistances)
        r1 = resistances[0] if resistances else None
        gap = (r1 - price) if r1 else None
        price_ok = bool(r1 and price >= r1 * 0.995) or dist_r <= 0.35
        cur = f"当前{price:.2f}" + (f"，距第一压力差{gap:.2f}元" if gap is not None else "")
        if price_ok:
            cur += "，已接近/触及"
        else:
            cur += "，未到"
        dims.append(CheckDim("价位到位", f"触及压力位：{zones}", cur, price_ok))

        vol_ok = vol_r >= 1.2 or upper_sh >= 0.8
        cur_vol = f"量比{vol_r:.2f}，上影{upper_sh:.2f}%"
        if amount_yi is not None:
            cur_vol += f"，额约{amount_yi:.1f}亿"
        dims.append(
            CheckDim("量能配合", "冲高放量或长上影：量比≥1.2 或 上影≥0.8%", cur_vol, vol_ok)
        )

        rsi_hot = bool(rsi_now is not None and rsi_now > 70)
        j_hot = bool(j_now is not None and j_now > 90)
        rsi_turn_down = bool(rsi_now is not None and rsi_prev is not None and rsi_now < rsi_prev and rsi_now > 60)
        mom_ok = (rsi_hot or j_hot or rsi_turn_down) and upper_sh >= 0.5
        rtxt = f"RSI={rsi_now:.1f}" if rsi_now is not None else "RSI=-"
        jtxt = f"J={j_now:.1f}" if j_now is not None else "J=-"
        cur_m = f"{rtxt}，{jtxt}，上影{upper_sh:.2f}%"
        dims.append(
            CheckDim("动量拐点", "RSI/J 超买或拐头向下 + 冲高回落上影", cur_m, mom_ok)
        )
    else:
        # 远离子带：给简况，不强制三确认
        dims.append(
            CheckDim(
                "价位观察",
                f"距支撑{dist_s:.2f}% / 距压力{dist_r:.2f}%",
                f"现价{price:.2f}，未进入关键带（阈值{approach_pct}%）",
                False,
            )
        )
        dims.append(
            CheckDim(
                "量能观察",
                "量比与结构",
                f"量比{vol_r:.2f}" + (f"，额约{amount_yi:.1f}亿" if amount_yi is not None else ""),
                vol_r < 0.85,
            )
        )
        rtxt = f"RSI={rsi_now:.1f}" if rsi_now is not None else "RSI=-"
        jtxt = f"J={j_now:.1f}" if j_now is not None else "J=-"
        dims.append(
            CheckDim("动量观察", "RSI / KDJ", f"{rtxt}，{jtxt}", False)
        )

    passed = sum(1 for d in dims if d.ok)
    total = len(dims)

    if mode == "低吸":
        if passed >= 3:
            action: Action = "买入"
            headline = "低吸三重确认齐备，可考虑买入/回补"
        elif passed == 2:
            action = "观望"
            headline = "低吸仅两项确认，差一维，暂观望"
        else:
            action = "观望"
            headline = "低吸三重确认未齐，观望等待"
    elif mode == "减仓":
        if passed >= 3:
            action = "卖出"
            headline = "减仓三重确认齐备，可考虑减仓/卖出"
        elif passed == 2:
            action = "观望"
            headline = "减仓两项确认，警惕但可再等一根"
        else:
            action = "观望"
            headline = "减仓条件不足，暂观望"
    else:
        action = "观望"
        headline = "未到关键支撑/压力带，继续观察"

    return TripleConfirm(
        mode=mode,
        dims=dims,
        passed=passed,
        total=total,
        action=action,
        headline=headline,
        near_level=near,
        dist_pct=dist,
    )


def format_checklist_text(tc: TripleConfirm, name: str = "") -> str:
    """纯文本（通知流 detail）。"""
    prefix = f"{name}：" if name else ""
    lines = [
        f"{prefix}【{tc.mode or '扫描'}三重确认】{tc.passed}/{tc.total} — {tc.headline}",
    ]
    for d in tc.dims:
        mark = "✓" if d.ok else "✗"
        lines.append(f"{mark} {d.name}｜标准：{d.standard}｜现状：{d.current}")
    return "\n".join(lines)


def format_checklist_html(tc: TripleConfirm, name: str = "") -> str:
    """简易 HTML，供前端直接插入。"""
    title = f"{name} " if name else ""
    rows = []
    for d in tc.dims:
        cls = "ok" if d.ok else "bad"
        mark = "✓" if d.ok else "✗"
        rows.append(
            f'<div class="chk-row {cls}"><span class="chk-mark">{mark}</span>'
            f'<span class="chk-name">{d.name}</span>'
            f'<span class="chk-std">{d.standard}</span>'
            f'<span class="chk-cur">{d.current}</span></div>'
        )
    return (
        f'<div class="chk">'
        f'<div class="chk-head">{title}【{tc.mode or "扫描"}】{tc.passed}/{tc.total} {tc.headline}</div>'
        f'{"".join(rows)}'
        f"</div>"
    )
