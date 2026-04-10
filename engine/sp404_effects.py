"""SP-404 MK2 effects list — CC#83 value → effect name per bus type.

Three different effect lists depending on which bus:
  - BUS 1 & 2 (Ch1, Ch2): 42 effects including Direct FX and performance FX
  - BUS 3 & 4 (Ch3, Ch4): 40 effects, different order, no Direct FX
  - INPUT FX  (Ch5):       18 effects, vocal/amp focused

Source: Roland SP-404MK2 Reference Manual v2.00+
        https://static.roland.com/manuals/sp-404mk2_reference_v200/eng/37138823.html
"""

# CC#83 values for BUS 1 and BUS 2 (MIDI channels 1-2)
BUS12_FX = {
    0: "(OFF)",
    1: "Direct FX1",
    2: "Direct FX2",
    3: "Direct FX3",
    4: "Direct FX4",
    5: "Direct FX5",
    6: "Scatter",
    7: "Downer",
    8: "Ha-Dou",
    9: "Ko-Da-Ma",
    10: "Zan-Zou",
    11: "To-Gu-Ro",
    12: "SBF",
    13: "Stopper",
    14: "Tape Echo",
    15: "TimeCtrlDly",
    16: "Super Filter",
    17: "WrmSaturator",
    18: "303 VinylSim",
    19: "404 VinylSim",
    20: "Cassette Sim",
    21: "Lo-fi",
    22: "Reverb",
    23: "Chorus",
    24: "JUNO Chorus",
    25: "Flanger",
    26: "Phaser",
    27: "Wah",
    28: "Slicer",
    29: "Tremolo/Pan",
    30: "Chromatic PS",
    31: "Hyper-Reso",
    32: "Ring Mod",
    33: "Crusher",
    34: "Overdrive",
    35: "Distortion",
    36: "Equalizer",
    37: "Compressor",
    38: "SX Reverb",
    39: "SX Delay",
    40: "Cloud Delay",
    41: "Back Spin",
}

# CC#83 values for BUS 3 and BUS 4 (MIDI channels 3-4)
BUS34_FX = {
    0: "(OFF)",
    1: "303 VinylSim",
    2: "404 VinylSim",
    3: "Cassette Sim",
    4: "Lo-fi",
    5: "Downer",
    6: "Compressor",
    7: "Equalizer",
    8: "Isolator",
    9: "Super Filter",
    10: "Filter+Drive",
    11: "WrmSaturator",
    12: "Overdrive",
    13: "Distortion",
    14: "Crusher",
    15: "Ring Mod",
    16: "SBF",
    17: "Resonator",
    18: "Hyper-Reso",
    19: "Chromatic PS",
    20: "Reverb",
    21: "Ha-Dou",
    22: "Zan-Zou",
    23: "Sync Delay",
    24: "TimeCtrlDly",
    25: "Ko-Da-Ma",
    26: "Tape Echo",
    27: "Chorus",
    28: "JUNO Chorus",
    29: "Flanger",
    30: "Phaser",
    31: "Wah",
    32: "Slicer",
    33: "Tremolo/Pan",
    34: "To-Gu-Ro",
    35: "DJFX Looper",
    36: "Scatter",
    37: "SX Reverb",
    38: "SX Delay",
    39: "Cloud Delay",
}

# CC#83 values for INPUT FX (MIDI channel 5)
INPUT_FX = {
    0: "(OFF)",
    1: "Auto Pitch",
    2: "Vocoder",
    3: "Harmony",
    4: "Gt Amp Sim",
    5: "Chorus",
    6: "JUNO Chorus",
    7: "Reverb",
    8: "TimeCtrlDly",
    9: "Chromatic PS",
    10: "Downer",
    11: "WrmSaturator",
    12: "303 VinylSim",
    13: "404 VinylSim",
    14: "Cassette Sim",
    15: "Lo-fi",
    16: "Equalizer",
    17: "Compressor",
}

# Map tab key → which FX list to use
TAB_FX_LIST = {
    "bus1_fx": BUS12_FX,
    "bus2_fx": BUS12_FX,
    "bus3_fx": BUS34_FX,
    "bus4_fx": BUS34_FX,
    "input_fx": INPUT_FX,
}


def fx_name_for_tab(tab_key: str, cc83_value: int) -> str:
    """Get the effect name for a CC#83 value on the given bus tab."""
    fx_list = TAB_FX_LIST.get(tab_key, {})
    return fx_list.get(cc83_value, f"#{cc83_value}")


def fx_list_for_tab(tab_key: str) -> list[tuple[int, str]]:
    """Get all (cc_value, name) pairs for a bus tab, sorted by value."""
    fx_list = TAB_FX_LIST.get(tab_key, {})
    return sorted(fx_list.items())


def fx_count_for_tab(tab_key: str) -> int:
    """How many effects are available on this bus."""
    return len(TAB_FX_LIST.get(tab_key, {}))
