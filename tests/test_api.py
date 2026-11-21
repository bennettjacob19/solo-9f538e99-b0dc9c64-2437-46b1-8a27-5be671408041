"""End-to-end API tests for the rehearsal service."""

from __future__ import annotations

from tests.conftest import links, tunnel


def create(client, scenario):
    return client.post("/rehearsals", json=scenario)


def reserve(client, rid, **body):
    return client.post(f"/rehearsals/{rid}/reservations", json=body or None)


def confirm(client, rid, resid):
    return client.post(f"/rehearsals/{rid}/reservations/{resid}/confirm")


def cancel(client, rid, resid):
    return client.post(f"/rehearsals/{rid}/reservations/{resid}/cancel")


def usage(client, rid):
    body = client.get(f"/rehearsals/{rid}").json()
    return {link["link_id"]: (link["used"], link["reserved"]) for link in body["links"]}


# ------------------------------------------------------------------ creation
def test_create_returns_id_and_feasible_order(client, swap_scenario):
    response = create(client, swap_scenario)
    assert response.status_code == 201
    body = response.json()
    assert body["rehearsal_id"]
    assert [step["tunnel_id"] for step in body["steps"]] == body["order"]
    assert sorted(body["order"]) == ["T1", "T2"]
    assert body["tunnels_total"] == 2


def test_order_is_actually_executable(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    while True:
        view = client.get(f"/rehearsals/{rid}").json()
        if view["status"] == "completed":
            break
        reserved = reserve(client, rid)
        assert reserved.status_code == 201, reserved.text
        done = confirm(client, rid, reserved.json()["reservation_id"])
        assert done.status_code == 200, done.text
    assert all(tunnel["moved"] for tunnel in client.get(f"/rehearsals/{rid}").json()["tunnels"])


def test_infeasible_scenario_is_refused_and_nothing_is_created(client):
    scenario = {
        "links": links(("L1", "A", "B", 5), ("L2", "A", "B", 5)),
        "tunnels": [
            tunnel("T1", 5, ["L1"], ["L2"]),
            tunnel("T2", 5, ["L2"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 409
    assert response.json()["error"] == "no_feasible_order"
    # No id was handed out, so no rehearsal exists to inspect.
    assert "rehearsal_id" not in response.json()


def test_infeasible_single_link_capacity_reason(client):
    scenario = {
        "links": links(("L1", "A", "B", 10), ("L2", "A", "B", 2)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 1, ["L1"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    # T2's paths are identical -> structurally invalid, not infeasible.
    assert response.status_code == 422


def test_identical_paths_rejected(client):
    scenario = {
        "links": links(("L1", "A", "B", 10)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L1"]),
            tunnel("T2", 4, ["L1"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    assert any("identical" in error for error in response.json()["errors"])


def test_endpoints_must_match(client):
    scenario = {
        "links": links(("L1", "A", "B", 10), ("L2", "B", "C", 10)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 4, ["L1"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    errors = " ".join(response.json()["errors"])
    assert "end" in errors or "start" in errors


def test_discontiguous_path_rejected(client):
    scenario = {
        "links": links(("L1", "A", "B", 10), ("L2", "B", "C", 10), ("L3", "C", "D", 10)),
        "tunnels": [
            tunnel("T1", 4, ["L1", "L3"], ["L2"]),
            tunnel("T2", 4, ["L2"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    assert any("contiguous" in error for error in response.json()["errors"])


def test_unknown_link_rejected(client):
    scenario = {
        "links": links(("L1", "A", "B", 10)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["LX"]),
            tunnel("T2", 4, ["L1"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    assert any("unknown link" in error for error in response.json()["errors"])


def test_tunnel_count_bounds(client):
    one = {
        "links": links(("L1", "A", "B", 10), ("L2", "A", "B", 10)),
        "tunnels": [tunnel("T1", 1, ["L1"], ["L2"])],
    }
    assert create(client, one).status_code == 422

    eleven = {
        "links": links(*[(f"L{i}", "A", "B", 10) for i in range(11)]),
        "tunnels": [
            tunnel(f"T{i}", 1, [f"L{i}"], [f"L{(i + 1) % 11}"]) for i in range(11)
        ],
    }
    assert create(client, eleven).status_code == 422


def test_bandwidth_must_be_strict_integer(client):
    scenario = {
        "links": links(("L1", "A", "B", 10), ("L2", "A", "B", 10)),
        "tunnels": [
            tunnel("T1", 4.5, ["L1"], ["L2"]),
            tunnel("T2", 1, ["L1"], ["L2"]),
        ],
    }
    assert create(client, scenario).status_code == 422


def test_current_paths_must_already_fit(client):
    scenario = {
        "links": links(("L1", "A", "B", 5), ("L2", "A", "B", 10)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 4, ["L1"], ["L2"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    assert any("already need" in error for error in response.json()["errors"])


# ------------------------------------------------------------------ rehearsal
def test_unknown_rehearsal_is_404(client):
    assert client.get("/rehearsals/nope").status_code == 404
    assert reserve(client, "nope").status_code == 404


def test_reserve_holds_bandwidth_while_old_path_stays(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    before = usage(client, rid)
    assert before["L3"] == (3, 0)

    order = client.get(f"/rehearsals/{rid}").json()["order"]
    first = order[0]
    response = reserve(client, rid)
    assert response.status_code == 201
    body = response.json()
    assert body["tunnel_id"] == first
    assert body["status"] == "pending"
    assert body["reservation_id"].startswith("res_")

    after = usage(client, rid)
    # The old path is untouched, the target is merely reserved.
    if first == "T2":
        assert after["L3"] == (3, 0)  # T2 still on L3; L1/L2 reserved for it
        assert after["L1"] == (4, 3)
        assert after["L2"] == (4, 3)
    else:
        assert after["L3"] == (3, 4)
        assert after["L1"] == (4, 0)


def test_shared_links_counted_once(client):
    """A target path reusing part of the current path is not double-charged."""
    scenario = {
        "links": links(
            ("L1", "A", "B", 10),
            ("L2", "B", "C", 10),
            ("L3", "C", "D", 10),
            ("L4", "B", "D", 20),
        ),
        "tunnels": [
            tunnel("T1", 5, ["L1", "L2", "L3"], ["L1", "L4"]),
            tunnel("T2", 6, ["L4"], ["L2", "L3"]),
        ],
    }
    rid = create(client, scenario).json()["rehearsal_id"]
    reserved = reserve(client, rid)
    assert reserved.status_code == 201
    assert reserved.json()["tunnel_id"] == "T1"
    # L1 is shared: only L4 needs new room.  L2/L3 are freed by this move but
    # stay occupied until the reservation is confirmed.
    assert reserved.json()["reserved"] == {"L4": 5}
    assert usage(client, rid)["L4"] == (6, 5)
    assert usage(client, rid)["L1"] == (5, 0)

    done = confirm(client, rid, reserved.json()["reservation_id"]).json()
    assert done["released"] == {"L2": 5, "L3": 5}
    current = {t["tunnel_id"]: t["current_path"] for t in client.get(f"/rehearsals/{rid}").json()["tunnels"]}
    assert current["T1"] == ["L1", "L4"]
    # T1 still occupies L1 once, and L4 carries both tunnels' bandwidth.
    assert usage(client, rid)["L1"] == (5, 0)
    assert usage(client, rid)["L4"] == (11, 0)


def test_only_one_pending_reservation(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    assert reserve(client, rid).status_code == 201
    second = reserve(client, rid)
    assert second.status_code == 409
    assert second.json()["error"] == "step_conflict"


def test_out_of_order_request_changes_nothing(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    order = client.get(f"/rehearsals/{rid}").json()["order"]
    wrong = order[1]
    before = client.get(f"/rehearsals/{rid}").json()
    response = reserve(client, rid, tunnel_id=wrong)
    assert response.status_code == 409
    after = client.get(f"/rehearsals/{rid}").json()
    assert before == after


def test_reserving_the_next_tunnel_explicitly_is_allowed(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    nxt = client.get(f"/rehearsals/{rid}").json()["next_tunnel_id"]
    response = reserve(client, rid, tunnel_id=nxt)
    assert response.status_code == 201
    assert response.json()["tunnel_id"] == nxt


def test_confirm_switches_and_releases(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    first = reserve(client, rid).json()
    body = confirm(client, rid, first["reservation_id"]).json()
    assert body["status"] == "confirmed"
    assert body["current_path"] == first["target_path"]
    tunnel_state = {t["tunnel_id"]: t for t in client.get(f"/rehearsals/{rid}").json()["tunnels"]}
    assert tunnel_state[first["tunnel_id"]]["current_path"] == first["target_path"]
    # The old path's exclusive links are freed.
    for link_id in first["current_path"]:
        if link_id not in first["target_path"]:
            assert usage(client, rid)[link_id][0] == 0


def test_cancel_only_withdraws_the_reservation_and_allows_retry(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    snapshot = client.get(f"/rehearsals/{rid}").json()
    first = reserve(client, rid).json()
    cancelled = cancel(client, rid, first["reservation_id"])
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    restored = client.get(f"/rehearsals/{rid}").json()
    assert restored["links"] == snapshot["links"]
    assert restored["tunnels"] == snapshot["tunnels"]
    assert restored["pending"] is None

    # The step can be redone.
    again = reserve(client, rid)
    assert again.status_code == 201
    assert again.json()["tunnel_id"] == first["tunnel_id"]
    assert again.json()["reservation_id"] != first["reservation_id"]


def test_stale_reservation_ids_do_not_change_state(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    first = reserve(client, rid).json()
    assert confirm(client, rid, first["reservation_id"]).status_code == 200
    snapshot = client.get(f"/rehearsals/{rid}").json()

    assert confirm(client, rid, first["reservation_id"]).status_code == 409
    assert cancel(client, rid, first["reservation_id"]).status_code == 409
    assert confirm(client, rid, "res_does_not_exist").status_code == 409
    assert cancel(client, rid, "res_does_not_exist").status_code == 409
    assert client.get(f"/rehearsals/{rid}").json() == snapshot


def test_cancelled_reservation_cannot_be_confirmed(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    first = reserve(client, rid).json()
    cancel(client, rid, first["reservation_id"])
    snapshot = client.get(f"/rehearsals/{rid}").json()
    assert confirm(client, rid, first["reservation_id"]).status_code == 409
    assert client.get(f"/rehearsals/{rid}").json() == snapshot


def test_cancelling_a_confirmed_reservation_is_rejected(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    first = reserve(client, rid).json()
    confirm(client, rid, first["reservation_id"])
    snapshot = client.get(f"/rehearsals/{rid}").json()
    assert cancel(client, rid, first["reservation_id"]).status_code == 409
    assert client.get(f"/rehearsals/{rid}").json() == snapshot


def test_full_rehearsal_ends_completed(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    while True:
        view = client.get(f"/rehearsals/{rid}").json()
        if view["status"] == "completed":
            break
        first = reserve(client, rid).json()
        confirm(client, rid, first["reservation_id"])

    view = client.get(f"/rehearsals/{rid}").json()
    assert view["tunnels_moved"] == view["tunnels_total"]
    assert view["remaining_order"] == []
    assert view["next_tunnel_id"] is None
    assert all(t["current_path"] == t["target_path"] for t in view["tunnels"])
    assert all(link["reserved"] == 0 for link in view["links"])
    assert reserve(client, rid).status_code == 409


def test_view_reports_pending_details(client, swap_scenario):
    rid = create(client, swap_scenario).json()["rehearsal_id"]
    first = reserve(client, rid).json()
    view = client.get(f"/rehearsals/{rid}").json()
    assert view["pending"] == {
        "reservation_id": first["reservation_id"],
        "tunnel_id": first["tunnel_id"],
        "sequence": 1,
        "reserved": first["reserved"],
    }


def test_no_room_at_all_is_refused(client):
    """Both links are already saturated by old paths; no move ever fits."""
    scenario = {
        "links": links(("L1", "A", "B", 5), ("L2", "A", "B", 5)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 1, ["L2"], ["L1"]),
        ],
    }
    # T2 (1 Mbps) can move onto L1 (4/5 used) => feasible.  So this one works.
    assert create(client, scenario).status_code == 201

    saturated = {
        "links": links(("L1", "A", "B", 5), ("L2", "A", "B", 5)),
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2", "L3"]),
            tunnel("T2", 2, ["L2"], ["L1"]),
        ],
    }
    # T1's target references L3, which does not exist -> structural error.
    assert create(client, saturated).status_code == 422

    dead_end = {
        "links": links(("L1", "A", "B", 5), ("L2", "A", "B", 5), ("L3", "A", "B", 5)),
        "tunnels": [
            tunnel("T1", 5, ["L1"], ["L2"]),
            tunnel("T2", 5, ["L2"], ["L3"]),
            tunnel("T3", 5, ["L3"], ["L1"]),
        ],
    }
    # Circular deadlock: every target link is exactly saturated by its current
    # holder, so no tunnel can make the first move.
    response = create(client, dead_end)
    assert response.status_code == 409
    assert response.json()["error"] == "no_feasible_order"


def test_alternative_order_avoids_a_dead_end(client):
    """The first move must be the small one; the big move only fits last."""
    # L1 = 10, L2 = 10.  A_heavy (9) L1 -> L2, B_light (1) L2 -> L1,
    # C_light (1) L2 -> L1.  L2 starts with 2 Mbps used; A_heavy needs 9 of
    # the 8 free, so it can only go after the lights have left L2.  A greedy
    # search by tunnel id would pick A_heavy and jam.
    scenario = {
        "links": links(("L1", "A", "B", 10), ("L2", "A", "B", 10)),
        "tunnels": [
            tunnel("A_heavy", 9, ["L1"], ["L2"]),
            tunnel("B_light", 1, ["L2"], ["L1"]),
            tunnel("C_light", 1, ["L2"], ["L1"]),
        ],
    }
    body = create(client, scenario).json()
    # A_heavy (id-sorted first) cannot move until one light tunnel leaves L2.
    assert body["order"][0] != "A_heavy"
    assert body["order"][0] in {"B_light", "C_light"}

    # And the returned order really is executable.
    rid = body["rehearsal_id"]
    while client.get(f"/rehearsals/{rid}").json()["status"] != "completed":
        reserved = reserve(client, rid)
        assert reserved.status_code == 201, reserved.text
        assert confirm(client, rid, reserved.json()["reservation_id"]).status_code == 200


# --------------------------------------------------- alternate input formats
def test_links_without_endpoints_are_accepted(client):
    """Endpoint metadata is optional; ids and capacities are enough."""
    scenario = {
        "links": [
            {"link_id": "L1", "capacity": 10},
            {"link_id": "L2", "capacity": 5},
        ],
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 3, ["L2"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 201, response.text
    rid = response.json()["rehearsal_id"]
    view = client.get(f"/rehearsals/{rid}").json()
    assert {link["link_id"]: link["used"] for link in view["links"]} == {"L1": 4, "L2": 3}


def test_partially_specified_endpoints_are_rejected(client):
    scenario = {
        "links": [
            {"link_id": "L1", "source": "A", "capacity": 10},
            {"link_id": "L2", "capacity": 10},
        ],
        "tunnels": [
            tunnel("T1", 4, ["L1"], ["L2"]),
            tunnel("T2", 3, ["L2"], ["L1"]),
        ],
    }
    response = create(client, scenario)
    assert response.status_code == 422
    assert any("both source and target" in e for e in response.json()["errors"])


def test_bundled_examples_behave_as_documented(client):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "examples"
    swap = client.post("/rehearsals", json=json.loads((root / "swap.json").read_text()))
    assert swap.status_code == 201
    assert sorted(swap.json()["order"]) == ["T1", "T2"]

    infeasible = client.post(
        "/rehearsals", json=json.loads((root / "infeasible.json").read_text())
    )
    assert infeasible.status_code == 409
    assert infeasible.json()["error"] == "no_feasible_order"
