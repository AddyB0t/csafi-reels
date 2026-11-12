"""Link, handle and reel-ID extraction. Resources come from here, never from the LLM."""
import hashlib
import re

_IG_REEL = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})",
    re.IGNORECASE,
)
_URL = re.compile(r"\bhttps?://[^\s<>\"'`)\]}]+", re.IGNORECASE)
# Bare domains such as github.com/user/repo or linktr.ee/name, as often seen on screen.
_BARE = re.compile(
    r"(?<![@\w/.])((?:[a-z0-9-]+\.)+(?:com|io|ai|dev|app|co|org|net|me|ee|gg|sh|xyz|so|tv|link|site|tech|page)"
    r"(?:/[^\s<>\"'`)\]}]*)?)",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"(?<![\w.@/])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)")
_TRAILING = ".,;:!?'\""


def instagram_shortcode(url: str) -> str | None:
    m = _IG_REEL.search(url or "")
    return m.group(1) if m else None


def find_instagram_links(text: str) -> list[str]:
    seen: dict[str, str] = {}
    for m in _IG_REEL.finditer(text or ""):
        seen.setdefault(m.group(1), f"https://www.instagram.com/reel/{m.group(1)}/")
    return list(seen.values())


def reel_key(*, reel_video_id: str | None = None, shortcode: str | None = None, url: str | None = None) -> str:
    if reel_video_id:
        return f"igv_{reel_video_id}"
    if shortcode:
        return f"sc_{shortcode}"
    if url:
        return "url_" + hashlib.sha1(url.encode()).hexdigest()[:16]
    raise ValueError("need an id, shortcode or url")


def _clean(url: str) -> str:
    return url.rstrip(_TRAILING)


def extract_urls(text: str) -> list[str]:
    """Explicit URLs plus bare domains (github.com/x), deduplicated, in order."""
    text = text or ""
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = _clean(u)
        key = u.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")
        if len(key) < 4 or key in seen:
            return
        seen.add(key)
        out.append(u)

    for m in _URL.finditer(text):
        add(m.group(0))
    stripped = _URL.sub(" ", text)
    for m in _BARE.finditer(stripped):
        add("https://" + m.group(1))
    return out


def extract_handles(text: str) -> list[str]:
    out: list[str] = []
    for m in _HANDLE.finditer(text or ""):
        h = m.group(1).rstrip(".")
        if h.lower() not in {x.lower() for x in out}:
            out.append(h)
    return out


def extract_hashtags(text: str) -> list[str]:
    tags = re.findall(r"#([\w]+)", text or "")
    seen: list[str] = []
    for t in tags:
        if t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    return seen


def classify_url(url: str) -> str:
    u = url.lower()
    if "github.com" in u or "gitlab.com" in u or "huggingface.co" in u:
        return "repo"
    if u.endswith(".pdf"):
        return "pdf"
    if any(d in u for d in ("linktr.ee", "beacons.ai", "bio.link", "lnk.bio", "stan.store")):
        return "link-in-bio"
    if "youtube.com" in u or "youtu.be" in u:
        return "video"
    if "instagram.com" in u:
        return "instagram"
    return "website"
