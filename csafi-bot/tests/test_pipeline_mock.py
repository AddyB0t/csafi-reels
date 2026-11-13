"""End to end with a generated video, mock AI, no Whisper/OCR. Needs ffmpeg."""
import shutil
import subprocess

import pytest

from csafi_bot import library
from csafi_bot.db import DB
from csafi_bot.pipeline import Pipeline

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_video(path):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
    ], check=True)


def test_file_to_folder_and_duplicate(settings, tmp_path):
    video = tmp_path / "sample.mp4"
    make_video(video)
    db = DB(settings.db_path)
    p = Pipeline(settings, db)

    rid, _ = db.enqueue(reel_key="file_a", source="test", caption="Build a CLI in Python. Repo: github.com/acme/cli #python")
    out = p.process(db.get(rid), local_file=video)
    assert out["status"] == "filed", out["error"]
    folder = settings.library_dir / out["folder_name"]
    assert (folder / library.README_NAME).exists()
    assert "https://github.com/acme/cli" in (folder / library.RESOURCES_NAME).read_text(encoding="utf-8")
    assert (settings.library_dir / library.INDEX_NAME).exists()
    assert (folder / library.STORYBOARD_NAME).stat().st_size > 1000
    assert (settings.thumbs_dir / f"{rid}.jpg").stat().st_size > 500
    assert out["analysis"]["pacing"]["words_spoken"] == 0

    rid2, _ = db.enqueue(reel_key="file_b", source="test")
    out2 = p.process(db.get(rid2), local_file=video)
    assert out2["status"] == "duplicate" and out2["duplicate_of"] == rid


def test_load_audio_with_ffmpeg(tmp_path):
    pytest.importorskip("numpy")
    from csafi_bot.transcribe import SAMPLE_RATE, load_audio

    video = tmp_path / "a.mp4"
    make_video(video)
    audio = load_audio(video)
    assert audio.dtype.name == "float32"
    assert abs(len(audio) / SAMPLE_RATE - 6) < 0.5
    assert 0 < abs(audio).max() <= 1.0


def test_uploaded_file_from_queue(settings, tmp_path):
    video = tmp_path / "up.mp4"
    make_video(video)
    db = DB(settings.db_path)
    rid, _ = db.enqueue(reel_key="file_up", source="upload", local_path=str(video), creator="@maker",
                        caption="Try linktr.ee/maker")
    out = Pipeline(settings, db).process(db.get(rid))
    assert out["status"] == "filed", out["error"]
    assert out["folder_name"].endswith("(@maker)")


def test_missing_upload_fails_cleanly(settings, tmp_path):
    db = DB(settings.db_path)
    rid, _ = db.enqueue(reel_key="file_gone", source="upload", local_path=str(tmp_path / "gone.mp4"))
    out = Pipeline(settings, db).process(db.get(rid))
    assert out["status"] == "failed" and "missing" in out["error"]


def test_failure_is_recorded(settings):
    db = DB(settings.db_path)
    rid, _ = db.enqueue(reel_key="sc_none", source="test")
    out = Pipeline(settings, db).process(db.get(rid))
    assert out["status"] == "failed" and out["stage"] == "download"
