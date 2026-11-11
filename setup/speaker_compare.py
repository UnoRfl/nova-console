#!/usr/bin/env python3
"""Compare speaker models on the replay clips: how far apart are owner and others?"""
import sys
from pathlib import Path

import numpy as np
import sherpa_onnx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_ears as E  # noqa: E402

CLIPS = Path(__file__).resolve().parent / "replay-clips"
OWNER = "en-GB-RyanNeural"


def run(model: Path) -> None:
    ext = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=4, provider="cpu"))

    def emb(p):
        st = ext.create_stream()
        st.accept_waveform(sample_rate=E.RATE, waveform=E.read_audio(p))
        st.input_finished()
        e = np.asarray(ext.compute(st), dtype=np.float32)
        return e / np.linalg.norm(e)

    by_voice = {}
    for p in sorted(CLIPS.glob("*.mp3")):
        voice = p.stem.split("-", 1)[1].rsplit("-", 1)[0]
        by_voice.setdefault(voice, []).append(emb(p))
    own = by_voice[OWNER]
    mean = np.mean(np.stack(own[:6]), axis=0)
    mean /= np.linalg.norm(mean)
    print(f"\n{model.name}")
    for voice, es in by_voice.items():
        es = es[6:] if voice == OWNER else es
        s = [float(np.dot(e, mean)) for e in es]
        print(f"  {voice:22} min {min(s):.2f}  mean {np.mean(s):.2f}  max {max(s):.2f}"
              + ("   <- owner (unseen clips)" if voice == OWNER else ""))


for name in sys.argv[1:]:
    run(E.MODELS / name)
