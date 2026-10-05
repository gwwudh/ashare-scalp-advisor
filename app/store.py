from __future__ import annotations

import threading
import time
from dataclasses import asdict
from typing import Any

from app.models import Bar, Quote, SignalEvent, Situation


class StateStore:
    def __init__(self, max_events: int = 8) -> None:
        self._lock = threading.RLock()
        self.quotes: dict[str, Quote] = {}
        # code -> { "5m"|"1m"|"fs"|"day": bars }
        self.klines: dict[str, dict[str, list[Bar]]] = {}
        self.situations: dict[str, Situation] = {}
        self.events: list[SignalEvent] = []
        self.chart_markers: dict[str, list[dict[str, Any]]] = {}
        self._max_events = max_events
        self._cooldown_until: dict[str, float] = {}

    # 兼容旧字段访问
    @property
    def kline_primary(self) -> dict[str, list[Bar]]:
        return {c: m.get("5m", []) for c, m in self.klines.items()}

    @property
    def kline_trigger(self) -> dict[str, list[Bar]]:
        return {c: m.get("1m", []) for c, m in self.klines.items()}

    def set_quote(self, q: Quote) -> None:
        with self._lock:
            self.quotes[q.code] = q

    def set_kline(self, code: str, tf: str, bars: list[Bar]) -> None:
        with self._lock:
            self.klines.setdefault(code, {})[tf] = bars

    def set_klines(
        self,
        code: str,
        primary: list[Bar],
        trigger: list[Bar],
        fs: list[Bar] | None = None,
        day: list[Bar] | None = None,
    ) -> None:
        with self._lock:
            bucket = self.klines.setdefault(code, {})
            bucket["5m"] = primary
            bucket["1m"] = trigger
            if fs is not None:
                bucket["fs"] = fs
            if day is not None:
                bucket["day"] = day

    def get_bars(self, code: str, tf: str) -> list[Bar]:
        with self._lock:
            return list(self.klines.get(code, {}).get(tf, []))

    def set_situation(self, sit: Situation) -> None:
        with self._lock:
            self.situations[sit.code] = sit

    def can_notify(self, key: str, cooldown_sec: int) -> bool:
        with self._lock:
            now = time.time()
            until = self._cooldown_until.get(key, 0)
            if now < until:
                return False
            self._cooldown_until[key] = now + cooldown_sec
            return True

    def add_event(self, ev: SignalEvent) -> None:
        with self._lock:
            self.events.append(ev)
            if len(self.events) > self._max_events:
                self.events = self.events[-self._max_events :]
            if ev.marker:
                arr = self.chart_markers.setdefault(ev.code, [])
                arr.append(ev.marker)
                if len(arr) > 50:
                    self.chart_markers[ev.code] = arr[-50:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "quotes": {k: asdict(v) for k, v in self.quotes.items()},
                "situations": {k: asdict(v) for k, v in self.situations.items()},
                "events": [asdict(e) for e in self.events[-8:]],
                "markers": {k: list(v) for k, v in self.chart_markers.items()},
            }

    def kline_payload(self, code: str, which: str = "5m") -> list[dict[str, Any]]:
        tf = which
        if which in ("primary", "5m"):
            tf = "5m"
        elif which in ("trigger", "1m"):
            tf = "1m"
        elif which in ("fs", "minute"):
            tf = "fs"
        elif which in ("day",):
            tf = "day"
        with self._lock:
            bars = self.klines.get(code, {}).get(tf) or []
            out: list[dict[str, Any]] = []
            for b in bars:
                row: dict[str, Any] = {
                    "time": b.time,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                if b.avg is not None:
                    row["avg"] = b.avg
                out.append(row)
            return out
