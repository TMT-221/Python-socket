"""
SocketRoom Directory Server (Gateway)
Listens on port 5000. Coordinates chat server nodes, room allocation,
user logins, SQLite persistence, and cluster health monitoring.
"""

import argparse
import collections
import hmac
import os
import sqlite3
import logging
import socket
import socketserver
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from admin_auth import load_or_create_admin_secret
from database import Database
from protocol import (
    ACTION_CREATE_ROOM,
    ACTION_CREATE_ROOM_RESP,
    ACTION_DELETE_ROOM,
    ACTION_DELETE_ROOM_RESP,
    ACTION_ROOM_DELETED,
    ACTION_HEARTBEAT,
    ACTION_HEARTBEAT_ACK,
    ACTION_INFRA_STATUS,
    ACTION_INFRA_STATUS_RESP,
    ACTION_LIST_ROOMS,
    ACTION_LIST_ROOMS_RESP,
    ACTION_LOG_MESSAGE,
    ACTION_LOGIN,
    ACTION_LOGIN_RESP,
    ACTION_REGISTER_NODE,
    ACTION_REGISTER_NODE_RESP,
    ACTION_SIMULATE_NODE,
    ACTION_SYNC_HISTORY,
    ACTION_SYNC_HISTORY_RESP,
    ProtocolError,
    recv_packet,
    send_packet,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DirectoryServer] %(message)s",
    datefmt="%H:%M:%S",
)


class DirectoryServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 5000, db_path: Optional[str] = None):
        self.host = host
        self.port = port
        self.db = Database(db_path) if db_path else Database()
        self.admin_secret = load_or_create_admin_secret()
        # log_event is sometimes called while node state is locked.
        self.lock = threading.RLock()

        # In-memory tracking of nodes: node_name -> {host, port, is_active, last_heartbeat, simulated_down, stats}
        self.nodes: Dict[str, Dict[str, Any]] = {
            "Chat Server 1": {
                "id": "node-1",
                "name": "Chat Server 1",
                "host": "127.0.0.1",
                "port": int(os.environ.get("SOCKETROOM_NODE1_PORT", "5001")),
                "is_active": True,
                "simulated_down": False,
                "last_heartbeat": time.time(),
                "online_clients": 0,
                "messages_per_min": 0,
            },
            "Chat Server 2": {
                "id": "node-2",
                "name": "Chat Server 2",
                "host": "127.0.0.1",
                "port": int(os.environ.get("SOCKETROOM_NODE2_PORT", "5002")),
                "is_active": True,
                "simulated_down": False,
                "last_heartbeat": time.time(),
                "online_clients": 0,
                "messages_per_min": 0,
            },
        }

        # In-memory active users: username -> session info
        self.active_users: Dict[str, Dict[str, Any]] = {}

        # Activity log ring buffer (recent 50 entries)
        self.activity_log = collections.deque(maxlen=50)
        self._seed_activity_log()

        self._running = False
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._monitor_thread: Optional[threading.Thread] = None

    def _seed_activity_log(self) -> None:
        self.log_event("SYS_START", f"Directory Server khởi động trên {self.host}:{self.port}")

    def log_event(self, event_type: str, detail: str) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "event": event_type,
            "detail": detail,
        }
        with self.lock:
            self.activity_log.appendleft(entry)
        logging.info(f"[{event_type}] {detail}")

    def handle_client(self, sock: socket.socket, addr: Any) -> None:
        """Processes incoming requests from clients, nodes, or web bridges."""
        try:
            while self._running:
                packet = recv_packet(sock)
                if not packet:
                    break

                action = packet.get("action")
                data = packet.get("data", {})
                resp = self._dispatch(action, data, sock, addr)
                if resp:
                    send_packet(sock, resp)
        except ProtocolError as pe:
            logging.debug(f"Protocol error from {addr}: {pe}")
        except Exception as e:
            logging.debug(f"Connection ended for {addr}: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _dispatch(self, action: Optional[str], data: Dict[str, Any], sock: socket.socket, addr: Any) -> Optional[Dict[str, Any]]:
        if action == ACTION_LOGIN:
            raw_name = data.get("name")
            name = (raw_name.strip() if isinstance(raw_name, str) else "")[:60] or "Khách"
            handle = f"@{name.lower().replace(' ', '')}"
            user_info = {
                "name": name,
                "handle": handle,
                "initials": "".join([part[0].upper() for part in name.split()[:2]]) or "U",
                "login_time": time.time(),
            }
            with self.lock:
                self.active_users[name] = user_info

            self.log_event("USER_LOGIN", f"Người dùng {name} ({handle}) đã đăng nhập từ {addr[0]}:{addr[1]}")
            rooms = self.db.get_rooms()
            return {
                "action": ACTION_LOGIN_RESP,
                "status": "ok",
                "data": {
                    "user": user_info,
                    "rooms": rooms,
                    "nodes": self._get_nodes_view(),
                    "gateway": f"{self.host}:{self.port}",
                },
            }

        elif action == ACTION_LIST_ROOMS:
            rooms = self.db.get_rooms()
            return {
                "action": ACTION_LIST_ROOMS_RESP,
                "status": "ok",
                "data": {"rooms": rooms},
            }

        elif action == ACTION_CREATE_ROOM:
            raw_name = data.get("name")
            raw_desc = data.get("description")
            name = raw_name.strip().lower().replace(" ", "-").lstrip("#") if isinstance(raw_name, str) else ""
            desc = raw_desc.strip() if isinstance(raw_desc, str) else ""
            requested_server = data.get("server", "auto")

            if not name or not desc:
                return {
                    "action": ACTION_CREATE_ROOM_RESP,
                    "status": "error",
                    "message": "Vui lòng nhập tên và mô tả phòng.",
                }
            if len(name) > 50 or len(desc) > 200:
                return {
                    "action": ACTION_CREATE_ROOM_RESP,
                    "status": "error",
                    "message": "Tên phòng tối đa 50 ký tự, mô tả tối đa 200 ký tự.",
                }

            # Server allocation logic
            assigned_server = self._allocate_server(requested_server)
            if not assigned_server:
                return {
                    "action": ACTION_CREATE_ROOM_RESP,
                    "status": "error",
                    "message": "Không có Chat Server nào đang hoạt động để gán phòng mới.",
                }

            room_id = f"room-{uuid.uuid4().hex}"
            success = self.db.create_room(room_id, name, desc, assigned_server, privacy="Công khai")
            if not success:
                return {
                    "action": ACTION_CREATE_ROOM_RESP,
                    "status": "error",
                    "message": "Tên phòng đã tồn tại trong hệ thống.",
                }

            self.log_event("OPEN_ROOM", f"#{name} được điều phối tới {assigned_server}")
            new_room = self.db.get_room(room_id)
            return {
                "action": ACTION_CREATE_ROOM_RESP,
                "status": "ok",
                "data": {"room": new_room},
            }

        elif action == ACTION_DELETE_ROOM:
            supplied = data.get("admin_secret")
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self.admin_secret):
                return {
                    "action": ACTION_DELETE_ROOM_RESP,
                    "status": "error",
                    "message": "Administrator authorization required",
                }
            room_id = data.get("room_id")
            if not isinstance(room_id, str) or not room_id:
                return {
                    "action": ACTION_DELETE_ROOM_RESP,
                    "status": "error",
                    "message": "Room ID required",
                }
            room = self.db.delete_room(room_id)
            if room is None:
                return {
                    "action": ACTION_DELETE_ROOM_RESP,
                    "status": "error",
                    "message": "Room does not exist",
                }
            self._notify_room_deleted(room_id)
            self.log_event("DELETE_ROOM", f"#{room['name']} was deleted")
            return {
                "action": ACTION_DELETE_ROOM_RESP,
                "status": "ok",
                "data": {"room_id": room_id},
            }

        elif action == ACTION_SYNC_HISTORY:
            room_id = data.get("room_id")
            if not room_id:
                return {"action": ACTION_SYNC_HISTORY_RESP, "status": "error", "message": "room_id required"}
            room = self.db.get_room(room_id)
            if not room:
                return {"action": ACTION_SYNC_HISTORY_RESP, "status": "error", "message": "Room does not exist"}
            room_id = room["id"]
            messages = self.db.get_messages(room_id, limit=60)
            return {
                "action": ACTION_SYNC_HISTORY_RESP,
                "status": "ok",
                "data": {"room_id": room_id, "messages": messages},
            }

        elif action == ACTION_LOG_MESSAGE:
            # Sent by Chat Servers to persist messages in SQLite
            room_id = data.get("room_id")
            user = data.get("user")
            initials = data.get("initials", "U")
            tone = data.get("tone", "lan")
            time_str = data.get("time", datetime.now().strftime("%H:%M"))
            body = data.get("body", "")

            if not room_id or not user or not body:
                return {"action": "LOG_MESSAGE_ACK", "status": "error", "message": "Missing message fields"}
            room = self.db.get_room(room_id)
            if not room:
                return {"action": "LOG_MESSAGE_ACK", "status": "error", "message": "Room does not exist"}
            try:
                msg_id = self.db.save_message(room["id"], user, initials, tone, time_str, body)
            except sqlite3.IntegrityError:
                return {"action": "LOG_MESSAGE_ACK", "status": "error", "message": "Room no longer exists"}
            self.log_event("NEW_MESSAGE", f"{user} → #{room['name']} · đã lưu và broadcast")
            return {"action": "LOG_MESSAGE_ACK", "status": "ok", "msg_id": msg_id}

        elif action == ACTION_REGISTER_NODE:
            name = data.get("name")
            host = data.get("host", "127.0.0.1")
            port = data.get("port")
            if name and port:
                with self.lock:
                    self.nodes[name] = {
                        "id": name.lower().replace(" ", "-"),
                        "name": name,
                        "host": host,
                        "port": port,
                        "is_active": True,
                        "simulated_down": False,
                        "last_heartbeat": time.time(),
                        "online_clients": data.get("online_clients", 0),
                        "messages_per_min": data.get("messages_per_min", 10),
                    }
                self.db.update_node_heartbeat(name, host, port, is_active=True)
                self.log_event("NODE_ONLINE", f"{name} ({host}:{port}) đã đăng ký thành công")
                return {"action": ACTION_REGISTER_NODE_RESP, "status": "ok"}

        elif action == ACTION_HEARTBEAT:
            name = data.get("name")
            if name in self.nodes:
                with self.lock:
                    node = self.nodes[name]
                    node["last_heartbeat"] = time.time()
                    node["online_clients"] = data.get("online_clients", node.get("online_clients", 0))
                    node["messages_per_min"] = data.get("messages_per_min", node.get("messages_per_min", 0))
                    if not node["simulated_down"]:
                        node["is_active"] = True
                self.db.update_node_heartbeat(name, node["host"], node["port"], is_active=node["is_active"])
                return {"action": ACTION_HEARTBEAT_ACK, "status": "ok", "timestamp": time.time()}

        elif action == ACTION_SIMULATE_NODE:
            node_name = data.get("node_name", "Chat Server 2")
            turn_off = data.get("offline", True)
            with self.lock:
                if node_name in self.nodes:
                    node = self.nodes[node_name]
                    node["simulated_down"] = turn_off
                    node["is_active"] = not turn_off
                    self.db.set_node_active(node_name, not turn_off)
                    status_text = "ngắt kết nối (mô phỏng)" if turn_off else "khôi phục hoạt động"
                    self.log_event("SIMULATE_NODE", f"{node_name} đã được {status_text}")
                    return {"action": "SIMULATE_NODE_RESP", "status": "ok", "is_active": not turn_off}
            return {"action": "SIMULATE_NODE_RESP", "status": "error", "message": "Node không tồn tại"}

        elif action == ACTION_INFRA_STATUS:
            return {
                "action": ACTION_INFRA_STATUS_RESP,
                "status": "ok",
                "data": {
                    "gateway": {
                        "host": self.host,
                        "port": self.port,
                        "clients": len(self.active_users),
                        "total_rooms": len(self.db.get_rooms()),
                        "database": "SQLite",
                        "is_active": True,
                    },
                    "nodes": self._get_nodes_view(),
                    "recent_logs": list(self.activity_log)[:15],
                },
            }

        return {"action": "UNKNOWN_ACTION", "status": "error"}

    def _notify_room_deleted(self, room_id: str) -> None:
        """Ask local nodes to evict subscriptions after persistence commits."""
        with self.lock:
            ports = {node["port"] for node in self.nodes.values()
                     if isinstance(node.get("port"), int)}
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0) as node_sock:
                    node_sock.settimeout(1.0)
                    send_packet(node_sock, {
                        "action": ACTION_ROOM_DELETED,
                        "data": {"room_id": room_id},
                    })
                    recv_packet(node_sock)
            except (OSError, ProtocolError) as exc:
                logging.debug("Could not notify chat node at port %s: %s", port, exc)

    def _allocate_server(self, preference: str) -> Optional[str]:
        with self.lock:
            active_nodes = [name for name, info in self.nodes.items() if info["is_active"] and not info["simulated_down"]]
            if not active_nodes:
                return None
            if preference in active_nodes:
                return preference
            # Round-robin / lowest load
            # Count rooms assigned to each active node
            rooms = self.db.get_rooms()
            counts = {name: sum(1 for r in rooms if r["server_node"] == name) for name in active_nodes}
            return min(counts, key=counts.get)

    def _get_nodes_view(self) -> List[Dict[str, Any]]:
        with self.lock:
            result = []
            for name, info in self.nodes.items():
                rooms_count = sum(1 for r in self.db.get_rooms() if r["server_node"] == name)
                result.append({
                    "id": info["id"],
                    "name": info["name"],
                    "host": info["host"],
                    "port": info["port"],
                    "is_active": info["is_active"] and not info["simulated_down"],
                    "simulated_down": info["simulated_down"],
                    "online_clients": info["online_clients"],
                    "rooms_count": rooms_count,
                    "messages_per_min": info["messages_per_min"],
                })
            return result

    def _health_monitor(self) -> None:
        """Background thread checking heartbeats every 4 seconds."""
        while self._running:
            time.sleep(4)
            now = time.time()
            with self.lock:
                for name, node in self.nodes.items():
                    if node["simulated_down"]:
                        continue
                    # If no heartbeat within 12 seconds, mark inactive
                    if now - node["last_heartbeat"] > 12:
                        if node["is_active"]:
                            node["is_active"] = False
                            self.db.set_node_active(name, False)
                            self.log_event("NODE_OFFLINE", f"{name} quá hạn heartbeat (>12s), đã chuyển sang Offline")

    def start(self) -> None:
        self._running = True

        class ThreadedTCPHandler(socketserver.BaseRequestHandler):
            def handle(inner_self):
                self.handle_client(inner_self.request, inner_self.client_address)

        socketserver.TCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer((self.host, self.port), ThreadedTCPHandler)

        self._monitor_thread = threading.Thread(target=self._health_monitor, daemon=True)
        self._monitor_thread.start()

        logging.info(f"Directory Server (Gateway) listening on {self.host}:{self.port} (TCP)")
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Shutting down Directory Server...")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        logging.info("Directory Server stopped.")


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Directory Server (Gateway)")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen (default 5000)")
    args = parser.parse_args()

    server = DirectoryServer(host=args.host, port=args.port)
    server.start()


if __name__ == "__main__":
    main()
