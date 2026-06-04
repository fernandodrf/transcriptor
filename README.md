# Transcriptor

A self-hostable meeting-transcription web app with **speaker diarization**. Upload
audio, get back a clean, speaker-labelled, time-stamped transcript you can search,
edit, and export. Runs against a privacy-respecting cloud API or fully offline on
your own GPU.

> This is the open-source edition. It ships a small FastAPI backend, a dependency-free
> vanilla-JS web UI, and a CLI client.

---

## Features

- **Two transcription engines**
  - **Soniox** (cloud) — fast, multilingual, with diarization. EU endpoint available.
  - **Parakeet V3** (local, optional) — NVIDIA NeMo ASR + [pyannote](https://github.com/pyannote/pyannote-audio)
    diarization, fully offline. Self-disables if the local environment isn't present.
- **Speaker diarization** — segments are grouped and labelled per speaker; rename speakers inline.
- **Web UI** (no build step) — drag-and-drop upload, batch processing, live audio player with
  click-to-seek sync, full-text search, inline transcript editing, and per-transcript history
  (stored in your browser's `localStorage`).
- **Exports** — JSON, Markdown, or plain text.
- **CLI client** — transcribe from the terminal, compare providers, export to file.
- **Optional auth** — set `APP_PASSWORD` to gate the whole app behind HTTP Basic; otherwise it runs open.

---

## Quick start

```bash
# 1. Install
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Configure
cp .env.example .env
#   Edit .env and set SONIOX_API_KEY=...   (get a key at https://soniox.com/)

# 3. Run
.venv/bin/python server.py
#   -> open http://localhost:8700
```

### Docker

```bash
cp .env.example .env   # fill in SONIOX_API_KEY
docker compose up --build -d
```

The Docker image is **cloud-only** (no torch/NeMo) — small and quick to build. The local
Parakeet provider runs only from a source checkout with a GPU (see below).

### CLI

```bash
python transcribe_cli.py meeting.mp3                       # default: soniox
python transcribe_cli.py meeting.mp3 -p all -f markdown    # compare engines
python transcribe_cli.py meeting.mp3 -f json -o out.json
```

---

## Architecture

```
┌─────────────┐   multipart    ┌────────────────────┐
│  Web UI     │ ─────────────▶ │  FastAPI server    │
│  (vanilla   │   /api/        │  server.py         │
│   JS)       │ ◀───────────── │                    │
└─────────────┘   JSON         │  ┌──────────────┐  │   REST    ┌──────────┐
                               │  │ Soniox       │ ─┼─────────▶ │ Soniox   │
┌─────────────┐                │  └──────────────┘  │           │ cloud    │
│  CLI client │ ─────────────▶ │  ┌──────────────┐  │           └──────────┘
│ transcribe  │                │  │ Parakeet V3  │  │  subprocess (Py 3.13)
│  _cli.py    │                │  │  (optional)  │ ─┼─▶ parakeet_worker.py
└─────────────┘                │  └──────────────┘  │   + pyannote diarization
                               └────────────────────┘
```

Every provider returns the same shape, so the UI and CLI don't care which engine ran:

```json
{
  "provider": "soniox",
  "duration_sec": 12.4,
  "segments": [
    { "speaker": "SPEAKER_0", "text": "Hello.", "start": 0.0, "end": 1.2 }
  ]
}
```

### API

| Method | Path                  | Description                                  |
|--------|-----------------------|----------------------------------------------|
| GET    | `/health`             | Liveness probe                               |
| GET    | `/api/providers`      | Which engines are configured                 |
| POST   | `/api/transcribe`     | Transcribe one file (`provider`, `language`) |
| POST   | `/api/transcribe/all` | Run every configured engine for comparison   |

---

## The local provider (Parakeet V3)

The local engine is optional and only active from a source checkout. It needs a **separate
Python 3.13 virtualenv** because NVIDIA NeMo isn't compatible with 3.14:

```bash
# Separate venv for NeMo (keep it out of the main .venv)
python3.13 -m venv ~/.local/share/parakeet-venv
~/.local/share/parakeet-venv/bin/pip install nemo_toolkit[asr] pyannote.audio torchaudio
```

Then point the server at it (in `.env`):

```bash
PARAKEET_PYTHON=~/.local/share/parakeet-venv/bin/python
HF_TOKEN=hf_...     # for pyannote diarization (free token)
```

`ffmpeg` must be on `PATH` (audio is resampled to 16 kHz mono before inference). On a
modern GPU, Parakeet V3 transcribes ~16 min of audio in a few seconds. If the venv or
worker is missing, the server logs nothing alarming — it just exposes Soniox only.

---

## Privacy

Transcription touches audio you may not want to leak, so engine choice matters:

- **Soniox (cloud)** — audio is uploaded for processing, then this app **deletes the uploaded
  file and the transcription from Soniox's servers** after fetching the result (see the
  `finally` block in `transcribe_soniox`). An EU endpoint is available via `SONIOX_API_URL`.
  You can also purge everything anytime: `python transcribe_cli.py --cleanup-soniox`.
- **Parakeet V3 (local)** — audio **never leaves your machine**. Fully offline inference and
  diarization. Best option for sensitive recordings.

---

## Configuration

All config is via environment variables (or `.env`). See [`.env.example`](.env.example) for
the full list. The essentials:

| Variable          | Purpose                                                            |
|-------------------|-------------------------------------------------------------------|
| `SONIOX_API_KEY`  | Soniox cloud key                                                  |
| `APP_PASSWORD`    | If set, protects the app with HTTP Basic auth (else open)         |
| `PARAKEET_PYTHON` | Path to the Python 3.13 interpreter for the local provider        |
| `HF_TOKEN`        | HuggingFace token for pyannote diarization (local provider)       |
| `PORT`            | Server port (default `8700`)                                      |
| `MAX_UPLOAD_MB`   | Upload size cap (default `500`)                                   |

Secrets can also be supplied as Docker secrets — the server reads `/run/secrets/<name>`
before falling back to the env var.

---

## Tech

Python 3.14 · FastAPI · httpx · vanilla JS (no framework, no build) · NVIDIA NeMo (Parakeet V3)
· pyannote.audio · Docker.

## License

[MIT](LICENSE)
