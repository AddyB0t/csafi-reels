import json

from csafi_bot.db import DB


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


def test_enqueue_keeps_local_path_and_creator(tmp_path):
    db = DB(tmp_path / "t.db")
    rid, _ = db.enqueue(reel_key="file_x", source="upload", local_path="/tmp/x.mp4", creator="someone")
    r = db.get(rid)
    assert r["local_path"] == "/tmp/x.mp4" and r["creator"] == "someone"


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    DB(path)
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE reels DROP COLUMN local_path")
    c.commit()
    c.close()
    db = DB(path)
    rid, _ = db.enqueue(reel_key="file_y", source="upload", local_path="/tmp/y.mp4")
    assert db.get(rid)["local_path"] == "/tmp/y.mp4"
