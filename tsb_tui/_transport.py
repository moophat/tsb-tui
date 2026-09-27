"""The transport exceptions `tsb_contract_client` does not yet publicly re-export:
`ConnectionClosed` (a send or resize racing the peer closing the socket),
`HandshakeError`/`ProtocolError` (the WebSocket upgrade, or its framing, failing outright), and
`HttpError` (a refused connection, a timeout, or a TLS failure on the REST calls underneath —
`Client.login`/`create_session`/`sessions`/`session`/`close`/`ws_ticket` all raise it for a
transport failure, distinct from `ApiError`'s non-2xx status). `tsb_contract_client.attachment`
itself already reaches into these same private `tsbc` submodules for the same transport, so
importing them here follows the existing pattern rather than widening to a bare `except Exception`.
Isolated in this one module so nothing else in `tui/` names a private `tsbc` submodule directly.
"""

from __future__ import annotations

from tsbc._aws import ConnectionClosed
from tsbc._http import HttpError
from tsbc._wsframe import HandshakeError, ProtocolError

__all__ = ["ConnectionClosed", "HandshakeError", "HttpError", "ProtocolError"]
