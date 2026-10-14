"""model/ — what the contract says, with no Textual in it (design §3.3).

    attachment.py   one attachment: every C2 frame into state; the pump, the socket's only reader
    events.py       the per-attachment event log (D22)
    fleet.py        GET /sessions polling, records, the need-you and held-by-others rules
    alerts.py       the operator alert stream (GET /alerts)
    actions.py      every action once: label, key, and when it is available

Every module here imports only the published contract client and the stdlib, so a phase that
builds a model test can import any of them without Textual being installed.
"""

from __future__ import annotations

from .alerts import Alert, AlertModel, AlertStreamer
from .attachment import (
    AttachmentPump,
    AttachmentState,
    CloseOutcome,
    PumpHooks,
    close_outcome,
)
from .events import Event, EventLog
from .fleet import (
    Chip,
    Fleet,
    FleetPoller,
    display_id,
    is_attachable,
    is_known_type,
    shortest_prefix,
)

__all__ = [
    "Alert",
    "AlertModel",
    "AlertStreamer",
    "AttachmentPump",
    "AttachmentState",
    "Chip",
    "CloseOutcome",
    "Event",
    "EventLog",
    "Fleet",
    "FleetPoller",
    "PumpHooks",
    "close_outcome",
    "display_id",
    "is_attachable",
    "is_known_type",
    "shortest_prefix",
]