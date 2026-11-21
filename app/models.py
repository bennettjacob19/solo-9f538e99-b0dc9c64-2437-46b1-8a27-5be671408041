"""Request/response schemas for the rehearsal API.

Field names accept a few common aliases so the Swagger page (``/docs``) is
forgiving: ``id``/``link_id``, ``from``/``source``, ``to``/``target``,
``path``/``current_path``, ``new_path``/``target_path``, ``edges``/``links``,
``circuits``/``tunnels``.  Unknown extra fields are ignored.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

EXAMPLE_SCENARIO: dict[str, Any] = {
    "links": [
        {"link_id": "L1", "source": "A", "target": "B", "capacity": 10},
        {"link_id": "L2", "source": "B", "target": "C", "capacity": 10},
        {"link_id": "L3", "source": "A", "target": "C", "capacity": 5},
        {"link_id": "L4", "source": "C", "target": "D", "capacity": 10},
    ],
    "tunnels": [
        {
            "tunnel_id": "T1",
            "bandwidth": 4,
            "current_path": ["L1", "L2"],
            "target_path": ["L3"],
        },
        {
            "tunnel_id": "T2",
            "bandwidth": 3,
            "current_path": ["L3"],
            "target_path": ["L1", "L2"],
        },
    ],
}


class LinkIn(BaseModel):
    """A directed link with an integer capacity in Mbps.

    ``source``/``target`` are optional: when every link supplies them the paths
    are checked for contiguity and endpoint equality; when they are omitted the
    service still works, it just cannot make those geometric checks.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    link_id: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("link_id", "id", "name", "link"),
            description="Unique link identifier used by tunnel paths.",
        ),
    ]
    source: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            validation_alias=AliasChoices("source", "src", "from", "start", "from_node", "tail"),
            description="Directed start node (optional).",
        ),
    ]
    target: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            validation_alias=AliasChoices("target", "dst", "to", "end", "to_node", "head"),
            description="Directed end node (optional).",
        ),
    ]
    capacity: Annotated[
        int,
        Field(
            strict=True,
            ge=1,
            validation_alias=AliasChoices("capacity", "cap", "capacity_mbps", "bandwidth"),
            description="Capacity in integer Mbps.",
        ),
    ]


class TunnelIn(BaseModel):
    """A tunnel with a current path and a target path sharing the same endpoints."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    tunnel_id: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("tunnel_id", "id", "name", "tunnel"),
            description="Unique tunnel identifier.",
        ),
    ]
    bandwidth: Annotated[
        int,
        Field(strict=True, ge=1, description="Tunnel bandwidth in integer Mbps."),
    ]
    current_path: Annotated[
        list[str],
        Field(
            min_length=1,
            validation_alias=AliasChoices(
                "current_path", "path", "old_path", "existing_path", "from_path"
            ),
            description="Ordered list of directed link ids currently in use.",
        ),
    ]
    target_path: Annotated[
        list[str],
        Field(
            min_length=1,
            validation_alias=AliasChoices(
                "target_path", "new_path", "planned_path", "to_path", "destination_path"
            ),
            description="Ordered list of directed link ids to migrate onto.",
        ),
    ]


class RehearsalIn(BaseModel):
    """Scenario submitted to create a rehearsal."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={"example": EXAMPLE_SCENARIO},
    )

    links: Annotated[
        list[LinkIn],
        Field(
            min_length=0,
            validation_alias=AliasChoices("links", "edges", "link_specs", "topology", "network"),
            description="Directed links with capacities.",
        ),
    ]
    tunnels: Annotated[
        list[TunnelIn],
        Field(
            validation_alias=AliasChoices("tunnels", "circuits", "tunnel_specs"),
            description="Between 2 and 10 tunnels to migrate.",
        ),
    ]


class ReserveIn(BaseModel):
    """Optional body for creating a reservation."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={"example": {"tunnel_id": "T1"}},
    )

    tunnel_id: Annotated[
        str | None,
        Field(
            default=None,
            validation_alias=AliasChoices("tunnel_id", "id", "tunnel"),
            description="Tunnel to reserve; defaults to the next tunnel in the order.",
        ),
    ]


# --------------------------------------------------------------------- outputs
class OrderStepOut(BaseModel):
    sequence: int
    tunnel_id: str
    bandwidth: int


class RehearsalCreatedOut(BaseModel):
    rehearsal_id: str
    status: str
    created_at: str
    order: list[str] = Field(description="Tunnel ids in a safe migration order.")
    steps: list[OrderStepOut]
    tunnels_total: int
    links_total: int


class TunnelStatusOut(BaseModel):
    tunnel_id: str
    bandwidth: int
    moved: bool
    current_path: list[str] = Field(description="Path currently carrying traffic.")
    initial_path: list[str]
    target_path: list[str]


class LinkUsageOut(BaseModel):
    link_id: str
    source: str | None = None
    target: str | None = None
    capacity: int
    used: int
    reserved: int
    available: int


class PendingOut(BaseModel):
    reservation_id: str
    tunnel_id: str
    sequence: int
    reserved: dict[str, int]


class RehearsalViewOut(BaseModel):
    rehearsal_id: str
    status: str
    created_at: str
    order: list[str]
    remaining_order: list[str]
    next_tunnel_id: str | None
    tunnels_total: int
    tunnels_moved: int
    tunnels: list[TunnelStatusOut]
    links: list[LinkUsageOut]
    pending: PendingOut | None


class ReservationOut(BaseModel):
    rehearsal_id: str
    reservation_id: str
    tunnel_id: str
    sequence: int
    status: str
    reserved: dict[str, int] = Field(
        description="Bandwidth held per link; links shared with the current path are free."
    )
    current_path: list[str]
    target_path: list[str]
    next_tunnel_id: str | None


class ResolutionOut(BaseModel):
    rehearsal_id: str
    reservation_id: str
    tunnel_id: str
    sequence: int
    status: str
    current_path: list[str] = Field(description="Path carrying traffic after this call.")
    released: dict[str, int] = Field(
        default_factory=dict,
        description="Old-path bandwidth released on links not used by the new path.",
    )
    rehearsal_status: str
    next_tunnel_id: str | None
