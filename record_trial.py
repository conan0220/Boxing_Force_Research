"""Record one five-second multi-receiver IMU trial on Windows."""

from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from anrot_imu import AnrotGatewayParser, ImuFrame


COUNTDOWN_SECONDS = 3
RECORDING_SECONDS = 5.0
DEFAULT_BAUD_RATE = 921600
CSV_HEADER = (
    "sample_index",
    "elapsed_us",
    "device_time_ms",
    "acc_x_g",
    "acc_y_g",
    "acc_z_g",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
    "mag_x_ut",
    "mag_y_ut",
    "mag_z_ut",
    "quat_w",
    "quat_x",
    "quat_y",
    "quat_z",
)
MANIFEST_HEADER = (
    "trial_id",
    "subject_id",
    "subject_height_cm",
    "subject_weight_kg",
    "session_id",
    "recorded_at",
    "trial_path",
    "peak_force_kgf",
    "peak_force_n",
    "quality_status",
)
RECEIVER_SECTION = re.compile(r"^receiver\.(?P<port>[^\s]+)$", re.IGNORECASE)
NODE_KEY = re.compile(r"^node\.(?P<node_id>\d+)$", re.IGNORECASE)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NodeConfig:
    node_id: int
    wear_location: str


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    port: str
    group_id: int
    baud_rate: int
    nodes: tuple[NodeConfig, ...]


@dataclass(frozen=True, slots=True)
class TrialConfig:
    config_path: Path
    output_root: Path
    subject_id: str
    subject_height_cm: float
    subject_weight_kg: float
    session_id: str
    receivers: tuple[ReceiverConfig, ...]


def _parse_int(value: str, *, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as error:
        raise ConfigError(f"{name} 必須是整數") from error
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{name} 必須介於 {minimum} 與 {maximum} 之間")
    return parsed


def _parse_positive_number(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ConfigError(f"{name} 必須是數值") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ConfigError(f"{name} 必須是有限且大於 0 的數值")
    return parsed


def load_config(path: Path) -> TrialConfig:
    path = Path(path)
    if path.suffix.lower() != ".cfg":
        raise ConfigError("--config 必須指向 .cfg 檔案")
    if not path.is_file():
        raise ConfigError(f"找不到 config：{path}")

    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            parser.read_file(file)
    except (OSError, configparser.Error) as error:
        raise ConfigError(f"無法讀取 config：{error}") from error

    if not parser.has_section("recording"):
        raise ConfigError("config 缺少 [recording] section")
    recording = parser["recording"]
    for required_key in ("subject_height_cm", "subject_weight_kg"):
        if required_key not in recording:
            raise ConfigError(f"[recording] 缺少 {required_key}")
    output_root = Path(str(recording.get("output_root", "data"))).expanduser()
    subject_id = str(recording.get("subject_id", "")).strip()
    subject_height_cm = _parse_positive_number(
        recording["subject_height_cm"], name="subject_height_cm"
    )
    subject_weight_kg = _parse_positive_number(
        recording["subject_weight_kg"], name="subject_weight_kg"
    )
    session_id = str(recording.get("session_id", "")).strip()
    default_baud = _parse_int(
        str(recording.get("default_baud_rate", DEFAULT_BAUD_RATE)),
        name="default_baud_rate",
        minimum=1,
        maximum=10_000_000,
    )

    receivers: list[ReceiverConfig] = []
    seen_ports: set[str] = set()
    seen_groups: set[int] = set()
    for section_name in parser.sections():
        match = RECEIVER_SECTION.fullmatch(section_name)
        if not match:
            if section_name.lower() != "recording":
                raise ConfigError(f"不支援的 config section：[{section_name}]")
            continue

        port = match.group("port").upper()
        if port in seen_ports:
            raise ConfigError(f"Port 重複：{port}")
        seen_ports.add(port)
        section = parser[section_name]
        if "group_id" not in section:
            raise ConfigError(f"[{section_name}] 缺少 group_id")
        group_id = _parse_int(section["group_id"], name=f"{port}.group_id", minimum=0, maximum=255)
        if group_id in seen_groups:
            raise ConfigError(f"同一 Trial 不得有重複 Group ID：{group_id}")
        seen_groups.add(group_id)
        baud_rate = _parse_int(
            section.get("baud_rate", str(default_baud)),
            name=f"{port}.baud_rate",
            minimum=1,
            maximum=10_000_000,
        )

        nodes: list[NodeConfig] = []
        seen_nodes: set[int] = set()
        allowed_keys = {"group_id", "baud_rate"}
        for key, value in section.items():
            node_match = NODE_KEY.fullmatch(key)
            if not node_match:
                if key not in allowed_keys:
                    raise ConfigError(f"[{section_name}] 不支援的 key：{key}")
                continue
            node_id = _parse_int(
                node_match.group("node_id"), name=f"{port}.{key}", minimum=0, maximum=15
            )
            if node_id in seen_nodes:
                raise ConfigError(f"[{section_name}] Node ID 重複：{node_id}")
            wear_location = value.strip()
            if not wear_location:
                raise ConfigError(f"[{section_name}] {key} 的佩帶位置不得為空")
            seen_nodes.add(node_id)
            nodes.append(NodeConfig(node_id=node_id, wear_location=wear_location))
        if not nodes:
            raise ConfigError(f"[{section_name}] 至少需要一個 node.<NODE_ID>")
        receivers.append(
            ReceiverConfig(
                port=port,
                group_id=group_id,
                baud_rate=baud_rate,
                nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
            )
        )

    if not receivers:
        raise ConfigError("config 至少需要一個 [receiver.<PORT>] section")
    return TrialConfig(
        config_path=path.resolve(),
        output_root=output_root.resolve(),
        subject_id=subject_id,
        subject_height_cm=subject_height_cm,
        subject_weight_kg=subject_weight_kg,
        session_id=session_id,
        receivers=tuple(sorted(receivers, key=lambda item: item.port)),
    )


def _format_number(value: float) -> str:
    return format(value, ".9g")


class SensorCsvWriter:
    def __init__(self, path: Path) -> None:
        self.final_path = path
        self.part_path = path.with_suffix(path.suffix + ".part")
        self._file: TextIO | None = None
        self._writer: Any = None
        self.row_count = 0

    def open(self) -> None:
        self.part_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.part_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._file, lineterminator="\n")
        self._writer.writerow(CSV_HEADER)

    def append(self, frame: ImuFrame, *, elapsed_us: int) -> None:
        assert self._writer is not None
        self._writer.writerow(
            [
                self.row_count,
                elapsed_us,
                frame.device_time_ms,
                *(_format_number(value) for value in frame.acceleration_g),
                *(_format_number(value) for value in frame.gyroscope_dps),
                *(_format_number(value) for value in frame.magnetometer_ut),
                *(_format_number(value) for value in frame.quaternion_wxyz),
            ]
        )
        self.row_count += 1

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def finalize(self) -> None:
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._file = None
        os.replace(self.part_path, self.final_path)

    def abort(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def parse_peak_force(raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as error:
        raise ValueError("Peak force 必須是數值") from error
    if not value.is_finite() or value <= 0:
        raise ValueError("Peak force 必須是有限且大於 0 的 kgf 數值")
    return value


def prompt_peak_force() -> Decimal:
    while True:
        try:
            return parse_peak_force(input("Enter peak force (kgf): "))
        except ValueError as error:
            print(f"輸入無效：{error}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, path)


def _file_fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _append_manifest(output_root: Path, row: dict[str, Any]) -> None:
    path = output_root / "dataset_manifest.csv"
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=MANIFEST_HEADER, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _open_serial_ports(config: TrialConfig) -> dict[str, Any]:
    try:
        import serial
    except ImportError as error:
        raise RuntimeError("缺少 pyserial；請先執行：python -m pip install -r requirements.txt") from error

    opened: dict[str, Any] = {}
    try:
        for receiver in config.receivers:
            print(f"開啟 {receiver.port} @ {receiver.baud_rate} baud ...")
            connection = serial.Serial(receiver.port, receiver.baud_rate, timeout=0)
            connection.reset_input_buffer()
            opened[receiver.port] = connection
    except BaseException:
        for connection in opened.values():
            try:
                connection.close()
            except Exception:
                pass
        raise
    return opened


def _close_serial_ports(connections: dict[str, Any]) -> None:
    for connection in connections.values():
        try:
            connection.close()
        except Exception:
            pass


def record_trial(config: TrialConfig) -> int:
    # Port access is a preflight step. If it fails, no Trial directory is
    # created and the caller receives a non-zero exit status.
    connections = _open_serial_ports(config)
    trial_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trial_directory = config.output_root / "trials" / f"{timestamp}_{trial_id[:8]}"
    imu_directory = trial_directory / "imu"
    writers: dict[tuple[str, int, int], SensorCsvWriter] = {}
    source_configs: dict[tuple[str, int, int], tuple[ReceiverConfig, NodeConfig]] = {}
    try:
        trial_directory.mkdir(parents=True, exist_ok=False)
        imu_directory.mkdir()
        shutil.copyfile(config.config_path, trial_directory / "recording.cfg")
        for receiver in config.receivers:
            for node in receiver.nodes:
                identity = (receiver.port, receiver.group_id, node.node_id)
                filename = f"imu_{receiver.port}_G{receiver.group_id}_N{node.node_id}.csv"
                sink = SensorCsvWriter(imu_directory / filename)
                sink.open()
                writers[identity] = sink
                source_configs[identity] = (receiver, node)
    except BaseException:
        _close_serial_ports(connections)
        for sink in writers.values():
            sink.abort()
        raise

    parsers = {receiver.port: AnrotGatewayParser() for receiver in config.receivers}
    capture_error: str | None = None
    recording_started_at: datetime | None = None
    recording_ended_at: datetime | None = None
    actual_recording_seconds = 0.0

    try:
        print("\n請就定位，錄製即將開始：")
        for remaining in range(COUNTDOWN_SECONDS, 0, -1):
            print(f"{remaining} ...", flush=True)
            time.sleep(1.0)

        for connection in connections.values():
            connection.reset_input_buffer()
        for parser in parsers.values():
            parser.reset()

        started = time.perf_counter()
        deadline = started + RECORDING_SECONDS
        recording_started_at = datetime.now(timezone.utc)
        print("開始錄製 IMU（5 秒）...", flush=True)
        last_flush = started

        while True:
            now = time.perf_counter()
            if now >= deadline:
                break
            received = False
            for receiver in config.receivers:
                connection = connections[receiver.port]
                waiting = max(0, int(connection.in_waiting))
                if waiting == 0:
                    continue
                received = True
                data = connection.read(waiting)
                observed = time.perf_counter()
                elapsed_us = max(0, round((observed - started) * 1_000_000))
                for frame in parsers[receiver.port].feed(data):
                    identity = (receiver.port, frame.group_id, frame.node_id)
                    sink = writers.get(identity)
                    if sink is not None:
                        sink.append(frame, elapsed_us=elapsed_us)
            if now - last_flush >= 1.0:
                for sink in writers.values():
                    sink.flush()
                last_flush = now
            if not received:
                time.sleep(0.001)

        actual_recording_seconds = time.perf_counter() - started
        recording_ended_at = datetime.now(timezone.utc)
        print("錄製完成。")
    except KeyboardInterrupt:
        capture_error = "user_aborted"
        print("\n使用者中斷錄製。", file=sys.stderr)
    except BaseException as error:
        capture_error = f"capture_error:{type(error).__name__}:{error}"
        print(f"錄製錯誤：{error}", file=sys.stderr)
    finally:
        _close_serial_ports(connections)
        for sink in writers.values():
            try:
                sink.finalize()
            except Exception as error:
                capture_error = capture_error or f"file_finalize_error:{type(error).__name__}:{error}"
                sink.abort()

    if capture_error == "user_aborted":
        peak_force = None
    else:
        try:
            peak_force = prompt_peak_force()
        except (KeyboardInterrupt, EOFError):
            print("\nPeak force 輸入已取消。", file=sys.stderr)
            capture_error = capture_error or "ground_truth_input_aborted"
            peak_force = None
    quality_flags: list[str] = []
    if capture_error:
        quality_flags.append(capture_error)
    for identity, sink in writers.items():
        if sink.row_count == 0:
            port, group_id, node_id = identity
            quality_flags.append(f"no_frames:{port}:G{group_id}:N{node_id}")
    if actual_recording_seconds and actual_recording_seconds < RECORDING_SECONDS * 0.98:
        quality_flags.append("recording_duration_too_short")

    if capture_error in {"user_aborted", "ground_truth_input_aborted"}:
        quality_status = "aborted"
    elif quality_flags:
        quality_status = "invalid"
    else:
        quality_status = "complete"

    recorded_at = datetime.now(timezone.utc).isoformat()
    if peak_force is not None:
        force_n = peak_force * Decimal("9.80665")
        _write_json(
            trial_directory / "ground_truth.json",
            {
                "schema_version": 1,
                "trial_id": trial_id,
                "peak_force_kgf": float(peak_force),
                "peak_force_n": float(force_n),
                "measurement_method": "manual",
                "recorded_at": recorded_at,
            },
        )
    else:
        force_n = None

    sources: list[dict[str, Any]] = []
    for identity, sink in writers.items():
        receiver, node = source_configs[identity]
        if sink.final_path.exists():
            size_bytes, sha256 = _file_fingerprint(sink.final_path)
        else:
            size_bytes, sha256 = 0, ""
        sources.append(
            {
                "port": receiver.port,
                "group_id": receiver.group_id,
                "node_id": node.node_id,
                "wear_location": node.wear_location,
                "baud_rate": receiver.baud_rate,
                "csv_path": sink.final_path.relative_to(trial_directory).as_posix(),
                "row_count": sink.row_count,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )

    _write_json(
        trial_directory / "trial.json",
        {
            "schema_version": 1,
            "trial_id": trial_id,
            "subject_id": config.subject_id,
            "subject_height_cm": config.subject_height_cm,
            "subject_weight_kg": config.subject_weight_kg,
            "session_id": config.session_id,
            "quality_status": quality_status,
            "quality_flags": quality_flags,
            "countdown_seconds": COUNTDOWN_SECONDS,
            "requested_recording_seconds": RECORDING_SECONDS,
            "actual_recording_seconds": actual_recording_seconds,
            "recording_started_at": recording_started_at.isoformat() if recording_started_at else None,
            "recording_ended_at": recording_ended_at.isoformat() if recording_ended_at else None,
            "sources": sources,
        },
    )

    _append_manifest(
        config.output_root,
        {
            "trial_id": trial_id,
            "subject_id": config.subject_id,
            "subject_height_cm": _format_number(config.subject_height_cm),
            "subject_weight_kg": _format_number(config.subject_weight_kg),
            "session_id": config.session_id,
            "recorded_at": recorded_at,
            "trial_path": trial_directory.relative_to(config.output_root).as_posix(),
            "peak_force_kgf": "" if peak_force is None else str(peak_force),
            "peak_force_n": "" if force_n is None else str(force_n),
            "quality_status": quality_status,
        },
    )

    print(f"Trial 狀態：{quality_status}")
    print(f"輸出目錄：{trial_directory}")
    if quality_flags:
        print("品質標記：")
        for flag in quality_flags:
            print(f"  - {flag}")
    return 0 if quality_status == "complete" else 1


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="錄製一次 5 秒 HI221 IMU 拳擊力量 Trial。")
    parser.add_argument("--config", required=True, type=Path, help="Trial 設定檔（*.cfg）")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        config = load_config(arguments.config)
        return record_trial(config)
    except ConfigError as error:
        print(f"Config 錯誤：{error}", file=sys.stderr)
        return 2
    except BaseException as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
