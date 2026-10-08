import tempfile
import unittest
from pathlib import Path

from record_trial import ConfigError, load_config, parse_peak_force


class RecorderConfigTests(unittest.TestCase):
    def test_loads_multiple_receivers_and_nodes(self) -> None:
        text = """\
[recording]
output_root = data
subject_height_cm = 175.5
subject_weight_kg = 70.25
punch_hand = right
default_baud_rate = 921600

[receiver.COM3]
group_id = 1
node.0 = left_wrist
node.1 = right_wrist

[receiver.COM4]
group_id = 2
baud_rate = 460800
node.3 = torso
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.cfg"
            path.write_text(text, encoding="utf-8")
            config = load_config(path)
        self.assertEqual(len(config.receivers), 2)
        self.assertEqual(config.receivers[0].port, "COM3")
        self.assertEqual(config.subject_height_cm, 175.5)
        self.assertEqual(config.subject_weight_kg, 70.25)
        self.assertEqual(config.punch_hand, "right")
        self.assertEqual(config.receivers[0].nodes[1].wear_location, "right_wrist")
        self.assertEqual(config.receivers[1].baud_rate, 460800)

    def test_rejects_duplicate_group_ids(self) -> None:
        text = """\
[recording]
subject_height_cm = 175
subject_weight_kg = 70
punch_hand = left

[receiver.COM3]
group_id = 1
node.0 = left_wrist
[receiver.COM4]
group_id = 1
node.0 = torso
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.cfg"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_requires_positive_subject_height_and_weight(self) -> None:
        text = """\
[recording]
subject_height_cm = 0
subject_weight_kg = 70
punch_hand = right

[receiver.COM3]
group_id = 1
node.0 = left_wrist
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.cfg"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_requires_left_or_right_punch_hand(self) -> None:
        text = """\
[recording]
subject_height_cm = 175
subject_weight_kg = 70
punch_hand = ambidextrous

[receiver.COM3]
group_id = 1
node.0 = left_wrist
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.cfg"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_peak_force_must_be_positive_and_finite(self) -> None:
        self.assertEqual(str(parse_peak_force("42.5")), "42.5")
        for invalid in ("0", "-1", "NaN", "Infinity", "abc"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_peak_force(invalid)


if __name__ == "__main__":
    unittest.main()
