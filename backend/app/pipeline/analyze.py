"""Source analysis for Z CLIPS: probe, scene cuts, audio energy, and —
when (and only when) a transcription model is genuinely installed —
word-level transcription.

Transcription engines, in preference order:
1. Vosk (small English) — the lightweight engine that fits the 256 MB
   free tier (~210 MB peak). See app/pipeline/transcribe.py.
2. faster-whisper (tiny/base/small) — only on a >=1 GB host (~630 MB
   peak); kept as a fallback for accuracy when it is installed.

Honesty rules enforced here:
- If no transcription engine is installed, no transcript is invented.
  The job log records the exact limitation, analysis proceeds on scene
  cuts + audio energy only, and no caption file is produced downstream.
- Scores are computed from measured signals only (scene cuts, measured
  audio RMS, real transcript words). Without a transcript, the
  speech-presence component is 0, not a guess.
- Everything is deterministic: fixed thresholds, fixed candidate lengths,
  sorted inputs, no randomness, no wall-clock dependence.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from ..storage import user_clip_dir

FFPROBE = "/usr/bin/ffprobe"
FFMPEG = "/usr/bin/ffmpeg"

TRANSCRIPT_NOT_INSTALLED_MSG = (
    "Transcription model not installed — captions will be unavailable; "
    "install Vosk (small English) from Owner > System & Models, or "
    "faster-whisper (tiny) on a host with >=1 GB RAM"
)

# Words that make an opening line a strong hook (checked in the first 3s
# of a candidate when a transcript exists).
STRONG_HOOK_WORDS = {
    "free", "secret", "shocking", "insane", "amazing", "unbelievable",
    "wait", "watch", "stop", "never", "always", "best", "worst", "huge",
    "crazy", "wow", "look", "first", "finally", "breaking", "exclusive",
    "impossible", "unreal", "mind", "blowing",
}

SCENE_THRESHOLD = 0.4
MIN_CLIP_S = 10.0
MAX_CLIP_S = 90.0
PREFERRED_MIN_S = 15.0
PREFERRED_MAX_S = 60.0
TOP_N = 5


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def probe_source(path: str) -> dict:
    """ffprobe a file. Returns duration/width/height/has_audio/streams.

    Raises FileNotFoundError when the file is missing and RuntimeError
    when ffprobe cannot read it — both with a clear, human-readable message.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Source video file not found: {path}")
    if p.stat().st_size == 0:
        raise RuntimeError(f"Source video file is empty: {path}")
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams",
         "-of", "json", str(p)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe could not read source video: {(result.stderr or '').strip()[:400]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned unparseable output: {exc}") from exc
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    width = height = None
    has_audio = False
    fps = 0.0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and width is None:
            width = int(stream.get("width") or 0) or None
            height = int(stream.get("height") or 0) or None
            fr = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
            try:
                num, den = fr.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        elif stream.get("codec_type") == "audio":
            has_audio = True
    if duration <= 0:
        raise RuntimeError("Source video has no readable duration (0s).")
    if width is None or height is None:
        raise RuntimeError("Source video has no video stream.")
    return {
        "duration_s": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "fps": fps,
        "file_size": p.stat().st_size,
        "streams": len(data.get("streams", [])),
    }


# ---------------------------------------------------------------------------
# Scene cuts
# ---------------------------------------------------------------------------
def detect_scene_cuts(path: str, duration: float) -> list[float]:
    """Cut points (seconds) from ffmpeg's scene filter. No extra dependency.

    Returns sorted times including 0.0. On ffmpeg failure returns [0.0]
    (analysis can still proceed on fixed windows) rather than failing.
    """
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path),
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
         "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    cuts = [0.0]
    for line in (result.stderr or "").splitlines():
        if "showinfo" not in line:
            continue
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            if 0.5 < t < duration - 0.5:
                cuts.append(round(t, 3))
    return sorted(set(cuts))


# ---------------------------------------------------------------------------
# Audio energy (per-second RMS, dBFS)
# ---------------------------------------------------------------------------
def audio_energy_per_second(path: str, duration: float) -> dict[int, float]:
    """Mean RMS level (dBFS) per whole second, from one ffmpeg astats pass.

    Returns {second: rms_db}. Empty dict when there is no audio track or
    the measurement produced nothing. -60 dB is used as the floor for
    silence; values are typically in [-60, 0].
    """
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path), "-vn",
         "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    buckets: dict[int, list[float]] = {}
    current_t: float | None = None
    for line in (result.stderr or "").splitlines():
        if "ametadata" not in line and "astats" not in line:
            continue
        m_time = re.search(r"pts_time:([0-9.]+)", line)
        if m_time:
            current_t = float(m_time.group(1))
        m_rms = re.search(r"RMS_level:\s*(-?[0-9.]+|-\s*inf)", line)
        if m_rms and current_t is not None:
            raw = m_rms.group(1).replace(" ", "")
            try:
                val = -60.0 if raw in ("-inf", "-") else float(raw)
            except ValueError:
                continue
            sec = int(current_t)
            if 0 <= sec < math.ceil(duration):
                buckets.setdefault(sec, []).append(max(-60.0, min(0.0, val)))
    out = {s: sum(v) / len(v) for s, v in buckets.items() if v}
    if out:
        return out
    # Fallback: one overall volumedetect value, applied flat to all seconds.
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path), "-vn",
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    m = re.search(r"mean_volume:\s*(-?[0-9.]+)", result.stderr or "")
    if m:
        val = max(-60.0, min(0.0, float(m.group(1))))
        return {s: val for s in range(int(math.ceil(duration)))}
    return {}


# ---------------------------------------------------------------------------
# Transcription (only when a model is genuinely installed)
# ---------------------------------------------------------------------------
def transcript_path(job) -> Path:
    return user_clip_dir(job.user_id) / f"job_{job.id}_transcript.json"


def load_transcript_for_job(job) -> list[dict]:
    """Word list previously written by :func:`analyze_source`, or [].

    Each word: {"start": float, "end": float, "word": str}. Empty when no
    transcript exists — callers must treat that as "no captions", never
    as an invitation to invent caption text.
    """
    p = transcript_path(job)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        words = data.get("words", []) if isinstance(data, dict) else []
        return [w for w in words
                if isinstance(w, dict) and "start" in w and "end" in w and "word" in w]
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []


def transcribe_source(path: str, job) -> list[dict]:
    """Transcribe word-by-word, preferring Vosk, falling back to faster-whisper.

    Returns word dicts, and writes them to the per-job transcript file.
    Returns [] when no transcription engine is usable — the caller logs
    the honest limitation. A transcription failure is also non-fatal: it
    returns [] and raises nothing, so analysis/render can still proceed
    without captions (the error is recorded in the transcript file's
    "error").
    """
    # 1. Vosk (small English) — the lightweight engine that fits the
    #    256 MB free tier. transcribe_words() handles model download
    #    (once, on first use) and never raises.
    try:
        from .transcribe import VOSK_MODEL_NAME, transcribe_words

        words = transcribe_words(path)
        if words:
            payload = {"model": VOSK_MODEL_NAME, "words": words, "error": ""}
            transcript_path(job).write_text(json.dumps(payload), encoding="utf-8")
            return words
    except Exception:
        # Vosk path failed entirely — fall through to faster-whisper.
        pass

    # 2. faster-whisper — only when a model is genuinely installed
    #    (needs >=1 GB RAM; not present on the free tier).
    from ..services.models_manager import get_transcribe_model

    chosen = get_transcribe_model()
    if chosen is None:
        return []
    model_name, model_path = chosen
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(path), word_timestamps=True, vad_filter=False)
        words = []
        for seg in segments:
            for w in (getattr(seg, "words", None) or []):
                words.append({
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": str(w.word),
                })
        payload = {"model": model_name, "words": words, "error": ""}
        transcript_path(job).write_text(json.dumps(payload), encoding="utf-8")
        return words
    except Exception as exc:
        payload = {"model": model_name, "words": [], "error": str(exc)[:500]}
        try:
            transcript_path(job).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass
        return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _mean_energy(energy: dict[int, float], start: float, end: float) -> float:
    secs = [energy[s] for s in range(int(start), int(end) + 1) if s in energy]
    if not secs:
        return -60.0
    return sum(secs) / len(secs)


def _words_in(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["end"] > start and w["start"] < end]


def _score_candidate(start: float, end: float, duration: float, cuts: list[float],
                     energy: dict[int, float], words: list[dict],
                     has_transcript: bool) -> dict:
    length = end - start
    # Duration (25): 15-60s preferred, 10-90s allowed.
    if PREFERRED_MIN_S <= length <= PREFERRED_MAX_S:
        dur_score = 25.0
    elif length < PREFERRED_MIN_S:
        dur_score = 15.0 + max(0.0, length - MIN_CLIP_S) * 2.0
    elif length <= MAX_CLIP_S:
        dur_score = 25.0 - (length - PREFERRED_MAX_S) * 0.5
    else:
        dur_score = 5.0
    # Audio energy (25): RMS normalised from [-60, 0] dB.
    mean_db = _mean_energy(energy, start, end)
    energy_score = max(0.0, min(1.0, (mean_db + 50.0) / 50.0)) * 25.0 if energy else 0.0
    # Speech presence (20): only from a real transcript; 0 without one.
    speech_score = 0.0
    win_words = _words_in(words, start, end) if has_transcript else []
    if has_transcript and win_words:
        covered = sum(max(0.0, min(w["end"], end) - max(w["start"], start)) for w in win_words)
        speech_score = min(1.0, covered / max(length, 1.0)) * 20.0
    # Hook (0-100, contributes 15 to the total).
    hook = 0.0
    if any(abs(c - start) <= 0.75 for c in cuts):
        hook += 40.0
    opening = _words_in(words, start, min(end, start + 3.0)) if has_transcript else []
    opening_text = " ".join(w["word"] for w in opening).strip()
    if opening_text:
        lowered = opening_text.lower()
        if "?" in opening_text or "!" in opening_text:
            hook += 35.0
        elif any(w in lowered.split() for w in STRONG_HOOK_WORDS):
            hook += 25.0
    first3_db = _mean_energy(energy, start, min(end, start + 3.0))
    if energy and first3_db > mean_db + 3.0:
        hook += 25.0
    hook_score = min(100.0, hook)
    # Completeness (15): ends at a scene cut, or at a sentence end.
    if any(abs(c - end) <= 0.75 for c in cuts):
        completeness = 15.0
        end_note = "ends at a scene cut"
    elif win_words and win_words[-1]["word"].strip().endswith((".", "!", "?")):
        completeness = 12.0
        end_note = "ends at a sentence end"
    else:
        completeness = 5.0
        end_note = "ends mid-segment"
    total = min(100.0, dur_score + energy_score + speech_score + hook_score * 0.15 + completeness)
    reasons = []
    reasons.append("starts on a scene cut" if any(abs(c - start) <= 0.75 for c in cuts) else "starts mid-scene")
    if energy:
        reasons.append(f"audio avg {mean_db:.0f} dB")
    if has_transcript:
        reasons.append(f"speech in {len(win_words)} words" if win_words else "no speech detected")
    else:
        reasons.append("no transcript (speech not measured)")
    reasons.append(end_note)
    return {
        "start_s": round(start, 2),
        "end_s": round(end, 2),
        "score": round(total, 1),
        "hook_score": round(hook_score, 1),
        "reason": "; ".join(reasons),
    }


def _candidate_windows(duration: float, cuts: list[float]) -> list[tuple[float, float]]:
    windows: set[tuple[float, float]] = set()
    for start in cuts:
        if start >= duration - MIN_CLIP_S:
            continue
        # End at the next scene cut that lands in the allowed range.
        for cut in cuts:
            if start + MIN_CLIP_S <= cut <= start + MAX_CLIP_S:
                windows.add((round(start, 3), round(cut, 3)))
        # Fixed-length windows as well (deterministic lengths).
        for length in (30.0, 45.0, 60.0, 20.0, 15.0):
            end = start + length
            if end <= duration:
                windows.add((round(start, 3), round(end, 3)))
    # Fallback when there are no usable cuts at all.
    if not windows and duration >= MIN_CLIP_S:
        windows.add((0.0, round(min(duration, 60.0), 3)))
    return sorted(windows)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze_source(db, source, job) -> list[dict]:
    """Analyse a source video and return the top 5 candidate moments.

    Each candidate: {start_s, end_s, score, hook_score, reason}.
    Updates the SourceVideo probe fields (duration/width/height/has_audio/
    file_size/status) on the passed session; the caller commits.
    Raises FileNotFoundError / RuntimeError when the file is missing or
    unreadable — the caller turns that into a failed job.
    """
    info = probe_source(source.stored_path)
    source.duration_s = info["duration_s"]
    source.width = info["width"]
    source.height = info["height"]
    source.has_audio = info["has_audio"]
    source.file_size = info["file_size"]
    source.status = "ready"
    source.error = ""
    db.flush()

    duration = info["duration_s"]
    if duration < MIN_CLIP_S:
        # Too short to clip meaningfully: one whole-file candidate.
        return [{
            "start_s": 0.0,
            "end_s": round(duration, 2),
            "score": 50.0,
            "hook_score": 50.0,
            "reason": "Source is shorter than 10s; whole video returned as a single clip",
        }]

    cuts = detect_scene_cuts(source.stored_path, duration)
    energy = audio_energy_per_second(source.stored_path, duration) if info["has_audio"] else {}

    words = transcribe_source(source.stored_path, job)
    has_transcript = bool(words)

    scored = [
        _score_candidate(s, e, duration, cuts, energy, words, has_transcript)
        for (s, e) in _candidate_windows(duration, cuts)
    ]
    scored.sort(key=lambda c: (-c["score"], c["start_s"], c["end_s"]))
    # Non-maximum suppression: skip a candidate overlapping >50% of the
    # shorter window with one already picked.
    picked: list[dict] = []
    for cand in scored:
        overlap = False
        for kept in picked:
            inter = max(0.0, min(cand["end_s"], kept["end_s"]) - max(cand["start_s"], kept["start_s"]))
            shorter = min(cand["end_s"] - cand["start_s"], kept["end_s"] - kept["start_s"])
            if shorter > 0 and inter / shorter > 0.5:
                overlap = True
                break
        if not overlap:
            picked.append(cand)
        if len(picked) >= TOP_N:
            break
    if not picked and scored:
        picked = scored[:1]
    return picked
