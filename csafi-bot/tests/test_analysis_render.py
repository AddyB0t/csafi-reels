from pathlib import Path

from csafi_bot import library, media
from csafi_bot.analyse import AnalysisInput, enforce_facts, extract, mock_analysis
from csafi_bot.schema import ReelAnalysis

FACTS = {"duration_s": 30.0, "resolution": "1080x1920", "aspect_ratio": "9:16", "fps": 30.0, "has_audio": True}


def make_input():
    return AnalysisInput(
        facts=FACTS,
        caption="New TTS repo! Comment VOICE for the link #ai #tts",
        creator_hint="githubsignals",
        transcript={"language": "en", "segments": [{"start": 0, "end": 2, "text": "This repo clones voices"}],
                    "text": "This repo clones voices"},
        ocr={"lines": [{"t": 1.0, "text": "github.com/acme/audio8"}]},
    )


def test_links_come_from_code_not_model():
    inp = make_input()
    ex = extract(inp)
    a = ReelAnalysis(
        title_topic="Audio8 TTS Voice Cloning", short_description="open-source TTS repo walkthrough",
        one_line_summary="s", overview="o",
        resources_mentioned=[
            {"name": "Audio8", "url": "https://github.com/acme/audio8", "how_to_use": "clone it"},
            {"name": "Fake", "url": "https://invented.example.com/x", "how_to_use": "n/a"},
        ],
        call_to_action={"type": "comment", "keyword_if_any": "VOICE"},
    )
    a, resources = enforce_facts(a, inp, ex)
    urls = [r["url"] for r in resources if r["url"]]
    assert urls == ["https://github.com/acme/audio8"]
    assert resources[0]["how_to_use"] == "clone it"
    assert resources[0]["found_in"] == "on screen"
    assert any("invented.example.com" in u for u in a.uncertainties)
    assert a.creator_handle == "githubsignals"
    assert not any("keyword" in u.lower() for u in a.uncertainties)


def test_keyword_not_in_source_is_flagged():
    inp = make_input()
    a = ReelAnalysis(title_topic="t", short_description="d", one_line_summary="s", overview="o",
                     call_to_action={"type": "comment", "keyword_if_any": "GUIDE"})
    a, _ = enforce_facts(a, inp, extract(inp))
    assert any("GUIDE" in u for u in a.uncertainties)


def test_pick_times_caps_and_covers_start():
    times = media.pick_times(60, [5, 10, 20, 40], max_frames=8)
    assert len(times) == 8
    assert times[0] < 1.0
    assert times == sorted(times)
    assert media.pick_times(0, [], 8) == [0.0]


def test_render_package_and_index(tmp_path: Path):
    inp = make_input()
    ex = extract(inp)
    a, resources = enforce_facts(mock_analysis(inp, ex), inp, ex)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    ctx = {"a": a, "facts": FACTS, "reel_key": "sc_x", "reel_url": "https://www.instagram.com/reel/x/",
           "caption": inp.caption, "resources": resources, "ocr_lines": inp.ocr["lines"], "backend": "mock",
           "transcript_lang": "en"}
    lib = tmp_path / "lib"
    folder = library.write_package(lib, "Test Folder (@me)", ctx=ctx, video=video)
    names = sorted(p.name for p in folder.iterdir())
    assert library.README_NAME in names and library.RESOURCES_NAME in names and library.MANIFEST_NAME in names
    assert any(n.startswith("VIDEO - ") for n in names)
    readme = (folder / library.README_NAME).read_text(encoding="utf-8")
    assert "sc_x" in readme and "This repo clones voices" in readme
    res = (folder / library.RESOURCES_NAME).read_text(encoding="utf-8")
    assert "https://github.com/acme/audio8" in res
    idx = library.rebuild_index(lib, [{"folder_name": "Test Folder (@me)", "reel_key": "sc_x", "creator": "me",
                                       "topic": a.title_topic, "summary": "s", "tags": ["ai"], "resources": resources,
                                       "source_url": None}])
    text = idx.read_text(encoding="utf-8")
    assert "Test Folder (@me)" in text and "1 reel filed" in text
    assert not list(lib.glob(".tmp-*"))
