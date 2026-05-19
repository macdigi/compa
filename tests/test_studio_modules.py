import unittest

from engine.studio_modules import (
    is_module_available,
    known_modules,
    module_availability_label,
    module_for_key,
    module_for_tab,
)


class StudioModuleTests(unittest.TestCase):
    def test_core_modules_are_ordered_for_studio_navigation(self):
        modules = known_modules()
        self.assertEqual([module.key for module in modules], [
            "performer",
            "clips",
            "sampler",
            "drum_synth",
            "synth",
            "mixer",
            "recorder",
        ])
        self.assertEqual(module_for_key("sampler").tab, "sampler")
        self.assertEqual(module_for_tab("drum_synth").label, "Drum Synth")

    def test_external_performer_is_available_on_pi3_without_internal_audio(self):
        module = module_for_key("performer")
        self.assertTrue(is_module_available(
            module, pi_generation=3, studio_audio_enabled=False))
        self.assertEqual(
            module_availability_label(
                module, pi_generation=3, studio_audio_enabled=False),
            "ready",
        )

    def test_internal_modules_are_gated_for_pi3_and_audio_gate(self):
        module = module_for_key("sampler")
        self.assertFalse(is_module_available(
            module, pi_generation=3, studio_audio_enabled=True))
        self.assertEqual(
            module_availability_label(
                module, pi_generation=3, studio_audio_enabled=True),
            "Pi 4+",
        )
        self.assertFalse(is_module_available(
            module, pi_generation=4, studio_audio_enabled=False))
        self.assertEqual(
            module_availability_label(
                module, pi_generation=4, studio_audio_enabled=False),
            "audio gated",
        )
        self.assertTrue(is_module_available(
            module, pi_generation=4, studio_audio_enabled=True))


if __name__ == "__main__":
    unittest.main()
