"""Engine package convenience exports.

Keep these imports optional so pure modules such as ai_pattern and
performance_recorder can be imported on machines without audio/MIDI
hardware dependencies installed.
"""

try:
    from .audio_engine import AudioEngine
except ImportError:
    AudioEngine = None

try:
    from .midi_input import MidiInput
except ImportError:
    MidiInput = None

try:
    from .pad_bank import PadBank, Pad, PlayMode
except ImportError:
    PadBank = Pad = PlayMode = None

try:
    from .sample_loader import SampleLoader
except ImportError:
    SampleLoader = None

try:
    from .kit_manager import KitManager
except ImportError:
    KitManager = None

try:
    from .atom_sq import AtomSQ, find_atom_sq_ports
except ImportError:
    AtomSQ = None
    find_atom_sq_ports = None

try:
    from .effects import (
        StateVariableFilter, Reverb, Delay, Bitcrusher, Drive,
        EffectsChain, MasterEffects,
    )
except ImportError:
    StateVariableFilter = Reverb = Delay = Bitcrusher = Drive = None
    EffectsChain = MasterEffects = None

try:
    from .recorder import Recorder
except ImportError:
    Recorder = None

try:
    from .sequencer import Sequencer
except ImportError:
    Sequencer = None
