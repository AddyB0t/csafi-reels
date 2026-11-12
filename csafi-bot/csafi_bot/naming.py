"""Folder and file naming: `<Topic> - <short description> (@creator)`, no dates, safe on
Windows, macOS, Linux and Google Drive."""
import re
import unicodedata

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_DATEY = re.compile(r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b")
MAX_LEN = 120

COLLISION_PHRASES = ["another take", "alternate version", "second share", "repost", "extended cut", "variant"]


def clean(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _DATEY.sub("", text)
    text = _ILLEGAL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].strip(" .-_") or text[:max_len]
    if text.lower() in _RESERVED:
        text = f"{text} reel"
    return text


def title_case(text: str) -> str:
    words = text.split()
    small = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
    out = []
    for i, w in enumerate(words):
        if w.isupper() or (len(w) > 1 and any(c.isupper() for c in w[1:])):
            out.append(w)
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def folder_name(topic: str, short: str, creator: str) -> str:
    t = title_case(clean(topic, 60)) or "Untitled Reel"
    d = clean(short, 60)
    c = clean(creator.lstrip("@"), 30)
    name = t
    if d and d.lower() != t.lower():
        name += f" - {d[:1].upper() + d[1:]}"
    if c:
        name += f" (@{c})"
    return clean(name, MAX_LEN) if len(name) > MAX_LEN else name


def unique_folder_name(base: str, taken: set[str], extra_phrases: list[str] | None = None) -> str:
    """Collisions get a descriptive phrase, never a date or bare number."""
    if base.lower() not in taken:
        return base
    stem, suffix = base, ""
    m = re.match(r"^(.*?)( \(@[^)]+\))$", base)
    if m:
        stem, suffix = m.group(1), m.group(2)
    for phrase in [*(extra_phrases or []), *COLLISION_PHRASES]:
        p = clean(phrase, 30)
        if not p or p.lower() in stem.lower():
            continue
        cand = f"{stem} - {p}{suffix}"
        if cand.lower() not in taken:
            return cand
    letters = "abcdefghijklmnopqrstuvwxyz"
    for a in letters:
        for b in letters:
            cand = f"{stem} - take {a}{b}{suffix}"
            if cand.lower() not in taken:
                return cand
    raise RuntimeError("could not find a free folder name")


def file_safe(text: str, max_len: int = 60) -> str:
    return clean(text, max_len) or "reel"
