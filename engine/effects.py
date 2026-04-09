"""
Real-time audio effects processor for pi-sampler.

Designed for Raspberry Pi 3B constraints:
- 44100 Hz sample rate, float32 stereo frames [N, 2]
- 256-sample buffer target
- Zero allocation in process() hot paths
- Numpy vectorized where possible
"""

import math
import numpy as np

SAMPLE_RATE = 44100


# ---------------------------------------------------------------------------
# 1. State Variable Filter
# ---------------------------------------------------------------------------

class StateVariableFilter:
    """12 dB/oct state-variable filter (lowpass / highpass / bandpass).

    Per-sample SVF: efficient, stable at high cutoff, no matrix math.
    """

    TYPES = ("lowpass", "highpass", "bandpass")

    def __init__(self, cutoff=1000.0, resonance=0.0, filter_type="lowpass"):
        self.bypass = False
        self._type = filter_type
        self._cutoff = float(cutoff)
        self._resonance = float(resonance)
        # SVF state per channel: [ic1eq, ic2eq]
        self._state = np.zeros((2, 2), dtype=np.float32)  # [channel, state]
        self._update_coefficients()

    # -- coefficient helpers ------------------------------------------------

    def _update_coefficients(self):
        q = 0.5 + self._resonance * 9.5  # map 0-1 -> Q 0.5-10
        g = math.tan(math.pi * min(self._cutoff, SAMPLE_RATE * 0.499) / SAMPLE_RATE)
        k = 1.0 / q
        self._a1 = 1.0 / (1.0 + g * (g + k))
        self._a2 = g * self._a1
        self._a3 = g * self._a2

    # -- parameter setters --------------------------------------------------

    def set_cutoff(self, hz):
        self._cutoff = float(np.clip(hz, 20.0, 20000.0))
        self._update_coefficients()

    def set_resonance(self, q):
        self._resonance = float(np.clip(q, 0.0, 1.0))
        self._update_coefficients()

    def set_type(self, type_str):
        if type_str in self.TYPES:
            self._type = type_str

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        """Filter *buffer* in-place.  buffer shape: [N, 2] float32."""
        if self.bypass:
            return
        a1, a2, a3 = self._a1, self._a2, self._a3
        k = 1.0 / (0.5 + self._resonance * 9.5)
        ftype = self._type
        n_samples = buffer.shape[0]

        for ch in range(2):
            ic1eq = float(self._state[ch, 0])
            ic2eq = float(self._state[ch, 1])
            samples = buffer[:, ch]
            for i in range(n_samples):
                v0 = float(samples[i])
                v3 = v0 - ic2eq
                v1 = a1 * ic1eq + a2 * v3
                v2 = ic2eq + a2 * ic1eq + a3 * v3
                ic1eq = 2.0 * v1 - ic1eq
                ic2eq = 2.0 * v2 - ic2eq
                if ftype == "lowpass":
                    samples[i] = v2
                elif ftype == "highpass":
                    samples[i] = v0 - k * v1 - v2
                else:  # bandpass
                    samples[i] = v1
            self._state[ch, 0] = ic1eq
            self._state[ch, 1] = ic2eq

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "StateVariableFilter",
            "bypass": self.bypass,
            "cutoff": self._cutoff,
            "resonance": self._resonance,
            "filter_type": self._type,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            cutoff=d.get("cutoff", 1000.0),
            resonance=d.get("resonance", 0.0),
            filter_type=d.get("filter_type", "lowpass"),
        )
        obj.bypass = d.get("bypass", False)
        return obj


# ---------------------------------------------------------------------------
# 2. Schroeder Reverb (4 comb + 2 allpass)
# ---------------------------------------------------------------------------

class Reverb:
    """Classic Schroeder reverb with 4 parallel comb filters feeding
    2 series allpass filters.  All buffers pre-allocated."""

    # Comb delay lengths in samples (prime-ish, spread for density)
    _COMB_LENGTHS = (1557, 1617, 1491, 1422)
    # Allpass delay lengths
    _AP_LENGTHS = (225, 556)
    _AP_GAIN = 0.5

    def __init__(self, room=0.5, damping=0.5, mix=0.3):
        self.bypass = False
        self._room = float(room)
        self._damping = float(damping)
        self._mix = float(mix)

        # Pre-allocate comb filter state
        self._comb_buffers = [
            np.zeros((length, 2), dtype=np.float32)
            for length in self._COMB_LENGTHS
        ]
        self._comb_indices = [0] * len(self._COMB_LENGTHS)
        self._comb_filter_state = np.zeros((len(self._COMB_LENGTHS), 2), dtype=np.float32)

        # Pre-allocate allpass state
        self._ap_buffers = [
            np.zeros((length, 2), dtype=np.float32)
            for length in self._AP_LENGTHS
        ]
        self._ap_indices = [0] * len(self._AP_LENGTHS)

        # Scratch buffer (max expected frame size 1024)
        self._scratch = np.zeros((1024, 2), dtype=np.float32)

    # -- parameter setters --------------------------------------------------

    def set_room(self, size):
        self._room = float(np.clip(size, 0.0, 1.0))

    def set_damping(self, val):
        self._damping = float(np.clip(val, 0.0, 1.0))

    def set_mix(self, val):
        self._mix = float(np.clip(val, 0.0, 1.0))

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        if self.bypass:
            return
        n = buffer.shape[0]
        # Ensure scratch is large enough (should not allocate in steady state)
        if self._scratch.shape[0] < n:
            self._scratch = np.zeros((n, 2), dtype=np.float32)

        wet = self._scratch[:n]
        wet[:] = 0.0
        feedback = self._room * 0.85 + 0.1  # map 0-1 -> 0.1-0.95
        damp = self._damping

        # --- 4 parallel comb filters (vectorised per-sample with numpy) ---
        for ci in range(len(self._COMB_LENGTHS)):
            buf = self._comb_buffers[ci]
            blen = buf.shape[0]
            idx = self._comb_indices[ci]
            filt = self._comb_filter_state[ci]  # [2] per channel damping state

            for i in range(n):
                out = buf[idx]  # [2]
                # One-pole damping filter on comb output
                filt[:] = out * (1.0 - damp) + filt * damp
                buf[idx] = buffer[i] + filt * feedback
                wet[i] += out
                idx = (idx + 1) % blen

            self._comb_indices[ci] = idx
            self._comb_filter_state[ci] = filt

        # Scale comb output
        wet *= 0.25

        # --- 2 series allpass filters ---
        g = self._AP_GAIN
        for ai in range(len(self._AP_LENGTHS)):
            buf = self._ap_buffers[ai]
            blen = buf.shape[0]
            idx = self._ap_indices[ai]

            for i in range(n):
                delayed = buf[idx]
                inp = wet[i]
                wet[i] = delayed - inp * g
                buf[idx] = inp + delayed * g
                idx = (idx + 1) % blen

            self._ap_indices[ai] = idx

        # --- mix ---
        mix = self._mix
        buffer[:n] = buffer[:n] * (1.0 - mix) + wet * mix

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "Reverb",
            "bypass": self.bypass,
            "room": self._room,
            "damping": self._damping,
            "mix": self._mix,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            room=d.get("room", 0.5),
            damping=d.get("damping", 0.5),
            mix=d.get("mix", 0.3),
        )
        obj.bypass = d.get("bypass", False)
        return obj


# ---------------------------------------------------------------------------
# 3. Delay (with stereo ping-pong option)
# ---------------------------------------------------------------------------

class Delay:
    """Stereo delay with optional ping-pong.  Circular buffer, zero-alloc."""

    MAX_DELAY_MS = 2000
    MAX_DELAY_SAMPLES = int(SAMPLE_RATE * MAX_DELAY_MS / 1000)

    def __init__(self, time_ms=250.0, feedback=0.4, mix=0.3, ping_pong=False):
        self.bypass = False
        self._time_ms = float(time_ms)
        self._delay_samples = int(self._time_ms * SAMPLE_RATE / 1000)
        self._feedback = float(feedback)
        self._mix = float(mix)
        self._ping_pong = bool(ping_pong)

        # Pre-allocate circular buffer for max delay
        self._buffer = np.zeros((self.MAX_DELAY_SAMPLES, 2), dtype=np.float32)
        self._write_pos = 0

    # -- parameter setters --------------------------------------------------

    def set_time_ms(self, ms):
        self._time_ms = float(np.clip(ms, 0.0, self.MAX_DELAY_MS))
        self._delay_samples = int(self._time_ms * SAMPLE_RATE / 1000)

    def set_feedback(self, val):
        self._feedback = float(np.clip(val, 0.0, 0.95))

    def set_mix(self, val):
        self._mix = float(np.clip(val, 0.0, 1.0))

    def set_ping_pong(self, enabled):
        self._ping_pong = bool(enabled)

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        if self.bypass or self._delay_samples == 0:
            return
        n = buffer.shape[0]
        delay = self._delay_samples
        fb = self._feedback
        mix = self._mix
        buf = self._buffer
        blen = self.MAX_DELAY_SAMPLES
        wp = self._write_pos
        ping_pong = self._ping_pong

        for i in range(n):
            rp = (wp - delay) % blen
            delayed = buf[rp].copy()  # [2] -- tiny stack copy, no heap alloc

            if ping_pong:
                # Cross-feed: left delayed -> right, right delayed -> left
                feed_l = buffer[i, 0] + delayed[1] * fb
                feed_r = buffer[i, 1] + delayed[0] * fb
                buf[wp, 0] = feed_l
                buf[wp, 1] = feed_r
            else:
                buf[wp] = buffer[i] + delayed * fb

            buffer[i] = buffer[i] * (1.0 - mix) + delayed * mix
            wp = (wp + 1) % blen

        self._write_pos = wp

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "Delay",
            "bypass": self.bypass,
            "time_ms": self._time_ms,
            "feedback": self._feedback,
            "mix": self._mix,
            "ping_pong": self._ping_pong,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            time_ms=d.get("time_ms", 250.0),
            feedback=d.get("feedback", 0.4),
            mix=d.get("mix", 0.3),
            ping_pong=d.get("ping_pong", False),
        )
        obj.bypass = d.get("bypass", False)
        return obj


# ---------------------------------------------------------------------------
# 4. Bitcrusher
# ---------------------------------------------------------------------------

class Bitcrusher:
    """Bit-depth reduction and sample-rate decimation."""

    def __init__(self, bits=16, downsample=1, mix=1.0):
        self.bypass = False
        self._bits = int(bits)
        self._downsample = int(downsample)
        self._mix = float(mix)
        # Hold value for downsampling (per channel)
        self._hold = np.zeros(2, dtype=np.float32)
        self._hold_counter = 0

    # -- parameter setters --------------------------------------------------

    def set_bits(self, n):
        self._bits = int(np.clip(n, 1, 16))

    def set_downsample(self, n):
        self._downsample = int(np.clip(n, 1, 100))

    def set_mix(self, val):
        self._mix = float(np.clip(val, 0.0, 1.0))

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        if self.bypass:
            return
        n = buffer.shape[0]
        bits = self._bits
        ds = self._downsample
        mix = self._mix

        # Bit crushing: quantise to fewer levels (vectorised)
        levels = float(2 ** bits - 1)
        if levels < 1.0:
            levels = 1.0

        if ds <= 1:
            # Pure bit crush -- fully vectorised
            crushed = np.round(buffer * levels) / levels
            if mix >= 1.0:
                buffer[:] = crushed
            else:
                buffer[:] = buffer * (1.0 - mix) + crushed * mix
        else:
            # Downsample + bit crush (need per-sample hold logic)
            hold = self._hold.copy()
            counter = self._hold_counter
            for i in range(n):
                if counter == 0:
                    hold[0] = math.floor(buffer[i, 0] * levels + 0.5) / levels
                    hold[1] = math.floor(buffer[i, 1] * levels + 0.5) / levels
                buffer[i, 0] = buffer[i, 0] * (1.0 - mix) + hold[0] * mix
                buffer[i, 1] = buffer[i, 1] * (1.0 - mix) + hold[1] * mix
                counter = (counter + 1) % ds
            self._hold[:] = hold
            self._hold_counter = counter

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "Bitcrusher",
            "bypass": self.bypass,
            "bits": self._bits,
            "downsample": self._downsample,
            "mix": self._mix,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            bits=d.get("bits", 16),
            downsample=d.get("downsample", 1),
            mix=d.get("mix", 1.0),
        )
        obj.bypass = d.get("bypass", False)
        return obj


# ---------------------------------------------------------------------------
# 5. Drive / Distortion
# ---------------------------------------------------------------------------

class Drive:
    """Soft-clipping distortion with tanh waveshaping and post-drive tone."""

    def __init__(self, drive=0.5, tone=0.5, mix=0.5):
        self.bypass = False
        self._drive = float(drive)
        self._tone = float(tone)
        self._mix = float(mix)
        # One-pole lowpass state for tone control (per channel)
        self._tone_state = np.zeros(2, dtype=np.float32)
        # Scratch buffer
        self._scratch = np.zeros((1024, 2), dtype=np.float32)

    # -- parameter setters --------------------------------------------------

    def set_drive(self, val):
        self._drive = float(np.clip(val, 0.0, 1.0))

    def set_tone(self, val):
        self._tone = float(np.clip(val, 0.0, 1.0))

    def set_mix(self, val):
        self._mix = float(np.clip(val, 0.0, 1.0))

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        if self.bypass:
            return
        n = buffer.shape[0]
        if self._scratch.shape[0] < n:
            self._scratch = np.zeros((n, 2), dtype=np.float32)

        # Drive gain: map 0-1 to 1x-50x
        gain = 1.0 + self._drive * 49.0
        driven = self._scratch[:n]
        np.multiply(buffer, gain, out=driven)
        np.tanh(driven, out=driven)

        # Tone: simple one-pole lowpass, cutoff mapped from tone param
        # tone 0 = dark (low cutoff), tone 1 = bright (nearly open)
        coeff = 0.01 + self._tone * 0.99  # filter coefficient (higher = brighter)
        state = self._tone_state.copy()
        for i in range(n):
            state = state + coeff * (driven[i] - state)
            driven[i] = state
        self._tone_state[:] = state

        # Mix
        mix = self._mix
        buffer[:n] = buffer[:n] * (1.0 - mix) + driven * mix

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "Drive",
            "bypass": self.bypass,
            "drive": self._drive,
            "tone": self._tone,
            "mix": self._mix,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            drive=d.get("drive", 0.5),
            tone=d.get("tone", 0.5),
            mix=d.get("mix", 0.5),
        )
        obj.bypass = d.get("bypass", False)
        return obj


# ---------------------------------------------------------------------------
# 6. Effects Chain
# ---------------------------------------------------------------------------

class EffectsChain:
    """Ordered chain of effects with per-slot bypass."""

    def __init__(self):
        self.bypass = False
        self._effects = []   # list of effect instances
        self._bypassed = []  # per-slot bypass flags

    # -- chain management ---------------------------------------------------

    def add(self, effect):
        self._effects.append(effect)
        self._bypassed.append(False)

    def remove(self, index):
        if 0 <= index < len(self._effects):
            self._effects.pop(index)
            self._bypassed.pop(index)

    def bypass_slot(self, index, state):
        """Enable / disable bypass on a single slot."""
        if 0 <= index < len(self._bypassed):
            self._bypassed[index] = bool(state)

    # keep the name from the spec as well
    bypass_effect = bypass_slot

    def __len__(self):
        return len(self._effects)

    def __getitem__(self, index):
        return self._effects[index]

    # -- process ------------------------------------------------------------

    def process(self, buffer):
        if self.bypass:
            return
        for fx, bp in zip(self._effects, self._bypassed):
            if not bp and not fx.bypass:
                fx.process(buffer)

    # -- serialisation ------------------------------------------------------

    def to_dict(self):
        return {
            "type": "EffectsChain",
            "bypass": self.bypass,
            "effects": [
                {"effect": fx.to_dict(), "bypassed": bp}
                for fx, bp in zip(self._effects, self._bypassed)
            ],
        }

    @classmethod
    def from_dict(cls, d):
        chain = cls()
        chain.bypass = d.get("bypass", False)
        for item in d.get("effects", []):
            fx = _deserialise_effect(item["effect"])
            if fx is not None:
                chain.add(fx)
                chain._bypassed[-1] = item.get("bypassed", False)
        return chain


# ---------------------------------------------------------------------------
# 7. Master Effects (Compressor + Limiter + 3-band EQ)
# ---------------------------------------------------------------------------

class Compressor:
    """Simple envelope-follower compressor."""

    def __init__(self, threshold=-12.0, ratio=4.0, attack_ms=5.0, release_ms=50.0):
        self.bypass = False
        self._threshold = float(threshold)     # dB
        self._ratio = float(ratio)
        self._attack_ms = float(attack_ms)
        self._release_ms = float(release_ms)
        self._envelope = 0.0  # linear envelope state
        self._update_coefficients()

    def _update_coefficients(self):
        self._attack_coeff = math.exp(-1.0 / (self._attack_ms * SAMPLE_RATE / 1000.0)) if self._attack_ms > 0 else 0.0
        self._release_coeff = math.exp(-1.0 / (self._release_ms * SAMPLE_RATE / 1000.0)) if self._release_ms > 0 else 0.0
        self._thresh_lin = 10.0 ** (self._threshold / 20.0)

    def set_threshold(self, db):
        self._threshold = float(db)
        self._update_coefficients()

    def set_ratio(self, r):
        self._ratio = max(1.0, float(r))

    def set_attack(self, ms):
        self._attack_ms = max(0.1, float(ms))
        self._update_coefficients()

    def set_release(self, ms):
        self._release_ms = max(0.1, float(ms))
        self._update_coefficients()

    def process(self, buffer):
        if self.bypass:
            return
        n = buffer.shape[0]
        att = self._attack_coeff
        rel = self._release_coeff
        thresh = self._thresh_lin
        ratio = self._ratio
        env = self._envelope

        for i in range(n):
            # Peak detection across both channels
            peak = max(abs(float(buffer[i, 0])), abs(float(buffer[i, 1])))
            # Envelope follower
            if peak > env:
                env = att * env + (1.0 - att) * peak
            else:
                env = rel * env + (1.0 - rel) * peak

            # Gain computation
            if env > thresh and env > 1e-10:
                db_over = 20.0 * math.log10(env / thresh)
                db_reduction = db_over * (1.0 - 1.0 / ratio)
                gain = 10.0 ** (-db_reduction / 20.0)
            else:
                gain = 1.0

            buffer[i] *= gain

        self._envelope = env

    def to_dict(self):
        return {
            "type": "Compressor",
            "bypass": self.bypass,
            "threshold": self._threshold,
            "ratio": self._ratio,
            "attack_ms": self._attack_ms,
            "release_ms": self._release_ms,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            threshold=d.get("threshold", -12.0),
            ratio=d.get("ratio", 4.0),
            attack_ms=d.get("attack_ms", 5.0),
            release_ms=d.get("release_ms", 50.0),
        )
        obj.bypass = d.get("bypass", False)
        return obj


class Limiter:
    """Brick-wall limiter at a configurable ceiling (default -0.5 dB)."""

    def __init__(self, ceiling_db=-0.5):
        self.bypass = False
        self._ceiling_db = float(ceiling_db)
        self._ceiling_lin = 10.0 ** (self._ceiling_db / 20.0)

    def set_ceiling(self, db):
        self._ceiling_db = float(db)
        self._ceiling_lin = 10.0 ** (self._ceiling_db / 20.0)

    def process(self, buffer):
        if self.bypass:
            return
        np.clip(buffer, -self._ceiling_lin, self._ceiling_lin, out=buffer)

    def to_dict(self):
        return {
            "type": "Limiter",
            "bypass": self.bypass,
            "ceiling_db": self._ceiling_db,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(ceiling_db=d.get("ceiling_db", -0.5))
        obj.bypass = d.get("bypass", False)
        return obj


class ThreeBandEQ:
    """Simple 3-band shelving EQ (low / mid / high).

    Uses first-order shelving filters for efficiency.
    Frequencies: low < 300 Hz, mid 300-3000 Hz, high > 3000 Hz.
    Gains in dB (default 0 = flat).
    """

    def __init__(self, low_db=0.0, mid_db=0.0, high_db=0.0):
        self.bypass = False
        self._low_db = float(low_db)
        self._mid_db = float(mid_db)
        self._high_db = float(high_db)

        # Filter states per channel
        self._lp_state = np.zeros(2, dtype=np.float32)
        self._hp_state = np.zeros(2, dtype=np.float32)

        # Crossover coefficients
        self._lp_coeff = 1.0 - math.exp(-2.0 * math.pi * 300.0 / SAMPLE_RATE)
        self._hp_coeff = 1.0 - math.exp(-2.0 * math.pi * 3000.0 / SAMPLE_RATE)

        self._update_gains()

    def _update_gains(self):
        self._low_gain = 10.0 ** (self._low_db / 20.0)
        self._mid_gain = 10.0 ** (self._mid_db / 20.0)
        self._high_gain = 10.0 ** (self._high_db / 20.0)

    def set_low(self, db):
        self._low_db = float(db)
        self._update_gains()

    def set_mid(self, db):
        self._mid_db = float(db)
        self._update_gains()

    def set_high(self, db):
        self._high_db = float(db)
        self._update_gains()

    def process(self, buffer):
        if self.bypass:
            return
        n = buffer.shape[0]
        lp_c = self._lp_coeff
        hp_c = self._hp_coeff
        lg = self._low_gain
        mg = self._mid_gain
        hg = self._high_gain

        lp_s = self._lp_state.copy()
        hp_s = self._hp_state.copy()

        for i in range(n):
            for ch in range(2):
                sample = float(buffer[i, ch])

                # Low band extraction
                lp_s[ch] += lp_c * (sample - lp_s[ch])
                low = lp_s[ch]

                # High band extraction
                hp_s[ch] += hp_c * (sample - hp_s[ch])
                high = sample - hp_s[ch]

                # Mid = remainder
                mid = sample - low - high

                buffer[i, ch] = low * lg + mid * mg + high * hg

        self._lp_state[:] = lp_s
        self._hp_state[:] = hp_s

    def to_dict(self):
        return {
            "type": "ThreeBandEQ",
            "bypass": self.bypass,
            "low_db": self._low_db,
            "mid_db": self._mid_db,
            "high_db": self._high_db,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            low_db=d.get("low_db", 0.0),
            mid_db=d.get("mid_db", 0.0),
            high_db=d.get("high_db", 0.0),
        )
        obj.bypass = d.get("bypass", False)
        return obj


class MasterEffects:
    """Master bus processing chain: EQ -> Compressor -> Limiter."""

    def __init__(self):
        self.bypass = False
        self.eq = ThreeBandEQ()
        self.compressor = Compressor()
        self.limiter = Limiter()

    def process(self, buffer):
        if self.bypass:
            return
        self.eq.process(buffer)
        self.compressor.process(buffer)
        self.limiter.process(buffer)

    def to_dict(self):
        return {
            "type": "MasterEffects",
            "bypass": self.bypass,
            "eq": self.eq.to_dict(),
            "compressor": self.compressor.to_dict(),
            "limiter": self.limiter.to_dict(),
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls()
        obj.bypass = d.get("bypass", False)
        obj.eq = ThreeBandEQ.from_dict(d.get("eq", {}))
        obj.compressor = Compressor.from_dict(d.get("compressor", {}))
        obj.limiter = Limiter.from_dict(d.get("limiter", {}))
        return obj


# ---------------------------------------------------------------------------
# Deserialisation registry
# ---------------------------------------------------------------------------

_EFFECT_CLASSES = {
    "StateVariableFilter": StateVariableFilter,
    "Reverb": Reverb,
    "Delay": Delay,
    "Bitcrusher": Bitcrusher,
    "Drive": Drive,
    "EffectsChain": EffectsChain,
    "Compressor": Compressor,
    "Limiter": Limiter,
    "ThreeBandEQ": ThreeBandEQ,
    "MasterEffects": MasterEffects,
}


def _deserialise_effect(d):
    """Reconstruct an effect instance from a dict."""
    cls = _EFFECT_CLASSES.get(d.get("type"))
    if cls is not None:
        return cls.from_dict(d)
    return None


def deserialise_effect(d):
    """Public helper -- reconstruct any effect from its to_dict() output."""
    return _deserialise_effect(d)
