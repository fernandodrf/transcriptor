#!/usr/bin/env python3
"""Parakeet V3 ASR worker — called by server.py as a subprocess.

Usage: python3.13 parakeet_worker.py <audio_path> [language]
Outputs JSON to stdout: {"segments": [{"text", "start", "end"}, ...], "transcribe_time": float}
"""

import json
import os
import sys
import time


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: parakeet_worker.py <audio_path> [language]"}))
        sys.exit(1)

    audio_path = sys.argv[1]
    # language arg accepted for interface compatibility; Parakeet V3 auto-detects
    # among its 25 supported languages, so no explicit language param is needed.
    _ = sys.argv[2] if len(sys.argv) > 2 else None

    # Redirect stdout to stderr during NeMo import and model loading,
    # because NeMo's internal logger writes to stdout despite being log output.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import nemo.collections.asr as nemo_asr

    model_name = os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")

    print(f"Loading model: {model_name}", file=sys.stderr)
    model = nemo_asr.models.ASRModel.from_pretrained(model_name)

    # Local attention for long audio support (>24 min)
    model.change_attention_model("rel_pos_local_attn", att_context_size=[256, 256])

    print(f"Transcribing: {audio_path}", file=sys.stderr)
    t0 = time.time()
    output = model.transcribe([audio_path], timestamps=True)
    transcribe_time = time.time() - t0

    result = output[0]
    text = result.text if hasattr(result, "text") else str(result)

    # Extract segment-level timestamps
    # NeMo returns timestamp as a dict with keys: segment, word, char, timestep
    # Each entry is a list of dicts, e.g. {"segment": "text", "start": 3.52, "end": 4.64}
    segments = []
    ts = getattr(result, "timestamp", None) or {}
    if isinstance(ts, dict) and ts.get("segment"):
        for seg in ts["segment"]:
            segments.append({
                "text": seg.get("segment", ""),
                "start": round(seg.get("start", 0), 3),
                "end": round(seg.get("end", 0), 3),
            })

    # Fallback: group word-level timestamps into sentences by punctuation
    if not segments and isinstance(ts, dict) and ts.get("word"):
        current_words = []
        seg_start = None
        for w in ts["word"]:
            word_text = w.get("word", "")
            word_start = w.get("start", 0)
            word_end = w.get("end", 0)
            if seg_start is None:
                seg_start = word_start
            current_words.append(word_text)
            if word_text.rstrip().endswith((".", "?", "!")):
                segments.append({
                    "text": " ".join(current_words).strip(),
                    "start": round(seg_start, 3),
                    "end": round(word_end, 3),
                })
                current_words = []
                seg_start = None
        if current_words:
            segments.append({
                "text": " ".join(current_words).strip(),
                "start": round(seg_start, 3),
                "end": round(word_end, 3),
            })

    # Last fallback: single segment with full text
    if not segments:
        segments = [{"text": text, "start": 0.0, "end": 0.0}]

    # Restore real stdout for JSON output
    sys.stdout = real_stdout
    print(json.dumps({
        "segments": segments,
        "transcribe_time": round(transcribe_time, 2),
    }))


if __name__ == "__main__":
    main()
