"""The countdown line (design D35, F12; session-countdown rules 9-11): text for the status row, computed from
the core's `ends_at` and `ends_by` and from nothing else.

Only the core decides whether a session is in a warning window and when it ends (rule 9): this module
turns the three values the record or a `notice` carries into words, against the client's wall clock. A
clock that disagrees with the core's moves the words, never the facts. An `ends_by` this build does not
know shows `ends_at` (rule 10), as a local time.

Nothing here imports Textual.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: str | None) -> datetime | None:
    """A contract timestamp (RFC 3339 UTC, `Z`), or None when it is absent or unreadable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def format_duration(seconds: float) -> str:
    """Under a minute `0:SS`, under an hour `m:ss`, under a day `Hh Mm`, beyond that `Dd Hh` (F12)."""
    total = max(int(seconds), 0)
    if total < 3600:
        return f"{total // 60}:{total % 60:02d}"
    if total < 86400:
        return f"{total // 3600}h {total % 3600 // 60}m"
    return f"{total // 86400}d {total % 86400 // 3600}h"


def local_time(when: datetime, now: float) -> str:
    """`HH:MM:SS` in the host's local time, with the date when it is not today."""
    local = when.astimezone()
    today = datetime.fromtimestamp(now).astimezone().date()
    return local.strftime("%H:%M:%S" if local.date() == today else "%Y-%m-%d %H:%M:%S")


def countdown_text(ends_by: str | None, ends_at: str | None, now: float) -> str | None:
    """The status row's countdown phrase, or None when `ends_at` cannot be read."""
    when = parse_timestamp(ends_at)
    if when is None:
        return None
    left = format_duration(when.timestamp() - now)
    if ends_by == "link_timeout":
        return f"▲ not answering · ends in {left} unless it answers"
    if ends_by == "idle":
        return f"▲ no bytes either way · ends in {left} · any byte keeps it"
    if ends_by == "unattended":
        return f"▲ no client input · ends in {left} · any input from a client keeps it"
    return f"▲ ends at {local_time(when, now)}"
