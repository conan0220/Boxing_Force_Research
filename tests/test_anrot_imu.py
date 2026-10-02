import struct
import unittest

from anrot_imu import AnrotGatewayParser, crc16_ccitt


def make_packet() -> bytes:
    node = bytearray(34)
    node[0] = 0x93
    node[1] = 7
    struct.pack_into("<3h", node, 2, 1000, -500, 250)
    struct.pack_into("<3h", node, 8, 100, -200, 300)
    struct.pack_into("<3h", node, 14, 10, -20, 30)
    struct.pack_into("<4h", node, 20, 32767, 0, 0, 0)
    struct.pack_into("<3h", node, 28, 0, 0, 0)
    payload = bytes([0x63, 4, 1, 0]) + struct.pack("<I", 12345) + bytes(node)
    prefix = b"\x5A\xA5" + struct.pack("<H", len(payload))
    crc = crc16_ccitt(prefix + payload)
    return prefix + struct.pack("<H", crc) + payload


class AnrotGatewayParserTests(unittest.TestCase):
    def test_parses_split_valid_packet(self) -> None:
        packet = make_packet()
        parser = AnrotGatewayParser()
        self.assertEqual(parser.feed(packet[:9]), [])
        frames = parser.feed(packet[9:])
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual((frame.group_id, frame.node_id, frame.device_time_ms), (4, 7, 12345))
        self.assertEqual(frame.acceleration_g, (1.0, -0.5, 0.25))
        self.assertEqual(frame.gyroscope_dps, (1.0, -2.0, 3.0))
        self.assertEqual(frame.magnetometer_ut, (10.0, -20.0, 30.0))
        self.assertAlmostEqual(frame.quaternion_wxyz[0], 1.0)

    def test_rejects_invalid_crc(self) -> None:
        packet = bytearray(make_packet())
        packet[-1] ^= 0xFF
        parser = AnrotGatewayParser()
        self.assertEqual(parser.feed(bytes(packet)), [])
        self.assertEqual(parser.invalid_crc_count, 1)


if __name__ == "__main__":
    unittest.main()
