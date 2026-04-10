"""Pattern chain / song mode engine.

Sequences pattern changes on bar boundaries using MIDI clock.
Counts ticks (24 per beat) → beats → bars → step changes.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class ChainStep:
    """One step in a pattern chain.

    fx_snapshot: optional dict of CC messages to send when this step starts.
    Format: {(channel, cc_number): value, ...}
    Example: {(0, 83): 18, (0, 19): 127}  → Bus1 FX=303VinylSim, FX ON
    """
    pattern: int = 0    # 0-63
    bars: int = 4       # 1-64
    fx_snapshot: dict = field(default_factory=dict)  # {(ch, cc): val}


@dataclass
class Chain:
    """A named sequence of pattern changes."""
    name: str = "Untitled"
    steps: list[ChainStep] = field(default_factory=list)
    time_sig_beats: int = 4  # beats per bar (4/4 default)
    loop: bool = True

    def total_bars(self) -> int:
        return sum(s.bars for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "time_sig_beats": self.time_sig_beats,
            "loop": self.loop,
            "steps": [
                {
                    "pattern": s.pattern,
                    "bars": s.bars,
                    "fx_snapshot": {f"{ch},{cc}": val
                                    for (ch, cc), val in s.fx_snapshot.items()}
                    if s.fx_snapshot else {},
                }
                for s in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Chain":
        chain = cls(
            name=data.get("name", "Untitled"),
            time_sig_beats=data.get("time_sig_beats", 4),
            loop=data.get("loop", True),
        )
        for s in data.get("steps", []):
            # Deserialize fx_snapshot: "ch,cc" string keys → (ch, cc) tuples
            raw_fx = s.get("fx_snapshot", {})
            fx = {}
            for key, val in raw_fx.items():
                parts = key.split(",")
                if len(parts) == 2:
                    fx[(int(parts[0]), int(parts[1]))] = val
            chain.steps.append(ChainStep(
                pattern=s.get("pattern", 0),
                bars=s.get("bars", 4),
                fx_snapshot=fx,
            ))
        return chain


class ChainPlayer:
    """Plays a pattern chain synchronized to MIDI clock.

    Wire on_tick() to the P6Midi.on_clock_tick callback.
    Wire on_pattern_change to p6.send_program_change().
    """

    def __init__(self):
        self.chain: Optional[Chain] = None
        self.playing = False
        self.sync_transport = True  # auto start/stop with P-6 transport

        # Position tracking
        self.step_index = 0
        self.bar_in_step = 0
        self.beat_in_bar = 0
        self.tick_count = 0

        # MIDI output (for FX snapshots)
        self._midi_out = None

        # Callbacks
        self.on_pattern_change: Optional[Callable[[int], None]] = None
        self.on_position_change: Optional[Callable[[], None]] = None

    @property
    def current_step(self) -> Optional[ChainStep]:
        if self.chain and 0 <= self.step_index < len(self.chain.steps):
            return self.chain.steps[self.step_index]
        return None

    @property
    def total_steps(self) -> int:
        return len(self.chain.steps) if self.chain else 0

    @property
    def position_text(self) -> str:
        """Human-readable position: 'Step 2/5 | Bar 3/8 | Beat 2'."""
        step = self.current_step
        if not step or not self.chain:
            return ""
        return (f"Step {self.step_index + 1}/{len(self.chain.steps)}"
                f" | Bar {self.bar_in_step + 1}/{step.bars}"
                f" | Beat {self.beat_in_bar + 1}")

    def load(self, chain: Chain) -> None:
        self.chain = chain
        self.stop()

    def start(self) -> None:
        """Start chain playback from the beginning."""
        if not self.chain or not self.chain.steps:
            return
        self.step_index = 0
        self.bar_in_step = 0
        self.beat_in_bar = 0
        self.tick_count = 0
        self.playing = True
        # Fire first pattern change immediately
        self._fire_pattern_change()
        log.info("Chain started: %s (%d steps)", self.chain.name, len(self.chain.steps))

    def stop(self) -> None:
        """Stop chain playback."""
        self.playing = False
        self.step_index = 0
        self.bar_in_step = 0
        self.beat_in_bar = 0
        self.tick_count = 0

    def on_tick(self) -> None:
        """Called on every MIDI clock tick (24 per beat).

        Counts ticks → beats → bars → step advances.
        """
        if not self.playing or not self.chain or not self.chain.steps:
            return

        self.tick_count += 1
        if self.tick_count < 24:
            return

        # Beat boundary
        self.tick_count = 0
        self.beat_in_bar += 1

        beats_per_bar = self.chain.time_sig_beats
        if self.beat_in_bar < beats_per_bar:
            return

        # Bar boundary
        self.beat_in_bar = 0
        self.bar_in_step += 1

        step = self.chain.steps[self.step_index]
        if self.bar_in_step < step.bars:
            return

        # Step boundary — advance to next step
        self.bar_in_step = 0
        self.step_index += 1

        if self.step_index >= len(self.chain.steps):
            if self.chain.loop:
                self.step_index = 0
            else:
                self.playing = False
                log.info("Chain finished")
                return

        self._fire_pattern_change()

    def _fire_pattern_change(self) -> None:
        step = self.chain.steps[self.step_index]
        if self.on_pattern_change:
            self.on_pattern_change(step.pattern)

        # Send FX snapshot CCs if present
        if step.fx_snapshot and self._midi_out:
            for (ch, cc), val in step.fx_snapshot.items():
                self._midi_out.send_cc(cc, val, channel=ch)
            log.info("Chain step %d: sent %d FX CCs",
                     self.step_index + 1, len(step.fx_snapshot))

        log.info("Chain step %d: pattern %d (%d bars)",
                 self.step_index + 1, step.pattern + 1, step.bars)


# ── File I/O ─────────────────────────────────────────────────────────

def save_chain(chain: Chain, directory: str) -> str:
    """Save chain to JSON. Returns filepath."""
    os.makedirs(directory, exist_ok=True)
    # Sanitize name for filename
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in chain.name)
    safe_name = safe_name.strip() or "chain"
    filepath = os.path.join(directory, f"{safe_name}.json")
    with open(filepath, "w") as f:
        json.dump(chain.to_dict(), f, indent=2)
    log.info("Chain saved: %s", filepath)
    return filepath


def load_chain(filepath: str) -> Chain:
    """Load chain from JSON."""
    with open(filepath) as f:
        return Chain.from_dict(json.load(f))


def list_chains(directory: str) -> list[str]:
    """List available chain files."""
    if not os.path.isdir(directory):
        return []
    return sorted([f for f in os.listdir(directory) if f.endswith(".json")])
