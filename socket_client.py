"""
SocketRoom Client Library
Thread-safe client for interacting with the Directory Server Gateway
and Chat Server nodes over TCP sockets using 4-byte framing.
"""

import logging
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from protocol import (
    ACTION_CHAT_ACK,
    ACTION_CHAT_MESSAGE,
    ACTION_CREATE_ROOM,
    ACTION_CREATE_ROOM_RESP,
    ACTION_JOIN_ROOM,
    ACTION_JOIN_ROOM_RESP,
    ACTION_LEAVE_ROOM,
    ACTION_LIST_ROOMS,
    ACTION_LIST_ROOMS_RESP,
    ACTION_ROOM_DELETED,
    ACTION_LOGIN,
    ACTION_LOGIN_RESP,
    ACTION_SYNC_HISTORY,
    ACTION_SYNC_HISTORY_RESP,
    ACTION_USER_PRESENCE,
    ProtocolError,
    recv_packet,
    send_packet,
)


class SocketRoomClient:
    def __init__(
        self,
        username: str = "Khách",
        gateway_host: str = "127.0.0.1",
        gateway_port: int = 5000,
        initials: Optional[str] = None,
        tone: str = "lan",
    ):
        self.username = username
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.initials = initials or "".join([p[0].upper() for p in username.split()[:2]]) or "U"
        self.tone = tone

        # Node socket connection for active room
        self.node_sock: Optional[socket.socket] = None
        self.current_node_address: Optional[tuple] = None
        self.current_room_id: Optional[str] = None
        self.connected = False

        # Callbacks
        self.on_message: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_presence: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_room_deleted: Optional[Callable[[Dict[str, Any]], None]] = None
        # Called with a human-readable message when the node rejects a join/chat request.
        self.on_error: Optional[Callable[[str], None]] = None

        self._recv_thread: Optional[threading.Thread] = None
        self._running = False
        self._send_lock = threading.Lock()

    # --- Gateway / Directory Server Calls (Short-lived or persistent RPC) ---

    def _call_gateway(self, action: str, data: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.gateway_host, self.gateway_port))
                send_packet(sock, {"action": action, "data": data})
                resp = recv_packet(sock)
                return resp
        except Exception as e:
            logging.debug(f"Gateway call {action} failed: {e}")
            return None

    def login(self) -> Optional[Dict[str, Any]]:
        """Authenticates with the Directory Server."""
        resp = self._call_gateway(ACTION_LOGIN, {"name": self.username})
        if resp and resp.get("status") == "ok":
            return resp.get("data")
        return None

    def get_rooms(self) -> List[Dict[str, Any]]:
        """Queries list of rooms from the Directory Server."""
        resp = self._call_gateway(ACTION_LIST_ROOMS, {})
        if resp and resp.get("status") == "ok":
            return resp.get("data", {}).get("rooms", [])
        return []

    def create_room(self, name: str, description: str, server: str = "auto") -> Optional[Dict[str, Any]]:
        """Requests room creation on the Directory Server."""
        resp = self._call_gateway(
            ACTION_CREATE_ROOM,
            {"name": name, "description": description, "server": server},
        )
        if resp and resp.get("status") == "ok":
            return resp.get("data", {}).get("room")
        elif resp:
            raise Exception(resp.get("message", "Lỗi tạo phòng."))
        return None

    def get_history(self, room_id: str) -> List[Dict[str, Any]]:
        """Fetches SQLite message history from Directory Server."""
        resp = self._call_gateway(ACTION_SYNC_HISTORY, {"room_id": room_id})
        if resp and resp.get("status") == "ok":
            return resp.get("data", {}).get("messages", [])
        return []

    # --- Chat Node Socket Connection (Long-lived stream for real-time exchange) ---

    def connect_to_node(self, host: str, port: int) -> bool:
        """Connects long-lived TCP socket to a Chat Server Node."""
        self.disconnect_node()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            self.node_sock = sock
            self.current_node_address = (host, port)
            self._running = True
            self.connected = True

            self._recv_thread = threading.Thread(target=self._node_receive_loop, args=(sock,), daemon=True)
            self._recv_thread.start()
            return True
        except Exception as e:
            logging.error(f"Failed to connect to chat node at {host}:{port}: {e}")
            self.node_sock = None
            self.connected = False
            return False

    def join_room(self, room_id: str, node_host: str = "127.0.0.1", node_port: int = 5001) -> bool:
        """Connects to the appropriate node (if not connected) and joins room."""
        if not self.node_sock or self.current_node_address != (node_host, node_port):
            ok = self.connect_to_node(node_host, node_port)
            if not ok:
                return False
        elif self.current_room_id and self.current_room_id != room_id:
            # Same node: unsubscribe from the previous room so its messages stop arriving.
            try:
                with self._send_lock:
                    send_packet(self.node_sock, {
                        "action": ACTION_LEAVE_ROOM,
                        "data": {"room_id": self.current_room_id},
                    })
            except (OSError, ProtocolError):
                self.disconnect_node()
                if not self.connect_to_node(node_host, node_port):
                    return False

        self.current_room_id = room_id
        with self._send_lock:
            send_packet(self.node_sock, {
                "action": ACTION_JOIN_ROOM,
                "data": {
                    "room_id": room_id,
                    "user": self.username,
                    "initials": self.initials,
                    "tone": self.tone,
                },
            })
        return True

    def send_chat_message(self, body: str, room_id: Optional[str] = None) -> bool:
        """Sends a chat message to the current room over the active node socket."""
        target_room = room_id or self.current_room_id
        if not self.node_sock or not target_room or not self.connected:
            return False

        try:
            with self._send_lock:
                send_packet(self.node_sock, {
                    "action": ACTION_CHAT_MESSAGE,
                    "data": {
                        "room_id": target_room,
                        "user": self.username,
                        "initials": self.initials,
                        "tone": self.tone,
                        "body": body,
                    },
                })
            return True
        except Exception as e:
            logging.error(f"Error sending chat message: {e}")
            self.disconnect_node()
            return False

    def _node_receive_loop(self, sock: Optional[socket.socket] = None) -> None:
        """Background thread receiving packets from Chat Server Node."""
        sock = sock or self.node_sock
        while self._running and sock is not None and self.node_sock is sock:
            try:
                packet = recv_packet(sock)
                if not packet:
                    break

                action = packet.get("action")
                data = packet.get("data", {})

                if action == ACTION_CHAT_MESSAGE and self.on_message:
                    self.on_message(data)
                elif action == ACTION_USER_PRESENCE and self.on_presence:
                    self.on_presence(data)
                elif action == ACTION_JOIN_ROOM_RESP:
                    if packet.get("status") == "error":
                        if self.on_error:
                            self.on_error(packet.get("message", "Không thể vào phòng."))
                    elif self.on_presence:
                        self.on_presence(data)
                elif action == ACTION_CHAT_ACK and packet.get("status") == "error":
                    if self.on_error:
                        self.on_error(packet.get("message", "Không thể gửi tin nhắn."))
                elif action == ACTION_ROOM_DELETED:
                    if data.get("room_id") == self.current_room_id:
                        self.current_room_id = None
                    if self.on_room_deleted:
                        self.on_room_deleted(data)

            except ProtocolError as pe:
                logging.debug(f"Protocol error in client receive loop: {pe}")
                break
            except Exception as e:
                logging.debug(f"Receive loop ended: {e}")
                break

        # A newer connection may already have replaced this socket; leave its state alone.
        if self.node_sock is not sock:
            return
        self.connected = False
        if self.on_disconnect and self._running:
            self.on_disconnect()

    def disconnect_node(self) -> None:
        """Closes connection to the chat node."""
        self._running = False
        if self.node_sock:
            try:
                if self.current_room_id:
                    with self._send_lock:
                        send_packet(self.node_sock, {
                            "action": ACTION_LEAVE_ROOM,
                            "data": {"room_id": self.current_room_id},
                        })
            except Exception:
                pass
            try:
                self.node_sock.close()
            except Exception:
                pass
            self.node_sock = None
        self.connected = False
