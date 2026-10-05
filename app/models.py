from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Action = Literal["买入", "卖出", "观望"]
EventKind = Literal[
    "situation",
    "buy",
    "sell",
    "hold",
    "hard_stop",
    "hard_take",
    "swing",
    "levels_reset",
    "checklist",
]


@dataclass
class Bar:
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    avg: float | None = None  # 分时均价（累计额/累计量）


@dataclass
class Quote:
    code: str
    name: str
    price: float
    prev_close: float
    change_pct: float
    ts: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    change: float | None = None
    turnover: float | None = None
    amplitude: float | None = None


@dataclass
class Levels:
    support: float | None
    resistance: float | None
    support_zone: tuple[float, float] | None = None
    resistance_zone: tuple[float, float] | None = None
    supports: list[float] = field(default_factory=list)  # S1,S2,S3 近→远（锁定档）
    resistances: list[float] = field(default_factory=list)  # R1,R2,R3 近→远（锁定档）


@dataclass
class Situation:
    code: str
    name: str
    timeframe: str
    price: float
    support: float | None
    resistance: float | None
    supports: list[float] = field(default_factory=list)
    resistances: list[float] = field(default_factory=list)
    holding_support: bool = False
    breakout_confirmed: bool = False
    breakout_failed: bool = False
    support_broken: bool = False
    trend: str = "震荡"  # 上涨 / 下跌 / 震荡
    action: Action = "观望"
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""
    confidence: str = "低"  # 低 / 中 / 高
    ts: float = 0.0
    markers: list[dict[str, Any]] = field(default_factory=list)
    change_pct: float | None = None
    # 锁定三档
    levels_locked: bool = True
    levels_version: int = 1
    levels_reset_reason: str = ""
    # 量价 / 波峰波谷
    swing: str = ""
    vp_summary: str = ""
    vp_tags: list[str] = field(default_factory=list)
    vp_buy_score: float = 0.0
    vp_sell_score: float = 0.0
    # 问财式三重确认
    checklist_mode: str = ""
    checklist_text: str = ""
    checklist_html: str = ""
    checklist_passed: int = 0
    checklist_total: int = 0
    near_key_level: bool = False


@dataclass
class SignalEvent:
    id: str
    kind: EventKind
    code: str
    name: str
    action: Action
    price: float
    stop_loss: float | None
    take_profit: float | None
    title: str
    detail: str
    ts: float
    marker: dict[str, Any] | None = None
    html: str = ""  # 可选富文本（三重确认）
