"""Order planner.

Given a scenario, find an order in which every tunnel can be moved exactly once
without ever exceeding a link's capacity.

Feasibility of moving tunnel ``t`` while the set ``S`` of tunnels has already
moved:

* every link in ``t``'s target path that is *not* in its current path needs
  ``t.bandwidth`` free (``capacity - current usage``);
* links shared by both paths already carry ``t``'s bandwidth on the old path,
  so they need nothing extra and are not charged twice;
* the old paths of tunnels not yet moved keep occupying their bandwidth, and
  links exclusively used by ``t``'s old path are only freed once ``t`` switches
  (i.e. after this step is confirmed).

Because the world state depends only on *which* tunnels have moved, the search
space is the 2^n subset lattice (n <= 10 -> at most 1024 states).  A DFS with a
"failed subset" memo is therefore exact: if any order exists, this returns one.
"""

from __future__ import annotations

from .domain import LinkRecord, TunnelRecord


def find_order(
    links: tuple[LinkRecord, ...], tunnels: tuple[TunnelRecord, ...]
) -> list[str] | None:
    """Return a feasible migration order (tunnel ids) or ``None``."""
    if not tunnels:
        return []

    caps = {link.link_id: link.capacity for link in links}
    ids = [t.tunnel_id for t in tunnels]
    by_id = {t.tunnel_id: t for t in tunnels}
    index = {tid: i for i, tid in enumerate(ids)}
    full = (1 << len(ids)) - 1

    usage: dict[str, int] = {link.link_id: 0 for link in links}
    for tunnel in tunnels:
        for link_id in tunnel.current_path:
            usage[link_id] += tunnel.bandwidth

    failed: set[int] = set()
    order: list[str] = []

    def fits(tunnel: TunnelRecord) -> bool:
        return all(
            usage[link_id] + tunnel.bandwidth <= caps[link_id]
            for link_id in tunnel.new_links
        )

    def move(tunnel: TunnelRecord, forward: bool) -> None:
        sign = 1 if forward else -1
        for link_id in tunnel.new_links:
            usage[link_id] += sign * tunnel.bandwidth
        for link_id in tunnel.released_links:
            usage[link_id] -= sign * tunnel.bandwidth

    def dfs(mask: int) -> bool:
        if mask == full:
            return True
        if mask in failed:
            return False
        candidates = [
            by_id[tid] for tid in ids if not (mask >> index[tid]) & 1 and fits(by_id[tid])
        ]
        # Cheapest moves first: deterministic and usually keeps options open.
        candidates.sort(key=lambda t: (t.bandwidth, t.tunnel_id))
        for tunnel in candidates:
            move(tunnel, forward=True)
            order.append(tunnel.tunnel_id)
            if dfs(mask | (1 << index[tunnel.tunnel_id])):
                return True
            order.pop()
            move(tunnel, forward=False)
        failed.add(mask)
        return False

    return list(order) if dfs(0) else None


def explain_infeasible(
    links: tuple[LinkRecord, ...], tunnels: tuple[TunnelRecord, ...]
) -> str:
    """Best-effort human explanation for a scenario with no feasible order."""
    caps = {link.link_id: link.capacity for link in links}
    for tunnel in tunnels:
        for link_id in tunnel.new_links:
            if tunnel.bandwidth > caps[link_id]:
                return (
                    f"tunnel {tunnel.tunnel_id!r} needs {tunnel.bandwidth} Mbps on "
                    f"link {link_id!r} but that link's capacity is only "
                    f"{caps[link_id]} Mbps"
                )
    return (
        "no ordering of the tunnels can be migrated: every remaining move "
        "would exceed a link's capacity"
    )
