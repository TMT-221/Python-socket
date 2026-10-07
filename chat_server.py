"""
SocketRoom Chat Server Node
Can run as Chat Server 1 (port 5001) or Chat Server 2 (port 5002).
Registers with Directory Server, maintains client socket connections,
broadcasts messages to room subscribers, and logs messages to SQLite.
"""

import argparse
import logging
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from protocol import (
    ACTION_CHAT_ACK,
    ACTION_CHAT_MESSAGE,
    ACTION_HEARTBEAT,
    ACTION_JOIN_ROOM,
    ACTION_JOIN_ROOM_RESP,
    ACTION_LIST_ROOMS,
    ACTION_ROOM_DELETED,
    ACTION_LEAVE_ROOM,
    ACTION_LOG_MESSAGE,
    ACTION_REGISTER_NODE,
    ACTION_USER_PRESENCE,
    ProtocolError,
    recv_packet,
    send_packet,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


class ChatServerNode:
    def __init__(
        self,
        name: str = "Chat Server 1",
        host: str = "127.0.0.1",
        port: int = 5001,
        dir_host: str = "127.0.0.1",
        dir_port: int = 5000,
    ):
        self.name = name
        self.host = host
        self.port = port
        self.dir_host = dir_host
        self.dir_port = dir_port

        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)

        self.lock = threading.Lock()
        # room_id -> set of socket.socket
        self.room_subscribers: Dict[str, Set[socket.socket]] = {}
        # sock -> {"user": name, "handle": handle, "initials": initials, "tone": tone, "rooms": set()}
        self.client_sessions: Dict[socket.socket, Dict[str, Any]] = {}
        # sock -> lock serialising writes, so concurrent broadcasts never interleave frames.
        self._send_locks: Dict[socket.socket, threading.Lock] = {}

        self.message_count_window = 0
        self.last_window_reset = time.time()
        self.messages_per_min = 35

        self._running = False
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    def register_with_directory(self) -> bool:
        """Sends registration packet to Directory Server."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(4.0)
                sock.connect((self.dir_host, self.dir_port))
                send_packet(sock, {
                    "action": ACTION_REGISTER_NODE,
                    "data": {
                        "name": self.name,
                        "host": self.host,
                        "port": self.port,
                        "online_clients": len(self.client_sessions),
                        "messages_per_min": self.messages_per_min,
                    },
                })
                resp = recv_packet(sock)
                if resp and resp.get("status") == "ok":
                    self.logger.info(f"Registered successfully with Directory Server at {self.dir_host}:{self.dir_port}")
                    return True
        except Exception as e:
            self.logger.warning(f"Could not connect to Directory Server: {e}")
        return False

    def send_log_to_directory(self, room_id: str, user: str, initials: str, tone: str, time_str: str, body: str) -> Optional[int]:
        """Persist a message before acknowledging or broadcasting it."""
        try:
            with socket.create_connection((self.dir_host, self.dir_port), timeout=4.0) as sock:
                sock.settimeout(4.0)
                send_packet(sock, {
                    "action": ACTION_LOG_MESSAGE,
                    "data": {
                        "room_id": room_id,
                        "user": user,
                        "initials": initials,
                        "tone": tone,
                        "time": time_str,
                        "body": body,
                    },
                })
                resp = recv_packet(sock)
                if resp and resp.get("status") == "ok":
                    return resp.get("msg_id")
                self.logger.error("Directory Server rejected message for #%s: %s", room_id, resp)
        except Exception as e:
            self.logger.error("Failed to persist message: %s", e)
        return None

    @staticmethod
    def _clean_field(value: Any, default: str, max_len: int) -> str:
        """Return a stripped, length-limited string or the default for invalid input."""
        if not isinstance(value, str):
            return default
        value = value.strip()[:max_len]
        return value or default

    def _send(self, sock: socket.socket, packet: Dict[str, Any]) -> None:
        """Send one framed packet to a client socket without interleaving with other threads."""
        with self.lock:
            send_lock = self._send_locks.get(sock)
        if send_lock is None:
            send_packet(sock, packet)
            return
        with send_lock:
            send_packet(sock, packet)

    def _room_exists(self, room_id: str) -> bool:
        if not isinstance(room_id, str) or not room_id:
            return False
        try:
            with socket.create_connection((self.dir_host, self.dir_port), timeout=2.0) as gateway:
                gateway.settimeout(2.0)
                send_packet(gateway, {"action": ACTION_LIST_ROOMS, "data": {}})
                response = recv_packet(gateway)
                return bool(response and response.get("status") == "ok" and any(
                    room.get("id") == room_id
                    for room in response.get("data", {}).get("rooms", [])
                ))
        except (OSError, ProtocolError):
            return False

    def _evict_deleted_room(self, room_id: str) -> None:
        with self.lock:
            subscribers = list(self.room_subscribers.pop(room_id, set()))
            for subscriber in subscribers:
                session = self.client_sessions.get(subscriber)
                if session:
                    session.get("rooms", set()).discard(room_id)
        for subscriber in subscribers:
            try:
                self._send(subscriber, {
                    "action": ACTION_ROOM_DELETED,
                    "data": {"room_id": room_id},
                })
            except (OSError, ProtocolError):
                self._cleanup_client(subscriber)

    def _heartbeat_loop(self) -> None:
        """Sends periodic heartbeat to Directory Server."""
        while self._running:
            try:
                # Calculate message rate
                now = time.time()
                elapsed = now - self.last_window_reset
                if elapsed >= 60.0:
                    self.messages_per_min = int((self.message_count_window / elapsed) * 60)
                    self.message_count_window = 0
                    self.last_window_reset = now

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(3.0)
                    sock.connect((self.dir_host, self.dir_port))
                    send_packet(sock, {
                        "action": ACTION_HEARTBEAT,
                        "data": {
                            "name": self.name,
                            "online_clients": len(self.client_sessions),
                            "messages_per_min": max(self.messages_per_min, len(self.client_sessions) * 5),
                        },
                    })
                    recv_packet(sock)
            except Exception:
                # Directory server may be restarting or busy; will retry
                pass
            time.sleep(3.5)

    def handle_client(self, sock: socket.socket, addr: Any) -> None:
        self.logger.info(f"Client connected from {addr[0]}:{addr[1]}")
        with self.lock:
            self.client_sessions[sock] = {
                "user": "Ẩn danh",
                "handle": "@anon",
                "initials": "AD",
                "tone": "minh",
                "rooms": set(),
                "addr": addr,
            }
            self._send_locks[sock] = threading.Lock()

        try:
            while self._running:
                packet = recv_packet(sock)
                if not packet:
                    break

                action = packet.get("action")
                data = packet.get("data", {})

                if action == ACTION_ROOM_DELETED:
                    room_id = data.get("room_id")
                    if self._room_exists(room_id):
                        self._send(sock, {
                            "action": ACTION_ROOM_DELETED,
                            "status": "error",
                            "message": "Room still exists",
                        })
                    else:
                        self._evict_deleted_room(room_id)
                        self._send(sock, {
                            "action": ACTION_ROOM_DELETED,
                            "status": "ok",
                            "data": {"room_id": room_id},
                        })
                    continue

                if action == ACTION_JOIN_ROOM:
                    room_id = data.get("room_id")
                    if not self._room_exists(room_id):
                        self._send(sock, {
                            "action": ACTION_JOIN_ROOM_RESP,
                            "status": "error",
                            "message": "Room does not exist",
                        })
                        continue
                    user = self._clean_field(data.get("user"), "Ẩn danh", 60)
                    initials = self._clean_field(data.get("initials"), "U", 4)
                    tone = data.get("tone") if data.get("tone") in {"minh", "lan", "tuan", "huy"} else "minh"
                    # Internal joins (web bridge) don't announce themselves to the room.
                    silent = bool(data.get("silent"))

                    with self.lock:
                        session = self.client_sessions.get(sock, {})
                        session["user"] = user
                        session["initials"] = initials
                        session["tone"] = tone
                        session["silent"] = silent
                        if "rooms" not in session:
                            session["rooms"] = set()
                        session["rooms"].add(room_id)

                        if room_id not in self.room_subscribers:
                            self.room_subscribers[room_id] = set()
                        self.room_subscribers[room_id].add(sock)

                        # Build current members list for this room (internal bridge sessions hidden)
                        members = []
                        for s in self.room_subscribers[room_id]:
                            s_info = self.client_sessions.get(s, {})
                            if s_info.get("silent"):
                                continue
                            members.append({
                                "name": s_info.get("user", "Ẩn danh"),
                                "initials": s_info.get("initials", "U"),
                                "tone": s_info.get("tone", "minh"),
                                "online": True,
                                "state": "Đang hoạt động",
                            })

                    if not silent:
                        self.logger.info(f"User {user} joined room #{room_id} (Total in room: {len(members)})")

                    # Send response back to joining client
                    self._send(sock, {
                        "action": ACTION_JOIN_ROOM_RESP,
                        "status": "ok",
                        "data": {
                            "room_id": room_id,
                            "members": members,
                            "online_count": len(members),
                        },
                    })

                    # Broadcast presence notice to others
                    if not silent:
                        self.broadcast_to_room(
                            room_id,
                            {
                                "action": ACTION_USER_PRESENCE,
                                "data": {
                                    "room_id": room_id,
                                    "user": user,
                                    "initials": initials,
                                    "tone": tone,
                                    "status": "joined",
                                    "members": members,
                                    "online_count": len(members),
                                },
                            },
                            exclude_sock=sock,
                        )

                elif action == ACTION_LEAVE_ROOM:
                    room_id = data.get("room_id")
                    self._remove_from_room(sock, room_id)

                elif action == ACTION_CHAT_MESSAGE:
                    room_id = data.get("room_id")
                    body = data.get("body", "")
                    body = body.strip() if isinstance(body, str) else ""
                    with self.lock:
                        session = self.client_sessions.get(sock, {})
                        joined = room_id in session.get("rooms", set())
                        user = session.get("user", "Ẩn danh")
                        initials = session.get("initials", "U")
                        tone = session.get("tone", "minh")

                    if not joined or not body or len(body) > 2000:
                        self._send(sock, {
                            "action": ACTION_CHAT_ACK,
                            "status": "error",
                            "message": "Phòng hoặc nội dung tin nhắn không hợp lệ.",
                        })
                        continue

                    time_str = datetime.now().strftime("%H:%M")
                    msg_id = self.send_log_to_directory(room_id, user, initials, tone, time_str, body)
                    if msg_id is None:
                        self._send(sock, {
                            "action": ACTION_CHAT_ACK,
                            "status": "error",
                            "message": "Không thể lưu tin nhắn. Hãy thử lại.",
                        })
                        continue

                    with self.lock:
                        self.message_count_window += 1
                    message = {
                        "id": msg_id,
                        "room_id": room_id,
                        "user": user,
                        "initials": initials,
                        "tone": tone,
                        "time": time_str,
                        "body": body,
                    }
                    try:
                        self._send(sock, {
                            "action": ACTION_CHAT_ACK,
                            "status": "ok",
                            "data": {"message": message},
                        })
                    except Exception:
                        pass
                    self.broadcast_to_room(room_id, {
                        "action": ACTION_CHAT_MESSAGE,
                        "data": message,
                    })

        except ProtocolError as pe:
            self.logger.debug(f"Protocol error from {addr}: {pe}")
        except Exception as e:
            self.logger.debug(f"Client disconnected: {e}")
        finally:
            self._cleanup_client(sock)

    def broadcast_to_room(self, room_id: str, packet: Dict[str, Any], exclude_sock: Optional[socket.socket] = None) -> None:
        """Sends a packet to all clients subscribed to room_id."""
        with self.lock:
            subscribers = list(self.room_subscribers.get(room_id, set()))

        dead_sockets = []
        for s in subscribers:
            if s is exclude_sock:
                continue
            try:
                self._send(s, packet)
            except Exception:
                dead_sockets.append(s)

        if dead_sockets:
            for ds in dead_sockets:
                self._cleanup_client(ds)

    def _remove_from_room(self, sock: socket.socket, room_id: str) -> None:
        with self.lock:
            if room_id in self.room_subscribers:
                self.room_subscribers[room_id].discard(sock)
            session = self.client_sessions.get(sock)
            if session and "rooms" in session:
                session["rooms"].discard(room_id)
                user = session.get("user", "Ẩn danh")
            else:
                user = "Ẩn danh"

        self.logger.info(f"User {user} left room #{room_id}")

    def _cleanup_client(self, sock: socket.socket) -> None:
        with self.lock:
            session = self.client_sessions.pop(sock, None)
            self._send_locks.pop(sock, None)
            if not session:
                return

            user = session.get("user", "Ẩn danh")
            rooms = session.get("rooms", set())
            for room_id in rooms:
                if room_id in self.room_subscribers:
                    self.room_subscribers[room_id].discard(sock)

        try:
            sock.close()
        except Exception:
            pass

        self.logger.info(f"Cleaned up session for {user}")

    def start(self) -> None:
        self._running = True

        class ThreadedTCPHandler(socketserver.BaseRequestHandler):
            def handle(inner_self):
                self.handle_client(inner_self.request, inner_self.client_address)

        socketserver.TCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer((self.host, self.port), ThreadedTCPHandler)

        # Register with Directory Server
        self.register_with_directory()

        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        self.logger.info(f"{self.name} listening on {self.host}:{self.port} (TCP)")
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            self.logger.info(f"Shutting down {self.name}...")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self.logger.info(f"{self.name} stopped.")


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Chat Server Node")
    parser.add_argument("--node", type=int, default=1, choices=[1, 2], help="Node number (1 or 2)")
    parser.add_argument("--name", help="Server name (e.g. 'Chat Server 1')")
    parser.add_argument("--port", type=int, help="Port to bind (5001 for Node 1, 5002 for Node 2)")
    parser.add_argument("--dir-port", type=int, default=5000, help="Directory Server port (default 5000)")
    args = parser.parse_args()

    node_num = args.node
    name = args.name or f"Chat Server {node_num}"
    port = args.port or (5001 if node_num == 1 else 5002)

    server = ChatServerNode(name=name, port=port, dir_port=args.dir_port)
    server.start()


if __name__ == "__main__":
    main()
