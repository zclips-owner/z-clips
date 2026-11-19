"""Technical QC for rendered clips.

Every check is measured, never assumed. A check that cannot be run
(freezedetect unavailable) is reported as a warning — not as a pass —
and a warning-severity check never fails the clip on its own. Only
``severity == "fail"`` checks decide the overall ``passed`` value.

run_qc(clip_path, caption_path, expected) -> {
    "passed": bool,
    "checks": [{"name": str, "passed": bool, "detail": str, "severity": "fail"|"warn"}]
}

``expected`` keys (all optional except duration when known):
  duration_s, width, height, has_audio, captions_expected,
  other_clip_hashes (list of sha256 hex strings of the user's other clips).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

FFPROBE = "/usr/bin/ffprobe"
FFMPEG = "/usr/bin/ffmpeg"


def _probe(path: str) -> dict | None:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _black_segments(path: str, duration: float) -> tuple[list[float], str]:
    """Durations (s) of black segments >0.5s, via blackdetect."""
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path),
         "-vf", "blackdetect=d=0.5:pic_th=0.98:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        return [], f"blackdetect could not run: {(result.stderr or '')[-300:].strip()}"
    durations = [float(m.group(1)) for m in
                 re.finditer(r"black_duration:([0-9.]+)", result.stderr or "")]
    return durations, ""


def _frozen_segments(path: str) -> tuple[list[float] | None, str]:
    """Durations (s) of frozen segments, via freezedetect.

    Returns (None, reason) when freezedetect is unavailable — that is a
    warning, not a pass.
    """
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path),
         "-vf", "freezedetect=n=0.003:d=0.5",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    stderr = result.stderr or ""
    if result.returncode != 0 or "freezedetect" in stderr and "No such filter" in stderr:
        return None, "freezedetect filter unavailable in this ffmpeg build"
    if result.returncode != 0:
        return None, f"freezedetect could not run: {stderr[-300:].strip()}"
    durations = [float(m.group(1)) for m in re.finditer(r"freeze_duration:([0-9.]+)", stderr)]
    return durations, ""


def _caption_parses(path: str) -> tuple[bool, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"caption file unreadable: {exc}"
    if "[Events]" not in text:
        return False, "caption file has no [Events] section"
    dialogues = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    if not dialogues:
        return False, "caption file has no Dialogue lines"
    bad = 0
    for ln in dialogues:
        parts = ln.split(",", 9)
        if len(parts) < 10:
            bad += 1
            continue
        if not re.match(r"^\d+:\d{2}:\d{2}\.\d{2}$", parts[1].strip()):
            bad += 1
    if bad:
        return False, f"{bad} of {len(dialogues)} caption lines have bad timestamps"
    return True, f"{len(dialogues)} caption lines parsed OK"


def run_qc(clip_path, caption_path, expected: dict) -> dict:
    expected = expected or {}
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str, severity: str = "fail") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "severity": severity})

    p = Path(str(clip_path))
    # 1. File exists and is non-empty.
    if not p.is_file():
        add("file_exists", False, f"Clip file not found: {clip_path}")
        return {"passed": False, "checks": checks}
    size = p.stat().st_size
    if size <= 0:
        add("file_exists", False, "Clip file is empty (0 bytes)")
        return {"passed": False, "checks": checks}
    add("file_exists", True, f"File exists, {size} bytes")

    # 2. Probe + duration.
    info = _probe(str(p))
    if info is None:
        add("probe", False, "ffprobe could not read the clip")
        return {"passed": False, "checks": checks}
    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    exp_dur = expected.get("duration_s")
    if exp_dur is not None:
        ok = abs(duration - float(exp_dur)) <= 0.5
        add("duration", ok,
            f"Duration {duration:.2f}s vs expected {float(exp_dur):.2f}s "
            f"(diff {abs(duration - float(exp_dur)):.2f}s, tolerance 0.5s)")
    else:
        add("duration", duration > 0, f"Duration {duration:.2f}s (no expected value supplied)")

    # 3. Video stream + 9:16 dimensions.
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        add("video_stream", False, "No video stream found")
    else:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        exp_w, exp_h = expected.get("width"), expected.get("height")
        if exp_w is not None and exp_h is not None:
            ok = abs(w - int(exp_w)) <= 2 and abs(h - int(exp_h)) <= 2
            add("video_stream", ok, f"Video {w}x{h} vs expected {exp_w}x{exp_h} (tolerance 2px)")
        else:
            ratio_ok = h > 0 and abs(w / h - 9 / 16) < 0.02
            add("video_stream", ratio_ok, f"Video {w}x{h}, aspect {w / h:.3f} (9:16 = 0.5625)")

    # 4. Audio present when expected.
    has_audio_stream = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    if expected.get("has_audio"):
        add("audio_present", has_audio_stream,
            "Audio stream present" if has_audio_stream else "Expected audio but no audio stream found")
    else:
        add("audio_present", True,
            "Audio stream present" if has_audio_stream else "No audio expected and none present")

    # 5. No excessive black frames (fail if black covers >20% of duration).
    if duration > 0:
        black_durs, black_err = _black_segments(str(p), duration)
        if black_err:
            add("no_black_frames", False, black_err)
        else:
            total_black = sum(black_durs)
            ok = total_black <= 0.20 * duration
            add("no_black_frames", ok,
                f"Black segments total {total_black:.2f}s of {duration:.2f}s "
                f"({100 * total_black / duration:.1f}%, limit 20%)")

    # 6. Frozen frames — warn only, never fails the clip.
    frozen, frozen_err = _frozen_segments(str(p))
    if frozen is None:
        add("no_frozen_frames", False,
            f"WARNING: freezedetect unavailable — frozen-frame check not performed ({frozen_err})",
            severity="warn")
    else:
        total_frozen = sum(frozen)
        add("no_frozen_frames", total_frozen == 0,
            f"Frozen segments total {total_frozen:.2f}s"
            + (" — frozen frames detected (warning only, clip not failed)" if total_frozen > 0 else ""),
            severity="warn")

    # 7. Captions exist and parse, when captions are expected.
    if expected.get("captions_expected"):
        if not caption_path:
            add("captions", False, "Captions expected but no caption file was produced")
        else:
            ok, detail = _caption_parses(str(caption_path))
            add("captions", ok, detail)
    else:
        add("captions", True, "No captions expected (no transcript available); no caption track required")

    # 8. Duplicate check against the user's other clips (by sha256).
    others = expected.get("other_clip_hashes") or []
    if others:
        digest = _sha256(str(p))
        add("duplicate_check", digest not in others,
            "Identical file to another of the user's clips (same sha256)" if digest in others
            else "Not identical to any of the user's other clips")
    else:
        add("duplicate_check", True,
            "Skipped: no other-clip hashes supplied to compare against",
            severity="warn")

    passed = all(c["passed"] for c in checks if c.get("severity", "fail") == "fail")
    return {"passed": passed, "checks": checks}
