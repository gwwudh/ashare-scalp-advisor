from __future__ import annotations

import time
import uuid
from typing import Any

from app.config import AppConfig, WatchStock
from app.engine.checklist import build_triple_confirm, format_checklist_html, format_checklist_text
from app.engine.levels import LevelLocker, atr, trend_label
from app.engine.volume_price import VolumePriceSignal, multi_tf_volume_price
from app.models import Action, Bar, EventKind, Levels, Quote, SignalEvent, Situation


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100


def _in_zone(price: float, zone: tuple[float, float] | None) -> bool:
    if not zone:
        return False
    lo, hi = zone
    return lo <= price <= hi


def evaluate_structure(
    bars: list[Bar],
    price: float,
    levels: Levels,
    cfg: AppConfig,
) -> dict[str, bool]:
    """Support/resistance state machine flags（基于锁定档中的当前最近支撑/压力）。"""
    hold_n = max(1, cfg.levels.hold_bars)
    buf = cfg.levels.breakout_buffer_pct

    holding_support = False
    breakout_confirmed = False
    breakout_failed = False
    support_broken = False

    if levels.support is None or levels.resistance is None or len(bars) < hold_n + 2:
        return {
            "holding_support": False,
            "breakout_confirmed": False,
            "breakout_failed": False,
            "support_broken": False,
        }

    s, r = levels.support, levels.resistance
    recent = bars[-hold_n:]
    prev = bars[-(hold_n + 3) : -hold_n] or bars[:1]

    touched = any(_in_zone(b.low, levels.support_zone) or b.low <= s * (1 + buf) for b in bars[-hold_n - 5 :])
    if touched and all(b.close > s * (1 + buf * 0.5) for b in recent):
        holding_support = True

    if all(b.close > r * (1 + buf) for b in recent) and any(b.close <= r for b in prev):
        breakout_confirmed = True

    pierced = any(b.high > r * (1 + buf) for b in bars[-8:])
    if pierced and price < r * (1 - buf * 0.5) and bars[-1].close < r:
        breakout_failed = True

    if levels.support_zone and bars[-1].close < levels.support_zone[0]:
        support_broken = True
    elif bars[-1].close < s * (1 - buf):
        support_broken = True

    return {
        "holding_support": holding_support,
        "breakout_confirmed": breakout_confirmed,
        "breakout_failed": breakout_failed,
        "support_broken": support_broken,
    }


def _stops(
    action: Action,
    price: float,
    levels: Levels,
    stock: WatchStock,
    bars: list[Bar],
    cfg: AppConfig,
) -> tuple[float | None, float | None]:
    a = atr(bars) or price * 0.01
    cost = stock.cost if stock.cost and stock.cost > 0 else price

    hard_sl = cost * (1 - cfg.risk.hard_stop_pct / 100)
    hard_tp = cost * (1 + cfg.risk.hard_take_pct / 100)

    if action == "买入":
        struct_sl = levels.support * (1 - cfg.levels.zone_pct) if levels.support else price - cfg.risk.default_atr_mult_sl * a
        stop = min(struct_sl, hard_sl) if stock.cost else struct_sl
        struct_tp = levels.resistance if levels.resistance and levels.resistance > price else price + cfg.risk.default_rr * (price - stop)
        take = min(struct_tp, hard_tp) if stock.cost else struct_tp
        return round(stop, 3), round(take, 3)

    if action == "卖出":
        struct_sl = levels.resistance * (1 + cfg.levels.zone_pct) if levels.resistance else price + cfg.risk.default_atr_mult_sl * a
        struct_tp = levels.support if levels.support and levels.support < price else price - cfg.risk.default_rr * (struct_sl - price)
        return round(struct_sl, 3), round(struct_tp, 3)

    stop = hard_sl if stock.cost else (levels.support if levels.support else price - cfg.risk.default_atr_mult_sl * a)
    take = hard_tp if stock.cost else (levels.resistance if levels.resistance else price + cfg.risk.default_rr * a)
    return round(stop, 3), round(take, 3)


def decide_action(
    flags: dict[str, bool],
    trend: str,
    price: float,
    stock: WatchStock,
    cfg: AppConfig,
    vp: VolumePriceSignal | None = None,
) -> tuple[Action, str, str]:
    reasons: list[str] = []
    conf = "低"

    if stock.cost and stock.cost > 0:
        chg = _pct(price, stock.cost)
        if chg <= -cfg.risk.hard_stop_pct:
            return "卖出", f"相对成本下跌 {chg:.2f}% ≥ {cfg.risk.hard_stop_pct}%（硬止损）", "高"
        if chg >= cfg.risk.hard_take_pct:
            return "卖出", f"相对成本上涨 {chg:.2f}% ≥ {cfg.risk.hard_take_pct}%（硬止盈）", "高"

    buy_votes = 0.0
    sell_votes = 0.0

    if flags["holding_support"]:
        buy_votes += 1
        reasons.append("已站稳支撑位")
    if flags["breakout_confirmed"]:
        buy_votes += 1
        reasons.append("确认突破压力位")
    if flags["breakout_failed"]:
        sell_votes += 1
        reasons.append("压力位突破失败")
    if flags["support_broken"]:
        sell_votes += 1
        reasons.append("支撑位被跌破")

    if trend == "下跌":
        sell_votes += 0.5
        reasons.append("下跌趋势延续")
    elif trend == "上涨":
        if flags["support_broken"] or flags["breakout_failed"]:
            sell_votes += 1
            reasons.append("上涨趋势出现反转迹象")
        else:
            reasons.append("趋势偏多")
    else:
        reasons.append("震荡整理")

    # 波峰/波谷量价：提高通知参考价值
    if vp and vp.swing:
        if vp.tags:
            reasons.append("量价:" + "、".join(vp.tags))
        if vp.actionable:
            if vp.buy_score > vp.sell_score:
                buy_votes += min(1.5, vp.buy_score)
            elif vp.sell_score > vp.buy_score:
                sell_votes += min(1.5, vp.sell_score)

    if sell_votes > buy_votes and sell_votes > 0:
        conf = "高" if sell_votes >= 2 else "中"
        return "卖出", "；".join(reasons), conf
    if buy_votes > sell_votes and buy_votes > 0:
        conf = "高" if buy_votes >= 2 else "中"
        return "买入", "；".join(reasons), conf
    return "观望", "；".join(reasons) if reasons else "暂无明确结构信号", "低"


def brief_advice(sit: Situation) -> str:
    """通知流用的指示性短句。"""
    r1 = f"{sit.resistance:.2f}" if sit.resistance is not None else "-"
    s1 = f"{sit.support:.2f}" if sit.support is not None else "-"
    name = sit.name or sit.code

    if sit.checklist_mode in ("低吸", "减仓") and sit.near_key_level:
        return f"{name}：【{sit.checklist_mode}】{sit.checklist_passed}/{sit.checklist_total} → {sit.action}"

    rs = "/".join(f"{x:.2f}" for x in sit.resistances[:3]) or "-"
    ss = "/".join(f"{x:.2f}" for x in sit.supports[:3]) or "-"

    if sit.levels_reset_reason in ("突破第三压力位", "跌破第三支撑位"):
        return f"{name}：{sit.levels_reset_reason}，三档已重置 撑[{ss}] 压[{rs}]"

    if "硬止损" in (sit.reason or ""):
        return f"{name}：触及硬止损，减仓"
    if "硬止盈" in (sit.reason or ""):
        return f"{name}：触及硬止盈，减仓"

    if sit.vp_summary and sit.action in ("买入", "卖出"):
        tip = "加仓" if sit.action == "买入" else "减仓"
        return f"{name}：{tip}｜{sit.vp_summary}"

    if sit.breakout_confirmed:
        return f"{name}：站稳压力位 {r1}，加仓"
    if sit.holding_support and sit.action == "买入":
        return f"{name}：站稳支撑位 {s1}，加仓"
    if sit.support_broken:
        return f"{name}：跌破支撑位 {s1}，减仓"
    if sit.breakout_failed:
        return f"{name}：压力位 {r1} 突破失败，减仓"
    if sit.vp_summary and sit.swing:
        return f"{name}：{sit.vp_summary}"
    if sit.action == "买入":
        return f"{name}：偏多，可加仓（撑 {s1} / 压 {r1}）"
    if sit.action == "卖出":
        return f"{name}：偏空，减仓（撑 {s1} / 压 {r1}）"
    return f"{name}：观望（撑 {s1} / 压 {r1}｜三档锁）"


class SignalEngine:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.locker = LevelLocker()
        self._last_flags: dict[str, dict[str, bool]] = {}
        self._last_action: dict[str, Action] = {}
        self._last_swing_key: dict[str, str] = {}
        self._last_levels_version: dict[str, int] = {}

    def _vp_for(
        self,
        primary_bars: list[Bar],
        trigger_bars: list[Bar] | None,
        extra: dict[str, list[Bar]] | None = None,
    ) -> VolumePriceSignal:
        sets: dict[str, list[Bar]] = {self.cfg.bars.primary: primary_bars}
        if trigger_bars:
            sets[self.cfg.bars.trigger] = trigger_bars
        if extra:
            sets.update({k: v for k, v in extra.items() if v})
        return multi_tf_volume_price(sets)

    def _build_situation(
        self,
        stock: WatchStock,
        quote: Quote,
        bars: list[Bar],
        levels: Levels,
        flags: dict[str, bool],
        trend: str,
        action: Action,
        reason: str,
        conf: str,
        sl: float | None,
        tp: float | None,
        timeframe: str,
        book_version: int,
        reset_reason: str,
        vp: VolumePriceSignal,
        levels_just_reset: bool,
        checklist_bars: list[Bar] | None = None,
    ) -> Situation:
        markers: list[dict[str, Any]] = []
        for i, p in enumerate(levels.supports[:3], start=1):
            markers.append({"type": f"support{i}", "price": p, "label": f"支撑{i}"})
        for i, p in enumerate(levels.resistances[:3], start=1):
            markers.append({"type": f"resistance{i}", "price": p, "label": f"压力{i}"})

        sit = Situation(
            code=stock.code,
            name=stock.name or quote.name,
            timeframe=timeframe,
            price=quote.price,
            support=levels.support,
            resistance=levels.resistance,
            supports=list(levels.supports),
            resistances=list(levels.resistances),
            holding_support=flags["holding_support"],
            breakout_confirmed=flags["breakout_confirmed"],
            breakout_failed=flags["breakout_failed"],
            support_broken=flags["support_broken"],
            trend=trend,
            action=action,
            stop_loss=sl,
            take_profit=tp,
            reason=reason,
            confidence=conf,
            ts=time.time(),
            markers=markers,
            change_pct=quote.change_pct,
            levels_locked=True,
            levels_version=book_version,
            levels_reset_reason=reset_reason if levels_just_reset else "",
            swing=vp.swing,
            vp_summary=vp.summary,
            vp_tags=list(vp.tags),
            vp_buy_score=vp.buy_score,
            vp_sell_score=vp.sell_score,
        )

        # 问财式三重确认：接近支撑/压力时覆盖建议（优先用日线/更长周期K）
        tc_bars = checklist_bars if checklist_bars else bars
        tc = build_triple_confirm(sit, tc_bars, quote)
        sit.checklist_mode = tc.mode
        sit.checklist_text = format_checklist_text(tc, sit.name)
        sit.checklist_html = format_checklist_html(tc, sit.name)
        sit.checklist_passed = tc.passed
        sit.checklist_total = tc.total
        sit.near_key_level = tc.near_level
        if tc.near_level and tc.mode in ("低吸", "减仓"):
            # 关键位附近以三重确认为准（含观望）
            sit.action = tc.action
            sit.confidence = "高" if tc.all_ok else ("中" if tc.passed >= 2 else "低")
            sit.reason = f"{tc.headline}；" + sit.reason
        return sit

    def analyze(
        self,
        stock: WatchStock,
        quote: Quote,
        primary_bars: list[Bar],
        trigger_bars: list[Bar] | None = None,
        extra_bars: dict[str, list[Bar]] | None = None,
    ) -> Situation:
        bars = primary_bars
        price = quote.price
        levels, book, just_reset = self.locker.resolve(
            stock.code, bars, price, self.cfg.levels, timeframe=self.cfg.bars.primary
        )
        flags = evaluate_structure(bars, price, levels, self.cfg)
        if trigger_bars and len(trigger_bars) >= 5:
            tflags = evaluate_structure(trigger_bars, price, levels, self.cfg)
            if tflags["support_broken"]:
                flags["support_broken"] = True
            if tflags["breakout_failed"]:
                flags["breakout_failed"] = True
            if tflags["breakout_confirmed"]:
                flags["breakout_confirmed"] = True

        vp = self._vp_for(primary_bars, trigger_bars, extra_bars)
        trend = trend_label(bars)
        action, reason, conf = decide_action(flags, trend, price, stock, self.cfg, vp)
        if just_reset and book.reset_reason:
            reason = f"{book.reset_reason}；" + reason
        sl, tp = _stops(action, price, levels, stock, bars, self.cfg)

        sit = self._build_situation(
            stock,
            quote,
            bars,
            levels,
            flags,
            trend,
            action,
            reason,
            conf,
            sl,
            tp,
            self.cfg.bars.primary,
            book.version,
            book.reset_reason,
            vp,
            just_reset,
            checklist_bars=(extra_bars or {}).get("day") or bars,
        )
        self._last_flags[stock.code] = flags
        self._last_action[stock.code] = action
        return sit

    def analyze_for_timeframe(
        self,
        stock: WatchStock,
        quote: Quote,
        bars: list[Bar],
        timeframe: str,
    ) -> Situation:
        """面板展示：按所选周期使用该周期的锁定三档（与其它周期相互独立）。"""
        price = quote.price
        levels, book, just_reset = self.locker.resolve(
            stock.code, bars, price, self.cfg.levels, timeframe=timeframe
        )
        flags = evaluate_structure(bars, price, levels, self.cfg)
        vp = multi_tf_volume_price({timeframe: bars})
        trend = trend_label(bars)
        action, reason, conf = decide_action(flags, trend, price, stock, self.cfg, vp)
        sl, tp = _stops(action, price, levels, stock, bars, self.cfg)
        return self._build_situation(
            stock,
            quote,
            bars,
            levels,
            flags,
            trend,
            action,
            reason,
            conf,
            sl,
            tp,
            timeframe,
            book.version,
            book.reset_reason,
            vp,
            just_reset,
        )

    def pop_swing_event(self, sit: Situation) -> SignalEvent | None:
        """同一波峰/波谷只通知一次。"""
        if not sit.swing or not sit.vp_summary:
            return None
        key = f"{sit.code}:{sit.swing}:{sit.vp_tags}:{round(sit.price, 2)}"
        # 用 summary 中的 swing_price 更稳：从 tags+action
        key = f"{sit.code}:{sit.swing}:{sit.vp_summary[:40]}"
        if self._last_swing_key.get(sit.code) == key:
            return None
        if not (sit.vp_buy_score >= 0.9 or sit.vp_sell_score >= 0.9 or sit.action in ("买入", "卖出")):
            return None
        self._last_swing_key[sit.code] = key
        action: Action = sit.action
        if action == "观望":
            if sit.vp_buy_score > sit.vp_sell_score:
                action = "买入"
            elif sit.vp_sell_score > sit.vp_buy_score:
                action = "卖出"
        kind: EventKind = "buy" if action == "买入" else "sell" if action == "卖出" else "swing"
        title = brief_advice(sit) if sit.action != "观望" else f"{sit.name}：{sit.vp_summary}"
        return SignalEvent(
            id=str(uuid.uuid4()),
            kind=kind,
            code=sit.code,
            name=sit.name,
            action=action,
            price=sit.price,
            stop_loss=sit.stop_loss,
            take_profit=sit.take_profit,
            title=title,
            detail=sit.vp_summary or sit.reason,
            ts=sit.ts,
            marker={
                "type": "swing_peak" if sit.swing == "peak" else "swing_trough",
                "price": sit.price,
                "label": "峰" if sit.swing == "peak" else "谷",
            },
        )

    def pop_levels_reset_event(self, sit: Situation) -> SignalEvent | None:
        if not sit.levels_reset_reason:
            return None
        prev = self._last_levels_version.get(sit.code)
        if prev == sit.levels_version:
            return None
        self._last_levels_version[sit.code] = sit.levels_version
        rs = "/".join(f"{x:.2f}" for x in sit.resistances[:3])
        ss = "/".join(f"{x:.2f}" for x in sit.supports[:3])
        title = f"{sit.name}：{sit.levels_reset_reason} → 撑[{ss}] 压[{rs}]"
        return SignalEvent(
            id=str(uuid.uuid4()),
            kind="levels_reset",
            code=sit.code,
            name=sit.name,
            action="观望",
            price=sit.price,
            stop_loss=sit.stop_loss,
            take_profit=sit.take_profit,
            title=title,
            detail=title,
            ts=sit.ts,
        )

    def events_from_situation(self, sit: Situation, force_situation: bool = False) -> list[SignalEvent]:
        events: list[SignalEvent] = []
        ts = sit.ts
        code = sit.code
        brief = brief_advice(sit)

        reset_ev = self.pop_levels_reset_event(sit)
        if reset_ev:
            events.append(reset_ev)

        swing_ev = self.pop_swing_event(sit)
        if swing_ev:
            if sit.checklist_html:
                swing_ev.html = sit.checklist_html
                swing_ev.detail = sit.checklist_text or swing_ev.detail
            events.append(swing_ev)

        def _rich(ev: SignalEvent) -> SignalEvent:
            if sit.checklist_html and sit.near_key_level:
                ev.html = sit.checklist_html
                ev.detail = sit.checklist_text or ev.detail
                if sit.checklist_mode:
                    ev.kind = "checklist" if ev.kind == "hold" else ev.kind
            return ev

        if force_situation:
            kind: EventKind = "hold"
            if sit.action == "买入":
                kind = "buy"
            elif sit.action == "卖出":
                kind = "sell"
            elif sit.near_key_level and sit.checklist_text:
                kind = "checklist"
            events.append(
                _rich(
                    SignalEvent(
                        id=str(uuid.uuid4()),
                        kind=kind,
                        code=code,
                        name=sit.name,
                        action=sit.action,
                        price=sit.price,
                        stop_loss=sit.stop_loss,
                        take_profit=sit.take_profit,
                        title=brief,
                        detail=sit.checklist_text or brief,
                        ts=ts,
                        html=sit.checklist_html if sit.near_key_level else "",
                    )
                )
            )
            return events

        if sit.action in ("买入", "卖出") and not swing_ev:
            kind_map = {"买入": "buy", "卖出": "sell"}
            events.append(
                _rich(
                    SignalEvent(
                        id=str(uuid.uuid4()),
                        kind=kind_map[sit.action],  # type: ignore[arg-type]
                        code=code,
                        name=sit.name,
                        action=sit.action,
                        price=sit.price,
                        stop_loss=sit.stop_loss,
                        take_profit=sit.take_profit,
                        title=brief,
                        detail=sit.checklist_text or brief,
                        ts=ts,
                        marker=None,
                        html=sit.checklist_html if sit.near_key_level else "",
                    )
                )
            )
        elif sit.near_key_level and sit.checklist_text and not swing_ev:
            # 接近关键位但观望：仍推送三重确认明细
            events.append(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    kind="checklist",
                    code=code,
                    name=sit.name,
                    action=sit.action,
                    price=sit.price,
                    stop_loss=sit.stop_loss,
                    take_profit=sit.take_profit,
                    title=brief,
                    detail=sit.checklist_text,
                    ts=ts,
                    html=sit.checklist_html,
                )
            )
        return events
