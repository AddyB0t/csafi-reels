from csafi_bot import links, naming


def test_shortcode_variants():
    assert links.instagram_shortcode("https://www.instagram.com/reel/DAbc123_-x/?igsh=1") == "DAbc123_-x"
    assert links.instagram_shortcode("https://instagram.com/someone/reel/Cxyz987/") == "Cxyz987"
    assert links.instagram_shortcode("https://www.instagram.com/p/Cpost555/") == "Cpost555"
    assert links.instagram_shortcode("https://example.com/reel/abc") is None


def test_reel_key_priority():
    assert links.reel_key(reel_video_id="42", shortcode="abc") == "igv_42"
    assert links.reel_key(shortcode="abc") == "sc_abc"
    assert links.reel_key(url="https://x.y/z").startswith("url_")


def test_extract_urls_and_bare_domains():
    text = "Repo: https://github.com/acme/tool. Also see linktr.ee/acme and huggingface.co/acme/model!"
    urls = links.extract_urls(text)
    assert "https://github.com/acme/tool" in urls
    assert "https://linktr.ee/acme" in urls
    assert "https://huggingface.co/acme/model" in urls
    assert len(urls) == len({u.lower() for u in urls})


def test_handles_and_hashtags():
    assert links.extract_handles("follow @github_signals and @a.b for more, mail me@x.com") == ["github_signals", "a.b"]
    assert links.extract_hashtags("#AI #ai #python") == ["AI", "python"]


def test_folder_name_format_and_sanitising():
    name = naming.folder_name("audio8 TTS voice cloning", "Open-source TTS repo walkthrough", "@githubsignals")
    assert name == "Audio8 TTS Voice Cloning - Open-source TTS repo walkthrough (@githubsignals)"
    bad = naming.folder_name('Topic: "a/b" <c> 2024-05-01', "x?y*z", "me")
    assert not any(ch in bad for ch in '<>:"/\\|?*')
    assert "2024" not in bad


def test_collision_never_uses_bare_number():
    base = "Topic - desc (@me)"
    taken = {base.lower()}
    nxt = naming.unique_folder_name(base, taken, ["python"])
    assert nxt == "Topic - desc - python (@me)"
    taken.add(nxt.lower())
    third = naming.unique_folder_name(base, taken, ["python"])
    assert third == "Topic - desc - another take (@me)"
    assert not any(c.isdigit() for c in third)


def test_windows_reserved_name():
    assert naming.clean("CON") == "CON reel"
