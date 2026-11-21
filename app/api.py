"""HTTP layer: thin translation between the engine and JSON."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from .domain import RehearsalRecord
from .models import (
    LinkUsageOut,
    OrderStepOut,
    PendingOut,
    RehearsalCreatedOut,
    RehearsalIn,
    RehearsalViewOut,
    ReservationOut,
    ReserveIn,
    ResolutionOut,
    TunnelStatusOut,
)

router = APIRouter(prefix="/rehearsals", tags=["rehearsals"])


def _engine(request: Request):
    return request.app.state.engine


def _steps(record: RehearsalRecord) -> list[OrderStepOut]:
    return [
        OrderStepOut(
            sequence=i + 1,
            tunnel_id=tunnel_id,
            bandwidth=record.tunnel(tunnel_id).bandwidth,  # type: ignore[union-attr]
        )
        for i, tunnel_id in enumerate(record.order)
    ]


@router.post(
    "",
    response_model=RehearsalCreatedOut,
    status_code=201,
    summary="Submit a scenario and start a rehearsal",
    responses={
        409: {"description": "No feasible order; no rehearsal was created."},
        422: {"description": "The scenario is structurally invalid."},
    },
)
def create(rehearsal: RehearsalIn, request: Request):
    record = _engine(request).create_rehearsal(rehearsal.links, rehearsal.tunnels)
    return RehearsalCreatedOut(
        rehearsal_id=record.rehearsal_id,
        status="completed" if record.completed else "active",
        created_at=record.created_at,
        order=list(record.order),
        steps=_steps(record),
        tunnels_total=len(record.tunnels),
        links_total=len(record.links),
    )


@router.get(
    "/{rehearsal_id}",
    response_model=RehearsalViewOut,
    summary="Inspect active paths and per-link used/reserved bandwidth",
)
def view(rehearsal_id: str, request: Request):
    record, used, reserved = _engine(request).view(rehearsal_id)
    pending = None
    if record.pending is not None:
        reservation = record.reservations[record.pending]
        pending = PendingOut(
            reservation_id=reservation.reservation_id,
            tunnel_id=reservation.tunnel_id,
            sequence=reservation.sequence,
            reserved=reservation.charge,
        )
    return RehearsalViewOut(
        rehearsal_id=record.rehearsal_id,
        status="completed" if record.completed else "active",
        created_at=record.created_at,
        order=list(record.order),
        remaining_order=list(record.remaining_order),
        next_tunnel_id=record.next_tunnel_id,
        tunnels_total=len(record.tunnels),
        tunnels_moved=record.moved_count,
        tunnels=[
            TunnelStatusOut(
                tunnel_id=t.tunnel_id,
                bandwidth=t.bandwidth,
                moved=t.moved,
                current_path=list(t.active_path),
                initial_path=list(t.current_path),
                target_path=list(t.target_path),
            )
            for t in record.tunnels
        ],
        links=[
            LinkUsageOut(
                link_id=link.link_id,
                source=link.source,
                target=link.target,
                capacity=link.capacity,
                used=used[link.link_id],
                reserved=reserved[link.link_id],
                available=link.capacity - used[link.link_id] - reserved[link.link_id],
            )
            for link in record.links
        ],
        pending=pending,
    )


@router.post(
    "/{rehearsal_id}/reservations",
    response_model=ReservationOut,
    status_code=201,
    summary="Reserve bandwidth for the next tunnel (one pending at a time)",
    responses={409: {"description": "Out of order, or another reservation is pending."}},
)
def reserve(
    rehearsal_id: str,
    request: Request,
    body: ReserveIn | None = Body(default=None),
):
    tunnel_id = body.tunnel_id if body is not None else None
    engine = _engine(request)
    reservation = engine.reserve(rehearsal_id, tunnel_id)
    record = engine.view(rehearsal_id)[0]
    tunnel = record.tunnel(reservation.tunnel_id)
    assert tunnel is not None
    return ReservationOut(
        rehearsal_id=record.rehearsal_id,
        reservation_id=reservation.reservation_id,
        tunnel_id=reservation.tunnel_id,
        sequence=reservation.sequence,
        status=reservation.status,
        reserved=reservation.charge,
        current_path=list(tunnel.active_path),
        target_path=list(tunnel.target_path),
        next_tunnel_id=record.next_tunnel_id,
    )


@router.post(
    "/{rehearsal_id}/reservations/{reservation_id}/confirm",
    response_model=ResolutionOut,
    summary="Confirm a pending reservation: switch the path and free the old one",
    responses={409: {"description": "Unknown, stale or non-pending reservation id."}},
)
def confirm(rehearsal_id: str, reservation_id: str, request: Request):
    engine = _engine(request)
    reservation, tunnel = engine.confirm(rehearsal_id, reservation_id)
    record = engine.view(rehearsal_id)[0]
    return ResolutionOut(
        rehearsal_id=record.rehearsal_id,
        reservation_id=reservation.reservation_id,
        tunnel_id=reservation.tunnel_id,
        sequence=reservation.sequence,
        status=reservation.status,
        current_path=list(tunnel.active_path),
        released=tunnel.released(),
        rehearsal_status="completed" if record.completed else "active",
        next_tunnel_id=record.next_tunnel_id,
    )


@router.post(
    "/{rehearsal_id}/reservations/{reservation_id}/cancel",
    response_model=ResolutionOut,
    summary="Cancel a pending reservation; the step can be retried",
    responses={409: {"description": "Unknown, stale or non-pending reservation id."}},
)
def cancel(rehearsal_id: str, reservation_id: str, request: Request):
    engine = _engine(request)
    reservation = engine.cancel(rehearsal_id, reservation_id)
    record = engine.view(rehearsal_id)[0]
    tunnel = record.tunnel(reservation.tunnel_id)
    assert tunnel is not None
    return ResolutionOut(
        rehearsal_id=record.rehearsal_id,
        reservation_id=reservation.reservation_id,
        tunnel_id=reservation.tunnel_id,
        sequence=reservation.sequence,
        status=reservation.status,
        current_path=list(tunnel.active_path),
        released={},
        rehearsal_status="completed" if record.completed else "active",
        next_tunnel_id=record.next_tunnel_id,
    )
