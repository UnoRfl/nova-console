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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nova_words  # noqa: E402

# "small" is the multilingual model: same size as "small.en", English just as
# good, and it can write the Tagalog he also speaks (measured 2026-11-14 on 22
# clips: Tagalog word errors 67% -> 14%). nova_words.language_for picks en or tl
# per clip. Override in ~/.nova/config.json with {"whisper_model": "small.en"}.
DEFAULT_MODEL = "small"

_model = None
_model_name = None
_lock = threading.Lock()
_load_error: str | None = None
_device = ""


def config_model() -> str:
    cfg_path = Path.home() / ".nova" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
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
        import os
        import numpy as np
        threads = max(4, (os.cpu_count() or 4))
        tried = []
        # The graphics card first: a spoken sentence takes ~0.2 s there against
        # ~1.8 s on the processor, and on a phone call that wait is felt on
        # every turn. Loading succeeds even when a CUDA library is missing, so
        # only a trial run proves the card really works; else the processor.
        for dev in ("cuda", "cpu"):
            try:
                if dev == "cuda":
                    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
                    for sub in (site.glob("*/bin") if site.is_dir() else []):
                        try:
                            os.add_dll_directory(str(sub))
                        except OSError:
                            pass
                        os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                model = WhisperModel(want, device=dev, compute_type="float16" if dev == "cuda" else "int8",
                                     cpu_threads=threads, num_workers=1)
                segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)
                list(segs)
                _model, _model_name, _load_error = model, want, None
                global _device
                _device = dev
                return _model
            except Exception as exc:                  # noqa: BLE001 - report anything
                tried.append(f"{dev}: {type(exc).__name__}: {str(exc)[:120]}")
        _load_error, _model = " | ".join(tried), None
        return _model


def status() -> dict:
    return {
        "loaded": _model is not None,
        "model": _model_name or config_model(),
        "device": _device,
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
        # Decode here rather than hand faster-whisper the bytes: its own
        # decoder calls av.open(metadata_errors=...), which newer PyAV builds
        # dropped, and that failed every clip with a TypeError.
        import av
        import numpy as np
        chunks = []
        with av.open(io.BytesIO(data)) as box:
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            for frame in box.decode(audio=0):
                chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
        if not chunks:
            return {"error": "empty_audio", "detail": "No audio could be decoded."}
        audio = np.concatenate(chunks).astype(np.float32)
        lang = nova_words.language_for(model, audio, _model_name or "")
        segments, info = model.transcribe(
            audio,
            language=lang,
            # Greedy decoding. For short spoken commands it is roughly twice as
            # fast as a beam search and the wording comes out the same.
            beam_size=1,
            # Trims silence so a long pause before speaking does not become a
            # hallucinated sentence -- Whisper invents text on pure silence.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            initial_prompt=nova_words.prompt_for(lang),   # Sir's vocabulary (config.json)
        )
        text = nova_words.fix(" ".join(seg.text.strip() for seg in segments).strip())
    except Exception as exc:                          # noqa: BLE001
        return {"error": "transcribe_failed", "detail": f"{type(exc).__name__}: {exc}"}

    return {
        "text": text,
        "ms": int((time.time() - began) * 1000),
        "bytes": len(data),
        "duration": round(getattr(info, "duration", 0.0), 2),
        "model": _model_name,
        "device": _device,
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
