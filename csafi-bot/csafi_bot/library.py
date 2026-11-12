"""Writes reel packages into the local library (the source of truth) and keeps INDEX current.
Optional one-way mirror to Google Drive with rclone."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from .naming import file_safe

INDEX_NAME = "INDEX - All reels catalog.md"
README_NAME = "README - Full breakdown and recreation guide.md"
RESOURCES_NAME = "RESOURCES - Creator links and repos.md"
MANIFEST_NAME = "MANIFEST.md"


def _cell(v: Any) -> str:
    return str(v or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


env = Environment(
    loader=PackageLoader("csafi_bot", "templates/reel"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=False,
    autoescape=False,
)
env.filters["cell"] = _cell


def render(name: str, **ctx: Any) -> str:
    return env.get_template(name).render(**ctx)


def video_file_name(topic: str) -> str:
    return f"VIDEO - {file_safe(topic)} reel original.mp4"


def write_package(
    library_dir: Path,
    folder: str,
    *,
    ctx: dict[str, Any],
    video: Path,
    frames: list[tuple[int, str, Path]] | None = None,
    replace_folder: str | None = None,
) -> Path:
    """Build the folder in a temp dir, then move it into place, so a crash never leaves half a package."""
    library_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=library_dir))
    try:
        files: list[dict[str, str]] = []
        vname = video_file_name(ctx["a"].title_topic)
        _link_or_copy(video, tmp / vname)
        files.append({"name": vname, "desc": "Original reel video, full quality"})
        files.append({"name": README_NAME, "desc": "Full breakdown: hook, script, shots, audio, CTA, specs, recreation steps"})
        files.append({"name": RESOURCES_NAME, "desc": "Every link, repo or tool found in the reel, and how to use it"})
        for idx, label, path in frames or []:
            fname = f"FRAME {idx:02d} - {file_safe(label, 50)}.jpg"
            shutil.copyfile(path, tmp / fname)
            files.append({"name": fname, "desc": f"Key frame: {label}"})
        files.append({"name": MANIFEST_NAME, "desc": "This file list"})

        full = {**ctx, "folder": folder, "files": files}
        (tmp / README_NAME).write_text(render("README.md.j2", **full), encoding="utf-8")
        (tmp / RESOURCES_NAME).write_text(render("RESOURCES.md.j2", **full), encoding="utf-8")
        (tmp / MANIFEST_NAME).write_text(render("MANIFEST.md.j2", **full), encoding="utf-8")

        for old in {replace_folder, folder} - {None}:
            target = library_dir / old
            if target.exists():
                shutil.rmtree(target)
        final = library_dir / folder
        tmp.replace(final)
        return final
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _link_or_copy(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def rebuild_index(library_dir: Path, reels: list[dict[str, Any]]) -> Path:
    entries = []
    for r in reels:
        folder = library_dir / (r.get("folder_name") or "")
        files = sorted(p.name for p in folder.iterdir()) if r.get("folder_name") and folder.is_dir() else []
        entries.append({**r, "files": files})
    text = render("INDEX.md.j2", reels=entries)
    dest = library_dir / INDEX_NAME
    fd, tmp = tempfile.mkstemp(prefix=".index-", dir=library_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)
    return dest


def rclone_mirror(remote: str, library_dir: Path, folder: str | None) -> str:
    """Copy one reel folder and the INDEX to Drive. Returns '' on success, else the error."""
    if not remote:
        return ""
    if not shutil.which("rclone"):
        return "rclone is not installed"
    cmds = [["rclone", "copyto", str(library_dir / INDEX_NAME), f"{remote}/{INDEX_NAME}"]]
    if folder:
        cmds.insert(0, ["rclone", "copy", str(library_dir / folder), f"{remote}/{folder}"])
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            return (r.stderr or "rclone failed")[-300:]
    return ""
