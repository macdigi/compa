import unittest

from engine.sp404_protocol import parse_padconf_settings


def _padconf_payload(record: bytes) -> bytes:
    header = bytearray(160)
    header[0:4] = b"RFPD"
    header[4:8] = (160).to_bytes(4, "big")
    header[8:12] = (3).to_bytes(4, "little")
    header[12:16] = (196 * 160).to_bytes(4, "big")
    header[0x80:0x8a] = b"PROJECT_05"
    return bytes(header) + record


class SP404PadconfParserTests(unittest.TestCase):
    def test_decodes_confirmed_a01_fields(self):
        record = bytearray(196)
        record[0x10:0x14] = (1).to_bytes(4, "big")
        record[0x14:0x18] = (0x7fffffff).to_bytes(4, "big")
        record[0x23] = 0
        record[0x2c:0x30] = (0x000e28b0).to_bytes(4, "big")
        record[0x3c:0x40] = (1).to_bytes(4, "big")
        record[0x53] = 2

        settings = parse_padconf_settings(
            "PROJECT_05", _padconf_payload(bytes(record)))

        self.assertEqual(len(settings), 1)
        a01 = settings[0]
        self.assertEqual(a01.pad_id, "A01")
        self.assertTrue(a01.gate)
        self.assertTrue(a01.loop)
        self.assertTrue(a01.reverse)
        self.assertTrue(a01.bpm_sync)
        self.assertEqual(a01.bus, 2)
        self.assertEqual(a01.reverse_boundary, 0x000e28b0)

    def test_decodes_confirmed_off_values(self):
        record = bytearray(196)
        record[0x10:0x14] = (0).to_bytes(4, "big")
        record[0x14:0x18] = (0).to_bytes(4, "big")
        record[0x23] = 1
        record[0x2c:0x30] = (0x00000200).to_bytes(4, "big")
        record[0x3c:0x40] = (0).to_bytes(4, "big")
        record[0x53] = 1

        settings = parse_padconf_settings(
            "PROJECT_05", _padconf_payload(bytes(record)))

        a01 = settings[0]
        self.assertFalse(a01.gate)
        self.assertFalse(a01.loop)
        self.assertFalse(a01.reverse)
        self.assertFalse(a01.bpm_sync)
        self.assertEqual(a01.bus, 1)


if __name__ == "__main__":
    unittest.main()
