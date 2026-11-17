"""Tests for IPC port allocation and path helpers."""

from pathlib import Path

import pytest

from harness.errors import PortAllocationError
from harness.paths import needs_ascii_link, project_hash
from harness.ports import BASE_PORT, PORT_SPAN, allocate_ipc_port, hash_start_port


def test_start_port_in_range_and_stable(tmp_path: Path) -> None:
    port = hash_start_port(tmp_path)
    assert BASE_PORT <= port < BASE_PORT + PORT_SPAN
    assert port == hash_start_port(tmp_path)


def test_allocate_skips_busy_ports(tmp_path: Path) -> None:
    start = hash_start_port(tmp_path)
    port = allocate_ipc_port(tmp_path, is_free=lambda p: p != start)
    assert port != start
    assert port - BASE_PORT == (start - BASE_PORT + 1) % PORT_SPAN


def test_allocate_wraps_around(tmp_path: Path) -> None:
    busy = set(range(hash_start_port(tmp_path), BASE_PORT + PORT_SPAN))
    port = allocate_ipc_port(tmp_path, is_free=lambda p: p not in busy)
    assert port not in busy


def test_allocate_reuses_free_recorded_port(tmp_path: Path) -> None:
    assert allocate_ipc_port(tmp_path, is_free=lambda _: True, reuse=40300) == 40300


def test_allocate_ignores_busy_or_out_of_range_reuse(tmp_path: Path) -> None:
    assert allocate_ipc_port(tmp_path, is_free=lambda _: True, reuse=50000) != 50000


def test_allocate_exhausted(tmp_path: Path) -> None:
    with pytest.raises(PortAllocationError):
        allocate_ipc_port(tmp_path, is_free=lambda _: False)


def test_needs_ascii_link() -> None:
    assert needs_ascii_link(Path("C:/Проекты/x"))
    assert needs_ascii_link(Path("C:/My Projects/x"))
    assert not needs_ascii_link(Path("C:/work/x"))


def test_project_hash_is_8_hex(tmp_path: Path) -> None:
    h = project_hash(tmp_path)
    assert len(h) == 8
    int(h, 16)
