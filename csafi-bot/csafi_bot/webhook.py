"""Meta Instagram messaging webhook: signature check and payload parsing.

Shared reels arrive as attachments of type `ig_reel` (payload: url, title, reel_video_id).
Older payloads use `share` / `ig_post`; pasted links arrive as message text.
"""
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from . import links

REEL_ATTACHMENT_TYPES = {"ig_reel", "reel", "share", "ig_post", "video"}


@dataclass
class IncomingReel:
    reel_key: str
    source: str
    source_url: str | None
    media_url: str | None
    caption: str | None
    sender_id: str | None


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_payload(payload: dict[str, Any], allowed_senders: set[str] | None = None) -> list[IncomingReel]:
    out: list[IncomingReel] = []
    if payload.get("object") not in ("instagram", "page"):
        return out
    for entry in payload.get("entry") or []:
        for event in entry.get("messaging") or []:
            msg = event.get("message") or {}
            if msg.get("is_echo") or msg.get("is_deleted") or msg.get("is_unsupported"):
                continue
            sender = str((event.get("sender") or {}).get("id") or "") or None
            if allowed_senders and sender not in allowed_senders:
                continue
            out.extend(_from_message(msg, sender))
    return _dedupe(out)


def _from_message(msg: dict[str, Any], sender: str | None) -> list[IncomingReel]:
    found: list[IncomingReel] = []
    for att in msg.get("attachments") or []:
        kind = att.get("type")
        if kind not in REEL_ATTACHMENT_TYPES:
            continue
        p = att.get("payload") or {}
        url = p.get("url")
        vid = p.get("reel_video_id") or p.get("ig_post_media_id") or p.get("id")
        title = p.get("title")
        shortcode = links.instagram_shortcode(url or "")
        if not (vid or shortcode or url):
            continue
        key = links.reel_key(reel_video_id=str(vid) if vid else None, shortcode=shortcode, url=url)
        found.append(
            IncomingReel(
                reel_key=key,
                source=f"webhook:{kind}",
                source_url=url if shortcode else None,
                media_url=None if shortcode else url,
                caption=title,
                sender_id=sender,
            )
        )
    for url in links.find_instagram_links(msg.get("text") or ""):
        sc = links.instagram_shortcode(url)
        found.append(
            IncomingReel(
                reel_key=links.reel_key(shortcode=sc),
                source="webhook:text",
                source_url=url,
                media_url=None,
                caption=None,
                sender_id=sender,
            )
        )
    return found


def _dedupe(items: list[IncomingReel]) -> list[IncomingReel]:
    seen: dict[str, IncomingReel] = {}
    for it in items:
        seen.setdefault(it.reel_key, it)
    return list(seen.values())
