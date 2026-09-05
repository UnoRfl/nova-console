#!/usr/bin/env python3
"""
nova_stt.py -- local speech-to-text with Whisper.

The panel records audio in the browser (which already has the right
microphone) and posts the bytes here; this module turns them into text.
Nothing is uploaded anywhere: the model runs on this machine, offline, once
the weights have been fetched the first time.

    python nova_stt.py --warm            # download the model and load it
    python nova_stt.py path/to/clip.webm # transcribe a file, for testing

faster-whisper decodes through PyAV, so the browser's WebM/Opus recording is
read directly -- no ffmpeg, no temporary WAV conversion.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from pathlib import Path

# "small.en" is the accuracy/speed sweet spot on CPU for English dictation.
# Override in ~/.nova/config.json with {"whisper_model": "base.en"}.
DEFAULT_MODEL = "small.en"

_model = None
_model_name = None
_lock = threading.Lock()
_load_error: str | None = None


def config_model() -> str:
    cfg_path = Path.home() / ".nova" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return str(cfg.get("whisper_model") or DEFAULT_MODEL)
    except (OSError, ValueError):
        return DEFAULT_MODEL


def load(name: str | None = None):
    """Load the model once. Safe to call from several threads."""
    global _model, _model_name, _load_error
    want = name or config_model()

    with _lock:
        if _model is not None and _model_name == want:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            _load_error = f"faster-whisper is not installed: {exc}"
            return None
        try:
            # int8 keeps it quick on a CPU without a meaningful accuracy cost
            # for dictation-length clips, and the default thread count is
            # conservative -- give it the cores the machine actually has.
            import os
            threads = max(4, (os.cpu_count() or 4))
            _model = WhisperModel(want, device="cpu", compute_type="int8",
                                  cpu_threads=threads, num_workers=1)
            _model_name = want
            _load_error = None
        except Exception as exc:                      # noqa: BLE001 - report anything
            _load_error = f"{type(exc).__name__}: {exc}"
            _model = None
        return _model


def status() -> dict:
    return {
        "loaded": _model is not None,
        "model": _model_name or config_model(),
        "error": _load_error,
    }


def transcribe(data: bytes) -> dict:
    """Turn recorded audio bytes into text."""
    if not data:
        return {"error": "empty_audio", "detail": "No audio was received."}

    model = load()
    if model is None:
        return {"error": "model_unavailable", "detail": _load_error or "Model failed to load."}

    began = time.time()
    try:
        segments, info = model.transcribe(
            io.BytesIO(data),
            language="en",
            # Greedy decoding. For short spoken commands it is roughly twice as
            # fast as a beam search and the wording comes out the same.
            beam_size=1,
            # Trims silence so a long pause before speaking does not become a
            # hallucinated sentence -- Whisper invents text on pure silence.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:                          # noqa: BLE001
        return {"error": "transcribe_failed", "detail": f"{type(exc).__name__}: {exc}"}

    return {
        "text": text,
        "ms": int((time.time() - began) * 1000),
        "bytes": len(data),
        "duration": round(getattr(info, "duration", 0.0), 2),
        "model": _model_name,
    }


def warm_in_background() -> None:
    """Load the model without blocking the bridge's startup."""
    threading.Thread(target=load, daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser(description="NOVA local speech-to-text")
    ap.add_argument("clip", nargs="?", help="audio file to transcribe")
    ap.add_argument("--warm", action="store_true", help="download and load the model, then exit")
    ap.add_argument("--model", help="override the model name")
    args = ap.parse_args()

    if args.warm or not args.clip:
        print(f"loading {args.model or config_model()} (first run downloads the weights)...")
        began = time.time()
        model = load(args.model)
        if model is None:
            print(f"FAILED: {_load_error}")
            return 1
        print(f"ready in {time.time() - began:.1f}s")
        return 0

    data = Path(args.clip).read_bytes()
    print(json.dumps(transcribe(data), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
