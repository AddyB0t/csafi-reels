import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import nh3
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from . import __version__, library, links
from .config import Settings, get_settings
from .db import DB, STATUSES
from .pipeline import Pipeline
from .worker import Worker

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
COOKIE = "csafi_session"
PAGE_SIZE = 25
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
MIN_VIDEO_BYTES = 10_000

_md = MarkdownIt("commonmark", {"linkify": False}).enable("table")
_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "em", "code", "pre", "blockquote",
    "table", "thead", "tbody", "tr", "th", "td", "a", "hr", "br",
}


def render_md(text: str) -> str:
    html = _md.render(text)
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes={"a": {"href"}, "th": {"style"}, "td": {"style"}},
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer nofollow",
    )


def create_app(settings: Settings | None = None, start_background: bool | None = None) -> FastAPI:
    s = settings or get_settings()
    s.ensure_dirs()
    db = DB(s.db_path)
    pipeline = Pipeline(s, db)
    worker = Worker(pipeline)
    background = s.run_worker if start_background is None else start_background
    session_secret = db.kv_get("session_secret") or secrets.token_urlsafe(32)
    db.kv_set("session_secret", session_secret)

    def queue_url(url: str, source: str) -> tuple[int, bool]:
        sc = links.instagram_shortcode(url)
        key = links.reel_key(shortcode=sc, url=None if sc else url)
        rid, created = db.enqueue(reel_key=key, source=source, source_url=url, sender_id=source.split(":", 1)[-1])
        if created:
            db.event(rid, f"queued from {source}")
            worker.notify()
        return rid, created

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if background:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="CSAFI Reels Library Bot", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db, app.state.worker, app.state.settings, app.state.pipeline = db, worker, s, pipeline
    templates = Jinja2Templates(directory=str(HERE / "templates" / "web"))
    templates.env.filters["fromjson"] = json.loads
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def session_value() -> str:
        return hmac.new(session_secret.encode(), s.dashboard_token.encode(), "sha256").hexdigest()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        public = path in ("/health", "/login") or path.startswith("/static/")
        if s.dashboard_token and not public:
            if not hmac.compare_digest(request.cookies.get(COOKIE, ""), session_value()):
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "login required"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("cross-site request blocked", status_code=403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    # ---------- health ----------
    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__}

    # ---------- auth ----------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: str = ""):
        if not s.dashboard_token:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": error})

    @app.post("/login")
    def login(token: str = Form(...)):
        if not s.dashboard_token or not hmac.compare_digest(token, s.dashboard_token):
            return RedirectResponse("/login?error=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, session_value(), httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # ---------- dashboard ----------
    def list_ctx(q: str, status: str, page: int) -> dict:
        status = status if status in STATUSES else ""
        page = max(1, page)
        rows, total = db.list_reels(q=q.strip(), status=status, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        return {
            "reels": rows, "total": total, "q": q, "status": status, "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)), "statuses": STATUSES,
            "counts": db.counts(), "current": worker.current,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "", status: str = "", page: int = 1, added: str = "", error: str = ""):
        ctx = list_ctx(q, status, page)
        ctx.update(
            added=added, error=error, ai=pipeline.analyser.backend_label(),
            auth=bool(s.dashboard_token), library_dir=str(s.library_dir),
        )
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.get("/partials/reels", response_class=HTMLResponse)
    def rows_partial(request: Request, q: str = "", status: str = "", page: int = 1):
        return templates.TemplateResponse(request, "_rows.html", list_ctx(q, status, page))

    @app.post("/add")
    def add(url: str = Form(...)):
        found = links.find_instagram_links(url)
        if not found:
            return RedirectResponse("/?error=Paste+an+Instagram+reel+link+(instagram.com/reel/...)", status_code=303)
        rid, created = queue_url(found[0], "dashboard")
        msg = "queued" if created else "already+in+library"
        return RedirectResponse(f"/?added={rid}-{msg}", status_code=303)

    def upload_error(text: str) -> RedirectResponse:
        return RedirectResponse(f"/?error={quote_plus(text)}", status_code=303)

    @app.post("/upload")
    def upload(
        video: UploadFile = File(...),
        caption: str = Form(""),
        creator: str = Form(""),
        url: str = Form(""),
    ):
        suffix = Path(video.filename or "").suffix.lower()
        if suffix not in VIDEO_EXTS:
            return upload_error("Upload an .mp4, .mov or .m4v video file.")
        limit = s.max_upload_mb * 1_048_576
        tmp = s.uploads_dir / f".part-{secrets.token_hex(8)}"
        h, size = hashlib.sha256(), 0
        try:
            with open(tmp, "wb") as f:
                while chunk := video.file.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        break
                    h.update(chunk)
                    f.write(chunk)
            if size > limit:
                return upload_error(f"The video is bigger than {s.max_upload_mb} MB (MAX_UPLOAD_MB).")
            if size < MIN_VIDEO_BYTES:
                return upload_error("The file is too small to be a video.")
            sha = h.hexdigest()
            dest = s.uploads_dir / f"{sha}{suffix}"
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

        found = links.find_instagram_links(url)
        rid, created = db.enqueue(
            reel_key=f"file_{sha[:16]}",
            source="upload",
            source_url=found[0] if found else None,
            local_path=str(dest),
            caption=caption.strip()[:5000] or None,
            creator=creator.strip().lstrip("@")[:30] or None,
        )
        if created:
            db.event(rid, f"uploaded {video.filename} ({size / 1_048_576:.1f} MB)")
            worker.notify()
        msg = "queued" if created else "already+in+library"
        return RedirectResponse(f"/?added={rid}-{msg}", status_code=303)

    def get_reel(reel_id: int) -> dict:
        r = db.get(reel_id)
        if not r:
            raise HTTPException(404, "reel not found")
        return r

    def folder_path(r: dict) -> Path | None:
        if not r.get("folder_name"):
            return None
        p = (s.library_dir / r["folder_name"]).resolve()
        if p.parent != s.library_dir.resolve() or not p.is_dir():
            return None
        return p

    @app.get("/reel/{reel_id}", response_class=HTMLResponse)
    def reel_page(request: Request, reel_id: int):
        r = get_reel(reel_id)
        folder = folder_path(r)
        docs = {}
        if folder:
            for key, name in (("readme", library.README_NAME), ("resources", library.RESOURCES_NAME),
                              ("manifest", library.MANIFEST_NAME)):
                f = folder / name
                docs[key] = render_md(f.read_text(encoding="utf-8")) if f.exists() else ""
        has_video = bool(folder and any(folder.glob("VIDEO - *.mp4")))
        return templates.TemplateResponse(
            request, "reel.html",
            {"r": r, "docs": docs, "events": db.events(reel_id), "folder": str(folder) if folder else "",
             "has_video": has_video, "current": worker.current},
        )

    @app.get("/reel/{reel_id}/video")
    def reel_video(reel_id: int):
        folder = folder_path(get_reel(reel_id))
        vids = sorted(folder.glob("VIDEO - *.mp4")) if folder else []
        if not vids:
            raise HTTPException(404, "no video")
        return FileResponse(vids[0], media_type="video/mp4")

    @app.post("/reel/{reel_id}/retry")
    def retry(reel_id: int):
        get_reel(reel_id)
        db.requeue(reel_id)
        db.event(reel_id, "retry requested from dashboard")
        worker.notify()
        return RedirectResponse(f"/reel/{reel_id}", status_code=303)

    @app.get("/api/status")
    def api_status():
        disk = shutil.disk_usage(s.data_dir)
        return {
            "counts": db.counts(),
            "current": worker.current,
            "ai": pipeline.analyser.backend_label(),
            "disk_free_gb": round(disk.free / 1e9, 1),
        }

    @app.get("/api/reels/{reel_id}")
    def api_reel(reel_id: int):
        r = get_reel(reel_id)
        return {k: r[k] for k in ("id", "status", "stage", "error", "folder_name", "topic", "creator", "summary")}

    return app
