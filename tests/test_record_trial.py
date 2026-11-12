import csv
import json
import struct
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import record_trial
from anrot_imu import crc16_ccitt
from record_trial import ConfigError, assess_source_quality, load_config, parse_peak_force


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


class SourceQualityTests(unittest.TestCase):
    def test_equal_rows_at_full_rate_have_no_flags(self) -> None:
        rows = {("COM5", 1, 0): 1999, ("COM5", 1, 1): 1999, ("COM8", 0, 0): 2000}
        self.assertEqual(assess_source_quality(rows, 5.0004), [])

    def test_flags_row_count_mismatch_within_group(self) -> None:
        rows = {("COM5", 1, 0): 1999, ("COM5", 1, 1): 1998}
        flags = assess_source_quality(rows, 5.0)
        self.assertEqual(flags, ["row_count_mismatch:COM5:G1:N0=1999,N1=1998"])

    def test_different_groups_may_have_different_row_counts(self) -> None:
        rows = {("COM5", 1, 0): 1999, ("COM8", 0, 0): 2001}
        self.assertEqual(assess_source_quality(rows, 5.0), [])

    def test_flags_sampling_rate_below_398_hz(self) -> None:
        rows = {("COM5", 1, 0): 1989}
        flags = assess_source_quality(rows, 5.0)
        self.assertEqual(flags, ["sampling_rate_too_low:COM5:G1:N0:397.8Hz"])
        self.assertEqual(assess_source_quality({("COM5", 1, 0): 1990}, 5.0), [])

    def test_flags_missing_frames(self) -> None:
        flags = assess_source_quality({("COM5", 1, 0): 0}, 5.0)
        self.assertEqual(flags, ["no_frames:COM5:G1:N0"])


def make_packet(group_id: int, node_ids: list[int], device_time_ms: int) -> bytes:
    blocks = bytearray()
    for node_id in node_ids:
        node = bytearray(34)
        node[0] = 0x93
        node[1] = node_id
        struct.pack_into("<3h", node, 2, 0, 0, 1000)
        struct.pack_into("<4h", node, 20, 32767, 0, 0, 0)
        blocks += node
    payload = bytes([0x63, group_id, len(node_ids), 0]) + struct.pack("<I", device_time_ms) + bytes(blocks)
    prefix = b"\x5A\xA5" + struct.pack("<H", len(payload))
    return prefix + struct.pack("<H", crc16_ccitt(prefix + payload)) + payload


class FakeConnection:
    """Serial stand-in that yields one gateway packet per read."""

    def __init__(self, packets) -> None:
        self._packets = packets
        self._pending = b""

    @property
    def in_waiting(self) -> int:
        if not self._pending:
            self._pending = next(self._packets, b"")
        return len(self._pending)

    def read(self, size: int) -> bytes:
        data, self._pending = self._pending[:size], self._pending[size:]
        return data

    def reset_input_buffer(self) -> None:
        self._pending = b""

    def close(self) -> None:
        pass


class RecordTrialRoutingTests(unittest.TestCase):
    CONFIG = """\
[recording]
output_root = {output_root}
subject_height_cm = 171
subject_weight_kg = 70
punch_hand = right

[receiver.COM5]
group_id = 1
node.0 = right_wrist
node.1 = right_shoulder
"""

    def _record(self, packets) -> tuple[int, Path]:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output_root = directory / "data"
        config_path = directory / "trial.cfg"
        config_path.write_text(self.CONFIG.format(output_root=output_root.as_posix()), encoding="utf-8")
        config = load_config(config_path)
        with (
            mock.patch.object(record_trial, "COUNTDOWN_SECONDS", 0),
            mock.patch.object(record_trial, "RECORDING_SECONDS", 0.2),
            mock.patch.object(
                record_trial, "_open_serial_ports", return_value={"COM5": FakeConnection(packets)}
            ),
            mock.patch.object(record_trial, "prompt_peak_force", return_value=Decimal("40")),
            mock.patch("builtins.print"),
        ):
            exit_code = record_trial.record_trial(config)
        return exit_code, config.output_root

    @staticmethod
    def _only_trial(output_root: Path) -> tuple[str, Path]:
        manifest = (output_root / "dataset_manifest.csv").read_text(encoding="utf-8")
        rows = list(csv.DictReader(manifest.splitlines()))
        assert len(rows) == 1
        return rows[0]["quality_status"], output_root / rows[0]["trial_path"]

    def test_complete_trial_is_stored_under_trials(self) -> None:
        packets = (make_packet(1, [0, 1], index * 10) for index in range(10**9))
        exit_code, output_root = self._record(packets)
        status, trial_path = self._only_trial(output_root)
        self.assertEqual((exit_code, status), (0, "complete"))
        self.assertEqual(trial_path.parent.name, "trials")
        self.assertEqual(list((output_root / "invalid_trial").iterdir()), [])
        metadata = json.loads((trial_path / "trial.json").read_text(encoding="utf-8"))
        self.assertTrue(all(source["sampling_rate_hz"] >= 398 for source in metadata["sources"]))
        self.assertTrue((trial_path / "ground_truth.json").is_file())
        self.assertTrue((trial_path / metadata["sources"][0]["csv_path"]).is_file())

    def test_row_count_mismatch_is_stored_under_invalid_trial(self) -> None:
        packets = (
            make_packet(1, [0, 1] if index % 2 else [0], index * 10) for index in range(10**9)
        )
        exit_code, output_root = self._record(packets)
        status, trial_path = self._only_trial(output_root)
        self.assertEqual((exit_code, status), (1, "invalid"))
        self.assertEqual(trial_path.parent.name, "invalid_trial")
        self.assertFalse((output_root / "trials").exists())
        metadata = json.loads((trial_path / "trial.json").read_text(encoding="utf-8"))
        self.assertTrue(any(flag.startswith("row_count_mismatch:COM5:G1:") for flag in metadata["quality_flags"]))

    def test_low_sampling_rate_is_stored_under_invalid_trial(self) -> None:
        packets = iter([make_packet(1, [0, 1], index * 10) for index in range(10)])
        exit_code, output_root = self._record(packets)
        status, trial_path = self._only_trial(output_root)
        self.assertEqual((exit_code, status), (1, "invalid"))
        self.assertEqual(trial_path.parent.name, "invalid_trial")
        metadata = json.loads((trial_path / "trial.json").read_text(encoding="utf-8"))
        low_rate = [flag for flag in metadata["quality_flags"] if flag.startswith("sampling_rate_too_low:")]
        self.assertEqual(len(low_rate), 2)


if __name__ == "__main__":
    unittest.main()
