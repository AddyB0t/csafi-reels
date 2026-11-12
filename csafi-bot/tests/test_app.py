import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from csafi_bot.app import create_app, render_md


def client(settings):
    return TestClient(create_app(settings, start_background=False))


def test_webhook_verify(settings):
    c = client(settings)
    ok = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403


def test_webhook_signature_and_enqueue(settings):
    c = client(settings)
    body = json.dumps({"object": "instagram", "entry": [{"messaging": [{"sender": {"id": "7"}, "message": {
        "attachments": [{"type": "ig_reel", "payload": {"url": "https://cdn/x", "reel_video_id": 9}}]}}]}]}).encode()
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    r = c.post("/webhook", content=body, headers={"x-hub-signature-256": sig, "content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"received": 1}
    assert c.app.state.db.get_by_key("igv_9")["status"] == "queued"


def test_dashboard_add_and_pages(settings):
    c = client(settings)
    assert "library is empty" in c.get("/").text
    r = c.post("/add", data={"url": "https://www.instagram.com/reel/Cabc12345/"}, follow_redirects=False)
    assert r.status_code == 303 and "queued" in r.headers["location"]
    assert "Cabc12345" in c.get("/partials/reels").text
    assert c.post("/add", data={"url": "nope"}, follow_redirects=False).headers["location"].startswith("/?error=")
    assert c.get("/reel/1").status_code == 200
    assert c.get("/reel/999").status_code == 404
    assert "default-src 'self'" in c.get("/").headers["content-security-policy"]


def test_cross_site_post_blocked(settings):
    c = client(settings)
    r = c.post("/add", data={"url": "https://www.instagram.com/reel/Cabc12345/"},
               headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_token_login(settings):
    settings.dashboard_token = "letmein"
    c = client(settings)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/status").status_code == 401
    assert c.get("/health").status_code == 200
    c.post("/login", data={"token": "wrong"})
    assert c.get("/api/status").status_code == 401
    c.post("/login", data={"token": "letmein"})
    assert c.get("/api/status").status_code == 200


def test_markdown_is_sanitised():
    html = render_md("<script>alert(1)</script>\n\n[x](javascript:alert(1)) **ok**")
    assert "<script" not in html and "javascript:" not in html and "<strong>ok</strong>" in html
