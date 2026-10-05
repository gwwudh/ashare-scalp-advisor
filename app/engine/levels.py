from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal

from app.config import LevelsConfig
from app.models import Bar, Levels

CN_TZ = timezone(timedelta(hours=8))


def session_date_str(ts: float | None = None) -> str:
    """A股交易日（按北京时间日历日）。"""
    if ts is None:
        dt = datetime.now(CN_TZ)
    else:
        dt = datetime.fromtimestamp(ts, CN_TZ)
    return dt.strftime("%Y-%m-%d")


def _swing_pivots(bars: list[Bar], left: int = 2, right: int = 2) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    n = len(bars)
    for i in range(left, n - right):
        window = bars[i - left : i + right + 1]
        h = bars[i].high
        l = bars[i].low
        if all(h >= b.high for b in window):
            highs.append(h)
        if all(l <= b.low for b in window):
            lows.append(l)
    return highs, lows


def atr(bars: list[Bar], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    use = trs[-period:] if len(trs) >= period else trs
    return sum(use) / len(use) if use else 0.0


def _dedupe_near(levels: list[float], min_gap_pct: float = 0.0015) -> list[float]:
    out: list[float] = []
    for x in levels:
        if not out or abs(x - out[-1]) / max(abs(out[-1]), 1e-9) >= min_gap_pct:
            out.append(x)
    return out


def _fill_resistances(candidates: list[float], price: float, window: list[Bar], need: int = 3) -> list[float]:
    above = sorted({round(x, 4) for x in candidates if x > price})
    above = _dedupe_near(above)
    if len(above) < need:
        highs = sorted({round(b.high, 4) for b in window if b.high > price})
        for h in highs:
            if h not in above:
                above.append(h)
            if len(above) >= need:
                break
        above = sorted(_dedupe_near(sorted(above)))
    if not above:
        top = max(b.high for b in window)
        step = max(top * 0.002, 0.01)
        above = [round(price + step * (i + 1), 3) for i in range(need)]
    while len(above) < need:
        last = above[-1]
        above.append(round(last * 1.004, 3))
    return above[:need]


def _fill_supports(candidates: list[float], price: float, window: list[Bar], need: int = 3) -> list[float]:
    below = sorted({round(x, 4) for x in candidates if x < price}, reverse=True)
    below = _dedupe_near(below)
    if len(below) < need:
        lows = sorted({round(b.low, 4) for b in window if b.low < price}, reverse=True)
        for lv in lows:
            if lv not in below:
                below.append(lv)
            if len(below) >= need:
                break
        below = _dedupe_near(sorted(set(below), reverse=True))
    if not below:
        bottom = min(b.low for b in window)
        step = max(price * 0.002, 0.01)
        below = [round(price - step * (i + 1), 3) for i in range(need)]
    while len(below) < need:
        last = below[-1]
        below.append(round(last * 0.996, 3))
    return below[:need]


def detect_levels(bars: list[Bar], price: float, cfg: LevelsConfig) -> Levels:
    """一次性计算三级支撑/压力（近→远）。"""
    if not bars:
        return Levels(support=None, resistance=None, supports=[], resistances=[])

    window = bars[-cfg.lookback :] if len(bars) > cfg.lookback else bars
    highs, lows = _swing_pivots(window)
    if not highs:
        highs = [max(b.high for b in window)]
    if not lows:
        lows = [min(b.low for b in window)]

    resistances = _fill_resistances(highs, price, window, 3)
    supports = _fill_supports(lows, price, window, 3)

    support = supports[0] if supports else None
    resistance = resistances[0] if resistances else None
    sz = (support * (1 - cfg.zone_pct), support * (1 + cfg.zone_pct)) if support else None
    rz = (resistance * (1 - cfg.zone_pct), resistance * (1 + cfg.zone_pct)) if resistance else None
    return Levels(
        support=support,
        resistance=resistance,
        support_zone=sz,
        resistance_zone=rz,
        supports=supports,
        resistances=resistances,
    )


def active_nearest_sr(
    price: float,
    supports: list[float],
    resistances: list[float],
    zone_pct: float,
) -> Levels:
    """
    在锁定的三档里取「当前价下方最近支撑 / 上方最近压力」用于结构判断。
    锁定档位本身不变。
    """
    below = [s for s in supports if s < price]
    above = [r for r in resistances if r > price]
    support = max(below) if below else (min(supports) if supports else None)
    resistance = min(above) if above else (max(resistances) if resistances else None)
    sz = (support * (1 - zone_pct), support * (1 + zone_pct)) if support else None
    rz = (resistance * (1 - zone_pct), resistance * (1 + zone_pct)) if resistance else None
    return Levels(
        support=support,
        resistance=resistance,
        support_zone=sz,
        resistance_zone=rz,
        supports=list(supports),
        resistances=list(resistances),
    )


ResetReason = Literal["", "开盘锁定", "突破第三压力位", "跌破第三支撑位"]


@dataclass
class LockedBook:
    session_date: str
    supports: list[float] = field(default_factory=list)  # S1..S3 近→远（锁定时）
    resistances: list[float] = field(default_factory=list)  # R1..R3 近→远（锁定时）
    version: int = 1
    locked_at: float = 0.0
    reset_reason: ResetReason = "开盘锁定"
    anchor_price: float = 0.0


class LevelLocker:
    """
    按「标的 + 周期」分别锁定三级支撑/压力（当日首次计算）；
    该周期内股价涨跌档位不变；
    仅当上涨突破第三压力，或下跌跌破第三支撑时，整组更换。
    切换周期使用另一套锁定账本。
    """

    def __init__(self) -> None:
        self._books: dict[str, LockedBook] = {}

    @staticmethod
    def _key(code: str, timeframe: str = "") -> str:
        tf = (timeframe or "default").strip().lower() or "default"
        return f"{code}:{tf}"

    def get(self, code: str, timeframe: str = "") -> LockedBook | None:
        return self._books.get(self._key(code, timeframe))

    def clear(self, code: str | None = None, timeframe: str | None = None) -> None:
        if code is None:
            self._books.clear()
            return
        if timeframe is None:
            prefix = f"{code}:"
            for k in list(self._books):
                if k.startswith(prefix) or k == code:
                    self._books.pop(k, None)
        else:
            self._books.pop(self._key(code, timeframe), None)

    def resolve(
        self,
        code: str,
        bars: list[Bar],
        price: float,
        cfg: LevelsConfig,
        now: float | None = None,
        timeframe: str = "",
    ) -> tuple[Levels, LockedBook, bool]:
        """
        返回 (用于结构判断的 Levels, 锁定账本, 是否刚发生重置)。
        Levels.supports/resistances 始终为该周期锁定的三档原值。
        """
        import time

        ts = now if now is not None else time.time()
        today = session_date_str(ts)
        key = self._key(code, timeframe)
        book = self._books.get(key)
        reset = False
        buf = cfg.breakout_buffer_pct

        def _new_book(reason: ResetReason) -> LockedBook:
            raw = detect_levels(bars, price, cfg)
            return LockedBook(
                session_date=today,
                supports=list(raw.supports[:3]),
                resistances=list(raw.resistances[:3]),
                version=(book.version + 1) if book and book.session_date == today else 1,
                locked_at=ts,
                reset_reason=reason,
                anchor_price=price,
            )

        if book is None or book.session_date != today or len(book.supports) < 3 or len(book.resistances) < 3:
            book = _new_book("开盘锁定")
            self._books[key] = book
            reset = True
        else:
            r3 = book.resistances[2]
            s3 = book.supports[2]
            if price > r3 * (1 + buf):
                book = _new_book("突破第三压力位")
                self._books[key] = book
                reset = True
            elif price < s3 * (1 - buf):
                book = _new_book("跌破第三支撑位")
                self._books[key] = book
                reset = True

        levels = active_nearest_sr(price, book.supports, book.resistances, cfg.zone_pct)
        return levels, book, reset


def trend_label(bars: list[Bar]) -> str:
    if len(bars) < 20:
        return "震荡"
    closes = [b.close for b in bars]
    ma_fast = sum(closes[-5:]) / 5
    ma_slow = sum(closes[-20:]) / 20
    last = closes[-1]
    slope = closes[-1] - closes[-10]
    if last > ma_fast > ma_slow and slope > 0:
        return "上涨"
    if last < ma_fast < ma_slow and slope < 0:
        return "下跌"
    return "震荡"
