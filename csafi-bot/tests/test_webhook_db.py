import hashlib
import hmac
import json

from csafi_bot.db import DB
from csafi_bot.webhook import parse_payload, verify_signature


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(attachments=None, text=None, sender="111", echo=False):
    msg = {"mid": "m1"}
    if attachments:
        msg["attachments"] = attachments
    if text:
        msg["text"] = text
    if echo:
        msg["is_echo"] = True
    return {"object": "instagram", "entry": [{"id": "1", "time": 1, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": "999"}, "timestamp": 1, "message": msg}]}]}


def test_signature():
    body = b'{"a":1}'
    assert verify_signature("s", body, sign("s", body))
    assert not verify_signature("s", body, sign("other", body))
    assert not verify_signature("s", body, None)
    assert not verify_signature("", body, sign("", body))


def test_parse_ig_reel_attachment():
    p = payload([{"type": "ig_reel", "payload": {"url": "https://lookaside.fbsbx.com/x", "title": "Cap", "reel_video_id": 123}}])
    [r] = parse_payload(p)
    assert r.reel_key == "igv_123"
    assert r.media_url == "https://lookaside.fbsbx.com/x"
    assert r.caption == "Cap"
    assert r.sender_id == "111"


def test_parse_text_link_and_skip_echo_and_allowlist():
    p = payload(text="look https://www.instagram.com/reel/Cabc12345/ nice")
    [r] = parse_payload(p)
    assert r.reel_key == "sc_Cabc12345"
    assert parse_payload(payload(text="https://www.instagram.com/reel/Cabc12345/", echo=True)) == []
    assert parse_payload(p, allowed_senders={"222"}) == []
    assert parse_payload({"object": "other"}) == []


def test_db_enqueue_dedupe_claim_requeue(tmp_path):
    db = DB(tmp_path / "t.db")
    rid, created = db.enqueue(reel_key="sc_a", source="test", source_url="u")
    assert created
    rid2, created2 = db.enqueue(reel_key="sc_a", source="test")
    assert rid2 == rid and not created2
    got = db.claim_next()
    assert got["id"] == rid and got["status"] == "downloading"
    assert db.claim_next() is None
    db.update(rid, status="failed", error="x", tags=["a", "b"])
    assert db.get(rid)["tags"] == ["a", "b"]
    assert db.requeue(rid)
    assert db.get(rid)["status"] == "queued"
    db.update(rid, status="analysing")
    assert db.reset_interrupted() == 1
    db.update(rid, status="filed", topic="Voice cloning", tags=["tts"])
    rows, total = db.list_reels(q="tts")
    assert total == 1 and rows[0]["id"] == rid
    assert json.dumps(db.counts())
