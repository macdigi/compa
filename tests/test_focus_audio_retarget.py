import unittest

from ui.p6_app import P6App


class _Profile:
    short_name = "SP-404MKII"
    audio_hint = "SP-404"
    midi_channels = {}


class _DeviceManager:
    def __init__(self):
        self._profile = _Profile()
        self.focus_key = "SP-404MKII"
        self.active = self._profile
        self.set_focus_calls = []

    @property
    def connected(self):
        return {"SP-404MKII": self._profile}

    def set_focus(self, short_name):
        self.set_focus_calls.append(short_name)
        if short_name not in self.connected:
            return False
        if short_name == self.focus_key:
            return False
        self.focus_key = short_name
        self.active = self.connected[short_name]
        return True


class _Recorder:
    def __init__(self):
        self._monitoring = False
        self.switch_calls = []
        self.start_calls = 0
        self.clear_calls = 0

    def switch_device(self, hint, preferred_rate=0, user_initiated=False):
        self.switch_calls.append((hint, preferred_rate, user_initiated))
        return True

    def start_monitoring(self):
        self.start_calls += 1

    def clear_playback_cache(self):
        self.clear_calls += 1


class _Twister:
    connected = False


class FocusAudioRetargetTests(unittest.TestCase):
    def _app(self):
        app = P6App.__new__(P6App)
        app.device_manager = _DeviceManager()
        app.recorder = _Recorder()
        app._monitor_source = ""
        app._midi_connections = {}
        app.twister = _Twister()
        app.screens = {"session": object()}
        app.current_screen_name = "session"
        app.atom_sq = None
        app.router = None
        app.push2_control_layout = 3
        app.push2_pad_page = 2
        app._retarget_chromatic_keyboard = lambda: None
        return app

    def test_tapping_already_focused_card_resyncs_audio(self):
        app = self._app()

        self.assertTrue(app.switch_focus("SP-404MKII", user_initiated=True))

        self.assertEqual(app.device_manager.set_focus_calls, [])
        self.assertEqual(app.recorder.switch_calls, [("SP-404", 0, True)])
        self.assertEqual(app.recorder.start_calls, 1)
        self.assertEqual(app.recorder.clear_calls, 1)
        self.assertEqual(app.push2_control_layout, 3)
        self.assertEqual(app.push2_pad_page, 2)

    def test_monitor_route_still_protects_recorder_binding(self):
        app = self._app()
        app._monitor_source = "P-6"

        self.assertTrue(app.switch_focus("SP-404MKII", user_initiated=True))

        self.assertEqual(app.recorder.switch_calls, [])
        self.assertEqual(app.recorder.start_calls, 1)


if __name__ == "__main__":
    unittest.main()
