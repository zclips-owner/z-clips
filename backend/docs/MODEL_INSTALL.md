# Z CLIPS — Model Installation Guide

Local, free/open-source models only. **No paid APIs are used** anywhere in
Z CLIPS — transcription, speech detection, analysis, rendering and QC all
run on this server with ffmpeg and locally installed models. If a model is
not installed, the affected feature degrades transparently instead of
pretending to work.

## How installs work

- The owner installs models from **Owner > System & Models**. That page
  shows each model's **size and license before** the install starts.
- Installing is **opt-in**: nothing is downloaded unless the owner clicks
  Install. The install runs in a background thread and its status
  (`not_installed` / `installing` / `installed` / `failed`) is recorded in
  the `models` table and shown on the page.
- The installer checks free disk space first (model size × 2 + 200 MB
  margin) and refuses with a clear error instead of failing halfway.
- Status is verified against the files on disk: a model is only reported
  as installed when its Python package imports **and** its model files
  exist. If the files are later deleted, the page reports
  `not_installed` again — it never claims an installed model that is not
  there.

## Models

| Model | Size (download) | Disk needed | RAM (CPU) | GPU | License |
|---|---|---|---|---|---|
| faster-whisper (tiny) | ~75 MB | ~350 MB | ~1 GB | No — CPU only | MIT |
| faster-whisper (base) | ~145 MB | ~500 MB | ~1 GB | No — CPU only | MIT |
| faster-whisper (small) | ~490 MB | ~1.2 GB | ~2 GB | No — CPU only | MIT |
| Silero VAD | ~2 MB | ~50 MB | ~256 MB | No — CPU only | MIT |

Notes:

- **faster-whisper (tiny / base / small)** — speech-to-text with word
  timestamps, used to score candidate moments and to burn word-synced
  captions into clips. Code and Whisper model weights are MIT licensed:
  <https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE>.
  Pip package: `faster-whisper`. Model weights are downloaded from
  Hugging Face (`Systran/faster-whisper-tiny`,
  `Systran/faster-whisper-base`, `Systran/faster-whisper-small`) into
  `storage/models/<name>/` (`model.bin` + `config.json`). Runs on CPU
  with `int8` compute; a GPU is **not** required and installing a model
  cannot create GPU capacity. Tiny is fastest on CPU and is the
  recommended first install; base/small are more accurate and slower.
  VRAM: 0 MB (no GPU is used).
- **Silero VAD** — voice activity detection (finds speech vs.
  silence/music). MIT licensed:
  <https://github.com/snakers4/silero-vad/blob/master/LICENSE>.
  Pip package: `silero-vad`. The ~2 MB ONNX model is loaded through the
  package on install. VRAM: 0 MB.
- **PySceneDetect** — optional, **not installed and not required**:
  scene cuts are detected with ffmpeg's built-in `select='gt(scene,0.4)'`
  filter, so no extra dependency is needed.
- **ffmpeg / ffprobe** — system package (already present at
  `/usr/bin/ffmpeg`, version 8.1.2 in the development VM). Used for
  probing, scene detection, audio measurement, rendering, loudnorm and
  QC. Install via the OS package manager (`apt install ffmpeg` on
  Debian/Ubuntu); it is not a Python package and is not downloaded by
  the model installer.

## What happens without faster-whisper (transparent limitation)

This is the important limitation to understand:

- **Captions are unavailable.** Clips are still analysed (scene cuts +
  audio energy), rendered vertical 9:16, loudness-normalised and QC'd,
  but they are rendered **with no caption track**. No caption text is
  ever invented or guessed.
- The job log records, in plain language:
  > "Transcription model not installed — captions will be unavailable;
  > install faster-whisper (tiny) from Owner > System & Models"
- QC records the caption check as "no captions expected" rather than
  passing a caption check that never ran, and candidate scoring gives
  the speech-presence component a score of 0 (it is not estimated from
  audio energy).
- The job does **not** fail and does **not** sit in a `needs_model`
  state because of this — it completes with caption-less clips, and the
  limitation is visible in the job log.

## Requirements summary

- CPU-only operation is fully supported; no GPU is required for any
  model above. Transcription speed on CPU depends on the machine:
  tiny is roughly real-time to a few times real-time on a modern CPU;
  small is several times slower.
- Disk: at least ~350 MB free for tiny alone; ~1.2 GB free for small.
- RAM: ~1 GB free while transcribing with tiny/base, ~2 GB with small.
- Network is needed once, at install time, to download the pip package
  and model weights. After that, transcription runs fully offline.
- No account, API key, or paid subscription is needed for any model.
