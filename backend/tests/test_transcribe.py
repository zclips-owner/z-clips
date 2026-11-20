"""Tests for the lightweight Vosk transcription engine.

The Vosk model is only present when it has been installed (owner install
or first-use download). The real-engine test is therefore skipped when
the model files are absent, so the suite stays green in a clean checkout.
The failure-path tests never need the model.
"""
import json
from pathlib import Path

import pytest

from app.pipeline import transcribe as tx

ASSETS = Path(__file__).resolve().parent / "assets"
SPEECH_WAV = ASSETS / "speech_short.wav"


def _vosk_model_present() -> bool:
    try:
        return tx._model_files_present(tx.model_dir())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Real engine (skipped when the Vosk model is not installed)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _vosk_model_present(), reason="Vosk model not installed")
def test_transcribe_words_returns_word_timings():
    """A short real speech file yields word dicts with start/end/word."""
    words = tx.transcribe_words(str(SPEECH_WAV))
    assert isinstance(words, list)
    assert len(words) >= 5, f"expected several words, got {words}"
    for w in words:
        assert set(w.keys()) == {"start", "end", "word"}
        assert isinstance(w["start"], float) and isinstance(w["end"], float)
        assert 0.0 <= w["start"] < w["end"] <= 6.5
        assert isinstance(w["word"], str) and w["word"].strip()
    # Timings must be non-decreasing (karaoke depends on ordering).
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    # The spoken line is "Hello and welcome to this short test ...".
    text = " ".join(w["word"] for w in words).lower()
    assert "hello" in text
    assert "welcome" in text


@pytest.mark.skipif(not _vosk_model_present(), reason="Vosk model not installed")
def test_transcribe_source_writes_transcript_file(tmp_path):
    """analyze.transcribe_source writes the per-job transcript JSON."""
    from app.pipeline.analyze import load_transcript_for_job, transcribe_source

    class Job:
        id = 999001
        user_id = 1

    # transcript_path uses user_clip_dir(job.user_id); point it at tmp.
    import app.pipeline.analyze as az
    import app.storage as storage

    orig = storage.user_clip_dir
    storage.user_clip_dir = lambda uid: tmp_path  # type: ignore
    az.user_clip_dir = lambda uid: tmp_path  # type: ignore
    try:
        words = transcribe_source(str(SPEECH_WAV), Job())
        assert len(words) >= 5
        loaded = load_transcript_for_job(Job())
        assert loaded == words
        payload = json.loads((tmp_path / "job_999001_transcript.json").read_text())
        assert payload["model"] == tx.VOSK_MODEL_NAME
        assert payload["error"] == ""
    finally:
        storage.user_clip_dir = orig
        az.user_clip_dir = orig


# ---------------------------------------------------------------------------
# Failure paths — never raise, never crash the job
# ---------------------------------------------------------------------------
def test_transcribe_missing_audio_returns_empty():
    """A missing audio file yields [] (job continues without captions)."""
    assert tx.transcribe_words("/nonexistent/nope.wav") == []


def test_transcribe_with_vosk_bad_model_returns_none(tmp_path):
    """An unusable model directory yields None, not an exception."""
    assert tx.transcribe_with_vosk(str(SPEECH_WAV), str(tmp_path / "no-model")) is None


def test_transcribe_with_vosk_garbage_audio_returns_none(tmp_path):
    """A non-audio file yields None (ffmpeg conversion fails honestly)."""
    bad = tmp_path / "not-audio.txt"
    bad.write_text("this is not audio", encoding="utf-8")
    model = tx.model_dir()
    if not tx._model_files_present(model):
        pytest.skip("Vosk model not installed")
    assert tx.transcribe_with_vosk(str(bad), str(model)) is None


def test_ensure_vosk_model_returns_existing_dir():
    """When the model is already installed, ensure returns it (no download)."""
    if not _vosk_model_present():
        pytest.skip("Vosk model not installed")
    d = tx.ensure_vosk_model()
    assert d is not None and d == tx.model_dir()
    assert tx._model_files_present(d)


# ---------------------------------------------------------------------------
# Registry / preference
# ---------------------------------------------------------------------------
def test_vosk_registered_and_preferred():
    """Vosk is in the model registry and is the preferred transcription engine."""
    from app.services import models_manager as mm

    assert "vosk-small-en" in mm._MODEL_BY_NAME
    meta = mm._MODEL_BY_NAME["vosk-small-en"]
    assert meta["min_ram_mb"] <= 256
    assert meta["pip_package"] == "vosk"
    if _vosk_model_present():
        assert mm.is_available("vosk-small-en") is True
        name, _path = mm.get_transcribe_model()
        assert name == "vosk-small-en"
