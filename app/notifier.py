from __future__ import annotations

import logging
from typing import Any

import httpx

from app.models import SignalEvent

log = logging.getLogger("notifier")


class Notifier:
    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = (webhook_url or "").strip()

    def emit(self, ev: SignalEvent) -> None:
        line = f"[{ev.kind}] {ev.title} @ {ev.price:.3f} | {ev.detail}"
        log.info(line)
        print(line, flush=True)
        if not self.webhook_url:
            return
        try:
            # 通用文本 webhook（飞书/企微需各自调格式；先发纯文本 JSON）
            payload: dict[str, Any] = {
                "msg_type": "text",
                "content": {"text": line},
                "msgtype": "text",
                "text": {"content": line},
            }
            httpx.post(self.webhook_url, json=payload, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook failed: %s", exc)
