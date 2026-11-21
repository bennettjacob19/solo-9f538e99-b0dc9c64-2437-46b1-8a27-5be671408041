"""Domain errors mapped to HTTP status codes by the API layer."""

from __future__ import annotations


class RehearsalError(Exception):
    """Base class for all expected domain failures."""

    code = "rehearsal_error"
    status_code = 400


class ScenarioError(RehearsalError):
    """The submitted scenario is structurally invalid; nothing was created."""

    code = "invalid_scenario"
    status_code = 422

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or "invalid scenario")
        self.errors = errors


class Infeasible(RehearsalError):
    """No ordering can move every tunnel; no rehearsal was created."""

    code = "no_feasible_order"
    status_code = 409

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RehearsalNotFound(RehearsalError):
    code = "rehearsal_not_found"
    status_code = 404


class StepConflict(RehearsalError):
    """Reservation requested while another is pending, or out of order."""

    code = "step_conflict"
    status_code = 409


class ReservationInvalid(RehearsalError):
    """Reservation id is unknown, expired, cancelled or already confirmed."""

    code = "reservation_invalid"
    status_code = 409
