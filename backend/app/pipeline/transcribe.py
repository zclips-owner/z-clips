"""Lightweight word-level transcription with Vosk (small English model).

This is the transcription engine that fits the 256 MB free tier. The
previous engine, faster-whisper (tiny), peaks at ~630 MB and is therefore
only usable on a >=1 GB host; Vosk peaks at ~210 MB for a 12 s clip and
~238 MB for a 60 s clip (measured locally, 2026-11-20), so it fits the
256 MB container and stays below the render step's own peak (229-342 MB).
Transcription and rendering happen at different times, so the pipeline
peak is the max of the two, not the sum.

Word timings are real Vosk word timestamps (``SetWords(True)``), which is
what the karaoke ASS renderer needs. Accuracy is lower than Whisper
(small model, English only) but the words and timings are genuine.

Honesty rules:
- This module NEVER invents a transcript. On any failure (model missing
  and download failed, ffmpeg conversion failed, Vosk error) it returns
  ``None`` / ``[]`` and the caller renders the clip without captions.
- The model is downloaded ONCE, on first use, into
  ``STORAGE_DIR/models/vosk-small-en`` and cached there for the life of
  the container. A lock file prevents concurrent downloads. If the
  container's storage is ephemeral the model is re-downloaded once on
  the next first use after a restart. The download is verified by
  SHA-256. No model is downloaded on import — only when a transcription
  is actually requested.
- Language: English only (vosk-model-small-en-us-0.15). Other languages
  are not supported by this engine; a non-English clip will produce a
  poor (mostly wrong) transcript, which is still better than inventing
  captions — the words come from the recogniser, not from a script.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
import urllib.request
import wave
import zipfile
from pathlib import Path

from ..config import MODEL_DIR

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model identity
# ---------------------------------------------------------------------------
VOSK_MODEL_NAME = "vosk-small-en"
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
# SHA-256 of the zip, verified 2026-11-20 against the upstream download.
VOSK_MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
# Files that must exist inside the extracted model directory.
VOSK_MODEL_REQUIRED = ("am", "conf", "graph", "ivector")
# Vosk's recogniser state (decoding lattice) grows with the audio length,
# so a long source would push the peak toward the 256 MB ceiling. Transcribe
# in fixed chunks with a fresh recogniser per chunk (the Model itself is
# loaded once and shared), which caps the peak regardless of source length.
# Measured: ~210 MB for a 12 s chunk, ~238 MB for a 60 s single pass.
# 15 s chunks keep the in-app worker peak (app imports + model + one
# chunk) under the 256 MB container limit.
CHUNK_S = 15.0
VOSK_MODEL_ZIP_MB = 41
VOSK_MODEL_DIR_MB = 68

FFMPEG = "/usr/bin/ffmpeg"


def model_dir() -> Path:
    return MODEL_DIR / VOSK_MODEL_NAME


def _model_files_present(d: Path) -> bool:
    return d.is_dir() and all((d / sub).exists() for sub in VOSK_MODEL_REQUIRED)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_vosk_model() -> Path | None:
    """Return the Vosk model directory, downloading it once if needed.

    Returns None when the model cannot be obtained (download failed,
    checksum mismatch, or extraction failed). Never raises.
    """
    d = model_dir()
    if _model_files_present(d):
        return d
    # Serialize concurrent first-use downloads with a lock directory.
    lock = MODEL_DIR / f".{VOSK_MODEL_NAME}.lock"
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        lock.mkdir(exist_ok=True)
        # Re-check after acquiring the (best-effort) lock — another
        # worker may have finished the download while we waited.
        if _model_files_present(d):
            return d
        log.info("Downloading Vosk model (%d MB) to %s", VOSK_MODEL_ZIP_MB, d)
        tmp = Path(tempfile.mkdtemp(prefix="vosk-dl-", dir=str(MODEL_DIR)))
        try:
            zip_path = tmp / "model.zip"
            req = urllib.request.Request(VOSK_MODEL_URL, headers={"User-Agent": "Z-CLIPS/1.0"})
            with urllib.request.urlopen(req, timeout=600) as resp, zip_path.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
            if _sha256_file(zip_path) != VOSK_MODEL_SHA256:
                log.warning("Vosk model download failed checksum verification")
                return None
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp)
            # The zip contains a single top-level directory.
            extracted = [p for p in tmp.iterdir() if p.is_dir()]
            if len(extracted) != 1 or not _model_files_present(extracted[0]):
                log.warning("Vosk model zip has unexpected layout")
                return None
            if d.exists():
                shutil.rmtree(d)
            shutil.move(str(extracted[0]), str(d))
            if not _model_files_present(d):
                return None
            log.info("Vosk model ready at %s", d)
            return d
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - never crash the job
        log.warning("Could not obtain Vosk model: %s", exc)
        return None
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _to_16k_mono_wav(src: str, dest: Path) -> bool:
    """Convert any audio/video file to 16 kHz mono PCM wav for Vosk."""
    cmd = [
        FFMPEG, "-y", "-v", "error",
        "-i", src,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dest),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
    except (subprocess.SubprocessError, OSError):
        return False


def _wav_duration(wav: Path) -> float:
    """Duration in seconds of a wav file (via the wave header)."""
    try:
        with wave.open(str(wav), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (wave.Error, OSError):
        return 0.0


def _transcribe_wav_chunk(rec, wav: Path, offset_s: float) -> list[dict]:
    """Feed one 16 kHz mono wav through a recogniser, offsetting timings."""
    words: list[dict] = []
    with wave.open(str(wav), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
            return []
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                for w in json.loads(rec.Result()).get("result", []):
                    words.append({
                        "start": round(float(w["start"]) + offset_s, 3),
                        "end": round(float(w["end"]) + offset_s, 3),
                        "word": str(w["word"]),
                    })
    for w in json.loads(rec.FinalResult()).get("result", []):
        words.append({
            "start": round(float(w["start"]) + offset_s, 3),
            "end": round(float(w["end"]) + offset_s, 3),
            "word": str(w["word"]),
        })
    return words


def transcribe_with_vosk(audio_path: str, model_path: str) -> list[dict] | None:
    """Transcribe with Vosk. Returns word dicts or None on any failure.

    Each word: {"start": float, "end": float, "word": str}. Never raises.
    Audio longer than CHUNK_S is transcribed in fixed chunks with a fresh
    recogniser per chunk, so peak memory stays capped regardless of source
    length (the Model is loaded once and shared across chunks).
    """
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except Exception as exc:  # noqa: BLE001
        log.warning("vosk package not importable: %s", exc)
        return None
    tmpdir = Path(tempfile.mkdtemp(prefix="vosk-tx-"))
    try:
        SetLogLevel(-1)  # keep Vosk's native LOG lines out of our logs
        wav = tmpdir / "audio16k.wav"
        if not _to_16k_mono_wav(audio_path, wav):
            log.warning("ffmpeg could not extract 16kHz mono audio from %s", audio_path)
            return None
        try:
            model = Model(model_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not load Vosk model from %s: %s", model_path, exc)
            return None
        duration = _wav_duration(wav)
        words: list[dict] = []
        t0 = time.time()
        if duration <= CHUNK_S:
            rec = KaldiRecognizer(model, 16000)
            rec.SetWords(True)
            try:
                words = _transcribe_wav_chunk(rec, wav, 0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("Vosk transcription failed: %s", exc)
                return None
        else:
            # Long source: split into CHUNK_S pieces with ffmpeg and
            # transcribe each with a fresh recogniser (offsets applied).
            n_chunks = int(duration // CHUNK_S) + (1 if duration % CHUNK_S else 0)
            for i in range(n_chunks):
                start = i * CHUNK_S
                chunk_wav = tmpdir / f"chunk{i}.wav"
                cmd = [
                    FFMPEG, "-y", "-v", "error",
                    "-ss", str(start), "-t", str(CHUNK_S),
                    "-i", str(wav),
                    "-c:a", "pcm_s16le", str(chunk_wav),
                ]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                except (subprocess.SubprocessError, OSError):
                    r = None
                if r is None or r.returncode != 0 or not chunk_wav.is_file():
                    log.warning("Could not extract audio chunk %d at %.1fs", i, start)
                    return None
                rec = KaldiRecognizer(model, 16000)
                rec.SetWords(True)
                try:
                    words.extend(_transcribe_wav_chunk(rec, chunk_wav, start))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Vosk transcription failed on chunk %d: %s", i, exc)
                    return None
                chunk_wav.unlink(missing_ok=True)
        log.info("Vosk transcribed %d words in %.1fs", len(words), time.time() - t0)
        return words
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def transcribe_words(audio_path: str) -> list[dict]:
    """Best-effort Vosk transcription. Returns [] on any failure.

    This is the single entry point the pipeline uses: it ensures the
    model (downloading once on first use) and returns word dicts, or []
    when transcription is unavailable so the clip renders without
    captions (honest degradation).
    """
    d = ensure_vosk_model()
    if d is None:
        return []
    words = transcribe_with_vosk(audio_path, str(d))
    return words if words is not None else []
