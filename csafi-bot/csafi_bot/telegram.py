"""Telegram: notifications, plus optional intake (send a reel link to the bot, no public URL needed)."""
import logging
import threading
import time
from typing import Callable

import httpx

from . import links

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, http_timeout: float = 20, **params) -> dict:
        r = httpx.post(API.format(token=self.token, method=method), json=params, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram error"))
        return data

    def send(self, text: str, chat_id: str | None = None) -> bool:
        if not self.token or not (chat_id or self.chat_id):
            return False
        try:
            self._call("sendMessage", chat_id=chat_id or self.chat_id, text=text[:4000], disable_web_page_preview=True)
            return True
        except Exception as e:  # noqa: BLE001 - notifications must never break the pipeline
            log.warning("Telegram send failed: %s", str(e)[:200])
            return False

    def check(self) -> str:
        if not self.token:
            return "not configured"
        try:
            me = self._call("getMe")["result"]
            return f"ok (@{me.get('username')})"
        except Exception as e:  # noqa: BLE001
            return f"error: {str(e)[:120]}"


def start_intake(
    tg: Telegram,
    allowed_users: set[str],
    on_link: Callable[[str, str], tuple[int, bool]],
    stop: threading.Event,
) -> threading.Thread:
    """Long-polls getUpdates. Any instagram reel link sent by an allowed user is queued."""

    def loop() -> None:
        offset = 0
        while not stop.is_set():
            try:
                data = tg._call("getUpdates", http_timeout=40, offset=offset, timeout=30, allowed_updates=["message"])
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    user = str((msg.get("from") or {}).get("id", ""))
                    chat = str((msg.get("chat") or {}).get("id", ""))
                    text = msg.get("text") or msg.get("caption") or ""
                    if allowed_users and user not in allowed_users:
                        tg.send("Sorry, you are not on the allow-list for this bot.", chat)
                        continue
                    if text.strip() in ("/start", "/id"):
                        tg.send(f"Send me an Instagram reel link to file it. Your user ID is {user}, chat ID {chat}.", chat)
                        continue
                    found = links.find_instagram_links(text)
                    if not found:
                        if text:
                            tg.send("I could not find an Instagram reel link in that message.", chat)
                        continue
                    for url in found:
                        reel_id, created = on_link(url, f"telegram:{user}")
                        tg.send(f"Queued reel #{reel_id}." if created else f"Reel #{reel_id} is already in the library.", chat)
            except Exception as e:  # noqa: BLE001 - keep polling through network blips
                log.warning("Telegram intake error: %s", str(e)[:200])
                stop.wait(10)

    t = threading.Thread(target=loop, name="telegram-intake", daemon=True)
    t.start()
    return t
