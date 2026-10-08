"""Train an XGBoost baseline from right-wrist IMU trials."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import xgboost as xgb


INPUT_LOCATION = "right_wrist"
FEATURE_SET = "right-wrist-impact-stats-v1"
IMPACT_HALF_WINDOW_US = 250_000
RANDOM_SEED = 42
SIGNAL_COLUMNS = (
    "acc_x_g",
    "acc_y_g",
    "acc_z_g",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
)


@dataclass(frozen=True, slots=True)
class TrialExample:
    trial_id: str
    trial_path: str
    peak_force_kgf: float
    features: np.ndarray


def _read_imu_csv(path: Path) -> dict[str, np.ndarray]:
    required = ("elapsed_us", *SIGNAL_COLUMNS)
    values: dict[str, list[float]] = {column: [] for column in required}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError(f"IMU CSV 缺少 header: {path}")
        missing = [column for column in required if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"IMU CSV 缺少欄位 {missing}: {path}")
        for row_number, row in enumerate(reader, start=2):
            try:
                for column in required:
                    values[column].append(float(row[column]))
            except (TypeError, ValueError) as error:
                raise ValueError(f"IMU CSV 第 {row_number} 列不是有效數值: {path}") from error
    if not values["elapsed_us"]:
        raise ValueError(f"IMU CSV 沒有資料列: {path}")
    arrays = {column: np.asarray(column_values, dtype=np.float64) for column, column_values in values.items()}
    if not all(np.all(np.isfinite(array)) for array in arrays.values()):
        raise ValueError(f"IMU CSV 包含非有限數值: {path}")
    return arrays


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def _magnitude_stats(prefix: str, values: np.ndarray) -> tuple[list[str], list[float]]:
    names = [
        f"{prefix}_mean",
        f"{prefix}_std",
        f"{prefix}_rms",
        f"{prefix}_peak",
        f"{prefix}_p95",
        f"{prefix}_p99",
    ]
    features = [
        float(np.mean(values)),
        float(np.std(values)),
        _rms(values),
        float(np.max(values)),
        float(np.percentile(values, 95)),
        float(np.percentile(values, 99)),
    ]
    return names, features


def extract_features(path: Path) -> tuple[list[str], np.ndarray]:
    data = _read_imu_csv(path)
    acceleration = np.column_stack([data[column] for column in SIGNAL_COLUMNS[:3]])
    gyroscope = np.column_stack([data[column] for column in SIGNAL_COLUMNS[3:]])
    acceleration_magnitude = np.linalg.norm(acceleration, axis=1)
    gyroscope_magnitude = np.linalg.norm(gyroscope, axis=1)

    impact_index = int(np.argmax(acceleration_magnitude))
    impact_elapsed_us = data["elapsed_us"][impact_index]
    impact_mask = np.abs(data["elapsed_us"] - impact_elapsed_us) <= IMPACT_HALF_WINDOW_US
    if not np.any(impact_mask):
        raise ValueError(f"無法建立撞擊區間: {path}")

    feature_names: list[str] = []
    feature_values: list[float] = []
    for window_name, mask in (("full", np.ones(len(acceleration_magnitude), dtype=bool)), ("impact", impact_mask)):
        for signal_name, magnitude in (
            ("acc_magnitude_g", acceleration_magnitude),
            ("gyro_magnitude_dps", gyroscope_magnitude),
        ):
            names, values = _magnitude_stats(f"{window_name}_{signal_name}", magnitude[mask])
            feature_names.extend(names)
            feature_values.extend(values)
        for column in SIGNAL_COLUMNS:
            selected = data[column][mask]
            feature_names.extend((f"{window_name}_{column}_rms", f"{window_name}_{column}_abs_peak"))
            feature_values.extend((_rms(selected), float(np.max(np.abs(selected)))))

    feature_array = np.asarray(feature_values, dtype=np.float32)
    if not np.all(np.isfinite(feature_array)):
        raise ValueError(f"特徵包含非有限數值: {path}")
    return feature_names, feature_array


def stratified_split(
    examples: list[TrialExample], *, seed: int = RANDOM_SEED
) -> tuple[list[TrialExample], list[TrialExample], list[TrialExample]]:
    """Create an exact 80/10/10 split for each complete block of ten force-sorted trials."""
    ordered = sorted(examples, key=lambda item: (item.peak_force_kgf, item.trial_id))
    rng = random.Random(seed)
    training: list[TrialExample] = []
    validation: list[TrialExample] = []
    testing: list[TrialExample] = []
    for start in range(0, len(ordered), 10):
        block = ordered[start : start + 10]
        rng.shuffle(block)
        if len(block) == 10:
            training.extend(block[:8])
            validation.append(block[8])
            testing.append(block[9])
        else:
            training.extend(block)
    rng.shuffle(training)
    rng.shuffle(validation)
    rng.shuffle(testing)
    return training, validation, testing


def _repository_root() -> Path:
    return Path(__file__).resolve().parent


def _git_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_examples(data_root: Path) -> tuple[list[str], list[TrialExample]]:
    repository_root = _repository_root()
    manifest_path = data_root / "dataset_manifest.csv"
    examples: list[TrialExample] = []
    expected_feature_names: list[str] | None = None
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row["quality_status"] != "complete":
                continue
            trial_directory = data_root / row["trial_path"]
            trial_metadata = json.loads((trial_directory / "trial.json").read_text(encoding="utf-8"))
            ground_truth = json.loads((trial_directory / "ground_truth.json").read_text(encoding="utf-8"))
            peak_force_kgf = float(ground_truth["peak_force_kgf"])
            if not math.isclose(peak_force_kgf, float(row["peak_force_kgf"]), abs_tol=1e-9):
                raise ValueError(f"Ground Truth 與 Dataset Manifest 不一致: {trial_directory}")
            matching_sources = [
                source for source in trial_metadata["sources"] if source["wear_location"] == INPUT_LOCATION
            ]
            if len(matching_sources) != 1:
                raise ValueError(f"Trial 必須正好包含一個 {INPUT_LOCATION} 來源: {trial_directory}")
            imu_path = trial_directory / matching_sources[0]["csv_path"]
            feature_names, features = extract_features(imu_path)
            if expected_feature_names is None:
                expected_feature_names = feature_names
            elif feature_names != expected_feature_names:
                raise ValueError(f"Feature schema 不一致: {imu_path}")
            examples.append(
                TrialExample(
                    trial_id=row["trial_id"],
                    trial_path=trial_directory.relative_to(repository_root).as_posix(),
                    peak_force_kgf=peak_force_kgf,
                    features=features,
                )
            )
    if expected_feature_names is None or not examples:
        raise ValueError("Dataset 沒有可用的 complete Trial")
    return expected_feature_names, examples


def _matrix(examples: list[TrialExample]) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.stack([example.features for example in examples]),
        np.asarray([example.peak_force_kgf for example in examples], dtype=np.float32),
    )


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = actual - predicted
    denominator = float(np.sum(np.square(actual - np.mean(actual))))
    return {
        "mae_kgf": float(np.mean(np.abs(residual))),
        "rmse_kgf": float(np.sqrt(np.mean(np.square(residual)))),
        "r2": float(1.0 - np.sum(np.square(residual)) / denominator) if denominator else 0.0,
    }


def _split_metadata(examples: list[TrialExample]) -> dict[str, Any]:
    return {
        "count": len(examples),
        "trials": [
            {"trial_id": example.trial_id, "path": example.trial_path} for example in examples
        ],
    }


def train(data_root: Path, output_root: Path, description: str) -> Path:
    repository_root = _repository_root()
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    feature_names, examples = load_examples(data_root)
    training, validation, testing = stratified_split(examples)
    if not validation or not testing:
        raise ValueError("至少需要 10 個 complete Trials 才能建立 Validation 和 Testing Set")

    x_train, y_train = _matrix(training)
    x_validation, y_validation = _matrix(validation)
    x_test, y_test = _matrix(testing)
    dtrain = xgb.DMatrix(x_train, label=y_train, feature_names=feature_names)
    dvalidation = xgb.DMatrix(x_validation, label=y_validation, feature_names=feature_names)
    dtest = xgb.DMatrix(x_test, label=y_test, feature_names=feature_names)

    parameters: dict[str, Any] = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "eta": 0.03,
        "max_depth": 3,
        "min_child_weight": 3,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 1.0,
        "alpha": 0.0,
        "seed": RANDOM_SEED,
        "nthread": 1,
    }
    booster = xgb.train(
        parameters,
        dtrain,
        num_boost_round=1_000,
        evals=[(dtrain, "training"), (dvalidation, "validation")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )
    iteration_end = booster.best_iteration + 1
    selected_booster = booster[:iteration_end]
    validation_prediction = selected_booster.predict(dvalidation)
    test_prediction = selected_booster.predict(dtest)

    training_id = str(uuid4())
    created_at = datetime.now(timezone.utc)
    run_name = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_right-wrist-acc-gyro-baseline_{training_id[:8]}"
    run_directory = output_root / "xgboost" / run_name
    run_directory.mkdir(parents=True, exist_ok=False)
    model_path = run_directory / "model.ubj"
    selected_booster.save_model(model_path)

    loaded_model = xgb.Booster()
    loaded_model.load_model(model_path)
    loaded_prediction = loaded_model.predict(dtest)
    if not np.allclose(test_prediction, loaded_prediction, rtol=1e-7, atol=1e-7):
        raise RuntimeError("重新載入的模型產生不同預測")

    metadata = {
        "schema_version": 1,
        "training_id": training_id,
        "created_at": created_at.isoformat(),
        "description": description,
        "model_file": model_path.name,
        "git_commit": _git_commit(repository_root),
        "target": "peak_force_kgf",
        "training_config": {
            "input_locations": [INPUT_LOCATION],
            "input_signals": list(SIGNAL_COLUMNS),
            "feature_set": FEATURE_SET,
            "feature_count": len(feature_names),
            "impact_window_ms": IMPACT_HALF_WINDOW_US * 2 / 1_000,
            "hyperparameters": parameters,
            "maximum_boost_rounds": 1_000,
            "early_stopping_rounds": 50,
            "best_iteration": booster.best_iteration,
            "model_tree_count": selected_booster.num_boosted_rounds(),
            "random_seed": RANDOM_SEED,
        },
        "data_split": {
            "strategy": "force-sorted-block-80-10-10",
            "training": _split_metadata(training),
            "validation": _split_metadata(validation),
            "testing": _split_metadata(testing),
        },
        "metrics": {
            "validation": _metrics(y_validation, validation_prediction),
            "testing": _metrics(y_test, test_prediction),
        },
    }
    metadata_path = run_directory / "training.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_directory


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train an XGBoost right-wrist force baseline.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/shouyi_withoutGloves_fourIMU"),
        help="Dataset root containing dataset_manifest.csv and trials/.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("training_artifacts"),
        help="Training Artifact root.",
    )
    parser.add_argument(
        "--description",
        default=(
            "第一個 XGBoost Baseline。使用 right_wrist 的三軸加速度與三軸角速度，"
            "並採用 80% Training、10% Validation、10% Testing。"
        ),
    )
    return parser


def main() -> int:
    arguments = build_argument_parser().parse_args()
    output = train(arguments.data_root, arguments.output_root, arguments.description)
    print(f"Training Artifact: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
