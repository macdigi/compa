#!/usr/bin/env python3
"""Offline voice-note transcription helper for local assistant workflows."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import zipfile


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"


def default_data_dir() -> Path:
    explicit = os.environ.get("COMPA_STT_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return xdg_data.expanduser() / "compa" / "voice-stt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe an audio file locally with ffmpeg + Vosk. "
            "Dependencies live in a private cache, not Compa's runtime venv."
        )
    )
    parser.add_argument("audio", nargs="?", help="Audio file to transcribe")
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="Bootstrap the private Vosk environment and model, then exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON object instead of plain transcript text",
    )
    parser.add_argument(
        "--data-dir",
        default=str(default_data_dir()),
        help="Durable cache root for the private venv and Vosk model",
    )
    parser.add_argument(
        "--model-url",
        default=MODEL_URL,
        help="Vosk model ZIP URL to download if the model is missing",
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=10,
        help="Positive nice increment while transcribing; use 0 to leave priority unchanged",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show dependency bootstrap and recognizer logs",
    )
    args = parser.parse_args()
    if not args.install_only and not args.audio:
        parser.error("audio is required unless --install-only is used")
    return args


def log(message: str, *, verbose: bool) -> None:
    if verbose:
        print(message, file=sys.stderr)


def run(cmd: list[str], *, verbose: bool) -> None:
    log("+ " + " ".join(cmd), verbose=verbose)
    subprocess.run(cmd, check=True)


def have_python_module(name: str) -> bool:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def private_python(venv_dir: Path, *, verbose: bool) -> Path:
    python = venv_dir / "bin" / "python"
    if not python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "venv", str(venv_dir)], verbose=verbose)
    return python


def ensure_vosk(venv_dir: Path, *, verbose: bool) -> None:
    if have_python_module("vosk"):
        return

    python = private_python(venv_dir, verbose=verbose)
    probe = subprocess.run(
        [str(python), "-c", "import vosk"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode != 0:
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--upgrade",
                "pip",
            ],
            verbose=verbose,
        )
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "vosk",
            ],
            verbose=verbose,
        )

    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


def looks_like_vosk_model(model_dir: Path) -> bool:
    return (
        model_dir.is_dir()
        and (model_dir / "am").is_dir()
        and (model_dir / "graph").is_dir()
        and (model_dir / "conf").is_dir()
    )


def download_file(url: str, destination: Path, *, verbose: bool) -> None:
    log(f"Downloading {url}", verbose=verbose)
    with urllib.request.urlopen(url) as response:
        with destination.open("wb") as out:
            shutil.copyfileobj(response, out)


def ensure_model(model_dir: Path, model_url: str, *, verbose: bool) -> None:
    if looks_like_vosk_model(model_dir):
        return

    model_parent = model_dir.parent
    model_parent.mkdir(parents=True, exist_ok=True)
    archive_path = model_parent / f"{MODEL_NAME}.zip"

    if not archive_path.exists():
        tmp_archive = archive_path.with_suffix(".zip.tmp")
        if tmp_archive.exists():
            tmp_archive.unlink()
        download_file(model_url, tmp_archive, verbose=verbose)
        tmp_archive.rename(archive_path)

    tmp_extract = model_parent / f".{MODEL_NAME}.extracting"
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)
    tmp_extract.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(tmp_extract)

    extracted = tmp_extract / MODEL_NAME
    if not looks_like_vosk_model(extracted):
        raise RuntimeError(f"Downloaded archive did not contain {MODEL_NAME}")
    if model_dir.exists():
        shutil.rmtree(model_dir)
    extracted.rename(model_dir)
    shutil.rmtree(tmp_extract)


def apply_nice(increment: int, *, verbose: bool) -> None:
    if increment <= 0:
        return
    try:
        os.nice(increment)
    except OSError as exc:
        log(f"Could not lower process priority: {exc}", verbose=verbose)


def convert_to_wav(audio_path: Path, wav_path: Path, *, verbose: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found in PATH")
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(wav_path),
        ],
        verbose=verbose,
    )


def transcribe_wav(wav_path: Path, model_dir: Path, *, verbose: bool) -> tuple[str, float]:
    from vosk import KaldiRecognizer, Model, SetLogLevel

    if not verbose:
        SetLogLevel(-1)

    start = time.monotonic()
    model = Model(str(model_dir))
    parts: list[str] = []
    with wave.open(str(wav_path), "rb") as wf:
        recognizer = KaldiRecognizer(model, wf.getframerate())
        recognizer.SetWords(True)
        while True:
            data = wf.readframes(8000)
            if not data:
                break
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    parts.append(text)
        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()
        if text:
            parts.append(text)
    return " ".join(parts), time.monotonic() - start


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()
    venv_dir = data_dir / "venv"
    model_dir = data_dir / "models" / MODEL_NAME

    ensure_vosk(venv_dir, verbose=args.verbose)
    ensure_model(model_dir, args.model_url, verbose=args.verbose)

    if args.install_only:
        print(f"data_dir: {data_dir}")
        print(f"venv: {venv_dir}")
        print(f"model: {model_dir}")
        return 0

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    apply_nice(args.nice, verbose=args.verbose)
    with tempfile.TemporaryDirectory(prefix="compa-voice-stt-") as tmp:
        wav_path = Path(tmp) / "input.wav"
        convert_to_wav(audio_path, wav_path, verbose=args.verbose)
        transcript, elapsed = transcribe_wav(wav_path, model_dir, verbose=args.verbose)

    if args.json:
        print(
            json.dumps(
                {
                    "audio": str(audio_path),
                    "data_dir": str(data_dir),
                    "model": str(model_dir),
                    "elapsed_seconds": round(elapsed, 3),
                    "transcript": transcript,
                },
                indent=2,
            )
        )
    else:
        print(transcript)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
