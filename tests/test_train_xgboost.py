import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from train_xgboost import TrialExample, extract_features, stratified_split


class XGBoostTrainingTests(unittest.TestCase):
    def test_extract_features_is_finite_and_stable(self) -> None:
        columns = [
            "sample_index",
            "elapsed_us",
            "device_time_ms",
            "acc_x_g",
            "acc_y_g",
            "acc_z_g",
            "gyro_x_dps",
            "gyro_y_dps",
            "gyro_z_dps",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "imu.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader()
                for index in range(100):
                    writer.writerow(
                        {
                            "sample_index": index,
                            "elapsed_us": index * 2_500,
                            "device_time_ms": index,
                            "acc_x_g": 10.0 if index == 50 else 1.0,
                            "acc_y_g": 0.1,
                            "acc_z_g": 0.2,
                            "gyro_x_dps": index * 0.1,
                            "gyro_y_dps": index * 0.2,
                            "gyro_z_dps": index * 0.3,
                        }
                    )
            names, features = extract_features(path)
        self.assertEqual(len(names), 48)
        self.assertEqual(features.shape, (48,))
        self.assertTrue(np.all(np.isfinite(features)))
        self.assertEqual(len(names), len(set(names)))

    def test_stratified_split_is_exact_and_disjoint(self) -> None:
        examples = [
            TrialExample(
                trial_id=f"trial-{index:03d}",
                trial_path=f"data/trials/trial-{index:03d}",
                peak_force_kgf=float(index),
                features=np.asarray([index], dtype=np.float32),
            )
            for index in range(102)
        ]
        training, validation, testing = stratified_split(examples, seed=42)
        self.assertEqual((len(training), len(validation), len(testing)), (82, 10, 10))
        all_ids = [item.trial_id for item in (*training, *validation, *testing)]
        self.assertEqual(len(all_ids), len(set(all_ids)))


if __name__ == "__main__":
    unittest.main()
