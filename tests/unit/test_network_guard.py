from __future__ import annotations

import socket

import pytest


def test_socket_connections_are_blocked_by_default() -> None:
    connection = socket.socket()
    try:
        with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
            connection.connect(("127.0.0.1", 1))
    finally:
        connection.close()
