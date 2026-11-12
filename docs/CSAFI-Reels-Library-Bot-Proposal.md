# CSAFI Reels Library Bot - Project Proposal for Review

*4 November 2026*

An automation that turns Instagram reels shared by the CSAFI team into a searchable, AI-written content library in Google Drive, ready to recreate. This document explains how it works, what it costs to run, and what is needed to go live.

| Key figure | Value |
| --- | --- |
| Running cost, free VPS plan, 10 reels/day | ~$8 / month |
| Running cost, paid VPS plan, 10 reels/day | ~$20 / month |
| AI cost per reel | ~$0.02 |
| Manual work per reel | 1 tap (forward) |

---

## 1. The goal

The CSAFI team shares useful reels in an Instagram group, but they get lost in the chat. Nobody has time to watch each one again, write down the hook and script, or find the links the creator mentioned. This project files every shared reel automatically into one Google Drive library, with a full breakdown and a recreation guide for each.

## 2. How it works

```
Instagram (Business account) -> Meta Webhook -> Bot server (FastAPI on VPS) -> OpenRouter (Gemini 2.5 Flash) -> Google Drive (rclone) -> Slack
```

End to end, a forwarded reel is filed in Drive in about 1 to 2 minutes.

### Step 1 - A team member forwards the reel
In the CSAFI group, tap Share on a reel and send it to the company's Instagram Business account. This is the only manual step.
- Meta's API cannot read group chats, so the reel must arrive as a normal 1-on-1 DM to the Business account.
- A link pasted as text (`instagram.com/reel/...`) also works. The bot finds the link inside the message.
- Optional: an allow-list of sender IDs so only CSAFI team members can submit reels.

### Step 2 - Meta sends a webhook to the bot
Within seconds, Meta posts the message to the bot's HTTPS address. The bot checks it is genuine and replies 200 OK at once.
- One-time setup: Meta calls `GET /webhook` with a challenge. The bot answers only if the verify token matches.
- Every message is signed by Meta with the `X-Hub-Signature-256` header (HMAC-SHA256 using the App Secret). Unsigned or wrongly signed requests are rejected.
- A shared reel arrives as an attachment of type `ig_reel` with a video link, the caption (title) and a `reel_video_id`.
- The bot saves the job in Postgres with status `queued` and returns immediately, so Meta never times out.

### Step 3 - Duplicate check
The bot looks up the reel ID in the database. If the reel is already filed, it is skipped and nothing is charged.
- The `reel_video_id` (or the shortcode from a pasted link) is the unique key.
- Meta sometimes delivers the same webhook twice. The unique key makes this safe.

### Step 4 - Download the video
A background worker downloads the video from the link Meta provided. If that fails, it tries yt-dlp. It retries twice before marking the reel failed.
- Meta's video links are temporary, so the download starts right away.
- ffprobe reads duration, resolution and frame rate for the format specs.
- ffmpeg makes a small 720p copy for the AI. The original stays untouched for Drive.

### Step 5 - AI watches the reel
The small copy is sent to Google Gemini 2.5 Flash through OpenRouter. Gemini sees the frames and hears the audio in one request.
- Request: OpenRouter Chat Completions with a `video_url` part (base64 data URL), as documented by OpenRouter.
- The prompt asks for strict JSON: topic, creator, summary, hook, timed script, shot list, on-screen text, audio, caption and hashtags, call to action, format specs, recreation steps, resources, tags, uncertainties.
- The reply is validated against a schema. If it is invalid, the bot retries up to 2 times with the error attached.
- Rules in the prompt: never invent links or claims; list anything unclear under Uncertainties.
- Token usage and cost from OpenRouter are saved per reel, so spend is always visible.
- No Whisper, no frame extraction and no GPU needed. This keeps the server small and cheap.

### Step 6 - Write the reel package
The bot turns the AI output into README, RESOURCES and MANIFEST files and gives the folder a clear name.
- Folder name format: `<Topic> - <short description> (@creator)`. No dates. Illegal characters removed.
- If a name already exists, a short descriptive phrase is added (never a number or date).
- The reel ID and URL go at the top of the README so anyone can trace it back.

### Step 7 - Upload to Google Drive
rclone copies the folder into the library in Drive, then rebuilds the master INDEX file.
- rclone is a free, widely used sync tool. Google login is done once on the server.
- The upload is checked (file count and sizes) before the reel is marked filed.
- `INDEX - All reels catalog.md` lists every reel in one file, so the whole library is searchable at once.

### Step 8 - Notify and show in the dashboard
Slack gets a short message with the folder name and Drive link. The dashboard shows the reel as filed.
- On any failure, Slack gets an alert with the reel, the failed step and the error.
- The dashboard lists every reel with status, search, the rendered README, and a Retry button.
- Team members can also paste a reel link directly into the dashboard.

> **Why forwarding instead of reading the group:** Meta's official API cannot read group chats. Forwarding to a Business account uses only official, approved APIs, so there is no risk of the Instagram account being banned for automation. No Instagram password is stored and no bot logs into Instagram.

### Database (PostgreSQL)

| Table | Holds |
| --- | --- |
| `reels` | One row per reel: ID, source link, sender, status, folder name, topic, creator, tags, summary, AI output, cost |
| `reel_events` | Step-by-step log per reel (queued, downloaded, analysed, uploaded, failed) for the dashboard |
| `webhook_log` | Raw incoming webhooks (trimmed) for debugging, kept for 14 days |
| `llm_calls` | Model, tokens, cost and time per AI call. No personal data |

### Security
- Every webhook is signature-checked with the Meta App Secret.
- HTTPS only, via Caddy and Let's Encrypt.
- Secrets live in an environment file on the server, never in code or logs.
- Dashboard behind a login token; never shows secrets.
- The bot never likes, follows, comments or sends DMs.
- AI and creator text is sanitised before it is shown in the dashboard.

## 3. What the team gets per reel

| File | Contents |
| --- | --- |
| README - Full breakdown and recreation guide.md | Hook, timed script, scene-by-scene shot list, audio, on-screen text, caption, CTA, format specs, step-by-step recreation guide |
| RESOURCES - Creator links and repos.md | Every real link, repo, tool or PDF mentioned, how to use it, and where it was found |
| MANIFEST.md | One line per file in the folder, with the reel ID and creator |
| VIDEO - \<topic\>.mp4 | The original reel video |
| INDEX - All reels catalog.md | One searchable catalog of every reel in the library (root folder) |

Example folder: "Audio8 TTS Voice Cloning - Open-source TTS repo walkthrough (@githubsignals)". No dates in names. Duplicates are detected and skipped.

**Included**
- Instagram webhook receiver (official Meta API)
- Video download with automatic fallback
- AI breakdown of video, audio and on-screen text
- PostgreSQL database for status, search and cost tracking
- Google Drive sync and master INDEX
- Slack notifications for new reels and failures
- Web dashboard: search, status, add by link, retry
- Deployment on a VPS, setup guide and handover call

**Not included**
- Automatic likes, follows, comments or DMs
- Reading the group chat directly (not allowed by Meta)
- Posting or scheduling content
- Video editing or content recreation itself

## 4. Milestones

| | Milestone | Deliverable |
| --- | --- | --- |
| M1 | Core and pipeline | Webhook server, Postgres database, download, AI analysis, file generation. Demo on sample reels. |
| M2 | Drive, Slack and dashboard | Google Drive sync, master INDEX, Slack alerts, web dashboard with search, manual add and retry. |
| M3 | Go-live and handover | Deploy to the VPS, connect the Meta app, live test, README and handover call. |

Each milestone is demoed and signed off before the next one starts.

## 5. VPS options

The bot needs a small always-on Linux server (VPS) so Meta can reach it. No GPU is needed.

| VPS | Per month | Notes |
| --- | ---: | --- |
| Oracle Cloud Always Free (Arm, up to 4 CPU / 24 GB) | $0 | Free forever, but Oracle can reclaim idle servers and sign-up capacity varies by region |
| DigitalOcean Basic 1 GB / 1 CPU | $6 | Cheapest paid option; enough for a few reels a day |
| DigitalOcean Basic 2 GB / 1 CPU | $12 | Recommended paid option; headroom for ffmpeg and the dashboard |
| Hetzner Cloud CPX22 (2 CPU / 4 GB) | $23.59 | Strong option in Europe; prices rose in 2026 |

**Recommendation:** start on Oracle Cloud Always Free ($0). If it is unavailable or unreliable, move to DigitalOcean 2 GB ($12/month). The move needs no code changes.

## 6. Monthly running costs

### Free VPS plan

| Service | Basis | 5 reels/day | 10 reels/day | 20 reels/day |
| --- | --- | ---: | ---: | ---: |
| AI analysis (Gemini 2.5 Flash via OpenRouter) | About $0.02 per reel | $3 | $6 | $12 |
| VPS (Oracle Cloud Always Free) | Free tier | $0 | $0 | $0 |
| Database (PostgreSQL on Neon) | Free tier, 0.5 GB | $0 | $0 | $0 |
| HTTPS (DuckDNS + Caddy + Let's Encrypt) | Free | $0 | $0 | $0 |
| Google Drive storage | 15 GB free, then Google One 100 GB | $0 | $2 | $2 |
| Meta Instagram API, rclone, Slack | Free | $0 | $0 | $0 |
| **Total per month** | | **~$3** | **~$8** | **~$14** |

### Paid VPS plan (DigitalOcean 2 GB)

Same as above, plus $12/month for the VPS: about **$15, $20 and $26 per month** at 5, 10 and 20 reels a day.

*Source: OpenRouter list price for Gemini 2.5 Flash ($0.30 per million input tokens, $2.50 per million output tokens), Google's 263 tokens per second of video, DigitalOcean and Hetzner list prices. Assumes 60-second reels. Checked 4 Nov 2026, excluding taxes and OpenRouter's card top-up fee.*

## 7. What is needed to go live

| Item | Why |
| --- | --- |
| Instagram Business or Creator account | Receives the forwarded reels |
| Facebook Page linked to that account, and admin access to a Meta developer app | Required by Meta to turn on the messaging webhook |
| OpenRouter account with prepaid credit (from $10) | Pays for the AI analysis |
| VPS: Oracle Cloud account (card for identity check only) or DigitalOcean | Hosts the bot |
| Neon account (free) | Hosts the PostgreSQL database |
| Google account with the Drive folder for the library | Where the library is stored |
| Slack workspace and channel (optional) | Receives notifications |

## 8. Risks and how we handle them

| Risk | Mitigation |
| --- | --- |
| Group chats cannot be read by Meta's API | Reels are forwarded to the Business account in a 1-on-1 DM. One tap per reel. |
| Oracle can reclaim idle free servers | A light daily job keeps it active. Fallback: DigitalOcean 2 GB for $12/month, no code changes. |
| Free-tier limits change | Postgres holds text only (videos live in Drive). Neon's free tier covers thousands of reels. |
| Meta changes its webhook format | Parsing lives in one module, so fixes are small and contained. |
| A reel is private or deleted before download | Marked failed, Slack alert, retry from the dashboard. |
| AI output is wrong or incomplete | Prompt forbids invented links; unclear items go under Uncertainties. |
| AI prices change | The model is a setting and can be switched without code changes. |

## 9. Tech stack

Python and FastAPI on a Linux VPS · PostgreSQL on Neon · Meta Instagram Messaging API (webhooks) · OpenRouter with Google Gemini 2.5 Flash (video input) · yt-dlp and ffmpeg · rclone for Google Drive · Slack Web API · Caddy with Let's Encrypt for HTTPS.

## Acceptance

The project is complete when a reel forwarded to the Business account appears in Google Drive as a named folder with README, RESOURCES, MANIFEST and video, the INDEX is updated, and a Slack message confirms it.
