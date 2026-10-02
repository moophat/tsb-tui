"""The transport exceptions, re-exported by the published contract client since contract-v0.2:
`ConnectionClosed` (a send or resize racing the peer closing the socket),
`HandshakeError`/`ProtocolError` (the WebSocket upgrade, or its framing, failing outright), and
`HttpError` (a refused connection, a timeout, or a TLS failure on the REST calls underneath —
`Client.login`/`create_session`/`sessions`/`session`/`close`/`ws_ticket` all raise it for a
transport failure, distinct from `ApiError`'s non-2xx status). Imported from `tsb_contract_client`,
the same class objects its transports raise, rather than reaching into private `tsbc` submodules.
Isolated in this one module so nothing else in `tui/` names these transport errors directly.
"""

from __future__ import annotations

from tsb_contract_client import (
    ConnectionClosed,
    HandshakeError,
    HttpError,
    ProtocolError,
)

__all__ = ["ConnectionClosed", "HandshakeError", "HttpError", "ProtocolError"]
