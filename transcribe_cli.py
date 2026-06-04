#!/usr/bin/env python3
"""
Transcriptor CLI — meeting transcription from the terminal.

Usage:
    python transcribe_cli.py meeting.mp3
    python transcribe_cli.py meeting.mp3 --provider parakeet --language de
    python transcribe_cli.py meeting.mp3 --provider all --output transcript.json
    python transcribe_cli.py meeting.mp3 --format markdown --output meeting.md
"""

import os
import sys
import json
import argparse
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SERVER = os.getenv("TRANSCRIPTOR_URL", "http://localhost:8700")


def transcribe_via_server(file_path: str, provider: str, language: str, server_url: str,
                          username: str = "", password: str = "") -> dict:
    """Send file to the Transcriptor server."""
    endpoint = "/api/transcribe/all" if provider == "all" else "/api/transcribe"

    with open(file_path, "rb") as f:
        files = {"file": (Path(file_path).name, f)}
        data = {"language": language}
        if provider != "all":
            data["provider"] = provider

        auth = (username, password) if username else None
        resp = httpx.post(
            f"{server_url}{endpoint}",
            files=files,
            data=data,
            timeout=3600,
            auth=auth,
        )
        resp.raise_for_status()
        return resp.json()


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render_text(result: dict) -> str:
    """Plain text output with speaker labels."""
    lines = []
    current_speaker = None
    for seg in result.get("segments", []):
        speaker = seg.get("speaker", "?")
        ts = format_timestamp(seg.get("start", 0))
        text = seg.get("text", "").strip()
        if speaker != current_speaker:
            lines.append(f"\n[{ts}] {speaker}:")
            current_speaker = speaker
        lines.append(f"  {text}")
    header = f"Provider: {result.get('provider', '?')} | Duration: {result.get('duration_sec', 0):.1f}s"
    return header + "\n" + "─" * len(header) + "\n" + "\n".join(lines)


def render_markdown(result: dict) -> str:
    """Markdown output suitable for notes (Obsidian, etc.)."""
    lines = [
        f"# Meeting Transcript",
        f"",
        f"- **Provider:** {result.get('provider', '?')}",
        f"- **Processing time:** {result.get('duration_sec', 0):.1f}s",
        f"",
        f"---",
        f"",
    ]
    current_speaker = None
    for seg in result.get("segments", []):
        speaker = seg.get("speaker", "?")
        ts = format_timestamp(seg.get("start", 0))
        text = seg.get("text", "").strip()
        if speaker != current_speaker:
            lines.append(f"### {speaker} `{ts}`\n")
            current_speaker = speaker
        lines.append(f"{text}\n")
    return "\n".join(lines)


def render_multi(results: dict, fmt: str) -> str:
    """Render results from multiple providers."""
    parts = []
    for provider_name, result in results.items():
        if "error" in result:
            parts.append(f"\n{'='*60}\n⚠ {provider_name}: {result['error']}\n")
        else:
            if fmt == "markdown":
                parts.append(render_markdown(result))
            else:
                parts.append(render_text(result))
    return "\n\n".join(parts)


def soniox_cleanup():
    """List and delete all files and transcriptions from Soniox."""
    api_key = os.getenv("SONIOX_API_KEY", "")
    if not api_key:
        # Try Docker secret
        try:
            api_key = Path("/run/secrets/soniox_api_key").read_text().strip()
        except (FileNotFoundError, PermissionError):
            pass
    if not api_key:
        print("Error: SONIOX_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    base = os.getenv("SONIOX_API_URL", "https://api.soniox.com").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}

    # List files
    print("Checking Soniox for stored data...\n", file=sys.stderr)
    resp = httpx.get(f"{base}/v1/files", headers=headers, timeout=15)
    resp.raise_for_status()
    files = resp.json().get("files", resp.json() if isinstance(resp.json(), list) else [])

    # List transcriptions
    resp_tx = httpx.get(f"{base}/v1/transcriptions", headers=headers, timeout=15)
    resp_tx.raise_for_status()
    transcriptions = resp_tx.json().get("transcriptions", resp_tx.json() if isinstance(resp_tx.json(), list) else [])

    if not files and not transcriptions:
        print("No files or transcriptions found on Soniox. All clean.", file=sys.stderr)
        return

    if files:
        print(f"Files ({len(files)}):", file=sys.stderr)
        for f in files:
            print(f"  - {f.get('id', '?')}  {f.get('filename', '?')}  {f.get('created_at', '?')}", file=sys.stderr)

    if transcriptions:
        print(f"\nTranscriptions ({len(transcriptions)}):", file=sys.stderr)
        for tx in transcriptions:
            print(f"  - {tx.get('id', '?')}  status={tx.get('status', '?')}  {tx.get('created_at', '?')}", file=sys.stderr)

    print(f"\nTotal: {len(files)} files, {len(transcriptions)} transcriptions", file=sys.stderr)

    confirm = input("\nDelete all? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.", file=sys.stderr)
        return

    deleted_tx = 0
    for tx in transcriptions:
        try:
            httpx.delete(f"{base}/v1/transcriptions/{tx['id']}", headers=headers, timeout=15)
            deleted_tx += 1
        except Exception as e:
            print(f"  Failed to delete transcription {tx['id']}: {e}", file=sys.stderr)

    deleted_f = 0
    for f in files:
        try:
            httpx.delete(f"{base}/v1/files/{f['id']}", headers=headers, timeout=15)
            deleted_f += 1
        except Exception as e:
            print(f"  Failed to delete file {f['id']}: {e}", file=sys.stderr)

    print(f"\nDeleted {deleted_tx} transcriptions and {deleted_f} files.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Transcriptor CLI — meeting transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s meeting.mp3                              # Soniox (default)
  %(prog)s meeting.mp3 -p parakeet                  # Local Parakeet V3
  %(prog)s meeting.mp3 -p all                       # Compare all providers
  %(prog)s meeting.mp3 -p soniox -l de -o out.md -f markdown
  %(prog)s meeting.mp3 -f json -o transcript.json   # JSON export
  %(prog)s --cleanup-soniox                         # Delete all data from Soniox
        """,
    )
    parser.add_argument("file", nargs="?", help="Audio/video file to transcribe")
    parser.add_argument("-p", "--provider", default="soniox",
                        choices=["soniox", "parakeet", "all"],
                        help="Transcription provider (default: soniox)")
    parser.add_argument("-l", "--language", default="auto",
                        help="Language code: en, de, es, auto (default: auto)")
    parser.add_argument("-f", "--format", default="text",
                        choices=["text", "markdown", "json"],
                        help="Output format (default: text)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file (default: stdout)")
    parser.add_argument("-s", "--server", default=DEFAULT_SERVER,
                        help=f"Transcriptor server URL (default: {DEFAULT_SERVER})")
    parser.add_argument("-u", "--username", default=os.getenv("TRANSCRIPTOR_USERNAME", ""),
                        help="HTTP Basic auth username (or TRANSCRIPTOR_USERNAME env var)")
    parser.add_argument("--password", default=os.getenv("TRANSCRIPTOR_PASSWORD", ""),
                        help="HTTP Basic auth password (or TRANSCRIPTOR_PASSWORD env var)")
    parser.add_argument("--cleanup-soniox", action="store_true",
                        help="List and delete all files/transcriptions from Soniox")

    args = parser.parse_args()

    if args.cleanup_soniox:
        soniox_cleanup()
        return

    if not args.file:
        parser.error("the following arguments are required: file")

    if not Path(args.file).exists():
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    print(f"⏳ Transcribing {Path(args.file).name} via {args.provider}...", file=sys.stderr)

    try:
        result = transcribe_via_server(args.file, args.provider, args.language, args.server,
                                      args.username, args.password)
    except httpx.ConnectError:
        print(f"\nError: Cannot connect to server at {args.server}", file=sys.stderr)
        print(f"Start the server first:  python server.py", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"\nError: Authentication failed. Use -u/--password or set TRANSCRIPTOR_USERNAME/TRANSCRIPTOR_PASSWORD.", file=sys.stderr)
        else:
            print(f"\nError: {e.response.status_code} — {e.response.text}", file=sys.stderr)
        sys.exit(1)

    # Format output
    if args.format == "json":
        output = json.dumps(result, indent=2, ensure_ascii=False)
    elif args.provider == "all":
        output = render_multi(result, args.format)
    elif args.format == "markdown":
        output = render_markdown(result)
    else:
        output = render_text(result)

    # Write output
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"✅ Saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
