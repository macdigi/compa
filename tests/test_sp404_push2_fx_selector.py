import unittest
from types import SimpleNamespace

from ui.p6_app import P6App


class _Midi:
    def __init__(self):
        self.calls = []
        self.note_calls = []

    def send_cc(self, cc, value, channel=0):
        self.calls.append((cc, value, channel))

    def send_note_on(self, note, velocity, channel=0):
        self.note_calls.append(("on", note, velocity, channel))

    def send_note_off(self, note, channel=0):
        self.note_calls.append(("off", note, channel))


class _Push2:
    def __init__(self):
        self.flashes = []
        self.restores = []

    def flash_pad(self, idx, color=0):
        self.flashes.append((idx, color))

    def restore_pad(self, idx):
        self.restores.append(idx)


class Push2SP404FxSelectorTests(unittest.TestCase):
    def _app(self, *, fx_on=0, fx_idx=7):
        app = P6App.__new__(P6App)
        midi = _Midi()
        app.current_screen_name = "device_workspace"
        app.push2_control = None
        app.push2_mode = "control"
        app.device_manager = SimpleNamespace(focus_key="SP-404MKII")
        app.twister = SimpleNamespace(active_bus=0)
        app.push2 = _Push2()
        app._midi_connections = {"SP-404MKII": midi}
        app.live_cc = {i: {} for i in range(16)}
        app.live_cc[0] = {
            app._SP404_FX_SELECT_CC: fx_idx,
            app._SP404_FX_ONOFF_CC: fx_on,
        }
        app._sp404_fx_select_accum = {}
        app._push2_shift_held = False
        app.push2_control_layout = 1
        app.push2_pad_page = 0
        app._push2_active_device_pads = set()
        app._push2_active_control_pads = set()
        app._push2_control_pad_started_at = {}
        app._push2_control_pad_release_notes = {}
        app._push2_current_control_pad = None
        app._push2_last_control_pad = None
        app._push2_sp_silence_started_at = 0.0
        app._sp404_active_fx_bus = 0
        app.config = {}
        app.recorder = SimpleNamespace(peak_levels=(1.0, 1.0))
        app._push2_sp_bank_count = lambda: 10
        app.record_performance_event = lambda *args, **kwargs: None
        app.record_performance_note = lambda *args, **kwargs: None
        app.push_hud = lambda *args, **kwargs: None
        return app, midi

    def test_sp404_defaults_to_quad_layout(self):
        app, _midi = self._app()

        self.assertEqual(app._push2_default_control_layout_for("SP-404MKII"), 1)
        self.assertEqual(app._push2_default_control_layout_for("P-6"), 0)

    def test_encoder_8_browses_fx_more_slowly(self):
        app, midi = self._app(fx_on=0, fx_idx=7)

        for _ in range(app._SP404_FX_SELECT_TICKS_PER_STEP - 1):
            app._on_push2_encoder(7, 1)

        self.assertEqual(midi.calls, [])
        self.assertEqual(app.live_cc[0][app._SP404_FX_SELECT_CC], 7)

        app._on_push2_encoder(7, 1)

        self.assertEqual(
            midi.calls,
            [
                (app._SP404_FX_SELECT_CC, 8, 0),
                (app._SP404_FX_ONOFF_CC, 0, 0),
            ],
        )
        self.assertEqual(app.live_cc[0][app._SP404_FX_SELECT_CC], 8)
        self.assertEqual(app.live_cc[0][app._SP404_FX_ONOFF_CC], 0)

    def test_encoder_8_does_not_force_fx_off_when_already_on(self):
        app, midi = self._app(fx_on=127, fx_idx=7)

        for _ in range(app._SP404_FX_SELECT_TICKS_PER_STEP):
            app._on_push2_encoder(7, 1)

        self.assertEqual(midi.calls, [(app._SP404_FX_SELECT_CC, 8, 0)])
        self.assertEqual(app.live_cc[0][app._SP404_FX_ONOFF_CC], 127)

    def test_top_select_recalls_configured_fx_favorite(self):
        app, midi = self._app(fx_on=0, fx_idx=7)
        app.config["SP404_FX_FAVORITES_BUS1_FX"] = "14,7"

        app._on_push2_button("top_select_1", 127)

        self.assertEqual(
            midi.calls,
            [
                (app._SP404_FX_SELECT_CC, 14, 0),
                (app._SP404_FX_ONOFF_CC, 0, 0),
            ],
        )
        self.assertEqual(app.live_cc[0][app._SP404_FX_SELECT_CC], 14)

    def test_sp404_push2_pad_stays_white_until_device_note_off(self):
        app, midi = self._app()
        pad_idx = 56  # quad-layout bank A, pad 1

        app._on_push2_pad(pad_idx, 100)
        app._on_push2_pad(pad_idx, 0)

        self.assertEqual(
            midi.note_calls,
            [("on", 48, 100, 0)],
        )
        self.assertEqual(app.push2.flashes, [(pad_idx, 122), (pad_idx, 122)])
        self.assertIn(("SP-404MKII", 0, 0), app._push2_active_control_pads)

        app._on_device_note("SP-404MKII", 0, 48, 0)

        self.assertEqual(app.push2.restores, [pad_idx])
        self.assertNotIn(("SP-404MKII", 0, 0), app._push2_active_control_pads)

    def test_sp404_push2_pad_gate_sends_note_off_on_release(self):
        app, midi = self._app()
        pad_idx = 56  # quad-layout bank A, pad 1
        pads = [None] * 160
        pads[0] = {"gate": True}
        app.sp404_lib = SimpleNamespace(
            cached_project_pad_settings=lambda: pads,
            read_project_pad_settings=lambda: pads,
        )

        app._on_push2_pad(pad_idx, 100)
        app._on_push2_pad(pad_idx, 0)

        self.assertEqual(
            midi.note_calls,
            [("on", 48, 100, 0), ("off", 48, 0)],
        )
        self.assertEqual(app.push2.restores, [pad_idx])
        self.assertNotIn(("SP-404MKII", 0, 0), app._push2_active_control_pads)

    def test_bottom_select_changes_bus_for_fx_controls(self):
        app, midi = self._app(fx_on=0, fx_idx=7)
        app.live_cc[1] = {app._SP404_FX_ONOFF_CC: 0, 16: 64}

        app._on_push2_button("bot_select_2", 127)
        app._on_push2_encoder(0, 1)
        app._on_push2_button("bot_select_8", 127)

        self.assertEqual(app._sp404_active_bus_index(), 1)
        self.assertEqual(
            midi.calls,
            [
                (16, 65, 1),
                (app._SP404_FX_ONOFF_CC, 127, 1),
            ],
        )

    def test_direct_fx_defaults_resolve_to_named_effect_params(self):
        app, _midi = self._app()

        display, param = app._sp404_effect_display_and_param_name(
            "bus1_fx", 3)

        self.assertEqual(display, "Delay")
        self.assertEqual(param, "TimeCtrlDly")

    def test_silence_fallback_clears_stuck_sp_pad_light(self):
        app, _midi = self._app()
        key = ("SP-404MKII", 0, 0)
        app._push2_active_control_pads.add(key)
        app._push2_current_control_pad = key
        app._push2_control_pad_started_at[key] = 0.0
        app.recorder.peak_levels = (0.0, 0.0)

        app._push2_expire_control_pad_lights()
        app._push2_sp_silence_started_at = 1.0
        app._push2_expire_control_pad_lights()

        self.assertNotIn(key, app._push2_active_control_pads)
        self.assertEqual(app.push2.restores, [56])

    def test_current_sp404_pad_status_text_uses_cached_padconf(self):
        app, _midi = self._app()
        pads = [None] * 160
        pads[0] = {
            "gate": True,
            "loop": False,
            "reverse": True,
            "bpm_sync": False,
            "bus": 2,
        }
        app.sp404_lib = SimpleNamespace(
            cached_project_pad_settings=lambda: pads,
            read_project_pad_settings=lambda: pads,
        )
        app._push2_current_control_pad = ("SP-404MKII", 0, 0)

        self.assertEqual(
            app._sp404_current_pad_status_text(),
            "G:ON L:OFF R:ON BPM:OFF BUS2",
        )


if __name__ == "__main__":
    unittest.main()
