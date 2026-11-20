"""Local AI model registry / installer for Z CLIPS.

Free/open-source, self-hosted models only. No paid APIs are used anywhere
in the pipeline. The owner sees each model's size and license BEFORE
installing (the Owner > System & Models page shows them); installs are
opt-in, run in a background thread, and record honest status in the
``models`` table. This module NEVER claims a model is installed when it
is not: :func:`is_available` checks both the Python import AND the model
files on disk, and :func:`list_models` cross-checks the DB row against
the files before reporting "installed".

Nothing is downloaded by importing this module. Downloads happen only
when the owner explicitly triggers :func:`install_model`.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import MODEL_DIR
from ..database import SessionLocal
from ..models import ModelInfo

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Licences verified against the upstream projects (see docs/MODEL_INSTALL.md):
# - faster-whisper code and Whisper model weights: MIT
#   (https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)
# - Silero VAD: MIT
#   (https://github.com/snakers4/silero-vad/blob/master/LICENSE)
# - Vosk code and vosk-model-small-en-us-0.15 weights: Apache 2.0
#   (https://github.com/alphacep/vosk-api/blob/master/LICENSE)
MODELS: list[dict] = [
    {
        "name": "vosk-small-en",
        "display_name": "Vosk (small English)",
        "purpose": "Speech-to-text transcription with word timestamps, used for captions. Lightweight — the only transcription engine that fits the 256 MB free tier (~210 MB peak). English only.",
        "size_mb": 68,
        "license": "Apache 2.0",
        "license_url": "https://github.com/alphacep/vosk-api/blob/master/LICENSE",
        "pip_package": "vosk",
        "hf_repo": None,
        "min_ram_mb": 256,
        "needs_gpu": False,
    },
    {
        "name": "faster-whisper-tiny",
        "display_name": "faster-whisper (tiny)",
        "purpose": "Speech-to-text transcription with word timestamps, used for captions.",
        "size_mb": 75,
        "license": "MIT",
        "license_url": "https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE",
        "pip_package": "faster-whisper",
        "hf_repo": "Systran/faster-whisper-tiny",
        "min_ram_mb": 1024,
        "needs_gpu": False,
    },
    {
        "name": "faster-whisper-base",
        "display_name": "faster-whisper (base)",
        "purpose": "Speech-to-text transcription with word timestamps (more accurate than tiny), used for captions.",
        "size_mb": 145,
        "license": "MIT",
        "license_url": "https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE",
        "pip_package": "faster-whisper",
        "hf_repo": "Systran/faster-whisper-base",
        "min_ram_mb": 1024,
        "needs_gpu": False,
    },
    {
        "name": "faster-whisper-small",
        "display_name": "faster-whisper (small)",
        "purpose": "Speech-to-text transcription with word timestamps (most accurate of the three), used for captions.",
        "size_mb": 490,
        "license": "MIT",
        "license_url": "https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE",
        "pip_package": "faster-whisper",
        "hf_repo": "Systran/faster-whisper-small",
        "min_ram_mb": 2048,
        "needs_gpu": False,
    },
    {
        "name": "silero-vad",
        "display_name": "Silero VAD",
        "purpose": "Voice activity detection — finds speech segments so analysis can tell speech from silence/music.",
        "size_mb": 2,
        "license": "MIT",
        "license_url": "https://github.com/snakers4/silero-vad/blob/master/LICENSE",
        "pip_package": "silero-vad",
        "hf_repo": None,
        "min_ram_mb": 256,
        "needs_gpu": False,
    },
]

_MODEL_BY_NAME = {m["name"]: m for m in MODELS}
_WHISPER_MODELS = ("faster-whisper-tiny", "faster-whisper-base", "faster-whisper-small")
VOSK_MODEL = "vosk-small-en"
# Preferred transcription model when several are installed: smallest first
# is fastest on CPU; the owner can install a larger one for accuracy.
_WHISPER_PREFERENCE = ("faster-whisper-tiny", "faster-whisper-base", "faster-whisper-small")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# File locations
# ---------------------------------------------------------------------------
def model_dir(name: str) -> Path:
    """On-disk directory for a registered model."""
    return MODEL_DIR / name


def _whisper_files_present(name: str) -> bool:
    d = model_dir(name)
    # A faster-whisper snapshot contains model.bin (+ config.json).
    return (d / "model.bin").is_file() and (d / "config.json").is_file()


def _vosk_files_present(name: str = VOSK_MODEL) -> bool:
    from ..pipeline.transcribe import VOSK_MODEL_REQUIRED

    d = model_dir(name)
    return d.is_dir() and all((d / sub).exists() for sub in VOSK_MODEL_REQUIRED)


def _silero_files_present() -> bool:
    d = model_dir("silero-vad")
    if (d / "silero_vad.onnx").is_file():
        return True
    # The silero-vad pip package can also load its bundled model from the
    # installed package; that counts only when the package is importable
    # AND its bundled onnx file is actually present.
    try:
        import silero_vad  # type: ignore

        pkg = Path(silero_vad.__file__).resolve().parent
        return any(pkg.rglob("*.onnx"))
    except Exception:
        return False


def _files_present(name: str) -> bool:
    if name in _WHISPER_MODELS:
        return _whisper_files_present(name)
    if name == VOSK_MODEL:
        return _vosk_files_present(name)
    if name == "silero-vad":
        return _silero_files_present()
    return model_dir(name).exists() and any(model_dir(name).iterdir())


def _import_available(name: str) -> bool:
    if name in _WHISPER_MODELS:
        return importlib.util.find_spec("faster_whisper") is not None
    if name == VOSK_MODEL:
        return importlib.util.find_spec("vosk") is not None
    if name == "silero-vad":
        return importlib.util.find_spec("silero_vad") is not None
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_available(name: str) -> bool:
    """True only when the package imports AND the model files exist on disk.

    Never trust the DB status alone — a row can say "installed" while the
    files were deleted, and this function must not repeat that claim.
    """
    if name not in _MODEL_BY_NAME:
        return False
    return _import_available(name) and _files_present(name)


def transcribe_available() -> bool:
    """True when at least one transcription model is genuinely usable."""
    return is_available(VOSK_MODEL) or any(is_available(n) for n in _WHISPER_MODELS)


def get_vosk_model() -> tuple[str, str] | None:
    """Return ``(name, model_path)`` for Vosk, or None when unusable."""
    if is_available(VOSK_MODEL):
        return VOSK_MODEL, str(model_dir(VOSK_MODEL))
    return None


def get_transcribe_model() -> tuple[str, str] | None:
    """Return ``(name, model_path)`` for the best available transcription model.

    Vosk is preferred: it is the only engine that fits the 256 MB free
    tier. faster-whisper (tiny -> base -> small) is the fallback for
    hosts with >=1 GB RAM. Returns None when no transcription model is
    installed.
    """
    vosk = get_vosk_model()
    if vosk is not None:
        return vosk
    for name in _WHISPER_PREFERENCE:
        if is_available(name):
            return name, str(model_dir(name))
    return None


def list_models(db) -> list[dict]:
    """Merge the registry with the ModelInfo DB rows.

    The reported ``status`` is verified against the files on disk: a DB row
    saying "installed" whose files are missing is reported as
    "not_installed" (with a note), never as installed.
    """
    out: list[dict] = []
    for m in MODELS:
        row = db.get(ModelInfo, m["name"])
        status = row.status if row is not None else "not_installed"
        verified = True
        note = ""
        if status == "installed" and not is_available(m["name"]):
            # DB says installed but the files/import are gone: be honest.
            status = "not_installed"
            verified = False
            note = "Previously recorded as installed, but the model files or package are no longer present."
        elif status == "installed":
            verified = True
        entry = dict(m)
        entry.update(
            {
                "status": status,
                "verified": verified,
                "note": note,
                "path": (row.path if row is not None else "") or "",
                "error": (row.error if row is not None else "") or "",
                "installed_at": row.installed_at.isoformat() if row is not None and row.installed_at else None,
                "available": is_available(m["name"]),
            }
        )
        out.append(entry)
    return out


def install_model(db, name: str) -> dict:
    """Start an opt-in install of ``name`` in a background thread.

    The caller (Owner > System & Models) must already have shown the
    model's size and license to the owner before calling this — this
    function never silently downloads a large file. Raises ValueError
    for an unknown model name. Returns a dict describing the install
    that was started (or the current state when already installed /
    already installing). The background thread records progress in the
    ModelInfo row (status/path/error) using its own DB session.
    """
    if name not in _MODEL_BY_NAME:
        raise ValueError(f"Unknown model: {name}")
    meta = _MODEL_BY_NAME[name]
    row = db.get(ModelInfo, name)
    if row is None:
        row = ModelInfo(
            name=name,
            size_mb=meta["size_mb"],
            license=meta["license"],
            status="not_installed",
        )
        db.add(row)
        db.flush()
    if is_available(name):
        row.status = "installed"
        row.path = str(model_dir(name))
        row.error = ""
        row.installed_at = row.installed_at or _utcnow()
        db.commit()
        return {"name": name, "status": "installed", "detail": "Model is already installed."}
    if row.status == "installing":
        return {"name": name, "status": "installing", "detail": "Install already in progress."}
    # Disk-space guard: refuse (honestly) rather than failing halfway.
    free_mb = shutil.disk_usage(MODEL_DIR).free // (1024 * 1024)
    needed_mb = meta["size_mb"] * 2 + 200  # model + temp files + margin
    if free_mb < needed_mb:
        row.status = "failed"
        row.error = (
            f"Not enough disk space: {meta['size_mb']} MB model needs about "
            f"{needed_mb} MB free, only {free_mb} MB available."
        )
        db.commit()
        return {"name": name, "status": "failed", "detail": row.error}
    row.status = "installing"
    row.error = ""
    row.path = str(model_dir(name))
    db.commit()
    thread = threading.Thread(target=_install_worker, args=(name,), name=f"install-{name}", daemon=True)
    thread.start()
    return {"name": name, "status": "installing", "detail": "Install started in the background."}


# ---------------------------------------------------------------------------
# Background installer (runs in its own thread with its own DB session)
# ---------------------------------------------------------------------------
def _set_status(name: str, status: str, path: str = "", error: str = "") -> None:
    db = SessionLocal()
    try:
        row = db.get(ModelInfo, name)
        if row is None:
            row = ModelInfo(name=name, status=status)
            db.add(row)
        row.status = status
        if path:
            row.path = path
        row.error = error[:500]
        if status == "installed":
            row.installed_at = _utcnow()
        row.updated_at = _utcnow()
        db.commit()
    finally:
        db.close()


def _pip_install(package: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", package],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "pip install failed")[-800:]
        raise RuntimeError(f"pip install {package} failed: {tail}")


def _install_worker(name: str) -> None:
    meta = _MODEL_BY_NAME[name]
    dest = model_dir(name)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if meta["pip_package"]:
            _pip_install(meta["pip_package"])
        if name in _WHISPER_MODELS:
            if not meta["hf_repo"]:
                raise RuntimeError("No Hugging Face repo configured for this model.")
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                _pip_install("huggingface-hub")
                from huggingface_hub import snapshot_download  # noqa: F401
            snapshot_download(repo_id=meta["hf_repo"], local_dir=str(dest))
            if not _whisper_files_present(name):
                raise RuntimeError(
                    "Download finished but model.bin/config.json are missing from "
                    f"{dest}; the download may have been incomplete."
                )
        elif name == VOSK_MODEL:
            # Vosk model: download the official zip (checksum-verified)
            # and extract it into the model directory. Reuses the same
            # ensure logic as the pipeline's first-use download.
            from ..pipeline.transcribe import ensure_vosk_model

            got = ensure_vosk_model()
            if got is None or not _vosk_files_present(name):
                raise RuntimeError(
                    "Vosk model download failed (see worker log); the model "
                    "files are not present in "
                    f"{dest}."
                )
        elif name == "silero-vad":
            # The silero-vad package loads its ONNX model on first use; force
            # that load now so the file is actually present, and record where
            # it ended up. If the load downloads, that happens inside this
            # explicit, owner-triggered install only.
            try:
                from silero_vad import load_silero_vad  # type: ignore

                load_silero_vad()
            except Exception as exc:  # pragma: no cover - depends on env
                raise RuntimeError(f"Could not load Silero VAD model: {exc}") from exc
            if not _silero_files_present():
                raise RuntimeError("Silero VAD loaded but no .onnx model file could be found on disk.")
        if not is_available(name):
            raise RuntimeError(
                "Install steps finished but the model is still not usable "
                "(package import or model files missing)."
            )
        _set_status(name, "installed", path=str(dest))
    except Exception as exc:
        _set_status(name, "failed", path=str(dest), error=str(exc))
