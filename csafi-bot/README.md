# CSAFI Reels Library Bot (local, zero-cost)

Turns Instagram reels shared by the CSAFI team into a searchable library of folders, one per reel,
each with a full breakdown and recreation guide. Everything runs on one machine with a local GPU.
No paid APIs.

This is the `no-meta` branch: no Meta webhook and no Telegram. You download a reel yourself and
upload the video in the dashboard. Nothing needs a developer account.

```
Upload a video in the dashboard ─┐
Paste a public reel link ────────┴─> queue (SQLite) -> worker, one reel at a time:
                                      ffprobe -> scenes -> key frames (full resolution)
                                      -> OCR, one frame per second -> Whisper transcript
                                      -> vision model on each key frame (Ollama)
                                      -> text model, pass 1: what the reel teaches
                                      -> text model, pass 2: how the reel is made
                                      -> folder + INDEX
```

Each reel folder, named `<Topic> - <short description> (@creator)` (no dates):

| File | Contents |
| --- | --- |
| `VIDEO - <topic> reel original.mp4` | Original video |
| `README - Full breakdown and recreation guide.md` | Overview, key points, tools named, hook, why it works, timed script, shot list, exact on-screen text, audio, pacing numbers, caption, CTA, steps to recreate, uncertainties |
| `STORYBOARD - Key frames.jpg` | One picture with the key frames in time order |
| `RESOURCES - Creator links and repos.md` | Every link found in the caption, on screen or spoken, where it was found and how to use it, plus gaps |
| `MANIFEST.md` | File list with reel ID and creator |

`INDEX - All reels catalog.md` in the library root lists every reel in one searchable file.

## What makes the output trustworthy

- **Links come from code, not the AI.** URLs, handles and hashtags are pulled by regex from the
  caption, OCR and transcript. Any link the model makes up is removed and noted under Uncertainties.
- **Exact on-screen text comes from OCR** (RapidOCR), not the vision model, which misreads text.
  OCR reads one full-resolution frame per second, and marks text that is only a subtitle of the speech.
- **The timed script is the Whisper transcript**, copied by code. The model does not retype it.
- **Video facts come from ffprobe** (duration, resolution, fps, aspect ratio). **Pacing numbers**
  (scenes, cuts, words per minute) are counted by code.
- **Every field is required in the model's JSON grammar**, so a small model cannot skip sections.
  A thin answer is sent back once for a second try.
- **A comment keyword is checked** word for word against the caption and on-screen text.
- **Duplicates are caught twice:** by reel shortcode or upload hash, and by a hash of the video
  (catches the same reel arriving once as an upload and once as a pasted link).
- **One model on the GPU at a time.** Whisper is freed before the vision model loads; each Ollama
  model is unloaded after use. Fits 8 GB VRAM.
- **Never touches Instagram.** No login, no likes, follows, comments or DMs.

## Quick start with Docker (recommended)

Needs Docker with Compose v2.24+, and for GPU: an NVIDIA driver plus the NVIDIA Container Toolkit
(Linux) or Docker Desktop with the WSL2 backend (Windows).

```bash
git clone <this repo> csafi-bot && cd csafi-bot     # or cd into the csafi-bot folder
cp .env.example .env                                 # then edit .env
docker compose up -d --build
docker compose logs -f ollama-pull                   # first run: waits for the two models to download
docker compose exec bot python -m csafi_bot doctor   # every check should PASS
```

Open <http://127.0.0.1:8765>. Files appear in `./data/library/`.

## Add a reel

1. Download the reel video to your computer (any downloader, or save it from your phone).
2. In the dashboard, under **Upload a reel video**, pick the file.
3. Optional but useful: paste the reel link, the creator handle and the caption. The caption is
   where most links and hashtags come from.
4. Press **Upload and file**. The reel shows in the list and files on its own.

Uploaded videos are kept in `data/uploads/` so **Retry** works. Limit: `MAX_UPLOAD_MB` (500).
Pasting a link also works for public reels, but Instagram often blocks downloads without login.

No NVIDIA GPU? Run CPU-only (much slower):

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
```

Useful commands:

```bash
docker compose exec bot python -m csafi_bot run-one /app/data/sample.mp4 --caption "caption text"
docker compose exec bot python -m csafi_bot add https://www.instagram.com/reel/XXXX/
docker compose exec bot python -m csafi_bot rebuild-index
docker compose logs -f bot
docker compose down          # stop; data stays in ./data and the ollama volume
```

## Without Docker (Windows)

1. Install Python 3.12, ffmpeg (`winget install ffmpeg`), Git and [Ollama](https://ollama.com).
2. `ollama pull qwen2.5vl:7b` and `ollama pull qwen3:8b` (check the tags on ollama.com/library).
3. Copy `.env.example` to `.env` and edit it.
4. Double-click `start.bat`. It creates the venv, installs packages, starts Ollama and the bot.
5. `.venv\Scripts\python -m csafi_bot doctor`

For Whisper on the GPU outside Docker, faster-whisper needs CUDA 12 and cuDNN 9 libraries on PATH.
If they are missing it falls back to CPU automatically (set `WHISPER_DEVICE=cpu` to skip the try).

## Choosing models (8 GB VRAM)

| Role | Default | Lighter fallback |
| --- | --- | --- |
| Vision (frames) | `qwen2.5vl:7b` | `qwen2.5vl:3b`, `gemma3:4b` |
| Text (breakdown JSON) | `qwen3:8b` | `qwen3:4b` |
| Speech | Whisper `small` | `base`; `large-v3-turbo` if VRAM allows |

Set them in `.env` (`VISION_MODEL`, `TEXT_MODEL`, `WHISPER_MODEL`). With Docker, rebuild after
changing `WHISPER_MODEL` (it is baked into the image) and re-run `docker compose up -d ollama-pull`
after changing an Ollama model.

## Optional: mirror to Google Drive

Off by default. Run `rclone config` once and create a remote called `gdrive`. With Docker, copy the resulting
`rclone.conf` into `./rclone/`. Then set `RCLONE_REMOTE=gdrive:CSAFI Reels Library`. Each filed reel
folder and the INDEX are copied to Drive. The local folder stays the source of truth.

## Keep it always on

- Turn off sleep while plugged in (Windows: `powercfg /change standby-timeout-ac 0`).
- Docker restarts the services after a reboot (`restart: unless-stopped`); enable "Start Docker
  Desktop when you sign in". Without Docker, add `start.bat` to Task Scheduler "At log on".
- Back up `data/csafi.db` with the library folder.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Dashboard says "Ollama not running" | Reels get a mock draft. Start Ollama, then press **Retry** on the reel |
| `CUDA out of memory` in Ollama | Use a smaller model or lower `MAX_FRAMES` / `FRAME_WIDTH` |
| Whisper errors about `libcudnn` | Set `WHISPER_DEVICE=cpu` |
| Reel failed at `download` | Instagram blocked the link download, or the reel is private. Download the video and upload it |
| Upload says "bigger than ... MB" | Raise `MAX_UPLOAD_MB` in `.env` and restart |
| Script has wrong names or words | Set `WHISPER_MODEL=large-v3-turbo` (needs a GPU), rebuild, then press **Re-run** |
| On-screen text is missed | Lower `OCR_INTERVAL_S` to `0.5`, or raise `OCR_MAX_FRAMES` for long reels |
| Breakdown is weak | Paste the caption when you upload. Try a bigger model, or edit the README by hand. Local 7-8B models are weaker than cloud ones |

Logs: `data/logs/csafi.log`. Per-reel activity: the **Activity** tab on each reel page.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## Layout

```
csafi_bot/
  app.py          FastAPI: dashboard, video upload, login, security headers
  pipeline.py     one reel end to end
  worker.py       single background worker
  download.py     uploaded file, or yt-dlp for a link (never with cookies)
  media.py        ffprobe, scene detection, frames, pacing numbers, storyboard
  transcribe.py   faster-whisper, GPU with CPU fallback, glossary hint from caption and OCR
  ocr.py          RapidOCR, text blocks with time ranges, subtitle marking
  analyse.py      prompts, vision notes, two-pass synthesis, fact enforcement, mock fallback
  ollama.py       Ollama REST client
  schema.py       Pydantic models for the AI output
  naming.py       folder naming rules
  library.py      write packages, INDEX, rclone mirror
  db.py           SQLite schema and queries
  templates/      reel markdown templates and dashboard pages
  static/         dashboard CSS and JS (no external requests)
```
