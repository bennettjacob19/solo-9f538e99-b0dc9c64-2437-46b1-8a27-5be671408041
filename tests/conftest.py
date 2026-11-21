"""Shared fixtures: a fresh in-memory app per test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_memory_app


@pytest.fixture
def client():
    with TestClient(create_memory_app()) as c:
        yield c


def links(*specs):
    return [
        {"link_id": link_id, "source": source, "target": target, "capacity": capacity}
        for link_id, source, target, capacity in specs
    ]


def tunnel(tunnel_id, bandwidth, current, target):
    return {
        "tunnel_id": tunnel_id,
        "bandwidth": bandwidth,
        "current_path": current,
        "target_path": target,
    }


@pytest.fixture
def swap_scenario():
    """Two tunnels that must swap paths: T1 frees L1/L2 only by moving to L3."""
    return {
        "links": links(
            ("L1", "A", "B", 10),
            ("L2", "B", "C", 10),
            ("L3", "A", "C", 5),
        ),
        "tunnels": [
            tunnel("T1", 4, ["L1", "L2"], ["L3"]),
            tunnel("T2", 3, ["L3"], ["L1", "L2"]),
        ],
    }
