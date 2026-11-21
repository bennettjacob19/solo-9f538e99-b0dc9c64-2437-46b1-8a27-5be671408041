"""The planner must agree with brute force on random small scenarios."""

from __future__ import annotations

import itertools
import random

from app.domain import LinkRecord, TunnelRecord
from app.planner import find_order


def _simulate(links, tunnels, order) -> bool:
    used = {link.link_id: 0 for link in links}
    for tunnel in tunnels:
        for link_id in tunnel.current_path:
            used[link_id] += tunnel.bandwidth
    caps = {link.link_id: link.capacity for link in links}
    for tunnel_id in order:
        tunnel = next(t for t in tunnels if t.tunnel_id == tunnel_id)

        def fits():
            return all(
                used[link_id] + tunnel.bandwidth <= caps[link_id]
                for link_id in set(tunnel.target_path) - set(tunnel.current_path)
            )

        if not fits():
            return False
        for link_id in set(tunnel.target_path) - set(tunnel.current_path):
            used[link_id] += tunnel.bandwidth
        for link_id in set(tunnel.current_path) - set(tunnel.target_path):
            used[link_id] -= tunnel.bandwidth
    return True


def _scenario(rng: random.Random):
    node_count = rng.randint(2, 4)
    nodes = [chr(ord("A") + i) for i in range(node_count)]
    # Two parallel directed links per ordered node pair, so a tunnel always has
    # an alternative route between the same endpoints.
    edges = [
        (f"L{s}{d}_{k}", s, d)
        for s in nodes
        for d in nodes
        if s != d
        for k in range(2)
    ]
    links = tuple(
        LinkRecord(link_id=i, source=s, target=d, capacity=rng.randint(2, 12))
        for i, s, d in edges
    )
    by_neighbours = {}
    for link_id, s, d in edges:
        by_neighbours.setdefault((s, d), []).append(link_id)

    def path(s, d):
        """One or two contiguous links from ``s`` to ``d``."""
        choices = [
            ("via", middle)
            for middle in nodes
            if middle not in (s, d)
            and (s, middle) in by_neighbours
            and (middle, d) in by_neighbours
        ]
        choices.append(("direct", None))
        kind, middle = rng.choice(choices)
        if kind == "direct":
            return [rng.choice(by_neighbours[(s, d)])]
        return [
            rng.choice(by_neighbours[(s, middle)]),
            rng.choice(by_neighbours[(middle, d)]),
        ]

    tunnels = []
    for t in range(rng.randint(2, 4)):
        s, d = rng.sample(nodes, 2)
        current = path(s, d)
        for _ in range(20):
            target = path(s, d)
            if target != current:
                break
        else:
            continue
        tunnels.append(
            TunnelRecord(
                tunnel_id=f"T{t}",
                bandwidth=rng.randint(1, 5),
                current_path=tuple(current),
                target_path=tuple(target),
            )
        )
    return links, tuple(tunnels)


def test_planner_matches_brute_force():
    rng = random.Random(20261121)
    checked = 0
    for _ in range(250):
        links, tunnels = _scenario(rng)
        if any(
            sum(t.bandwidth for t in tunnels if l.link_id in t.current_path) > l.capacity
            for l in links
        ):
            continue

        order = find_order(links, tunnels)
        exhaustive = [
            perm
            for perm in itertools.permutations(t.tunnel_id for t in tunnels)
            if _simulate(links, tunnels, perm)
        ]

        assert (order is not None) == bool(exhaustive), (
            f"planner={order} brute={exhaustive[:3]} links={links} tunnels={tunnels}"
        )
        if order is not None:
            assert sorted(order) == sorted(t.tunnel_id for t in tunnels)
            assert _simulate(links, tunnels, order) is True, (
                f"planner returned an infeasible order {order}"
            )
        checked += 1
    assert checked > 120
