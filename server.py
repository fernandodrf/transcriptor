"""
Transcriptor Server — Multi-provider transcription API
Supports: Soniox (cloud), Parakeet V3 (local, optional)

This is the open-source demo edition. It exposes a small transcription API and
serves a single-page web UI. Optional HTTP Basic auth can be enabled with the
APP_PASSWORD environment variable; otherwise the server runs open (intended to
sit behind your own reverse proxy / auth in production).
"""

import os
import time
import shutil
import httpx
import asyncio
import secrets
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("transcriptor")


def get_secret(name: str, env_var: str, default: str = "") -> str:
    """Read a secret from /run/secrets/<name> (Docker), fall back to env var."""
    try:
        return Path(f"/run/secrets/{name}").read_text().strip()
    except (FileNotFoundError, PermissionError):
        return os.getenv(env_var, default)


app = FastAPI(title="Transcriptor", version="1.0.0")

ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8700").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Optional HTTP Basic auth — enabled only when APP_PASSWORD is set.
# The browser handles the native login prompt; no login page required.
# ---------------------------------------------------------------------------

security = HTTPBasic(auto_error=False)
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = get_secret("app_password", "APP_PASSWORD")


async def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """No-op when APP_PASSWORD is unset; otherwise enforce HTTP Basic."""
    if not APP_PASSWORD:
        return
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username, APP_USERNAME)
        and secrets.compare_digest(credentials.password, APP_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------------------------------------------------------------------------
# Rate limiter — simple in-memory per-IP, 30 req/min
# ---------------------------------------------------------------------------

_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "30"))
RATE_LIMIT_WINDOW = 60  # seconds


async def check_rate_limit(request: Request):
    if request.url.path == "/health":
        return
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = [t for t in _rate_limit_store.get(client_ip, []) if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX:
        _rate_limit_store[client_ip] = timestamps
        raise HTTPException(429, "Rate limit exceeded. Try again later.")
    timestamps.append(now)
    _rate_limit_store[client_ip] = timestamps


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mkv", ".wma", ".aac"}
ALLOWED_LANGUAGES = {"auto", "en", "de", "es", "fr", "it", "pt", "nl", "ja", "zh", "ko", "ru", "ar", "hi", "pl", "uk", "cs", "sv", "da", "fi", "el", "he", "hu", "no", "ro", "tr"}


def validate_upload_filename(file: UploadFile) -> str:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            415, f"Unsupported file type: '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return ext


async def save_validated_upload(file: UploadFile) -> str:
    """Stream an upload to a temp file while enforcing size limits."""
    ext = validate_upload_filename(file)
    tmp_path = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, f"File too large. Maximum: {MAX_UPLOAD_BYTES // (1024*1024)} MB"
                    )
                tmp.write(chunk)
        if total == 0:
            raise HTTPException(400, "Empty file")
        return tmp_path
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def validate_language(language: str) -> str:
    """Validate and sanitize language parameter."""
    lang = language.strip().lower()[:5]
    if lang not in ALLOWED_LANGUAGES:
        raise HTTPException(400, f"Unsupported language: '{language}'. Use: {', '.join(sorted(ALLOWED_LANGUAGES))}")
    return lang


# ---------------------------------------------------------------------------
# Unified response format
# ---------------------------------------------------------------------------

def unified_response(provider: str, segments: list, raw: dict = None, duration_sec: float = 0):
    """Return a consistent response shape regardless of provider."""
    return {
        "provider": provider,
        "duration_sec": round(duration_sec, 2),
        "segments": segments,       # [{speaker, text, start, end}, ...]
        "raw": raw,                 # original provider response (optional)
    }


# ---------------------------------------------------------------------------
# Provider: Soniox (cloud)
# ---------------------------------------------------------------------------

SONIOX_API_URL = os.getenv("SONIOX_API_URL", "https://api.soniox.com")
SONIOX_MODEL = os.getenv("SONIOX_MODEL", "stt-async-v4")


async def transcribe_soniox(file_path: str, language: str) -> dict:
    api_key = get_secret("soniox_api_key", "SONIOX_API_KEY")
    if not api_key:
        raise HTTPException(400, "SONIOX_API_KEY not configured")

    base = SONIOX_API_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    t0 = time.time()
    file_id = None
    tx_id = None

    async with httpx.AsyncClient(timeout=600) as client:
        try:
            # Step 1: Upload file
            with open(file_path, "rb") as f:
                upload_resp = await client.post(
                    f"{base}/v1/files",
                    headers=headers,
                    files={"file": (Path(file_path).name, f)},
                )
            upload_resp.raise_for_status()
            file_id = upload_resp.json()["id"]

            # Step 2: Create transcription
            tx_body = {
                "model": SONIOX_MODEL,
                "file_id": file_id,
                "enable_speaker_diarization": True,
            }
            if language and language != "auto":
                tx_body["language_hints"] = [language]
            tx_resp = await client.post(
                f"{base}/v1/transcriptions",
                headers={**headers, "Content-Type": "application/json"},
                json=tx_body,
            )
            tx_resp.raise_for_status()
            tx_id = tx_resp.json()["id"]

            # Step 3: Poll until completed
            for _ in range(180):  # up to 15 min
                status_resp = await client.get(
                    f"{base}/v1/transcriptions/{tx_id}",
                    headers=headers,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()
                status = status_data.get("status")
                if status == "completed":
                    break
                if status == "error":
                    raise HTTPException(
                        500, f"Soniox error: {status_data.get('error_message', 'unknown')}"
                    )
                await asyncio.sleep(5)
            else:
                raise HTTPException(504, "Soniox transcription timed out")

            # Step 4: Get transcript
            transcript_resp = await client.get(
                f"{base}/v1/transcriptions/{tx_id}/transcript",
                headers=headers,
            )
            transcript_resp.raise_for_status()
            data = transcript_resp.json()
        finally:
            # Always clean up uploaded file + transcription on Soniox's servers
            try:
                if tx_id:
                    await client.delete(f"{base}/v1/transcriptions/{tx_id}", headers=headers)
                if file_id:
                    await client.delete(f"{base}/v1/files/{file_id}", headers=headers)
                if tx_id or file_id:
                    logger.info(f"Soniox cleanup: deleted transcription {tx_id} and file {file_id}")
            except Exception as e:
                logger.warning(f"Soniox cleanup failed: {e}")

    # Build segments from tokens, grouping by speaker
    segments = []
    current_speaker = None
    current_text = []
    current_start = 0
    current_end = 0

    for token in data.get("tokens", []):
        speaker = token.get("speaker") or "SPEAKER_0"
        if speaker != current_speaker:
            if current_text:
                segments.append({
                    "speaker": current_speaker or "SPEAKER_0",
                    "text": "".join(current_text).strip(),
                    "start": round(current_start / 1000, 3),
                    "end": round(current_end / 1000, 3),
                })
            current_speaker = speaker
            current_text = [token.get("text", "")]
            current_start = token.get("start_ms", 0)
            current_end = token.get("end_ms", 0)
        else:
            current_text.append(token.get("text", ""))
            current_end = token.get("end_ms", 0)

    if current_text:
        segments.append({
            "speaker": current_speaker or "SPEAKER_0",
            "text": "".join(current_text).strip(),
            "start": round(current_start / 1000, 3),
            "end": round(current_end / 1000, 3),
        })

    return unified_response("soniox", segments, data, time.time() - t0)


# ---------------------------------------------------------------------------
# Provider: Local (Parakeet V3 + pyannote) — optional
#
# Requires a separate Python 3.13 venv with NeMo (NeMo is incompatible with
# 3.14). Point PARAKEET_PYTHON at that interpreter. If the worker script or
# interpreter is missing, the local provider self-disables and the server
# still runs the cloud provider(s) normally.
# ---------------------------------------------------------------------------

PARAKEET_PYTHON = os.getenv("PARAKEET_PYTHON", os.path.expanduser("~/.local/share/parakeet-venv/bin/python"))
PARAKEET_MODEL = os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")
_PARAKEET_WORKER = Path(__file__).parent / "parakeet_worker.py"

# Available only if the worker script + the 3.13 interpreter both exist
_HAS_PARAKEET = _PARAKEET_WORKER.exists() and shutil.which(PARAKEET_PYTHON) is not None

# Lazy-loaded diarization model (heavy, only load once on first request)
_diarize_pipeline = None

# Single-thread executor for Parakeet: the pyannote diarization pipeline holds
# a CUDA context that is thread-local. Pinning to one worker ensures the same
# thread is always used, preventing silent deadlocks on sequential requests.
_parakeet_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet")


def _get_diarize_pipeline():
    global _diarize_pipeline
    if _diarize_pipeline is None:
        hf_token = get_secret("hf_token", "HF_TOKEN")
        if not hf_token:
            return None
        from pyannote.audio import Pipeline
        logger.info("Loading pyannote diarization pipeline...")
        _diarize_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )
        logger.info("Diarization pipeline loaded.")
    return _diarize_pipeline


async def transcribe_parakeet(file_path: str, language: str) -> dict:
    """Transcribe using Parakeet V3 (subprocess) with optional pyannote diarization."""
    t0 = time.time()

    loop = asyncio.get_event_loop()

    def _run():
        import subprocess as sp

        # Convert to 16kHz mono WAV (Parakeet expects single-channel input)
        wav_tmp = file_path + ".16k.wav"
        try:
            sp.run([
                "ffmpeg", "-y", "-i", file_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", wav_tmp,
            ], capture_output=True, check=True)

            # Call Parakeet worker subprocess
            lang_args = [language] if language and language != "auto" else []
            env = {**os.environ, "PARAKEET_MODEL": PARAKEET_MODEL}
            result = sp.run(
                [PARAKEET_PYTHON, str(_PARAKEET_WORKER), wav_tmp] + lang_args,
                capture_output=True, text=True, timeout=600, env=env,
            )
            if result.returncode != 0:
                logger.error(f"Parakeet worker failed: {result.stderr}")
                raise RuntimeError(f"Parakeet transcription failed: {result.stderr[:500]}")

            import json as _json
            worker_output = _json.loads(result.stdout)
            raw_segments = worker_output["segments"]

            # Try diarization (reuse the same WAV for pyannote)
            diarize_pipe = _get_diarize_pipeline()
            if diarize_pipe is not None:
                try:
                    import torchaudio
                    waveform, sr = torchaudio.load(wav_tmp, backend="soundfile")
                    logger.info(f"Diarizing: waveform shape={waveform.shape}, sr={sr}")
                    diarize_result = diarize_pipe({
                        "waveform": waveform,
                        "sample_rate": sr,
                    })
                    # New pyannote wraps result in DiarizeOutput
                    diarization = getattr(diarize_result, "speaker_diarization", diarize_result)
                    # Build list of diarization turns for matching
                    turns = [(turn, speaker) for turn, _, speaker in diarization.itertracks(yield_label=True)]
                    # Assign speaker to each segment based on overlap or nearest turn
                    for seg in raw_segments:
                        seg_mid = (seg["start"] + seg["end"]) / 2
                        best_speaker = None
                        # First try: exact overlap
                        for turn, speaker in turns:
                            if turn.start <= seg_mid <= turn.end:
                                best_speaker = speaker
                                break
                        # Fallback: find nearest turn
                        if best_speaker is None and turns:
                            best_speaker = min(turns, key=lambda t: abs((t[0].start + t[0].end) / 2 - seg_mid))[1]
                        seg["speaker"] = best_speaker or "SPEAKER_0"
                    logger.info(f"Diarization complete: found {len(set(s['speaker'] for s in raw_segments))} speakers")
                except Exception as e:
                    logger.warning(f"Diarization failed: {e}", exc_info=True)
                    for seg in raw_segments:
                        seg["speaker"] = "SPEAKER_0"
            else:
                for seg in raw_segments:
                    seg["speaker"] = "SPEAKER_0"

            return raw_segments
        finally:
            try:
                os.unlink(wav_tmp)
            except OSError:
                pass

    segments = await loop.run_in_executor(_parakeet_executor, _run)
    return unified_response("parakeet", segments, None, time.time() - t0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

PROVIDERS = {
    "soniox": transcribe_soniox,
}

if _HAS_PARAKEET:
    PROVIDERS["parakeet"] = transcribe_parakeet


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/providers", dependencies=[Depends(require_auth), Depends(check_rate_limit)])
async def list_providers():
    """Return which providers are configured."""
    configured = {
        "soniox": bool(get_secret("soniox_api_key", "SONIOX_API_KEY")),
    }
    if _HAS_PARAKEET:
        configured["parakeet"] = True  # available if worker script + python exist
    return configured


@app.post("/api/transcribe", dependencies=[Depends(require_auth), Depends(check_rate_limit)])
async def transcribe(
    file: UploadFile = File(...),
    provider: str = Form("soniox"),
    language: str = Form("auto"),
):
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {provider}. Use: {list(PROVIDERS.keys())}")

    language = validate_language(language)
    tmp_path = await save_validated_upload(file)

    try:
        result = await PROVIDERS[provider](tmp_path, language)
        return JSONResponse(result)
    finally:
        os.unlink(tmp_path)


@app.post("/api/transcribe/all", dependencies=[Depends(require_auth), Depends(check_rate_limit)])
async def transcribe_all(
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """Run transcription on all configured providers for comparison."""
    language = validate_language(language)
    tmp_path = await save_validated_upload(file)

    results = {}
    providers_status = await list_providers()

    try:
        tasks = []
        active_providers = []
        for name, is_configured in providers_status.items():
            if is_configured:
                active_providers.append(name)
                tasks.append(PROVIDERS[name](tmp_path, language))

        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for name, outcome in zip(active_providers, outcomes):
            if isinstance(outcome, Exception):
                results[name] = {"error": str(outcome)}
            else:
                results[name] = outcome
    finally:
        os.unlink(tmp_path)

    return JSONResponse(results)


# Serve the single-page Web UI
static_dir = Path(__file__).parent / "static"


@app.get("/", dependencies=[Depends(require_auth), Depends(check_rate_limit)])
async def serve_index():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index), media_type="text/html")
    raise HTTPException(404, "Web UI not found")


@app.get("/{filename:path}")
async def serve_static(filename: str):
    """Serve static assets (JS, CSS files)."""
    file_path = static_dir / filename
    if file_path.exists() and file_path.is_file() and static_dir in file_path.resolve().parents:
        return FileResponse(str(file_path))
    raise HTTPException(404)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8700"))
    uvicorn.run(app, host="0.0.0.0", port=port,
                proxy_headers=True, forwarded_allow_ips="*")
