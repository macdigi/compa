"""SP-404 MK2 per-effect Ctrl 1-6 parameter table.

Built from Roland's official SP-404 MK2 Reference Manual (v2.00).
Each effect maps to an ordered list of (param_name, range) tuples. The
list order matches the SP-404's Ctrl knob layout:

    Ctrl 1  →  params[0]  (main 1, MIDI CC 16)
    Ctrl 2  →  params[1]  (main 2, MIDI CC 17)
    Ctrl 3  →  params[2]  (main 3, MIDI CC 18)
    Ctrl 4  →  params[3]  (sub 1,  MIDI CC 80)
    Ctrl 5  →  params[4]  (sub 2,  MIDI CC 81)
    Ctrl 6  →  params[5]  (sub 3,  MIDI CC 82)

Effects with fewer than 6 params leave higher Ctrl slots unmapped —
the renderer falls back to "Ctrl N" for those.

Source: https://static.roland.com/manuals/sp-404mk2_reference_v200/eng/37138696.html
"""

SP404_EFFECT_PARAMS: dict[str, list[tuple[str, str]]] = {
    "303 VinylSim": [
        ("Comp",       "0 - 100"),
        ("Noise",      "0 - 100"),
        ("Wow Flut",   "0 - 100"),
        ("Level",      "0 - 100"),
    ],
    "404 VinylSim": [
        ("Frequency",  "0 - 100"),
        ("Noise",      "0 - 100"),
        ("Wow Flut",   "0 - 100"),
    ],
    "Auto Pitch": [
        ("Pitch",      "-100 - 100"),
        ("Formant",    "-100 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("At Pitch",   "0 - 100"),
        ("Key",        "CHROMA, A, Bb, B, C, Db, D, Eb, E, F, Gb, G, Ab"),
        ("Robot",      "OFF, ON"),
    ],
    "Back Spin": [
        ("Length",     "1/1, 1/2, 1/4, 1/8, 1/16"),
        ("Speed",      "0 - 100"),
        ("Back SW",    "OFF, ON"),
    ],
    "Cassette Sim": [
        ("Tone",       "0 - 100"),
        ("Hiss",       "0 - 100"),
        ("Age",        "0 - 60 yrs"),
        ("Drive",      "0 - 100"),
        ("Wow Flut",   "0 - 100"),
        ("Catch",      "0 - 100"),
    ],
    "Chorus": [
        ("Depth",      "0 - 100"),
        ("Rate",       "0.33 - 2.30 sec"),
        ("Balance",    "100:0 - 0:100 %"),
        ("EQ Low",     "-15 - 15 dB"),
        ("EQ High",    "-15 - 15 dB"),
        ("Level",      "0 - 100"),
    ],
    "Chromatic PS": [
        ("Pitch 1",    "-24 - 12 semi"),
        ("Pitch 2",    "-24 - 12 semi"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Pan 1",      "L50 - R50"),
        ("Pan 2",      "L50 - R50"),
    ],
    "Cloud Delay": [
        ("Window",     "0 - 100"),
        ("Pitch",      "-12 - +12"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Feedback",   "0 - 99 %"),
        ("Cloudy",     "0 - 100"),
        ("Lo-Fi",      "OFF, ON"),
    ],
    "Compressor": [
        ("Sustain",    "0 - 100"),
        ("Attack",     "0 - 100"),
        ("Ratio",      "0 - 100"),
        ("Level",      "0 - 100"),
    ],
    "Crusher": [
        ("Filter",     "331 - 15392 Hz"),
        ("Rate",       "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
    ],
    "DJFX Looper": [
        ("Length",     "0.230 - 0.012 sec"),
        ("Speed",      "-100 - 100"),
        ("Loop SW",    "OFF, ON"),
    ],
    "Distortion": [
        ("Drive",      "0 - 100"),
        ("Tone",       "-100 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Level",      "0 - 100"),
    ],
    "Downer": [
        ("Depth",      "0 - 100"),
        ("Rate",       "1/32, 1/16, 1/8, 1/4, 1/2, 1, 2"),
        ("Filter",     "0 - 100"),
        ("Pitch",      "OFF, ON"),
        ("Resonance",  "0 - 100"),
    ],
    "Equalizer": [
        ("Low Gain",   "-15 - 15 dB"),
        ("Mid Gain",   "-15 - 15 dB"),
        ("High Gain",  "-15 - 15 dB"),
        ("Low Freq",   "20 - 400 Hz"),
        ("Mid Freq",   "200 - 8000 Hz"),
        ("High Freq",  "2000 - 16000 Hz"),
    ],
    "Filter+Drive": [
        ("Cutoff",     "20 - 16000 Hz"),
        ("Resonance",  "0 - 100"),
        ("Drive",      "0 - 100"),
        ("Flt Type",   "HPF, LPF"),
        ("Low Freq",   "20 - 16000 Hz"),
        ("Low Gain",   "-24 - 24 dB"),
    ],
    "Flanger": [
        ("Depth",      "0 - 100"),
        ("Rate",       "0 - 100 / 4.000 - 0.016 bars"),
        ("Manual",     "0 - 100"),
        ("Resonance",  "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Sync",       "OFF, ON"),
    ],
    "Gt Amp Sim": [
        ("Amp Type",   "JC, TWIN, BG, MATCH, MS, SLDN"),
        ("Drive",      "0 - 100"),
        ("Level",      "0 - 100"),
        ("Bass",       "-100 - 100"),
        ("Middle",     "-100 - 100"),
        ("Treble",     "-100 - 100"),
    ],
    "Ha-Dou": [
        ("Mod Depth",  "0 - 100"),
        ("Time",       "0 - 100"),
        ("Level",      "0 - 100"),
        ("Low Cut",    "FLAT, 20 - 800 Hz"),
        ("High Cut",   "630 - 12500 Hz, FLAT"),
        ("Pre Delay",  "0 - 100"),
    ],
    "Harmony": [
        ("Pitch",      "-100 - 100"),
        ("Formant",    "-100 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("At Pitch",   "0 - 100"),
        ("Key",        "CHROMA, A, Bb, B, C, Db, D, Eb, E, F, Gb, G, Ab"),
        ("Harmony",    "Root, P5, Oct, UpDn, UpDnP5, 3rd, 5thUp, 5thDn, 7thUp, 7thDn"),
    ],
    "Hyper-Reso": [
        ("Note",       "-17 - -1, 1 - 18"),
        ("Spread",     "UNISON, TINY, SMALL, MEDIUM, HUGE"),
        ("Character",  "0 - 100"),
        ("Scale",      "C maj - B maj, C min - B min"),
        ("Feedback",   "0 - 99 %"),
        ("Env Mod",    "0 - 100"),
    ],
    "Isolator": [
        ("Low",        "-INF, -41.87 - +12 dB"),
        ("Mid",        "-INF, -41.87 - +12 dB"),
        ("High",       "-INF, -41.87 - +12 dB"),
    ],
    "JUNO Chorus": [
        ("Mode",       "JUNO 1, JUNO 2, JUNO12, JX-1 1, JX-1 2"),
        ("Noise",      "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
    ],
    "Ko-Da-Ma": [
        ("Time",       "1/32 - 1/1 sync"),
        ("Feedback",   "0 - 99 %"),
        ("Send",       "0 - 100"),
        ("L Damp F",   "FLAT, 80 - 800 Hz"),
        ("H Damp F",   "630 - 12500 Hz, FLAT"),
        ("Mode",       "SINGLE, PAN"),
    ],
    "Lo-fi": [
        ("Pre Filt",   "1 - 6"),
        ("Lofi Type",  "1 - 9"),
        ("Tone",       "-100 - 100"),
        ("Cutoff",     "200 - 8000 Hz"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Level",      "0 - 100"),
    ],
    "Overdrive": [
        ("Drive",      "0 - 100"),
        ("Tone",       "-100 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Level",      "0 - 100"),
    ],
    "Phaser": [
        ("Depth",      "0 - 100"),
        ("Rate",       "0 - 100 / 4.000 - 0.016 bars"),
        ("Manual",     "0 - 100"),
        ("Resonance",  "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Sync",       "OFF, ON"),
    ],
    "Resonator": [
        ("Root",       "C1 - G9"),
        ("Bright",     "0 - 100"),
        ("Feedback",   "0 - 99 %"),
        ("Chord",      "Root, Oct, UpDn, P5, m3, m5, m7, m7oct, m9, m11, M3, M5, M7, M7oct, M9, M11"),
        ("Panning",    "0 - 100"),
        ("Env Mod",    "0 - 100"),
    ],
    "Reverb": [
        ("Type",       "AMBI, ROOM, HALL1, HALL2"),
        ("Time",       "0 - 100"),
        ("Level",      "0 - 100"),
        ("Low Cut",    "FLAT, 20 - 800 Hz"),
        ("High Cut",   "630 - 12500 Hz, FLAT"),
        ("Pre Delay",  "0 - 100 ms"),
    ],
    "Ring Mod": [
        ("Frequency",  "0 - 100"),
        ("Sens",       "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Polarity",   "OFF, ON"),
        ("EQ Low",     "-15 - 15 dB"),
        ("EQ High",    "-15 - 15 dB"),
    ],
    "SBF": [
        ("Interval",   "0 - 100"),
        ("Width",      "0 - 100"),
        ("Balance",    "100:0 - 0:100 %"),
        ("Type",       "SBF1, SBF2, SBF3, SBF4, SBF5, SBF6"),
        ("Gain",       "-INF, -52.3 - +10.0 dB"),
    ],
    "SX Delay": [
        ("Time",       "1/32 - 1/1 sync"),
        ("Feedback",   "0 - 99 %"),
        ("Balance",    "100:0 - 0:100 %"),
    ],
    "SX Reverb": [
        ("Time",       "0 - 100"),
        ("Tone",       "-12 - +12"),
        ("Balance",    "100:0 - 0:100 %"),
    ],
    "Scatter": [
        ("Type",       "1 - 10"),
        ("Depth",      "10 - 100"),
        ("Scatter",    "OFF, ON"),
        ("Speed",      "SINGLE, DOUBLE"),
    ],
    "Slicer": [
        ("Pattern",    "1 - 32"),
        ("Speed",      "0 - 100 / 2/1 - 1/64T"),
        ("Depth",      "0 - 100"),
        ("Shuffle",    "0 - 100"),
        ("Mode",       "LEGATO, SLASH"),
        ("Sync",       "OFF, ON"),
    ],
    "Stopper": [
        ("Depth",      "0 - 100"),
        ("Rate",       "1/128, 1/64, 1/32, 1/16, 1/8, 1/4, 1/2, 1, 2"),
        ("Resonance",  "0 - 100"),
        ("Flt Mod",    "0 - 100"),
        ("Amp Mod",    "0 - 100"),
    ],
    "Super Filter": [
        ("Cutoff",     "0 - 100"),
        ("Resonance",  "0 - 100"),
        ("Flt Type",   "LPF, BPF, HPF"),
        ("Depth",      "0 - 100"),
        ("Rate",       "0 - 100"),
        ("Sync",       "OFF, ON"),
    ],
    "Sync Delay": [
        ("Time",       "1/32 - 1/1 sync"),
        ("Feedback",   "0 - 99 %"),
        ("Level",      "0 - 100"),
        ("L Damp F",   "FLAT, 80 - 800 Hz"),
        ("H Damp F",   "630 - 12500 Hz, FLAT"),
    ],
    "Tape Echo": [
        ("Time",       "10 - 800 ms"),
        ("Feedback",   "0 - 99"),
        ("Level",      "0 - 100"),
        ("Mode",       "S, M, L, S+M, S+L, M+L, S+M+L"),
        ("W/F Rate",   "0 - 100"),
        ("W/F Depth",  "0 - 100"),
    ],
    "TimeCtrlDly": [
        ("Time",       "0 - 100"),
        ("Feedback",   "0 - 99"),
        ("Level",      "0 - 100"),
        ("L Damp F",   "FLAT, 80 - 800 Hz"),
        ("H Damp F",   "630 - 12500 Hz, FLAT"),
        ("Sync",       "OFF, ON"),
    ],
    "To-Gu-Ro": [
        ("Depth",      "0 - 100"),
        ("Rate",       "0 - 100 / 1/128 - 2/1 sync"),
        ("Resonance",  "0 - 100"),
        ("Flt Mod",    "0 - 100"),
        ("Amp Mod",    "0 - 100"),
        ("Sync",       "OFF, ON"),
    ],
    "Tremolo/Pan": [
        ("Depth",      "0 - 100"),
        ("Rate",       "0 - 100 / 1.000 - 0.010"),
        ("Type",       "TRE, PAN"),
        ("Wave",       "TRI, SQR, SIN, SAW1, SAW2, TRP"),
        ("Sync",       "OFF, ON"),
    ],
    "Vocoder": [
        ("Note",       "-17 - -1, 1 - 18"),
        ("Formant",    "-100 - 100"),
        ("Tone",       "-100 - 100"),
        ("Scale",      "C maj - B maj, C min - B min"),
        ("Chord",      "Root, P5, Oct, UpDn, UpDnP5, 3rd, 5thUp, 5thDn, 7thUp, 7thDn"),
        ("Balance",    "100:0 - 0:100 %"),
    ],
    "Wah": [
        ("Peak",       "0 - 100"),
        ("Rate",       "0 - 100 / 1.000 - 0.010 bars"),
        ("Manual",     "0 - 100"),
        ("Depth",      "0 - 100"),
        ("Flt Type",   "LPF, BPF"),
        ("Sync",       "OFF, ON"),
    ],
    "WrmSaturator": [
        ("Drive",      "0 - 48 dB"),
        ("Eq Low",     "-24 - 24 dB"),
        ("Eq High",    "-24 - 24 dB"),
        ("Level",      "0 - 100"),
    ],
    "Zan-Zou": [
        ("Time",       "1 - 100 / 1/32 - 1/1 sync"),
        ("Feedback",   "0 - 99"),
        ("HF Damp",    "200 - 8000 Hz, OFF"),
        ("Level",      "0 - 100"),
        ("Mode",       "2TAP, 3TAP, 4TAP"),
        ("Sync",       "OFF, ON"),
    ],
}


def ctrl_label(effect_name: str, ctrl_idx: int) -> str:
    """Return the parameter name controlled by Ctrl `ctrl_idx` (0..5)
    of `effect_name`. Falls back to "Ctrl N" if the effect isn't in
    the table (e.g. user-assigned Direct FX slots) or `ctrl_idx` is
    past the effect's parameter count (Ctrl knobs that don't do
    anything for that effect)."""
    if not (0 <= ctrl_idx < 6):
        return f"Ctrl {ctrl_idx + 1}"
    params = SP404_EFFECT_PARAMS.get(effect_name)
    if not params or ctrl_idx >= len(params):
        return f"Ctrl {ctrl_idx + 1}"
    return params[ctrl_idx][0]


def ctrl_range(effect_name: str, ctrl_idx: int) -> str:
    """Return the documented value range string for Ctrl `ctrl_idx` of
    `effect_name`, or empty string if not known."""
    if not (0 <= ctrl_idx < 6):
        return ""
    params = SP404_EFFECT_PARAMS.get(effect_name)
    if not params or ctrl_idx >= len(params):
        return ""
    return params[ctrl_idx][1]


# ── Value formatter ─────────────────────────────────────────────────
# Maps a raw 0..127 MIDI value to the SP-404's on-screen display string
# for the parameter, by interpreting the range strings stored in
# SP404_EFFECT_PARAMS. Handles:
#
#   - Linear numeric ranges with units:
#       "0 - 100"          -> 0..100 integer
#       "0 - 99 %"         -> "65%"
#       "-15 - 15 dB"      -> "-7 dB"
#       "20 - 16000 Hz"    -> "8010 Hz"
#       "10 - 800 ms"      -> "405 ms"
#   - Binary toggles:
#       "OFF, ON"          -> "OFF" or "ON"
#   - Discrete enum lists (numeric or string):
#       "1/32, 1/16, 1/8, 1/4, 1/2, 1, 2"  -> bucketed into 7 values
#       "AMBI, ROOM, HALL1, HALL2"          -> bucketed into 4
#   - Special-case ranges:
#       "100:0 - 0:100 %"     -> "L:R" balance ratio
#       "L50 - R50"           -> pan position with C at center
#       "C1 - G9"             -> note name
#       "FLAT, X - Y unit"    -> FLAT at lo end, range above
#       "X - Y unit, FLAT"    -> FLAT at hi end, range below
#       "-INF, X - Y dB"      -> -INF at lo end, range above
#   - Dual-mode ranges ("0 - 100 / 4.000 - 0.016 bars"): currently
#     uses the first range half as a sensible default.
#
# Anything we can't parse falls back to the raw 0..127 value so the
# display always shows something useful.

import re as _re

_NUM = r'[+\-]?\d+(?:\.\d+)?'
_RE_LIN = _re.compile(r'^(' + _NUM + r')\s*-\s*(' + _NUM + r')\s*(.*)$')
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",
               "F#", "G", "G#", "A", "A#", "B")


def _interp_linear(midi_val: int, lo: float, hi: float) -> float:
    return lo + (midi_val / 127.0) * (hi - lo)


def _bucket_enum(midi_val: int, n: int) -> int:
    """Bucket 0..127 into 0..n-1 indices. Even-width buckets so the
    full 128-slot range distributes across every option."""
    if n <= 1:
        return 0
    return min(n - 1, max(0, midi_val * n // 128))


def _format_with_unit(v: float, unit: str, span: float,
                       lo_str: str = "", hi_str: str = "") -> str:
    """Format `v` with optional unit. If both range bounds were
    integers in the source string, render `v` as an integer (so
    "1 - 6" never shows up as "3.52"). Otherwise pick decimal
    precision from the span so small ranges keep precision."""
    unit = unit.strip()
    int_range = ("." not in lo_str) and ("." not in hi_str)
    if int_range:
        s = f"{int(round(v))}"
    elif span <= 1:
        s = f"{v:.3f}"
    elif span <= 5:
        s = f"{v:.2f}"
    elif span <= 20:
        s = f"{v:.1f}"
    else:
        s = f"{int(round(v))}"
    if unit:
        return f"{s} {unit}"
    return s


def format_value(effect_name: str, ctrl_idx: int, midi_val: int) -> str:
    """Return the SP-404's display-formatted value for Ctrl `ctrl_idx`
    on `effect_name`. `midi_val` is the raw 0..127 MIDI value. If the
    parameter's range can't be parsed, falls back to the raw integer."""
    midi_val = max(0, min(127, int(midi_val)))
    rng = ctrl_range(effect_name, ctrl_idx)
    if not rng:
        return str(midi_val)
    return _format_range(rng, midi_val)


def _format_range(rng: str, midi_val: int) -> str:
    s = rng.strip()

    # ── Binary toggle ────────────────────────────────────────────
    if s == "OFF, ON":
        return "ON" if midi_val >= 64 else "OFF"

    # ── Balance ratio (e.g. "100:0 - 0:100 %") ───────────────────
    if "100:0" in s and "0:100" in s:
        l_pct = round((127 - midi_val) / 127 * 100)
        r_pct = 100 - l_pct
        return f"{l_pct}:{r_pct}"

    # ── Pan "L50 - R50" ──────────────────────────────────────────
    if s == "L50 - R50":
        if midi_val == 64:
            return "C"
        if midi_val < 64:
            return f"L{round((64 - midi_val) / 64 * 50)}"
        return f"R{round((midi_val - 64) / 63 * 50)}"

    # ── Note name (e.g. "C1 - G9") ───────────────────────────────
    if _re.match(r'^[A-G]#?\d+\s*-\s*[A-G]#?\d+$', s):
        n = midi_val
        return f"{_NOTE_NAMES[n % 12]}{(n // 12) - 1}"

    # ── FLAT prefix: "FLAT, 20 - 800 Hz" — FLAT at lo end ────────
    if s.startswith("FLAT,"):
        if midi_val == 0:
            return "FLAT"
        rest = s.split(",", 1)[1].strip()
        m = _RE_LIN.match(rest)
        if m:
            lo_str, hi_str = m.group(1), m.group(2)
            lo, hi = float(lo_str), float(hi_str)
            v = lo + ((midi_val - 1) / 126.0) * (hi - lo)
            return _format_with_unit(v, m.group(3), abs(hi - lo),
                                       lo_str, hi_str)

    # ── FLAT suffix: "630 - 12500 Hz, FLAT" — FLAT at hi end ─────
    if s.rstrip().endswith(", FLAT") or s.rstrip().endswith(",FLAT"):
        if midi_val == 127:
            return "FLAT"
        rest = s[:s.rfind(",")].strip()
        m = _RE_LIN.match(rest)
        if m:
            lo_str, hi_str = m.group(1), m.group(2)
            lo, hi = float(lo_str), float(hi_str)
            v = lo + (midi_val / 126.0) * (hi - lo)
            return _format_with_unit(v, m.group(3), abs(hi - lo),
                                       lo_str, hi_str)

    # ── -INF prefix (e.g. "-INF, -41.87 - +12 dB") ───────────────
    if s.startswith("-INF,"):
        if midi_val == 0:
            return "-INF"
        rest = s.split(",", 1)[1].strip()
        m = _RE_LIN.match(rest)
        if m:
            lo_str, hi_str = m.group(1), m.group(2)
            lo, hi = float(lo_str), float(hi_str)
            v = lo + ((midi_val - 1) / 126.0) * (hi - lo)
            return _format_with_unit(v, m.group(3), abs(hi - lo),
                                       lo_str, hi_str)

    # ── OFF suffix: "200 - 8000 Hz, OFF" — OFF at hi end ─────────
    if s.rstrip().endswith(", OFF") or s.rstrip().endswith(",OFF"):
        if midi_val == 127:
            return "OFF"
        rest = s[:s.rfind(",")].strip()
        m = _RE_LIN.match(rest)
        if m:
            lo_str, hi_str = m.group(1), m.group(2)
            lo, hi = float(lo_str), float(hi_str)
            v = lo + (midi_val / 126.0) * (hi - lo)
            return _format_with_unit(v, m.group(3), abs(hi - lo),
                                       lo_str, hi_str)

    # ── Sync time division "1/X - 1/Y sync" ──────────────────────
    if _re.match(r'^1/\d+\s*-\s*1/\d+\s*sync$', s):
        SYNC_DIVS = ["1/32", "1/16", "1/8", "1/4", "1/2", "1/1"]
        return SYNC_DIVS[_bucket_enum(midi_val, len(SYNC_DIVS))]

    # ── Dual-mode "X - Y / X' - Y' unit" — use first range. ──────
    if " / " in s:
        first = s.split(" / ", 1)[0].strip()
        m = _RE_LIN.match(first)
        if m:
            lo_str, hi_str = m.group(1), m.group(2)
            lo, hi = float(lo_str), float(hi_str)
            return _format_with_unit(
                _interp_linear(midi_val, lo, hi),
                m.group(3), abs(hi - lo), lo_str, hi_str,
            )

    # ── Discrete enum lists ──────────────────────────────────────
    # If the raw string has commas (and we got past every special
    # prefix/suffix check), treat it as an enum bucket. Note that
    # _RE_LIN can spuriously match "-17 - -1, 1 - 18" with the trailing
    # ", 1 - 18" captured as a unit, so prefer the comma-split here
    # over the linear-range fallback for any string with commas.
    if "," in s:
        items = [x.strip() for x in s.split(",")]
        if len(items) >= 2:
            idx = _bucket_enum(midi_val, len(items))
            return items[idx]

    # ── Linear numeric range ─────────────────────────────────────
    m = _RE_LIN.match(s)
    if m:
        lo_str, hi_str = m.group(1), m.group(2)
        lo, hi = float(lo_str), float(hi_str)
        return _format_with_unit(
            _interp_linear(midi_val, lo, hi),
            m.group(3), abs(hi - lo), lo_str, hi_str,
        )

    # Unknown format — show raw value so the user still sees motion.
    return str(midi_val)
