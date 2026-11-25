"""Fixture marks and epoch profiles (development-plan.md §2: required-green versus published).

Every fixture carries a `mark`: `{"required-green": "eN"}`, `{"published": "eN"}`, or both. A
required-green fixture is one the provider must pass from epoch eN on. A published fixture is one
surfaces may build and mock against from eN, while the provider owes it only from its
required-green epoch, if it has one. Profile eK asserts every fixture whose required-green
epoch is at or before eK. Later profiles are supersets of earlier ones.
"""

from __future__ import annotations

EPOCHS = ("e0", "e1", "e2", "e3", "e4", "e4b", "e3b", "e5", "e6")
MARK_KEYS = ("required-green", "published")

REQUIRED = "required"    # asserted in this profile
PUBLISHED = "published"  # in scope for surfaces; the provider is not graded on it yet
FUTURE = "future"        # neither required nor published by this profile's epoch


def epoch_index(epoch: str) -> int:
    try:
        return EPOCHS.index(epoch)
    except ValueError:
        raise ValueError(f"unknown epoch {epoch!r} (known: {', '.join(EPOCHS)})") from None


def check_mark(mark: object) -> list[str]:
    """Problems with a fixture's mark; empty when the mark is well formed."""
    if not isinstance(mark, dict) or not mark:
        return ["mark must be an object with 'required-green' and/or 'published'"]
    problems = [f"unknown mark key {k!r}" for k in mark if k not in MARK_KEYS]
    for key in MARK_KEYS:
        if key in mark and mark[key] not in EPOCHS:
            problems.append(f"mark {key!r} must be one of {', '.join(EPOCHS)}, got {mark[key]!r}")
    if (not problems and "required-green" in mark and "published" in mark
            and epoch_index(mark["published"]) > epoch_index(mark["required-green"])):
        problems.append("a fixture cannot be required green before it is published")
    return problems


def status(mark: dict, profile: str) -> str:
    """REQUIRED, PUBLISHED or FUTURE for a well-formed mark under `profile`."""
    here = epoch_index(profile)
    required = mark.get("required-green")
    if required is not None and epoch_index(required) <= here:
        return REQUIRED
    first = min(epoch_index(mark[k]) for k in MARK_KEYS if k in mark)
    return PUBLISHED if first <= here else FUTURE


def describe(mark: dict) -> str:
    return ", ".join(f"{k}: {mark[k]}" for k in MARK_KEYS if k in mark)
