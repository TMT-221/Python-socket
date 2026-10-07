"""
SocketRoom Protocol Module
Implements 4-byte big-endian length prefix framing for robust TCP stream communication.
Header: [4 bytes uint32 length]
Payload: [UTF-8 JSON string]
"""

import json
import socket
import struct
from typing import Any, Dict, Optional, Tuple

HEADER_FORMAT = '>I'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 4 bytes


class ProtocolError(Exception):
    """Raised when protocol framing or packet decoding fails."""
    pass


def send_packet(sock: socket.socket, data: Dict[str, Any]) -> None:
    """
    Serializes a dictionary to JSON and sends it over the socket with a 4-byte
    big-endian length prefix.
    """
    try:
        json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
        packet_len = len(json_bytes)
        header = struct.pack(HEADER_FORMAT, packet_len)
        # Send full header followed by payload
        sock.sendall(header + json_bytes)
    except (socket.error, OSError) as e:
        raise ProtocolError(f"Socket send error: {e}") from e
    except Exception as e:
        raise ProtocolError(f"Serialization error: {e}") from e


def _recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Reads exactly `num_bytes` from the socket stream.
    Handles partial reads across TCP packets.
    Returns empty bytes if socket closes cleanly at boundary.
    """
    buf = bytearray()
    while len(buf) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(buf))
            if not chunk:
                if len(buf) == 0:
                    return b""
                raise ProtocolError(f"Connection closed prematurely (got {len(buf)} of {num_bytes} bytes)")
            buf.extend(chunk)
        except (socket.error, OSError) as e:
            if len(buf) == 0:
                return b""
            raise ProtocolError(f"Socket receive error: {e}") from e
    return bytes(buf)


def recv_packet(sock: socket.socket) -> Optional[Dict[str, Any]]:
    """
    Reads a 4-byte length prefix from the socket, then reads the full JSON payload.
    Returns parsed dictionary, or None if connection closed cleanly.
    """
    header_bytes = _recv_exact(sock, HEADER_SIZE)
    if not header_bytes:
        return None

    (payload_length,) = struct.unpack(HEADER_FORMAT, header_bytes)
    if payload_length <= 0 or payload_length > 10 * 1024 * 1024:  # 10MB sanity limit
        raise ProtocolError(f"Invalid payload length: {payload_length}")

    payload_bytes = _recv_exact(sock, payload_length)
    if not payload_bytes:
        return None

    try:
        data = json.loads(payload_bytes.decode('utf-8'))
        return data
    except Exception as e:
        raise ProtocolError(f"JSON decode error: {e}") from e


# Standard Action Constants
ACTION_LOGIN = "LOGIN"
ACTION_LOGIN_RESP = "LOGIN_RESP"
ACTION_LIST_ROOMS = "LIST_ROOMS"
ACTION_LIST_ROOMS_RESP = "LIST_ROOMS_RESP"
ACTION_CREATE_ROOM = "CREATE_ROOM"
ACTION_CREATE_ROOM_RESP = "CREATE_ROOM_RESP"
ACTION_DELETE_ROOM = "DELETE_ROOM"
ACTION_DELETE_ROOM_RESP = "DELETE_ROOM_RESP"
ACTION_ROOM_DELETED = "ROOM_DELETED"
ACTION_JOIN_ROOM = "JOIN_ROOM"
ACTION_JOIN_ROOM_RESP = "JOIN_ROOM_RESP"
ACTION_LEAVE_ROOM = "LEAVE_ROOM"
ACTION_CHAT_MESSAGE = "CHAT_MESSAGE"
ACTION_CHAT_ACK = "CHAT_ACK"
ACTION_SYNC_HISTORY = "SYNC_HISTORY"
ACTION_SYNC_HISTORY_RESP = "SYNC_HISTORY_RESP"
ACTION_USER_PRESENCE = "USER_PRESENCE"
ACTION_REGISTER_NODE = "REGISTER_NODE"
ACTION_REGISTER_NODE_RESP = "REGISTER_NODE_RESP"
ACTION_HEARTBEAT = "HEARTBEAT"
ACTION_HEARTBEAT_ACK = "HEARTBEAT_ACK"
ACTION_LOG_MESSAGE = "LOG_MESSAGE"
ACTION_INFRA_STATUS = "INFRA_STATUS"
ACTION_INFRA_STATUS_RESP = "INFRA_STATUS_RESP"
ACTION_SIMULATE_NODE = "SIMULATE_NODE"
