"""Alerting: send a message when something stays broken, and again when it recovers."""
from __future__ import annotations

import os
import sys
from typing import Callable

import requests

Sender = Callable[[str], None]


def stdout_sender(text: str) -> None:
    print(f"[alert] {text}", file=sys.stderr, flush=True)


def webhook_sender(url: str) -> Sender:
    def send(text: str) -> None:
        # "content" is what Discord reads, "text" is what Slack reads; extra keys are ignored.
        requests.post(url, json={"content": text, "text": text}, timeout=10).raise_for_status()
    return send


def telegram_sender(token: str, chat_id: str) -> Sender:
    def send(text: str) -> None:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10).raise_for_status()
    return send


def sender_from_env() -> Sender:
    """Telegram if configured, else a generic webhook, else just print."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return telegram_sender(token, chat)
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if url:
        return webhook_sender(url)
    return stdout_sender


class Alerter:
    """Debounced alert state machine.

    A problem must be seen `fail_after` polls in a row before it alerts (so one
    slow SSH reply does not page anyone). When it clears, a RESOLVED message follows.
    """

    def __init__(self, send: Sender, fail_after: int = 2):
        self.send = send
        self.fail_after = fail_after
        self.bad_count: dict[str, int] = {}
        self.firing: dict[str, str] = {}
        self.sent = 0

    def _safe_send(self, text: str) -> None:
        try:
            self.send(text)
            self.sent += 1
        except Exception as exc:                                  # a broken webhook must not kill the watcher
            print(f"[alert] could not deliver message: {exc}", file=sys.stderr, flush=True)

    def update(self, current: dict[str, str]) -> None:
        """`current` maps problem key -> message for everything wrong right now."""
        for key, msg in current.items():
            self.bad_count[key] = self.bad_count.get(key, 0) + 1
            if self.bad_count[key] >= self.fail_after and key not in self.firing:
                self.firing[key] = msg
                self._safe_send(f"ALERT: {msg}")
        for key in list(self.bad_count):
            if key not in current:
                del self.bad_count[key]
                if key in self.firing:
                    self._safe_send(f"RESOLVED: {self.firing.pop(key)}")
