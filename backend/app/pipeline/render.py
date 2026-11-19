"""Vertical 9:16 clip rendering for Z CLIPS (ffmpeg CLI only).

Direct cuts only — no fade transitions, per the product spec.
Audio is loudness-normalised to -14 LUFS (single-pass linear loudnorm).
Captions are burned in ONLY from a real faster-whisper transcript with
word timings; when no transcript exists, the clip is rendered with NO
caption track and that fact is logged — caption text is never invented.
Facecam "auto" honestly means: no separate facecam track was detected,
so the full frame is used (no fake PiP). Manual (Pro) overlays a PiP
crop of the given source region at bottom-right.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..storage import user_clip_dir
from .analyze import load_transcript_for_job, probe_source

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

ALLOWED_HEIGHTS = (1280, 1920, 3840)

# ASS styles, one per key in app.plans.CAPTION_STYLES. All are distinct:
# different font size / weight / outline / highlight colour. Karaoke uses
# word-level {\k} timing; the others use whole-line events.
# Colours are ASS &HAABBGGRR. Font is DejaVu Sans (present on Linux hosts;
# fontconfig substitutes elsewhere).
ASS_STYLES = {
    "simple": {
        "Fontname": "DejaVu Sans", "Fontsize": 52, "PrimaryColour": "&H00FFFFFF",
        "SecondaryColour": "&H0000FFFF", "OutlineColour": "&H00000000",
        "BackColour": "&H80000000", "Bold": 0, "Italic": 0, "BorderStyle": 1,
        "Outline": 2, "Shadow": 0, "Alignment": 2, "MarginV": 90,
    },
    "bold": {
        "Fontname": "DejaVu Sans", "Fontsize": 68, "PrimaryColour": "&H00FFFFFF",
        "SecondaryColour": "&H0000FFFF", "OutlineColour": "&H00000000",
        "BackColour": "&H80000000", "Bold": -1, "Italic": 0, "BorderStyle": 1,
        "Outline": 3, "Shadow": 1, "Alignment": 2, "MarginV": 100,
    },
    "karaoke": {
        "Fontname": "DejaVu Sans", "Fontsize": 68, "PrimaryColour": "&H00FFFFFF",
        "SecondaryColour": "&H0000A5FF", "OutlineColour": "&H00000000",
        "BackColour": "&H80000000", "Bold": -1, "Italic": 0, "BorderStyle": 1,
        "Outline": 3, "Shadow": 1, "Alignment": 2, "MarginV": 100,
    },
    "minimal": {
        "Fontname": "DejaVu Sans", "Fontsize": 38, "PrimaryColour": "&H00E0E0E0",
        "SecondaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000",
        "BackColour": "&H80000000", "Bold": 0, "Italic": 0, "BorderStyle": 1,
        "Outline": 1, "Shadow": 1, "Alignment": 2, "MarginV": 70,
    },
    "neon": {
        "Fontname": "DejaVu Sans", "Fontsize": 62, "PrimaryColour": "&H00FFFFFF",
        "SecondaryColour": "&H0000FFFF", "OutlineColour": "&H00FF00FF",
        "BackColour": "&H80000000", "Bold": -1, "Italic": 0, "BorderStyle": 1,
        "Outline": 3, "Shadow": 0, "Alignment": 2, "MarginV": 100,
    },
}


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _group_words(words: list[dict], start_s: float, end_s: float) -> list[list[dict]]:
    """Group segment words into caption lines (<=7 words, <=2.5s, or a
    sentence end). Words are already in time order."""
    lines: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if w["end"] <= start_s or w["start"] >= end_s:
            continue
        current.append(w)
        span = current[-1]["end"] - current[0]["start"]
        if len(current) >= 7 or span >= 2.5 or w["word"].strip().endswith((".", "!", "?")):
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


def write_ass_file(path: Path, words: list[dict], start_s: float, end_s: float,
                   style: str, height: int) -> Path:
    """Write an ASS caption file for one clip segment from word timings.

    Times are relative to the clip start. Karaoke lines carry per-word
    karaoke timing tags; other styles carry whole-line text. Only words from
    the real transcript are used.
    """
    style = style if style in ASS_STYLES else "simple"
    scale = height / 1280.0
    base = ASS_STYLES[style]
    fontsize = max(20, int(round(base["Fontsize"] * scale)))
    margin_v = max(30, int(round(base["MarginV"] * scale)))
    outline = base["Outline"]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(height * 9 / 16)}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {style},{base['Fontname']},{fontsize},{base['PrimaryColour']},"
        f"{base['SecondaryColour']},{base['OutlineColour']},{base['BackColour']},"
        f"{base['Bold']},{base['Italic']},0,0,100,100,0,0,{base['BorderStyle']},"
        f"{outline},{base['Shadow']},{base['Alignment']},20,20,{margin_v},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = _group_words(words, start_s, end_s)
    events = []
    for line in lines:
        line_start = max(0.0, line[0]["start"] - start_s)
        line_end = min(end_s - start_s, line[-1]["end"] - start_s)
        if line_end <= line_start:
            continue
        if style == "karaoke":
            text = ""
            for w in line:
                cs = max(1, int(round((w["end"] - w["start"]) * 100)))
                text += f"{{\\k{cs}}}{_escape_ass(w['word'].strip())} "
            text = text.strip()
        else:
            text = " ".join(_escape_ass(w["word"].strip()) for w in line)
        events.append(
            f"Dialogue: 0,{_ass_time(line_start)},{_ass_time(line_end)},{style},,"
            f"0,0,0,,{text}"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def _ffmpeg_escape_path(path: str) -> str:
    """Escape a file path for use inside an ffmpeg filter argument."""
    p = path.replace("\\", "/")
    p = p.replace("'", "'\\''")
    return "'" + p.replace(":", "\\:") + "'"


def _run_ffmpeg(args: list[str], what: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        tail = (result.stderr or "")[-1500:]
        raise RuntimeError(f"ffmpeg failed while {what} (exit {result.returncode}): {tail}")


def _make_thumbnail(source_path: str, clip_path: str, thumb_path: Path, at_s: float) -> Path:
    _run_ffmpeg([
        FFMPEG, "-y", "-v", "error", "-ss", f"{at_s:.2f}", "-i", str(clip_path),
        "-frames:v", "1", "-q:v", "3", str(thumb_path),
    ], "extracting the thumbnail")
    if not thumb_path.is_file() or thumb_path.stat().st_size == 0:
        raise RuntimeError("Thumbnail was not created or is empty.")
    return thumb_path


def render_clip(db, clip, job, source) -> dict:
    """Render one Clip to vertical 9:16.

    Returns {file_path, caption_path, thumb_path, width, height}.
    Raises RuntimeError on failure; the caller (run.process_job) records
    it and continues with the other candidates. The output file is
    verified to exist and be non-empty before returning.
    """
    height = int(job.export_height) if int(job.export_height) in ALLOWED_HEIGHTS else 1280
    width = height * 9 // 16
    duration = max(0.1, float(clip.end_s) - float(clip.start_s))
    out_dir = user_clip_dir(job.user_id)
    out_path = out_dir / f"clip_{clip.id}.mp4"
    ass_path = out_dir / f"clip_{clip.id}.ass"
    thumb_path = out_dir / f"clip_{clip.id}_thumb.jpg"

    # Captions: only from a real transcript. No transcript -> no captions.
    words = load_transcript_for_job(job)
    seg_words = [w for w in words if w["end"] > float(clip.start_s) and w["start"] < float(clip.end_s)]
    caption_path = ""
    if seg_words:
        write_ass_file(ass_path, seg_words, float(clip.start_s), float(clip.end_s),
                       job.caption_style or "simple", height)
        caption_path = str(ass_path)
    elif ass_path.is_file():
        ass_path.unlink()

    # Video filter: scale up to cover, centre-crop to 9:16 (works for both
    # landscape and already-vertical sources), then burn captions if any.
    main_vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=yuv420p"
    )
    if caption_path:
        main_vf += f",subtitles={_ffmpeg_escape_path(caption_path)}"

    facecam_mode = (job.facecam_mode or "auto").lower()
    has_audio = bool(source.has_audio)
    args: list[str] = [FFMPEG, "-y", "-v", "error",
                       "-ss", f"{float(clip.start_s):.3f}", "-t", f"{duration:.3f}",
                       "-i", str(source.stored_path)]
    if facecam_mode == "manual" and all(
        v is not None for v in (job.facecam_x, job.facecam_y, job.facecam_w, job.facecam_h)
    ):
        # PiP crop of the given source region, bottom-right. Coordinates
        # are fractions of the source frame (0-1); values >1 are treated
        # as absolute pixels (defensive, for older callers).
        info = probe_source(source.stored_path)
        sw, sh = info["width"], info["height"]
        vals = [float(job.facecam_x), float(job.facecam_y), float(job.facecam_w), float(job.facecam_h)]
        if max(vals) > 1.0:
            fx, fy, fw, fh = (int(v) for v in vals)
        else:
            fx, fy = int(vals[0] * sw), int(vals[1] * sh)
            fw, fh = int(vals[2] * sw), int(vals[3] * sh)
        fw = max(16, min(fw, sw - fx))
        fh = max(16, min(fh, sh - fy))
        fx = max(0, min(fx, sw - fw))
        fy = max(0, min(fy, sh - fh))
        pip_w, pip_h = width // 4, (width // 4) * 16 // 9
        args += ["-ss", f"{float(clip.start_s):.3f}", "-t", f"{duration:.3f}",
                 "-i", str(source.stored_path)]
        filter_complex = (
            f"[0:v]{main_vf}[main];"
            f"[1:v]crop={fw}:{fh}:{fx}:{fy},scale={pip_w}:{pip_h},setsar=1[pip];"
            f"[main][pip]overlay={width - pip_w - 16}:{height - pip_h - 16}[vout]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[vout]"]
    else:
        if facecam_mode == "auto":
            # Honest auto behaviour: no separate facecam track was
            # detected, so the full frame is used. No fake PiP is drawn.
            pass
        args += ["-vf", main_vf, "-map", "0:v"]
    if has_audio:
        args += ["-map", "0:a", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:linear=true",
                 "-c:a", "aac", "-b:a", "160k"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-t", f"{duration:.3f}", str(out_path)]

    _run_ffmpeg(args, f"rendering clip {clip.id}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"Render produced no output file for clip {clip.id}.")
    _make_thumbnail(source.stored_path, str(out_path), thumb_path, duration / 2.0)
    return {
        "file_path": str(out_path),
        "caption_path": caption_path,
        "thumb_path": str(thumb_path),
        "width": width,
        "height": height,
    }
