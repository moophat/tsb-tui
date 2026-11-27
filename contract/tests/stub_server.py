"""A stdlib HTTP + WebSocket server for tests: routes requests to a pluggable app.

Thin import of the shipped `tsbc/_serve.py` server (the same code `python -m tsbc serve` runs), so
the tests and the fixture server share one implementation. Test scaffolding only.
"""

from __future__ import annotations

from tsbc._serve import App, ServerWS, StubServer

__all__ = ["App", "ServerWS", "StubServer"]