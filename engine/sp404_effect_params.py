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
