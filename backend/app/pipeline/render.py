"""Clip rendering for Z CLIPS (ffmpeg CLI only).

Direct cuts only — no fade transitions, per the product spec.
Audio is loudness-normalised to -14 LUFS (single-pass linear loudnorm).
Captions are burned in ONLY from a real faster-whisper transcript with
word timings; when no transcript exists, the clip is rendered with NO
caption track and that fact is logged — caption text is never invented.
Facecam "auto" honestly means: no separate facecam track was detected,
so the full frame is used (no fake PiP). Manual (Lite/Pro) overlays a PiP
crop of the given source region at bottom-right, or — with a framing
layout — puts that region on the top half (facecam_top) or left half
(facecam_side) of the frame. Aspect ratio, caption typography, sticker
callout and social-post card are per-job editor fields.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..editor_fields import editor_fields_for_job
from ..plans import ASPECT_RATIO_DIMS
from ..storage import user_clip_dir
from .analyze import load_transcript_for_job, probe_source

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

ALLOWED_HEIGHTS = (1280, 1920, 3840)

# Karaoke current-word highlight: yellow (ASS &HAABBGGRR -> &H0000FFFF).
KARAOKE_HIGHLIGHT = "&H0000FFFF"
# Sticker callout: opaque orange background (#F97316), white bold text.
STICKER_BG = "&H001673F9"
# Social-post card: opaque white background, near-black text.
POSTCARD_BG = "&H00FFFFFF"
POSTCARD_TEXT = "&H001A1A1A"

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
        "SecondaryColour": KARAOKE_HIGHLIGHT, "OutlineColour": "&H00000000",
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


CAPTION_SIZE_FACTORS = {"small": 0.75, "medium": 1.0, "large": 1.35}
# (ASS Alignment, MarginV) per caption position; MarginV is scaled by height.
CAPTION_POSITIONS_ASS = {
    "bottom": (2, None),   # bottom-centre, MarginV = style MarginV
    "middle": (8, 0),      # middle-centre
    "top": (5, None),      # top-centre, MarginV = style MarginV
}


def output_dims(aspect_ratio: str, height: int) -> tuple[int, int]:
    """Output (width, height) for an aspect ratio at the given export height.

    The height is the requested export height; the width is derived from
    the aspect ratio (e.g. 16:9 at 1280 -> 2276x1280). Both are forced even
    (required by yuv420p).
    """
    w_ratio, h_ratio = ASPECT_RATIO_DIMS.get(aspect_ratio, (9, 16))
    width = int(round(height * w_ratio / h_ratio))
    width -= width % 2
    height -= height % 2
    return width, height


def write_ass_file(path: Path, words: list[dict], start_s: float, end_s: float,
                   style: str, height: int, width: int | None = None,
                   caption_size: str = "medium", caption_position: str = "bottom",
                   caption_bg: str = "outline", sticker_text: str = "",
                   post_author: str = "", post_text: str = "") -> Path:
    """Write an ASS caption file for one clip segment from word timings.

    Times are relative to the clip start. Karaoke lines carry per-word
    karaoke timing tags; other styles carry whole-line text. Only words from
    the real transcript are used. `caption_size` scales the font,
    `caption_position` moves the lines (bottom/middle/top), `caption_bg`
    picks none/box/outline behind the text. A non-empty `sticker_text`
    adds an orange callout label at the top for the whole clip; when both
    `post_author` and `post_text` are set, a white social-post card is
    added at the top for the whole clip.
    """
    style = style if style in ASS_STYLES else "simple"
    width = width or int(height * 9 / 16)
    scale = height / 1280.0
    base = ASS_STYLES[style]
    size_factor = CAPTION_SIZE_FACTORS.get(caption_size, 1.0)
    fontsize = max(20, int(round(base["Fontsize"] * scale * size_factor)))
    margin_v = max(30, int(round(base["MarginV"] * scale)))
    alignment, pos_margin = CAPTION_POSITIONS_ASS.get(caption_position, (2, None))
    if pos_margin is None:
        pos_margin = margin_v
    if caption_bg == "box":
        border_style, back_colour, outline = 3, "&H80000000", 0
    elif caption_bg == "none":
        border_style, back_colour, outline = 1, "&H00000000", 0
    else:  # outline (default)
        border_style, back_colour, outline = 1, "&H00000000", base["Outline"]
    # Overlay styles share the caption font; sizes scale with the frame.
    sticker_fontsize = max(20, int(round(46 * scale)))
    sticker_margin_v = max(24, int(round(40 * scale)))
    postcard_fontsize = max(20, int(round(44 * scale)))
    postcard_margin_v = max(24, int(round(30 * scale)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {style},{base['Fontname']},{fontsize},{base['PrimaryColour']},"
        f"{base['SecondaryColour']},{base['OutlineColour']},{back_colour},"
        f"{base['Bold']},{base['Italic']},0,0,100,100,0,0,{border_style},"
        f"{outline},{base['Shadow']},{alignment},20,20,{pos_margin},1\n"
        f"Style: sticker,DejaVu Sans,{sticker_fontsize},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,{STICKER_BG},-1,0,0,0,100,100,0,0,3,0,0,5,20,20,"
        f"{sticker_margin_v},1\n"
        f"Style: postcard,DejaVu Sans,{postcard_fontsize},{POSTCARD_TEXT},"
        f"{POSTCARD_TEXT},&H00000000,{POSTCARD_BG},0,0,0,0,100,100,0,0,3,0,0,5,"
        f"20,20,{postcard_margin_v},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    duration = max(0.1, end_s - start_s)
    events = []
    # Overlays span the whole clip and sit above the captions (layer 1).
    if sticker_text:
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},sticker,,"
            f"0,0,0,,{_escape_ass(sticker_text)}"
        )
    if post_author and post_text:
        card_text = (
            f"{{\\b1}}{_escape_ass(post_author)}{{\\b0}}\\N{_escape_ass(post_text)}"
        )
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},postcard,,"
            f"0,0,0,,{card_text}"
        )
    lines = _group_words(words, start_s, end_s)
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


def _even(n: int) -> int:
    return n - (n % 2)


def _facecam_crop(job, source) -> tuple[int, int, int, int] | None:
    """Clamped (x, y, w, h) pixel crop of the manual facecam region, or None
    when no manual region is set. Coordinates are fractions of the source
    frame (0-1); values >1 are treated as absolute pixels (defensive, for
    older callers)."""
    if (job.facecam_mode or "auto").lower() != "manual":
        return None
    if any(v is None for v in (job.facecam_x, job.facecam_y, job.facecam_w, job.facecam_h)):
        return None
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
    return fx, fy, fw, fh


def render_clip(db, clip, job, source) -> dict:
    """Render one Clip at the job's aspect ratio and framing layout.

    Returns {file_path, caption_path, thumb_path, width, height}.
    Raises RuntimeError on failure; the caller (run.process_job) records
    it and continues with the other candidates. The output file is
    verified to exist and be non-empty before returning. A requested
    facecam_top/facecam_side layout with no manual facecam region falls
    back to the standard layout and the fallback is written to the job log.
    """
    fields = editor_fields_for_job(job)
    height = int(job.export_height) if int(job.export_height) in ALLOWED_HEIGHTS else 1280
    width, height = output_dims(fields["aspect_ratio"], height)
    duration = max(0.1, float(clip.end_s) - float(clip.start_s))
    out_dir = user_clip_dir(job.user_id)
    out_path = out_dir / f"clip_{clip.id}.mp4"
    ass_path = out_dir / f"clip_{clip.id}.ass"
    thumb_path = out_dir / f"clip_{clip.id}_thumb.jpg"

    # Captions: only from a real transcript. No transcript -> no caption
    # lines, but the sticker / post-card overlays still need an ASS file.
    words = load_transcript_for_job(job)
    seg_words = [w for w in words if w["end"] > float(clip.start_s) and w["start"] < float(clip.end_s)]
    sticker_text = fields["sticker_text"]
    post_author, post_text = fields["post_author"], fields["post_text"]
    if post_author and not post_text or post_text and not post_author:
        # A post card needs BOTH author and text; a lone half is ignored.
        job.log = (job.log or "") + "Social-post card needs both an author and text — ignoring the half-filled card.\n"
        post_author, post_text = "", ""
    caption_path = ""
    if seg_words or sticker_text or (post_author and post_text):
        write_ass_file(
            ass_path, seg_words, float(clip.start_s), float(clip.end_s),
            job.caption_style or "simple", height, width=width,
            caption_size=fields["caption_size"],
            caption_position=fields["caption_position"],
            caption_bg=fields["caption_bg"],
            sticker_text=sticker_text,
            post_author=post_author, post_text=post_text,
        )
        caption_path = str(ass_path)
    elif ass_path.is_file():
        ass_path.unlink()

    # Framing layout: facecam_top / facecam_side need a manual facecam
    # region; otherwise fall back to standard (logged on the job).
    layout = fields["layout"]
    facecam_mode = (job.facecam_mode or "auto").lower()
    manual_crop = _facecam_crop(job, source) if facecam_mode == "manual" else None
    if layout in ("facecam_top", "facecam_side") and manual_crop is None:
        job.log = (job.log or "") + (
            f"Layout '{layout}' needs a manual facecam region — "
            "falling back to the standard layout.\n"
        )
        layout = "standard"

    # Video filter: scale up to cover, centre-crop to the output size
    # (works for both landscape and already-vertical sources), then burn
    # captions/overlays if any.
    main_vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=yuv420p"
    )
    if caption_path:
        main_vf += f",subtitles={_ffmpeg_escape_path(caption_path)}"

    has_audio = bool(source.has_audio)
    args: list[str] = [FFMPEG, "-y", "-v", "error",
                       "-ss", f"{float(clip.start_s):.3f}", "-t", f"{duration:.3f}",
                       "-i", str(source.stored_path)]
    if layout in ("facecam_top", "facecam_side") and manual_crop is not None:
        # Solo layouts: the facecam region and the gameplay share the
        # frame (top/bottom or left/right), built from one split input.
        fx, fy, fw, fh = manual_crop
        if layout == "facecam_top":
            fc_w, fc_h = width, _even(height // 2)
            main_w, main_h = width, height - fc_h
            join = "vstack"
        else:
            fc_w, fc_h = _even(width // 2), height
            main_w, main_h = width - fc_w, height
            join = "hstack"
        sub = f",subtitles={_ffmpeg_escape_path(caption_path)}" if caption_path else ""
        filter_complex = (
            f"[0:v]split=2[fc_src][main_src];"
            f"[fc_src]crop={fw}:{fh}:{fx}:{fy},scale={fc_w}:{fc_h},setsar=1[fc];"
            f"[main_src]scale={main_w}:{main_h}:force_original_aspect_ratio=increase,"
            f"crop={main_w}:{main_h},setsar=1[main];"
            f"[fc][main]{join},format=yuv420p{sub}[vout]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[vout]"]
    elif facecam_mode == "manual" and manual_crop is not None:
        # Standard layout with a manual facecam region: PiP crop of the
        # given source region, bottom-right.
        fx, fy, fw, fh = manual_crop
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
