"""Minimal incremental parser for ANROT HI221 0x63 gateway frames."""

from __future__ import annotations

import struct
from dataclasses import dataclass


SYNC = b"\x5A\xA5"
HEADER_SIZE = 6
MAX_PAYLOAD_SIZE = 4096
MAX_NODES = 16


@dataclass(frozen=True, slots=True)
class ImuFrame:
    group_id: int
    node_id: int
    device_time_ms: int
    acceleration_g: tuple[float, float, float]
    gyroscope_dps: tuple[float, float, float]
    magnetometer_ut: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]


def crc16_ccitt(data: bytes | bytearray, initial: int = 0) -> int:
    """Return the CRC-16/CCITT value used by the ANROT serial protocol."""
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            shifted = crc << 1
            if crc & 0x8000:
                shifted ^= 0x1021
            crc = shifted & 0xFFFF
    return crc


class AnrotGatewayParser:
    """Incrementally decode CRC-valid ANROT frames containing 0x63 payloads."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.invalid_crc_count = 0
        self.invalid_payload_count = 0

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[ImuFrame]:
        self._buffer.extend(data)
        decoded: list[ImuFrame] = []

        while len(self._buffer) >= HEADER_SIZE:
            sync_index = self._buffer.find(SYNC)
            if sync_index < 0:
                trailing = self._buffer[-1:] if self._buffer[-1] == SYNC[0] else b""
                self._buffer.clear()
                self._buffer.extend(trailing)
                break
            if sync_index:
                del self._buffer[:sync_index]
            if len(self._buffer) < HEADER_SIZE:
                break

            payload_length = struct.unpack_from("<H", self._buffer, 2)[0]
            if payload_length <= 0 or payload_length > MAX_PAYLOAD_SIZE:
                del self._buffer[0]
                self.invalid_payload_count += 1
                continue

            total_length = HEADER_SIZE + payload_length
            if len(self._buffer) < total_length:
                break

            packet = bytes(self._buffer[:total_length])
            del self._buffer[:total_length]
            received_crc = struct.unpack_from("<H", packet, 4)[0]
            calculated_crc = crc16_ccitt(packet[:4] + packet[6:])
            if calculated_crc != received_crc:
                self.invalid_crc_count += 1
                continue

            payload = packet[HEADER_SIZE:]
            frames = self._decode_0x63(payload)
            if frames is None:
                self.invalid_payload_count += 1
                continue
            decoded.extend(frames)

        return decoded

    @staticmethod
    def _decode_0x63(payload: bytes) -> list[ImuFrame] | None:
        if len(payload) < 8 or payload[0] != 0x63:
            return []

        group_id = payload[1]
        node_count = payload[2]
        if node_count > MAX_NODES:
            return None
        required = 8 + node_count * 34
        if len(payload) < required:
            return None

        device_time_ms = struct.unpack_from("<I", payload, 4)[0]
        frames: list[ImuFrame] = []
        offset = 8
        for _ in range(node_count):
            block = payload[offset : offset + 34]
            if block[0] != 0x93:
                return None
            node_id = block[1]
            acceleration = tuple(value / 1000.0 for value in struct.unpack_from("<3h", block, 2))
            magnetometer = tuple(value / 10.0 for value in struct.unpack_from("<3h", block, 8))
            gyroscope = tuple(value / 10.0 for value in struct.unpack_from("<3h", block, 14))
            quaternion = tuple(value / 32767.0 for value in struct.unpack_from("<4h", block, 20))
            frames.append(
                ImuFrame(
                    group_id=group_id,
                    node_id=node_id,
                    device_time_ms=device_time_ms,
                    acceleration_g=acceleration,  # type: ignore[arg-type]
                    gyroscope_dps=gyroscope,  # type: ignore[arg-type]
                    magnetometer_ut=magnetometer,  # type: ignore[arg-type]
                    quaternion_wxyz=quaternion,  # type: ignore[arg-type]
                )
            )
            offset += 34
        return frames
