from __future__ import annotations

import logging
import threading
import time

from app.config import AppConfig, WatchStock, load_watchlist
from app.engine.signals import SignalEngine
from app.llm import LLMAdvisor
from app.market.tencent import TencentMarket
from app.models import Situation
from app.notifier import Notifier
from app.store import StateStore

log = logging.getLogger("poller")


class MarketPoller:
    def __init__(
        self,
        cfg: AppConfig,
        store: StateStore,
        engine: SignalEngine,
        notifier: Notifier,
        stocks: list[WatchStock] | None = None,
        llm: LLMAdvisor | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.notifier = notifier
        self.llm = llm
        self.stocks = stocks or load_watchlist()
        self.market = TencentMarket()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_situation_bucket: dict[str, int] = {}
        self._last_action_notified: dict[str, str] = {}
        self._last_minute_notified: dict[str, int] = {}

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._loop_quotes, name="quotes", daemon=True),
            threading.Thread(target=self._loop_klines, name="klines", daemon=True),
            threading.Thread(target=self._loop_situation, name="situation", daemon=True),
        ]
        for t in self._threads:
            t.start()
        log.info(
            "poller started for %d stocks; llm=%s",
            len(self.stocks),
            "on" if (self.llm and self.llm.enabled and self.cfg.notify.use_llm) else "off",
        )

    def stop(self) -> None:
        self._stop.set()
        self.market.close()

    def reload_watchlist(self, stocks: list[WatchStock]) -> None:
        self.stocks = stocks

    def refresh_one(self, code: str) -> None:
        stock = next((s for s in self.stocks if s.code == code), None)
        if not stock:
            return
        try:
            primary = self.market.get_kline(
                stock.code, self.cfg.bars.primary, self.cfg.bars.primary_limit
            )
            trigger = self.market.get_kline(
                stock.code, self.cfg.bars.trigger, self.cfg.bars.trigger_limit
            )
            fs = self.market.get_kline(stock.code, "fs", 300)
            day = self.market.get_kline(stock.code, "day", 180)
            if primary:
                self.store.set_klines(stock.code, primary, trigger, fs=fs, day=day)
            q = self.market.get_quote(stock.code)
            q.code = stock.code
            if stock.name:
                q.name = stock.name
            self.store.set_quote(q)
            if primary:
                sit = self.engine.analyze(
                    stock, q, primary, trigger, extra_bars={"fs": fs, "day": day}
                )
                self.store.set_situation(sit)
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_one %s failed: %s", code, exc)

    def _enrich_with_llm(self, sit: Situation, force_situation: bool) -> list:
        events = self.engine.events_from_situation(sit, force_situation=force_situation)
        if not events:
            return events
        if not (self.cfg.notify.use_llm and self.llm and self.llm.enabled):
            return events
        advice = self.llm.advise(sit)
        if not advice:
            return events
        # 去掉模型可能重复输出的指令词前缀
        for prefix in ("买入：", "卖出：", "观望：", "买入:", "卖出:", "观望:", "买入 ", "卖出 ", "观望 "):
            if advice.startswith(prefix):
                advice = advice[len(prefix) :].strip()
                break
        for ev in events:
            # 接近关键位：保留三重确认明细，LLM 只作标题摘要
            if sit.near_key_level and (sit.checklist_html or sit.checklist_text):
                ev.title = advice
                if not ev.detail:
                    ev.detail = sit.checklist_text
                if not ev.html:
                    ev.html = sit.checklist_html
            else:
                ev.title = advice
                ev.detail = advice
        return events

    def _loop_quotes(self) -> None:
        while not self._stop.is_set():
            for s in list(self.stocks):
                try:
                    q = self.market.get_quote(s.code)
                    q.code = s.code
                    if s.name:
                        q.name = s.name
                    self.store.set_quote(q)
                    primary = self.store.get_bars(s.code, "5m")
                    trigger = self.store.get_bars(s.code, "1m")
                    fs = self.store.get_bars(s.code, "fs")
                    day = self.store.get_bars(s.code, "day")
                    if primary:
                        sit = self.engine.analyze(
                            s, q, primary, trigger, extra_bars={"fs": fs, "day": day}
                        )
                        self.store.set_situation(sit)
                        self._maybe_action_notify(sit)
                except Exception as exc:  # noqa: BLE001
                    log.warning("quote %s failed: %s", s.code, exc)
            self._stop.wait(self.cfg.poll.quote_interval_sec)

    def _loop_klines(self) -> None:
        while not self._stop.is_set():
            for s in list(self.stocks):
                try:
                    primary = self.market.get_kline(
                        s.code,
                        self.cfg.bars.primary,
                        self.cfg.bars.primary_limit,
                    )
                    trigger = self.market.get_kline(
                        s.code,
                        self.cfg.bars.trigger,
                        self.cfg.bars.trigger_limit,
                    )
                    fs = self.market.get_kline(s.code, "fs", 300)
                    day = self.market.get_kline(s.code, "day", 180)
                    if primary or fs or day:
                        self.store.set_klines(
                            s.code,
                            primary or self.store.get_bars(s.code, "5m"),
                            trigger or self.store.get_bars(s.code, "1m"),
                            fs=fs,
                            day=day,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("kline %s failed: %s", s.code, exc)
            self._stop.wait(self.cfg.poll.kline_interval_sec)

    def _emit_events(self, sit: Situation, force_situation: bool) -> None:
        for ev in self._enrich_with_llm(sit, force_situation=force_situation):
            self.store.add_event(ev)
            self.notifier.emit(ev)

    def _loop_situation(self) -> None:
        """常规节奏：每分钟每只股票一条局面通知。"""
        while not self._stop.is_set():
            now = time.time()
            bucket = int(now // 60)
            for s in list(self.stocks):
                if self._last_situation_bucket.get(s.code) == bucket:
                    continue
                q = self.store.quotes.get(s.code)
                primary = self.store.get_bars(s.code, "5m")
                trigger = self.store.get_bars(s.code, "1m")
                fs = self.store.get_bars(s.code, "fs")
                day = self.store.get_bars(s.code, "day")
                if not q or not primary:
                    continue
                try:
                    sit = self.engine.analyze(
                        s, q, primary, trigger, extra_bars={"fs": fs, "day": day}
                    )
                    self.store.set_situation(sit)
                    self._last_situation_bucket[s.code] = bucket
                    # 每分钟一条（即使操作建议未变）
                    if self._last_minute_notified.get(s.code) != bucket:
                        self._emit_events(sit, force_situation=True)
                        self._last_minute_notified[s.code] = bucket
                        self._last_action_notified[s.code] = sit.action
                except Exception as exc:  # noqa: BLE001
                    log.warning("situation %s failed: %s", s.code, exc)
            self._stop.wait(2.0)

    def _maybe_action_notify(self, sit: Situation) -> None:
        """操作建议变化时即时通知（买入/卖出/观望相互切换均触发）。"""
        prev = self._last_action_notified.get(sit.code)
        if prev is None:
            # 启动后先记状态，交给分钟节奏发首条，避免刷屏
            self._last_action_notified[sit.code] = sit.action
            return
        if prev == sit.action:
            return
        log.info("action change %s: %s -> %s", sit.code, prev, sit.action)
        self._emit_events(sit, force_situation=True)
        self._last_action_notified[sit.code] = sit.action
        # 本分钟已有即时通知，避免分钟循环再重复一条相同建议
        bucket = int(time.time() // 60)
        self._last_minute_notified[sit.code] = bucket
        self._last_situation_bucket[sit.code] = bucket
