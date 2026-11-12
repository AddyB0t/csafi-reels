# CSAFI Reels Library Bot (local, zero-cost)

Turns Instagram reels shared by the CSAFI team into a searchable library of folders, one per reel,
each with a full breakdown and recreation guide. Everything runs on one machine with a local GPU.
No paid APIs.

```
Forward a reel to the Business account ─┐
Send a reel link to the Telegram bot ───┼─> queue (SQLite) -> worker, one reel at a time:
Paste a link in the dashboard ──────────┘     download -> ffprobe -> Whisper transcript
                                              -> key frames -> OCR -> vision model (Ollama)
                                              -> text model (Ollama, strict JSON)
                                              -> folder + INDEX -> Telegram message
```

Each reel folder, named `<Topic> - <short description> (@creator)` (no dates):

| File | Contents |
| --- | --- |
| `VIDEO - <topic> reel original.mp4` | Original video |
| `README - Full breakdown and recreation guide.md` | Hook, timed script, shot list, audio, exact on-screen text, caption, CTA, specs, steps to recreate, uncertainties |
| `RESOURCES - Creator links and repos.md` | Every link found in the caption, on screen or spoken, where it was found and how to use it, plus gaps |
| `MANIFEST.md` | File list with reel ID and creator |

`INDEX - All reels catalog.md` in the library root lists every reel in one searchable file.

## What makes the output trustworthy

- **Links come from code, not the AI.** URLs, handles and hashtags are pulled by regex from the
  caption, OCR and transcript. Any link the model makes up is removed and noted under Uncertainties.
- **Exact on-screen text comes from OCR** (RapidOCR), not the vision model, which misreads text.
- **Video facts come from ffprobe** (duration, resolution, fps, aspect ratio).
- **A comment keyword is checked** word for word against the caption and on-screen text.
- **Duplicates are caught twice:** by reel ID/shortcode, and by a hash of the downloaded video
  (catches the same reel arriving once as a forward and once as a pasted link).
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

Open <http://127.0.0.1:8765>. Paste a reel link to test. Files appear in `./data/library/`.

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

## Connect Instagram (Meta webhook)

Meta's API cannot read group chats, so team members forward reels in a normal DM to the company's
Instagram Business account. Everything here is free and works in Development mode without app review.

1. Switch the Instagram account to **Business** or **Creator** and link a Facebook Page.
2. On <https://developers.facebook.com> create an app, add the **Instagram** product
   (messaging), and connect the account.
3. Give the bot a stable public HTTPS address (Meta must reach it):
   - **ngrok** (free static domain): put `NGROK_AUTHTOKEN` and `NGROK_DOMAIN` in `.env`, then
     `docker compose --profile tunnel up -d`.
   - or **Tailscale Funnel** on the host: `tailscale funnel 8765`.
   - Do not use Cloudflare quick tunnels: the URL changes on every restart.
4. In the app's webhook settings set the callback URL to `https://<your-domain>/webhook` and the
   verify token to `META_VERIFY_TOKEN`. Subscribe to the `messages` field.
5. Put the App Secret in `META_APP_SECRET`. Every webhook is checked with it (`X-Hub-Signature-256`).
6. Add each team member as a tester or role on the app, so their DMs trigger the webhook.
7. Set `DASHBOARD_TOKEN` too, because the dashboard is now reachable through the tunnel.
8. Forward a reel from a phone and watch it appear in the dashboard.

## Telegram (alerts and link intake)

1. Create a bot with [@BotFather](https://t.me/BotFather) and put the token in `TELEGRAM_BOT_TOKEN`.
2. Set `TELEGRAM_INTAKE=true`, restart, send `/id` to your bot, and copy the chat ID into
   `TELEGRAM_CHAT_ID` (and your user ID into `TELEGRAM_ALLOWED_USER_IDS`).
3. The bot now messages you when a reel is filed or fails, and queues any reel link you send it.
   Intake needs no public URL, so it works even before the Meta webhook is set up.

## Optional: mirror to Google Drive

Run `rclone config` once and create a remote called `gdrive`. With Docker, copy the resulting
`rclone.conf` into `./rclone/`. Then set `RCLONE_REMOTE=gdrive:CSAFI Reels Library`. Each filed reel
folder and the INDEX are copied to Drive. The local folder stays the source of truth.

## Keep it always on

- Turn off sleep while plugged in (Windows: `powercfg /change standby-timeout-ac 0`).
- Docker restarts the services after a reboot (`restart: unless-stopped`); enable "Start Docker
  Desktop when you sign in". Without Docker, add `start.bat` to Task Scheduler "At log on".
- If the machine was off, Meta retries webhooks for a while. Paste any missed links in the dashboard.
- Back up `data/csafi.db` with the library folder.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Dashboard says "Ollama not running" | Reels get a mock draft. Start Ollama, then press **Retry** on the reel |
| `CUDA out of memory` in Ollama | Use a smaller model or lower `MAX_FRAMES` / `FRAME_WIDTH` |
| Whisper errors about `libcudnn` | Set `WHISPER_DEVICE=cpu` |
| Reel failed at `download` | The reel is private or deleted, or the Meta link expired. Paste the reel link and retry |
| Webhook returns 403 | `META_APP_SECRET` is wrong, or the verify token does not match |
| Breakdown is weak | Try a bigger model, or edit the README by hand. Local 7-8B models are weaker than cloud ones |

Logs: `data/logs/csafi.log`. Per-reel activity: the **Activity** tab on each reel page.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## Layout

```
csafi_bot/
  app.py          FastAPI: webhook, dashboard, login, security headers
  webhook.py      Meta signature check and payload parsing
  pipeline.py     one reel end to end
  worker.py       single background worker
  download.py     Meta CDN link, then yt-dlp fallback (never with cookies)
  media.py        ffprobe, proxy copy, scene detection, frames
  transcribe.py   faster-whisper, GPU with CPU fallback
  ocr.py          RapidOCR
  analyse.py      vision notes, structured synthesis, link enforcement, mock fallback
  ollama.py       Ollama REST client
  schema.py       Pydantic models for the AI output
  naming.py       folder naming rules
  library.py      write packages, INDEX, rclone mirror
  telegram.py     alerts and link intake
  db.py           SQLite schema and queries
  templates/      reel markdown templates and dashboard pages
  static/         dashboard CSS and JS (no external requests)
```
