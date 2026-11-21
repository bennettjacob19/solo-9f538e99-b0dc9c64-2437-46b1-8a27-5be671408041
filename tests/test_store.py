"""Store-level and concurrency properties."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.engine import Engine
from app.main import create_app
from app.store import MemoryStore, SqliteStore, StoreConflict


# ------------------------------------------------------------------- stores
def test_sqlite_store_roundtrip(tmp_path):
    store = SqliteStore(str(tmp_path / "nested" / "dir" / "db.sqlite"))
    store.initialize()
    assert store.get("missing") is None

    store.create({"rehearsal_id": "r1", "value": 1})
    document, version = store.get("r1")
    assert document == {"rehearsal_id": "r1", "value": 1}
    assert version == 1

    assert store.replace("r1", version, {"rehearsal_id": "r1", "value": 2}) == 2
    assert store.get("r1")[0]["value"] == 2

    with pytest.raises(StoreConflict):
        store.replace("r1", 1, {"rehearsal_id": "r1", "value": 3})
    with pytest.raises(StoreConflict):
        store.create({"rehearsal_id": "r1"})
    store.close()


def test_state_survives_restart(tmp_path, swap_scenario):
    path = str(tmp_path / "state.db")

    with TestClient(create_app(SqliteStore(path))) as client:
        created = client.post("/rehearsals", json=swap_scenario).json()
        rid = created["rehearsal_id"]
        reservation = client.post(f"/rehearsals/{rid}/reservations").json()

    with TestClient(create_app(SqliteStore(path))) as client:
        view = client.get(f"/rehearsals/{rid}").json()
        assert view["pending"]["reservation_id"] == reservation["reservation_id"]
        confirmed = client.post(
            f"/rehearsals/{rid}/reservations/{reservation['reservation_id']}/confirm"
        )
        assert confirmed.status_code == 200


# -------------------------------------------------------------- concurrency
def _engine():
    store = MemoryStore()
    engine = Engine(store)
    engine.initialize()
    return engine


def test_only_one_of_many_parallel_reservations_wins(swap_scenario):
    engine = _engine()
    record = engine.create_rehearsal(
        [type("L", (), s) for s in swap_scenario["links"]],
        [type("T", (), s) for s in swap_scenario["tunnels"]],
    )
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            engine.reserve(record.rehearsal_id)
            results.append("ok")
        except Exception as exc:  # noqa: BLE001 - we inspect the type name
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("ok") == 1
    assert results.count("StepConflict") == 7
    view = engine.view(record.rehearsal_id)[0]
    assert view.pending is not None


def test_parallel_confirm_and_cancel_resolve_once(swap_scenario):
    engine = _engine()
    record = engine.create_rehearsal(
        [type("L", (), s) for s in swap_scenario["links"]],
        [type("T", (), s) for s in swap_scenario["tunnels"]],
    )
    reservation = engine.reserve(record.rehearsal_id)

    outcomes: list[str] = []
    barrier = threading.Barrier(6)

    def confirm_worker():
        barrier.wait()
        try:
            engine.confirm(record.rehearsal_id, reservation.reservation_id)
            outcomes.append("confirmed")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    def cancel_worker():
        barrier.wait()
        try:
            engine.cancel(record.rehearsal_id, reservation.reservation_id)
            outcomes.append("cancelled")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=confirm_worker) for _ in range(3)]
    threads += [threading.Thread(target=cancel_worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert (outcomes.count("confirmed") + outcomes.count("cancelled")) == 1
    assert outcomes.count("ReservationInvalid") == 5
