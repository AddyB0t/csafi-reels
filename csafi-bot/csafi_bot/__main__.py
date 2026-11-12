"""CLI: python -m csafi_bot <command>

  serve               dashboard + worker
  run-one FILE|URL    process one video file or reel link now, print the folder
  add URL             queue a reel link (processed by a running `serve`)
  rebuild-index       regenerate INDEX - All reels catalog.md
  doctor              check every dependency and print pass / fail
"""
import argparse
import hashlib
import logging
import os
import shutil
import sys
from pathlib import Path

from . import __version__, links


def _setup_logging(settings) -> None:
    from logging.handlers import RotatingFileHandler

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    fileh = RotatingFileHandler(settings.logs_dir / "csafi.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.handlers[:] = [console, fileh]
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_serve(args) -> int:
    import uvicorn

    from .app import create_app
    from .config import get_settings

    s = get_settings()
    _setup_logging(s)
    host = args.host or s.host
    # Inside Docker the container must listen on 0.0.0.0; compose publishes it on 127.0.0.1 only.
    in_docker = os.environ.get("CSAFI_IN_DOCKER") == "1"
    if host not in ("127.0.0.1", "localhost", "::1") and not in_docker and not s.dashboard_token:
        print("Refusing to listen on a non-local address without DASHBOARD_TOKEN set in .env.")
        return 2
    print(f"CSAFI Reels Library Bot {__version__}: dashboard on http://{host}:{args.port or s.port}")
    uvicorn.run(create_app(s), host=host, port=args.port or s.port, log_level="warning")
    return 0


def cmd_run_one(args) -> int:
    from .config import get_settings
    from .db import DB
    from .pipeline import Pipeline

    s = get_settings()
    _setup_logging(s)
    db = DB(s.db_path)
    target = args.target
    p = Path(target)
    local = p if p.exists() else None
    if local:
        key = "file_" + hashlib.sha1(str(p.resolve()).encode()).hexdigest()[:16]
        rid, _ = db.enqueue(reel_key=key, source="cli:file", source_url=None, local_path=str(p.resolve()),
                            caption=args.caption)
    else:
        sc = links.instagram_shortcode(target)
        if not sc and not target.startswith("http"):
            print(f"Not a file or link: {target}")
            return 2
        key = links.reel_key(shortcode=sc, url=None if sc else target)
        rid, _ = db.enqueue(reel_key=key, source="cli:url", source_url=target, caption=args.caption)
    db.update(rid, status="downloading")
    reel = db.get(rid)
    result = Pipeline(s, db).process(reel, local_file=local)
    if result["status"] == "filed":
        print(f"Filed: {s.library_dir / result['folder_name']}")
        return 0
    if result["status"] == "duplicate":
        print(f"Duplicate of reel #{result['duplicate_of']}; nothing new written.")
        return 0
    print(f"Failed at {result['stage']}: {result['error']}")
    return 1


def cmd_add(args) -> int:
    from .config import get_settings
    from .db import DB

    s = get_settings()
    found = links.find_instagram_links(args.url)
    if not found:
        print("That is not an Instagram reel link.")
        return 2
    sc = links.instagram_shortcode(found[0])
    rid, created = DB(s.db_path).enqueue(reel_key=links.reel_key(shortcode=sc), source="cli", source_url=found[0])
    print(f"Queued reel #{rid}." if created else f"Reel #{rid} is already in the library.")
    return 0


def cmd_rebuild_index(_args) -> int:
    from . import library
    from .config import get_settings
    from .db import DB

    s = get_settings()
    path = library.rebuild_index(s.library_dir, DB(s.db_path).filed_reels())
    print(f"Wrote {path}")
    return 0


def cmd_doctor(_args) -> int:
    from .config import get_settings
    from .ollama import Ollama

    s = get_settings()
    results: list[tuple[str, bool | None, str]] = []

    def check(name: str, ok: bool | None, detail: str = "") -> None:
        results.append((name, ok, detail))

    check("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0])
    for tool in ("ffmpeg", "ffprobe"):
        check(tool, shutil.which(tool) is not None, shutil.which(tool) or "not on PATH")
    for mod, label in (("faster_whisper", "faster-whisper"), ("scenedetect", "PySceneDetect"),
                       ("rapidocr_onnxruntime", "RapidOCR"), ("yt_dlp", "yt-dlp")):
        try:
            __import__(mod)
            check(label, True, "installed")
        except ImportError:
            check(label, False, "not installed (pip install -r requirements.txt)")
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        check("CUDA for Whisper", True if n else None, f"{n} GPU(s)" if n else "no GPU found, Whisper will use CPU")
    except Exception as e:  # noqa: BLE001
        check("CUDA for Whisper", None, str(e)[:80])

    if s.ai_backend == "mock":
        check("Ollama", None, "AI_BACKEND=mock, skipped")
    else:
        o = Ollama(s.ollama_url)
        if o.available():
            check("Ollama", True, s.ollama_url)
            for m in {s.vision_model, s.text_model}:
                ok = o.has_model(m)
                check(f"model {m}", ok, "pulled" if ok else f"run: ollama pull {m}")
        else:
            check("Ollama", False, f"not reachable at {s.ollama_url} (start: ollama serve)")

    if s.rclone_remote:
        check("rclone", shutil.which("rclone") is not None, s.rclone_remote)
    free = shutil.disk_usage(s.data_dir).free / 1e9
    check("Disk space", free > 10, f"{free:.1f} GB free in {s.data_dir}")

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else ("WARN" if ok is None else "FAIL")
        failed += ok is False
        print(f"  {mark:4}  {name.ljust(width)}  {detail}")
    print(f"\n{'All required checks passed.' if not failed else f'{failed} check(s) failed.'}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m csafi_bot", description="CSAFI Reels Library Bot")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="dashboard + worker")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(fn=cmd_serve)
    sp = sub.add_parser("run-one", help="process one file or link now")
    sp.add_argument("target")
    sp.add_argument("--caption", default=None, help="caption text, for local files")
    sp.set_defaults(fn=cmd_run_one)
    sp = sub.add_parser("add", help="queue a reel link")
    sp.add_argument("url")
    sp.set_defaults(fn=cmd_add)
    sub.add_parser("rebuild-index").set_defaults(fn=cmd_rebuild_index)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
