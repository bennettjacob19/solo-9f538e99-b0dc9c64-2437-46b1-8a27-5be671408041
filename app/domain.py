"""Immutable records for a rehearsal.

A rehearsal is stored as a single JSON document, so each state transition is one
atomic row update.  These dataclasses are the in-memory shape of that document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


@dataclass(frozen=True)
class LinkRecord:
    link_id: str
    source: str | None
    target: str | None
    capacity: int

    @property
    def has_endpoints(self) -> bool:
        return self.source is not None and self.target is not None


@dataclass(frozen=True)
class TunnelRecord:
    tunnel_id: str
    bandwidth: int
    # The path in use before the rehearsal (never mutated).
    current_path: tuple[str, ...]
    # The path to migrate onto (same endpoints as current_path).
    target_path: tuple[str, ...]
    moved: bool = False

    @property
    def active_path(self) -> tuple[str, ...]:
        return self.target_path if self.moved else self.current_path

    @property
    def new_links(self) -> tuple[str, ...]:
        """Links that need fresh bandwidth when this tunnel switches.

        Links present in both paths are *shared*: the old path already occupies
        them with this tunnel's bandwidth, so they are not charged twice.
        """
        old = set(self.current_path)
        return tuple(link for link in _dedupe(self.target_path) if link not in old)

    @property
    def released_links(self) -> tuple[str, ...]:
        """Links exclusively held by the old path; freed when the switch happens."""
        new = set(self.target_path)
        return tuple(link for link in _dedupe(self.current_path) if link not in new)

    def charge(self) -> dict[str, int]:
        return {link: self.bandwidth for link in self.new_links}

    def released(self) -> dict[str, int]:
        return {link: self.bandwidth for link in self.released_links}


@dataclass
class ReservationRecord:
    reservation_id: str
    tunnel_id: str
    sequence: int
    status: str  # pending | confirmed | cancelled
    charge: dict[str, int] = field(default_factory=dict)
    created_at: str = ""
    resolved_at: str | None = None


@dataclass
class RehearsalRecord:
    rehearsal_id: str
    created_at: str
    links: tuple[LinkRecord, ...]
    tunnels: tuple[TunnelRecord, ...]
    order: tuple[str, ...]
    reservations: dict[str, ReservationRecord] = field(default_factory=dict)
    pending: str | None = None

    # ------------------------------------------------------------- derived state
    def tunnel(self, tunnel_id: str) -> TunnelRecord | None:
        return next((t for t in self.tunnels if t.tunnel_id == tunnel_id), None)

    def link(self, link_id: str) -> LinkRecord | None:
        return next((l for l in self.links if l.link_id == link_id), None)

    @property
    def moved_count(self) -> int:
        return sum(1 for t in self.tunnels if t.moved)

    @property
    def completed(self) -> bool:
        return self.moved_count == len(self.tunnels)

    @property
    def next_tunnel_id(self) -> str | None:
        for tunnel_id in self.order:
            tunnel = self.tunnel(tunnel_id)
            if tunnel is not None and not tunnel.moved:
                return tunnel_id
        return None

    @property
    def remaining_order(self) -> tuple[str, ...]:
        return tuple(
            tunnel_id
            for tunnel_id in self.order
            if (t := self.tunnel(tunnel_id)) is not None and not t.moved
        )

    def sequence_of(self, tunnel_id: str) -> int:
        return self.order.index(tunnel_id) + 1

    def replace_tunnel(self, updated: TunnelRecord) -> None:
        self.tunnels = tuple(
            updated if t.tunnel_id == updated.tunnel_id else t for t in self.tunnels
        )

    # ----------------------------------------------------------- serialisation
    def to_document(self) -> dict[str, Any]:
        return {
            "rehearsal_id": self.rehearsal_id,
            "created_at": self.created_at,
            "links": [
                {
                    "link_id": l.link_id,
                    "source": l.source,
                    "target": l.target,
                    "capacity": l.capacity,
                }
                for l in self.links
            ],
            "tunnels": [
                {
                    "tunnel_id": t.tunnel_id,
                    "bandwidth": t.bandwidth,
                    "current_path": list(t.current_path),
                    "target_path": list(t.target_path),
                    "moved": t.moved,
                }
                for t in self.tunnels
            ],
            "order": list(self.order),
            "reservations": {
                rid: {
                    "reservation_id": r.reservation_id,
                    "tunnel_id": r.tunnel_id,
                    "sequence": r.sequence,
                    "status": r.status,
                    "charge": r.charge,
                    "created_at": r.created_at,
                    "resolved_at": r.resolved_at,
                }
                for rid, r in self.reservations.items()
            },
            "pending": self.pending,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "RehearsalRecord":
        return cls(
            rehearsal_id=doc["rehearsal_id"],
            created_at=doc["created_at"],
            links=tuple(LinkRecord(**link) for link in doc["links"]),
            tunnels=tuple(
                TunnelRecord(
                    tunnel_id=t["tunnel_id"],
                    bandwidth=t["bandwidth"],
                    current_path=tuple(t["current_path"]),
                    target_path=tuple(t["target_path"]),
                    moved=t["moved"],
                )
                for t in doc["tunnels"]
            ),
            order=tuple(doc["order"]),
            reservations={
                rid: ReservationRecord(
                    reservation_id=r["reservation_id"],
                    tunnel_id=r["tunnel_id"],
                    sequence=r["sequence"],
                    status=r["status"],
                    charge=dict(r["charge"]),
                    created_at=r["created_at"],
                    resolved_at=r["resolved_at"],
                )
                for rid, r in doc["reservations"].items()
            },
            pending=doc["pending"],
        )
