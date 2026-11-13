from pathlib import Path

from fastapi.testclient import TestClient

from csafi_bot.app import create_app, render_md


def client(settings):
    return TestClient(create_app(settings, start_background=False))


def test_upload_queues_and_dedupes(settings):
    c = client(settings)
    data = b"\x00" * 20_000
    form = {"caption": "Repo github.com/acme/cli", "creator": "@maker", "url": "https://www.instagram.com/reel/Cabc12345/"}
    r = c.post("/upload", files={"video": ("clip.mp4", data, "video/mp4")}, data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("-queued")
    rid = int(r.headers["location"].split("added=")[1].split("-")[0])
    reel = c.app.state.db.get(rid)
    assert reel["status"] == "queued" and reel["source"] == "upload"
    assert reel["creator"] == "maker" and reel["caption"].startswith("Repo")
    assert reel["source_url"] == "https://www.instagram.com/reel/Cabc12345/"
    assert Path(reel["local_path"]).read_bytes() == data
    assert not list(settings.uploads_dir.glob(".part-*"))

    again = c.post("/upload", files={"video": ("other.mp4", data, "video/mp4")}, follow_redirects=False)
    assert "already" in again.headers["location"]


def test_upload_rejects_bad_files(settings):
    c = client(settings)
    bad_ext = c.post("/upload", files={"video": ("x.txt", b"\x00" * 20_000, "text/plain")}, follow_redirects=False)
    assert bad_ext.headers["location"].startswith("/?error=")
    tiny = c.post("/upload", files={"video": ("x.mp4", b"\x00" * 10, "video/mp4")}, follow_redirects=False)
    assert tiny.headers["location"].startswith("/?error=")
    settings.max_upload_mb = 10
    big = c.post("/upload", files={"video": ("x.mp4", b"\x00" * (11 * 1_048_576), "video/mp4")}, follow_redirects=False)
    assert "bigger" in big.headers["location"]
    assert not list(settings.uploads_dir.iterdir())


def test_webhook_is_gone(settings):
    assert client(settings).post("/webhook", content=b"{}").status_code in (404, 405)


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


def test_reel_page_shows_filed_package(settings):
    from csafi_bot import library
    from csafi_bot.schema import ReelAnalysis

    c = client(settings)
    db = c.app.state.db
    rid, _ = db.enqueue(reel_key="file_page", source="upload")
    a = ReelAnalysis(title_topic="Voice cloning repo", one_line_summary="A repo clones voices.", overview="o",
                     key_points=["It clones a voice from 5 seconds of audio."])
    video = settings.data_dir / "v.mp4"
    video.write_bytes(b"0" * 100)
    board = settings.data_dir / "b.jpg"
    board.write_bytes(b"jpg")
    facts = {"duration_s": 30.0, "resolution": "1080x1920", "aspect_ratio": "9:16", "fps": 30.0}
    ctx = {"a": a, "facts": facts, "pacing": {"scene_count": 4, "avg_shot_s": 7.5}, "reel_key": "file_page",
           "reel_url": None, "caption": "", "resources": [], "ocr_lines": [], "backend": "mock", "transcript_lang": None}
    library.write_package(settings.library_dir, "Voice Cloning Repo", ctx=ctx, video=video, storyboard=board)
    (settings.thumbs_dir / f"{rid}.jpg").write_bytes(b"jpg")
    db.update(rid, status="filed", folder_name="Voice Cloning Repo", topic=a.title_topic, summary=a.one_line_summary,
              analysis={**a.model_dump(), "backend": "mock", "facts": facts, "pacing": ctx["pacing"]})

    page = c.get(f"/reel/{rid}").text
    assert "It clones a voice from 5 seconds of audio." in page and "4, 7.5 s average" in page
    assert "draft written without AI" in page and 'data-tab="storyboard"' in page
    assert c.get(f"/reel/{rid}/storyboard").content == b"jpg"
    assert c.get(f"/reel/{rid}/thumb").content == b"jpg"
    assert f'src="/reel/{rid}/thumb"' in c.get("/").text
    assert c.get("/reel/999/thumb").status_code == 404


def test_cross_site_post_blocked(settings):
    c = client(settings)
    r = c.post("/add", data={"url": "https://www.instagram.com/reel/Cabc12345/"},
               headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    r = c.post("/add", data={"url": "https://www.instagram.com/reel/Cabc12345/"},
               headers={"sec-fetch-site": "cross-site", "origin": "null"}, follow_redirects=False)
    assert r.status_code == 403


def test_same_origin_browser_post_allowed(settings):
    c = client(settings)
    r = c.post("/add", data={"url": "https://www.instagram.com/reel/Cabc12345/"},
               headers={"sec-fetch-site": "same-origin", "origin": "null"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/").headers["referrer-policy"] == "same-origin"


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
    assert "<script" not in html and 'href="javascript' not in html and "<strong>ok</strong>" in html
