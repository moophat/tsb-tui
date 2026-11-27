"""tsb_contract_client — the thin northbound client for TSB, stdlib only.

    from tsb_contract_client import Client
    c = Client("https://tsb.example")
    c.login(user, password)
    created = c.create_session(target_id, "new", "xterm-256color", 120, 40)
    att = c.attach(created["session"]["id"])   # or: await c.async_attach(...) inside an event loop

Built for contract-v0.3 (CONTRACT_VERSION). Field names are the contract's: read them from the
schemas at that tag, not from here.

The transport errors are exported here, as the same class objects the transports raise, so a
surface catches them without reaching into tsbc: ConnectionClosed (a send or receive after the
socket closed, whichever transport), HandshakeError and ProtocolError (the WebSocket upgrade or its
framing failing), HttpError (a refused connection, a timeout or a TLS failure underneath a call —
distinct from ApiError, a non-2xx answer).
"""

from tsbc._http import HttpError
from tsbc._wsframe import ConnectionClosed, HandshakeError, ProtocolError

from .attachment import AsyncAttachment, Attachment, AttachRefused
from .client import ApiError, Client
from .device_login import AsyncDeviceLogin, DeviceLoginError
from .events import AlertStream

CONTRACT_VERSION = "v0.26.1"

__all__ = [
    "CONTRACT_VERSION",
    "AlertStream",
    "ApiError",
    "AsyncAttachment",
    "AsyncDeviceLogin",
    "AttachRefused",
    "Attachment",
    "Client",
    "ConnectionClosed",
    "DeviceLoginError",
    "HandshakeError",
    "HttpError",
    "ProtocolError",
]
