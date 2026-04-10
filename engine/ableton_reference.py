"""Shared export helpers used by both Quota Suite and Quota Mini."""

from __future__ import annotations

import json
import os
import re
import shutil
import traceback
import logging
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple, List, Any

import soundfile as sf

PadAssignments = Mapping[int, Tuple[float, float, str]]
PadTypes = Mapping[int, str]
PadColors = Mapping[int, str]
PadAudioPaths = Mapping[int, str]


@dataclass
class PadMeta:
    pad: int
    start: float
    end: float
    text: str
    color: str
    file: str
    video_file: Optional[str] = None


@dataclass
class ExportKitResult:
    output_dir: str
    metas: List[PadMeta]
    cancelled: bool = False


class ExportKitError(Exception):
    """Raised when a pad kit export fails."""


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Bridge exports (Ableton .adg + Akai Force/MPC .Drum.xpm)
# -----------------------------------------------------------------------------

def _bridge_repo_root() -> str:
    """Return the best base directory for locating bundled bridge assets.

    In source/dev runs we want the repo root.
    In PyInstaller frozen builds we want the PyInstaller extraction root
    (typically `sys._MEIPASS`), where datas like `assets/bridge/...` live.

    This needs to be robust on Windows because module __file__ can point inside
    a PYZ archive (e.g. `.../PYZ-00.pyz/...`), which is not a real directory.
    """
    import sys
    from pathlib import Path

    # PyInstaller: locate the extraction root where bundled datas live.
    # Depending on onefile/onedir mode + PyInstaller version, assets may sit at:
    #   - <_MEIPASS>/assets/...
    #   - <_MEIPASS>/_internal/assets/...
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(str(meipass)):
        p = Path(str(meipass))
        if (p / "assets" / "bridge").is_dir():
            return str(p)
        if (p / "_internal" / "assets" / "bridge").is_dir():
            return str(p / "_internal")
        # Fallback: return the meipass dir even if bridge assets aren't found.
        return str(p)

    # Another frozen-build fallback: check alongside the executable.
    try:
        exe_dir = Path(getattr(sys, "executable", "") or "").resolve().parent
        if (exe_dir / "_internal" / "assets" / "bridge").is_dir():
            return str(exe_dir / "_internal")
        if (exe_dir / "assets" / "bridge").is_dir():
            return str(exe_dir)
    except Exception:
        pass

    # Source / unfrozen: walk up a couple levels and pick the first dir that
    # contains assets/bridge.
    try:
        here = Path(__file__).resolve()
        candidates = [here.parent, here.parent.parent, Path(os.getcwd())]
        for cand in candidates:
            if (cand / "assets" / "bridge").is_dir():
                return str(cand)
        # Fallback: repo root is usually one level above quota_core/
        return str(here.parent.parent)
    except Exception:
        return os.getcwd()


def _bridge_asset_path(*parts: str) -> str:
    return os.path.join(_bridge_repo_root(), "assets", "bridge", *parts)


def _safe_filename(name: str, fallback: str = "Kit") -> str:
    s = (name or "").strip() or fallback
    s = re.sub(r"[^A-Za-z0-9 _\-]+", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or fallback


def _write_silence_wav(dest_path: str, sr: int, channels: int = 1, seconds: float = 0.05) -> None:
    frames = max(1, int(round(float(sr) * float(seconds))))
    try:
        import numpy as np  # type: ignore

        data = np.zeros((frames, int(max(1, channels))), dtype=np.float32)
    except Exception:
        data = [[0.0] * int(max(1, channels)) for _ in range(frames)]
    sf.write(dest_path, data, int(sr), subtype="PCM_24")


def _hardlink_or_copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if os.path.exists(dst):
            os.remove(dst)
    except Exception:
        pass
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def _detect_ableton_user_library_root() -> Optional[str]:
    """Best-effort detection of Ableton's User Library root (macOS + Windows).

    macOS:
      ~/Library/Preferences/Ableton/Live <ver>/Library.cfg

    Windows:
      %APPDATA%\\Ableton\\Live *\\Preferences\\Library.cfg

    We prefer an explicit UserLibraryPath if present, else fall back to a
    ProjectPath that typically points at ".../Music/Ableton" and append
    "User Library".

    Returns the full path to the "User Library" root or None if not found.
    """
    try:
        cands = []

        # macOS
        try:
            prefs_root = os.path.join(os.path.expanduser("~"), "Library", "Preferences", "Ableton")
            if os.path.isdir(prefs_root):
                for name in os.listdir(prefs_root):
                    p = os.path.join(prefs_root, name, "Library.cfg")
                    if os.path.isfile(p):
                        cands.append(p)
        except Exception:
            pass

        # Windows
        try:
            appdata = os.environ.get("APPDATA")
            if appdata:
                ableton_root = os.path.join(appdata, "Ableton")
                if os.path.isdir(ableton_root):
                    for name in os.listdir(ableton_root):
                        p = os.path.join(ableton_root, name, "Preferences", "Library.cfg")
                        if os.path.isfile(p):
                            cands.append(p)
        except Exception:
            pass

        if not cands:
            return None

        # Pick most recently modified cfg
        cands2 = []
        for p in cands:
            try:
                cands2.append((os.path.getmtime(p), p))
            except Exception:
                cands2.append((0, p))
        cands2.sort(reverse=True)
        cfg = cands2[0][1]

        txt = open(cfg, "r", encoding="utf-8", errors="ignore").read()

        # Prefer explicit user library path if present.
        m = re.search(r'<UserLibraryPath\s+Value="([^"]+)"\s*/>', txt)
        if m:
            ul = m.group(1).strip()
            if ul and os.path.isdir(ul):
                return ul

        m = re.search(r'<ProjectPath\s+Value="([^"]+)"\s*/>', txt)
        if not m:
            return None
        base = m.group(1).strip()
        if not base:
            return None
        ul = os.path.join(base, "User Library")
        return ul if os.path.isdir(ul) else None
    except Exception:
        return None


def export_bridge_exports(
    *,
    kit_name: str,
    out_dir: str,
    pad_types: PadTypes,
    pad_assignments: PadAssignments,
    pad_audio_paths: Optional[PadAudioPaths],
    audio_array: Optional[Any],
    sample_rate: Optional[float],
    options: Mapping[str, Any],
) -> None:
    """Create Ableton + Akai bridge artifacts next to the exported kit.

    Important behavior:
    - Ableton: installs into the *real* Ableton User Library so racks load with samples (no blanks).
      Uses a kit-specific folder to avoid slot001.wav collisions between kits.
    - Akai Force/MPC: emits a folder containing `<Kit>.Drum.xpm` + `slot###.WAV` (UPPERCASE) because
      Force runs Linux and path casing matters.

    options keys (all optional):
      - ableton: bool (default True)
      - akai: bool (default True)
      - kit_size: int (inferred from pad usage if missing; clamped to 8/16/64/128)
      - total_slots: int (default 128)
      - bridge_dirname: str (default "Bridge")
    """

    ableton_on = bool(options.get("ableton", True))
    akai_on = bool(options.get("akai", True))
    if not ableton_on and not akai_on:
        return

    total_slots = int(options.get("total_slots", 128) or 128)
    total_slots = max(1, min(128, total_slots))

    def _infer_active_slots() -> "set[int]":
        """Return the set of *actually filled* pad indices.

        We only count a pad as active if it will export a real sample:
        - slice: has a pad_assignment with a non-zero duration
        - oneshot: has a valid audio path
        """
        pads: set[int] = set()

        # Slice pads
        try:
            for p, a in (pad_assignments or {}).items():
                try:
                    pi = int(p)
                except Exception:
                    continue
                try:
                    t0, t1, _txt = a
                    if float(t1) > float(t0):
                        pads.add(pi)
                except Exception:
                    # If assignment exists but is malformed, treat as inactive.
                    pass
        except Exception:
            pass

        # One-shot pads
        try:
            for p, t in (pad_types or {}).items():
                if t != "oneshot":
                    continue
                try:
                    pi = int(p)
                except Exception:
                    continue
                src = (pad_audio_paths or {}).get(pi)
                if src and os.path.exists(str(src)):
                    pads.add(pi)
        except Exception:
            pass

        return pads

    def _infer_kit_size(active: "set[int]") -> int:
        """Infer kit size as the highest contiguous filled pad count.

        This matches user expectation: if they filled 17 pads, we stop at 17
        (instead of rounding up to 64/128).
        """
        if not active:
            return 16

        n = 0
        while (n + 1) in active:
            n += 1
        if n > 0:
            return n
        return max(active)

    active_slots = _infer_active_slots()

    kit_size_opt = options.get("kit_size")
    kit_size = int(kit_size_opt) if kit_size_opt else _infer_kit_size(active_slots)
    kit_size = max(1, min(128, kit_size))

    sr = int(round(float(sample_rate or 48000)))

    # Determine channel count from audio_array if possible.
    channels = 1
    try:
        shape = getattr(audio_array, "shape", None)
        if shape and len(shape) == 2:
            channels = int(shape[1])
    except Exception:
        channels = 1

    bridge_root = os.path.join(out_dir, str(options.get("bridge_dirname") or "Bridge"))
    os.makedirs(bridge_root, exist_ok=True)

    safe_kit = _safe_filename(kit_name, fallback="QuotaBridgeKit")

    # Always emit a small status file into the Bridge folder.
    # This makes Windows failures debuggable when bridge artifacts don't appear.
    status_lines: List[str] = []

    def _status(line: str) -> None:
        try:
            status_lines.append(str(line))
        except Exception:
            pass

    try:
        import sys

        _status(f"platform={sys.platform}")
        _status(f"frozen={bool(getattr(sys, 'frozen', False))}")
        _status(f"sys._MEIPASS={getattr(sys, '_MEIPASS', None)}")
        _status(f"sys.executable={getattr(sys, 'executable', None)}")
    except Exception:
        pass

    base_root = _bridge_repo_root()
    _status(f"bridge_asset_root={base_root}")

    # Materialize the canonical slot WAVs in a temp folder (avoid clutter + confusion in exports).
    import tempfile
    slot_src_dir = tempfile.mkdtemp(prefix=f"qbridge_slots_{safe_kit}_")

    # Render/copy slot WAVs (ONLY up to kit_size; no filler files)
    # We'll name them using the exported WAV naming (slot-index + pad name) so Ableton pad names match.
    slot_display_names: Dict[int, str] = {}
    slot_wav_fns: Dict[int, str] = {}
    # Akai expects canonical slot### filenames (the .Drum.xpm references slot001.WAV etc).
    slot_wav_fns_akai: Dict[int, str] = {}

    for slot in range(1, kit_size + 1):
        pad_type = (pad_types or {}).get(slot)

        # Determine a friendly, deterministic name for this pad.
        base = ""
        if pad_type == "oneshot":
            src = (pad_audio_paths or {}).get(slot)
            if src:
                base = os.path.splitext(os.path.basename(str(src)))[0]
        else:
            assignment = (pad_assignments or {}).get(slot)
            try:
                base = str(assignment[2] or "") if assignment else ""
            except Exception:
                base = ""

        base = _safe_filename(base, fallback=f"slot{slot:03}")
        display = f"{slot:03} {base}" if base else f"{slot:03}"
        display = display.strip()

        slot_display_names[slot] = display

        # Use canonical filenames for bridge exports to avoid Windows path-length issues.
        # Pad labels come from `slot_display_names` (not the filenames).
        slot_wav_fns[slot] = f"slot{slot:03}.wav"
        slot_wav_fns_akai[slot] = f"slot{slot:03}.wav"

        slot_path = os.path.join(slot_src_dir, slot_wav_fns[slot])
        slot_path_akai = os.path.join(slot_src_dir, slot_wav_fns_akai[slot])

        if pad_type == "oneshot":
            src = (pad_audio_paths or {}).get(slot)
            if src and os.path.exists(src):
                try:
                    data, in_sr = sf.read(src, always_2d=True)
                    sf.write(slot_path, data, int(in_sr), subtype="PCM_24")
                except Exception:
                    shutil.copy2(src, slot_path)
            else:
                _write_silence_wav(slot_path, sr, channels=channels)

            # Ensure Akai-compatible canonical slot###.wav exists.
            if slot_path_akai != slot_path:
                _hardlink_or_copy(slot_path, slot_path_akai)
            continue

        # Default: slice
        assignment = (pad_assignments or {}).get(slot)
        if assignment and audio_array is not None and sr > 0:
            t0, t1, _txt = assignment
            try:
                t0f, t1f = float(t0), float(t1)
            except Exception:
                t0f, t1f = 0.0, 0.0
            if t1f > t0f:
                start_idx = max(0, int(t0f * sr))
                end_idx = max(start_idx, int(t1f * sr))
                try:
                    sf.write(slot_path, audio_array[start_idx:end_idx], sr, subtype="PCM_24")
                except Exception:
                    _write_silence_wav(slot_path, sr, channels=channels)
            else:
                _write_silence_wav(slot_path, sr, channels=channels)
        else:
            _write_silence_wav(slot_path, sr, channels=channels)

        if slot_path_akai != slot_path:
            _hardlink_or_copy(slot_path, slot_path_akai)

    # Ableton: install into real User Library and patch the .adg to point to a kit-specific folder.
    tpl_adg = _bridge_asset_path("ableton", "QuotaBridge_DrumRack_TEMPLATE_128.adg")
    _status(f"tpl_adg={tpl_adg}")
    _status(f"tpl_adg_exists={os.path.exists(tpl_adg)}")

    if ableton_on and os.path.exists(tpl_adg):
        try:
            # Windows default is typically Documents/Ableton/User Library; macOS is Music/Ableton/User Library.
            if os.name == "nt":
                default_ul = os.path.join(os.path.expanduser("~"), "Documents", "Ableton", "User Library")
            else:
                default_ul = os.path.join(os.path.expanduser("~"), "Music", "Ableton", "User Library")

            user_lib = (
                str(options.get("ableton_user_library_root") or "").strip()
                or _detect_ableton_user_library_root()
                or default_ul
            )
            _status(f"ableton_user_library_root={user_lib}")

            presets_dir = os.path.join(user_lib, "Presets", "Instruments", "Drum Rack")
            samples_dir = os.path.join(user_lib, "Samples", "Quota Bridge", safe_kit)
            os.makedirs(presets_dir, exist_ok=True)
            os.makedirs(samples_dir, exist_ok=True)

            # Copy slot samples into kit-specific folder (ONLY kit_size)
            for slot in range(1, kit_size + 1):
                slot_fn = slot_wav_fns.get(slot) or f"slot{slot:03}.wav"
                _hardlink_or_copy(
                    os.path.join(slot_src_dir, slot_fn),
                    os.path.join(samples_dir, slot_fn),
                )

            # Patch template FileRefs to point at Samples/Quota Bridge/<Kit>/slot###.wav
            # and clear references for pads above kit_size so Live doesn't mark them offline.
            # Also: remove template's fixed SampleEnd markers so each sample uses its own full length.
            import gzip

            tpl_xml = gzip.open(tpl_adg, "rb").read().decode("utf-8", errors="ignore")

            # Reset crop/loop markers that were baked into the template.
            # We intentionally don't want any trimming inside Ableton because the audio is already chopped.
            tpl_xml = re.sub(r'<SampleStart Value="\d+"\s*/>', '<SampleStart Value="0" />', tpl_xml)
            tpl_xml = re.sub(r'<SampleEnd Value="\d+"\s*/>', '<SampleEnd Value="0" />', tpl_xml)
            tpl_xml = re.sub(r'(<SustainLoop>[\s\S]*?<Start Value=")\d+("\s*/>)', r'\g<1>0\2', tpl_xml)
            tpl_xml = re.sub(r'(<SustainLoop>[\s\S]*?<End Value=")\d+("\s*/>)', r'\g<1>0\2', tpl_xml)
            tpl_xml = re.sub(r'(<ReleaseLoop>[\s\S]*?<Start Value=")\d+("\s*/>)', r'\g<1>0\2', tpl_xml)
            tpl_xml = re.sub(r'(<ReleaseLoop>[\s\S]*?<End Value=")\d+("\s*/>)', r'\g<1>0\2', tpl_xml)

            for slot in range(1, total_slots + 1):
                old_rel = f"Samples/Imported/slot{slot:03}.wav"
                if slot <= kit_size:
                    slot_name = slot_display_names.get(slot) or f"slot{slot:03}"
                    slot_fn = slot_wav_fns.get(slot) or f"slot{slot:03}.wav"

                    new_rel = f"Samples/Quota Bridge/{safe_kit}/{slot_fn}"

                    # Set sample-part display name (inside the Simpler). This isn't the pad label,
                    # but it helps keep the preset readable.
                    tpl_xml = tpl_xml.replace(
                        f'<Name Value="slot{slot:03}" />',
                        f'<Name Value="{slot_name}" />',
                    )

                    # Repoint sample file refs.
                    tpl_xml = tpl_xml.replace(
                        f'<RelativePath Value="{old_rel}" />',
                        f'<RelativePath Value="{new_rel}" />',
                    )

                    # Use forward slashes inside the .adg, but build an absolute path from the OS path.
                    user_lib_norm = user_lib.replace("\\", "/")
                    tpl_xml = re.sub(
                        rf'<Path Value="[^"]*{re.escape(old_rel)}" />',
                        f'<Path Value="{user_lib_norm}/{new_rel}" />',
                        tpl_xml,
                    )
                else:
                    # Clear both relative + absolute paths (best-effort) so it stays empty.
                    tpl_xml = tpl_xml.replace(
                        f'<Name Value="slot{slot:03}" />',
                        '<Name Value="" />',
                    )
                    tpl_xml = tpl_xml.replace(
                        f'<RelativePath Value="{old_rel}" />',
                        '<RelativePath Value="" />',
                    )
                    tpl_xml = re.sub(
                        rf'<Path Value="[^"]*{re.escape(old_rel)}" />',
                        '<Path Value="" />',
                        tpl_xml,
                    )

            # IMPORTANT: Drum Rack pad labels come from DrumBranchPreset <Name>, not the Simpler.
            # The template ships with 128 chains (one per pad). For a smaller kit, we should:
            # - name only the pads that exist
            # - remove extra chains entirely (otherwise Live shows placeholder pads labeled "Chain")
            def _xml_escape_attr(val: str) -> str:
                return (
                    (val or "")
                    .replace("&", "&amp;")
                    .replace('"', "&quot;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )

            branch_pat = re.compile(r'<DrumBranchPreset Id="(\d+)">[\s\S]*?</DrumBranchPreset>')

            def _patch_branch(m: re.Match) -> str:
                bid = int(m.group(1))
                slot = bid + 1
                block = m.group(0)

                if slot <= kit_size:
                    nm = _xml_escape_attr(slot_display_names.get(slot) or f"{slot:03}")

                    def _set_branch_name(m2: re.Match) -> str:
                        return f"{m2.group(1)}{nm}{m2.group(2)}"

                    block = re.sub(
                        r'(<DrumBranchPreset Id="\d+">\s*<Name Value=")[^"]*("\s*/>)',
                        _set_branch_name,
                        block,
                        count=1,
                    )
                    return block

                # We trim unused branches later; leave as-is here.
                return block

            tpl_xml = branch_pat.sub(_patch_branch, tpl_xml)

            # Rename the device itself (so it doesn't just say "Drum Rack").
            kit_label = _xml_escape_attr(safe_kit)

            def _set_root_username(m2: re.Match) -> str:
                return f"{m2.group(1)}{kit_label}{m2.group(2)}"

            tpl_xml = re.sub(
                r'(<DrumGroupDevice Id="0">[\s\S]*?<UserName Value=")[^"]*("\s*/>)',
                _set_root_username,
                tpl_xml,
                count=1,
            )

            # Trim BranchPresets down to kit_size so empty pads are truly empty.
            m_bp = re.search(r'<BranchPresets>([\s\S]*?)</BranchPresets>', tpl_xml)
            if m_bp:
                inner = m_bp.group(1)
                kept = []
                for mm in branch_pat.finditer(inner):
                    bid = int(mm.group(1))
                    if bid < kit_size:
                        kept.append(mm.group(0))
                new_inner = "".join(kept)
                tpl_xml = tpl_xml[: m_bp.start(1)] + new_inner + tpl_xml[m_bp.end(1) :]

            adg_out = os.path.join(presets_dir, f"{safe_kit}.adg")
            with gzip.open(adg_out, "wb") as gz:
                gz.write(tpl_xml.encode("utf-8"))

            _status(f"ableton_adg_out={adg_out}")
            _status(f"ableton_samples_dir={samples_dir}")

            # Drop a small hint file into the export folder so users know where the .adg went.
            try:
                hint = os.path.join(bridge_root, "ABLETON_README.txt")
                with open(hint, "w", encoding="utf-8") as f:
                    f.write(
                        "Ableton Drum Rack export\n"
                        "=======================\n\n"
                        f"A Drum Rack preset was installed into your Ableton User Library:\n\n"
                        f"Preset (.adg): {adg_out}\n"
                        f"Samples:        {samples_dir}\n\n"
                        "In Ableton Live: Browser → User Library → Presets → Instruments → Drum Rack\n"
                    )
            except Exception:
                pass
        except Exception as exc:
            _status(f"ableton_error={type(exc).__name__}: {exc}")

    # Akai Force/MPC kit folder
    tpl_xpm = _bridge_asset_path("akai", "QBRIDGE-XPM-TEST-128C.Drum.xpm")
    _status(f"tpl_xpm={tpl_xpm}")
    _status(f"tpl_xpm_exists={os.path.exists(tpl_xpm)}")

    if akai_on and os.path.exists(tpl_xpm):
        try:
            akai_dir = os.path.join(bridge_root, "Akai Force-MPC", safe_kit)
            os.makedirs(akai_dir, exist_ok=True)

            # Copy/link slot WAVs into kit folder with UPPERCASE extension (ONLY kit_size)
            for slot in range(1, kit_size + 1):
                src_fn = (slot_wav_fns_akai.get(slot) or f"slot{slot:03}.wav")
                dst_fn = f"slot{slot:03}.WAV"
                _hardlink_or_copy(
                    os.path.join(slot_src_dir, src_fn),
                    os.path.join(akai_dir, dst_fn),
                )

            with open(tpl_xpm, "r", encoding="utf-8", errors="ignore") as handle:
                xpm = handle.read()
            try:
                xpm = re.sub(r"(<ProgramName>)(.*?)(</ProgramName>)", rf"{safe_kit}", xpm, flags=re.DOTALL)
            except Exception:
                pass
            # Ensure SampleFile entries use UPPERCASE .WAV
            xpm = xpm.replace(".wav", ".WAV")

            # Force/MPC: default template had short slice endpoints (meant for tiny test beeps).
            # Reset to 0 so pads play the whole sample.
            xpm = re.sub(r"<SampleEnd>\d+</SampleEnd>", "<SampleEnd>0</SampleEnd>", xpm)
            xpm = re.sub(r"<SliceEnd>\d+</SliceEnd>", "<SliceEnd>0</SliceEnd>", xpm)

            # Name the pads/samples using our exported slot display names (001 ...),
            # while keeping SampleFile pointing at slot###.WAV.
            for slot in range(1, kit_size + 1):
                disp = slot_display_names.get(slot) or f"{slot:03}"
                disp = _safe_filename(disp, fallback=f"{slot:03}")
                xpm = xpm.replace(
                    f"<SampleName>slot{slot:03}</SampleName>",
                    f"<SampleName>{disp}</SampleName>",
                )

            # Clear sample refs above kit_size so pads are empty (not missing).
            for slot in range(kit_size + 1, total_slots + 1):
                xpm = xpm.replace(f"<SampleName>slot{slot:03}</SampleName>", "<SampleName></SampleName>")
                xpm = xpm.replace(f"<SampleFile>slot{slot:03}.WAV</SampleFile>", "<SampleFile></SampleFile>")

            xpm_out = os.path.join(akai_dir, f"{safe_kit}.Drum.xpm")
            with open(xpm_out, "w", encoding="utf-8") as handle:
                handle.write(xpm)

            _status(f"akai_dir={akai_dir}")
            _status(f"akai_xpm_out={xpm_out}")
        except Exception as exc:
            _status(f"akai_error={type(exc).__name__}: {exc}")

    # Write bridge status at the end so failures are visible to the user.
    try:
        status_path = os.path.join(bridge_root, "BRIDGE_STATUS.txt")
        with open(status_path, "w", encoding="utf-8") as f:
            f.write("\n".join(status_lines) + "\n")
    except Exception:
        pass



def export_transcript_text(text: str, dest_path: str) -> None:
    """Write the transcript text to disk."""
    with open(dest_path, "w", encoding="utf-8") as handle:
        handle.write(text or "")


def export_srt(segments: Iterable[Mapping[str, Any]], dest_path: str) -> None:
    """Write aligned transcript segments to an SRT file."""

    def _fmt(ts: float) -> str:
        hours, remainder = divmod(float(ts), 3600)
        minutes, seconds = divmod(remainder, 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02},{millis:03}"

    with open(dest_path, "w", encoding="utf-8") as handle:
        for idx, seg in enumerate(segments, 1):
            start = _fmt(seg.get("start", 0))
            end = _fmt(seg.get("end", 0))
            text = (seg.get("text") or "").strip()
            handle.write(f"{idx}\n{start} --> {end}\n{text}\n\n")


def export_pad_kit(
    *,
    pad_types: PadTypes,
    pad_assignments: PadAssignments,
    pad_audio_paths: Optional[PadAudioPaths] = None,
    pad_video_assignments: Optional[Mapping[int, Any]] = None,
    video_assets: Optional[Mapping[Any, Mapping[str, Any]]] = None,
    pad_colors: Optional[PadColors] = None,
    audio_array: Optional[Any] = None,
    sample_rate: Optional[float] = None,
    export_format: str,
    kit_name: str,
    out_dir: str,
    source_file: Optional[str],
    create_video_clip: Optional[Callable[[float, float, str], None]] = None,
    pad_numbers: Optional[Sequence[int]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    bridge_exports: Optional[Mapping[str, Any]] = None,
) -> ExportKitResult:
    """Export the current pad configuration to a folder containing media + kit.json.

    Optionally generates "Bridge exports" (Ableton Drum Rack .adg + Akai Force/MPC .Drum.xpm)
    if `bridge_exports` is provided.
    """
    export_format = (export_format or "").lower()
    if export_format not in {"wav", "mp4", "both"}:
        raise ExportKitError(f"Unsupported export format: {export_format}")

    created_files: List[str] = []
    try:
        os.makedirs(out_dir, exist_ok=True)
        pads = _resolve_pad_numbers(pad_types, pad_assignments, pad_audio_paths, pad_numbers)
        total = len(pads)
        metas: List[PadMeta] = []
        cancelled = False

        for idx, pad_num in enumerate(pads):
            if progress_cb:
                progress_cb(idx, total)
            if cancel_cb and cancel_cb():
                cancelled = True
                break

            pad_type = pad_types.get(pad_num, "slice")
            try:
                wav_filename: Optional[str] = None
                mp4_filename: Optional[str] = None

                if pad_type == "slice":
                    assignment = pad_assignments.get(pad_num)
                    if not assignment:
                        continue
                    t0, t1, text = assignment
                    if t1 <= t0:
                        continue
                    safe_text = _safe_label(text, fallback="slice")
                    base = f"{kit_name}_{pad_num:02}_{safe_text}"

                    # WAV export (always for 'wav' and 'both')
                    if export_format in {"wav", "both"}:
                        if audio_array is None or sample_rate in (None, 0):
                            continue
                        wav_filename = f"{base}.wav"
                        wav_path = os.path.join(out_dir, wav_filename)
                        start_idx = max(0, int(float(t0) * float(sample_rate)))
                        end_idx = max(start_idx, int(float(t1) * float(sample_rate)))
                        sf.write(wav_path, audio_array[start_idx:end_idx], int(sample_rate), subtype="PCM_24")
                        created_files.append(wav_path)

                    # MP4 export
                    if export_format in {"mp4", "both"}:
                        asset_path = _resolve_video_asset_path(pad_num, pad_video_assignments, video_assets)
                        mp4_filename = f"{base}.mp4"
                        mp4_path = os.path.join(out_dir, mp4_filename)

                        if asset_path and os.path.exists(asset_path):
                            shutil.copy(asset_path, mp4_path)
                        else:
                            if not create_video_clip:
                                if export_format == "mp4":
                                    raise ExportKitError("Video export requested but no callback provided.")
                            else:
                                create_video_clip(float(t0), float(t1), mp4_path)

                        # If we tried to create/copy a video file but it doesn't exist (audio-only pad), drop it gracefully for 'both'
                        if os.path.exists(mp4_path):
                            mp4_filename = mp4_filename
                            created_files.append(mp4_path)
                        else:
                            mp4_filename = None

                    metas.append(
                        PadMeta(
                            pad=pad_num,
                            start=float(t0),
                            end=float(t1),
                            text=text,
                            color=(pad_colors or {}).get(pad_num, "#ffffff"),
                            file=wav_filename or mp4_filename or "",
                            video_file=mp4_filename,
                        )
                    )
                else:
                    # One-shots: only export audio; MP4 exports are not applicable
                    if export_format == "mp4":
                        continue
                    if not pad_audio_paths:
                        continue
                    source_path = pad_audio_paths.get(pad_num)
                    if not source_path or not os.path.exists(source_path):
                        continue
                    safe_text = _safe_label(os.path.basename(source_path), fallback="oneshot", limit=48)
                    filename = f"{kit_name}_{pad_num:02}_{safe_text}"
                    out_path = os.path.join(out_dir, filename)
                    shutil.copy(source_path, out_path)
                    created_files.append(out_path)
                    metas.append(
                        PadMeta(
                            pad=pad_num,
                            start=0.0,
                            end=-1.0,
                            text="one-shot",
                            color=(pad_colors or {}).get(pad_num, "#ffffff"),
                            file=filename,
                            video_file=None,
                        )
                    )
            except Exception as exc:
                reason = f"Pad {pad_num} export failed: {exc}"
                logger.exception("[KIT-EXPORT] %s", reason)
                _cleanup_partial_export(created_files, out_dir, reason)
                raise ExportKitError(reason) from exc

        if progress_cb:
            progress_cb(total, total)

        if not cancelled:
            kit_payload = {
                "kit_name": kit_name,
                "source_file": source_file,
                "pads": [asdict(meta) for meta in metas],
            }
            kit_json_path = os.path.join(out_dir, "kit.json")
            created_files.append(kit_json_path)
            logger.info("[KIT-EXPORT] Writing kit.json to %s", kit_json_path)
            try:
                with open(kit_json_path, "w", encoding="utf-8") as handle:
                    json.dump(kit_payload, handle, indent=2)
                logger.info("[KIT-EXPORT] kit.json write completed")
            except Exception as exc:
                logger.exception("[KIT-EXPORT] Failed while writing kit.json")
                _cleanup_partial_export(created_files, out_dir, f"kit.json write failed: {exc}")
                raise ExportKitError(f"kit.json write failed: {exc}") from exc

            # Optional: Bridge exports (Ableton .adg + Akai .Drum.xpm)
            if bridge_exports:
                try:
                    export_bridge_exports(
                        kit_name=kit_name,
                        out_dir=out_dir,
                        pad_types=pad_types,
                        pad_assignments=pad_assignments,
                        pad_audio_paths=pad_audio_paths,
                        audio_array=audio_array,
                        sample_rate=sample_rate,
                        options=dict(bridge_exports),
                    )
                except Exception as exc:
                    logger.exception("[KIT-EXPORT] Bridge exports failed: %s", exc)
                    # Don't fail the main kit export if bridge export fails.

        return ExportKitResult(output_dir=out_dir, metas=metas, cancelled=cancelled)
    except ExportKitError:
        raise
    except Exception as exc:
        traceback.print_exc()
        _cleanup_partial_export(created_files, out_dir, f"Unexpected error: {exc}")
        raise ExportKitError(f"Kit export failed: {exc}") from exc


def _resolve_pad_numbers(
    pad_types: PadTypes,
    pad_assignments: PadAssignments,
    pad_audio_paths: Optional[PadAudioPaths],
    pad_numbers: Optional[Sequence[int]],
) -> List[int]:
    if pad_numbers:
        return list(pad_numbers)
    pads = sorted([p for p, t in pad_types.items() if t in ("slice", "oneshot")])
    if pads:
        return pads
    fallback = set(pad_assignments.keys())
    if pad_audio_paths:
        fallback.update(pad_audio_paths.keys())
    return sorted(fallback)


def _safe_label(text: str, *, fallback: str, limit: int = 20) -> str:
    candidate = re.sub(r"[^\w\-_\.]+", "_", (text or "").strip())
    candidate = candidate.strip("_") or fallback
    if len(candidate) > limit:
        candidate = candidate[:limit]
    return candidate or fallback


def _resolve_video_asset_path(
    pad_num: int,
    pad_video_assignments: Optional[Mapping[int, Any]],
    video_assets: Optional[Mapping[Any, Mapping[str, Any]]],
) -> Optional[str]:
    if not pad_video_assignments or not video_assets:
        return None
    asset_id = pad_video_assignments.get(pad_num)
    if asset_id is None:
        return None
    asset_info = video_assets.get(asset_id) if hasattr(video_assets, "get") else None
    if isinstance(asset_info, Mapping):
        return asset_info.get("path")
    return None


def _cleanup_partial_export(paths: List[str], out_dir: str, reason: str) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            logger.exception("[KIT-EXPORT] Failed to clean up %s", path)
    try:
        error_path = os.path.join(out_dir, "EXPORT_FAILED.txt")
        with open(error_path, "w", encoding="utf-8") as handle:
            handle.write(reason)
    except Exception:
        logger.exception("[KIT-EXPORT] Failed to write EXPORT_FAILED.txt")
