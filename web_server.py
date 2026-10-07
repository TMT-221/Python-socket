"""
SocketRoom Web Server & Real-time Bridge
Serves the HTML frontend on port 8080 and bridges browser HTTP/SSE
to the backend Python TCP socket cluster (Directory Server & Chat Nodes).
Implemented entirely with Python standard library.
"""

import argparse
import collections
import hashlib
import http.cookies
import http.server
import hmac
import json
import logging
import os
import select
import secrets
import socket
import sys
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from admin_auth import load_or_create_admin_secret

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from protocol import (
    ACTION_CHAT_ACK,
    ACTION_CHAT_MESSAGE,
    ACTION_CREATE_ROOM,
    ACTION_DELETE_ROOM,
    ACTION_INFRA_STATUS,
    ACTION_JOIN_ROOM,
    ACTION_JOIN_ROOM_RESP,
    ACTION_LEAVE_ROOM,
    ACTION_ROOM_DELETED,
    ACTION_LIST_ROOMS,
    ACTION_LOGIN,
    ACTION_SIMULATE_NODE,
    ACTION_SYNC_HISTORY,
    ACTION_USER_PRESENCE,
    recv_packet,
    send_packet,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WebServer] %(message)s",
    datefmt="%H:%M:%S",
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class AdminSessions:
    """Short-lived, revocable sessions backed by a host-only secret."""

    COOKIE_NAME = "socketroom_admin"
    LIFETIME = 12 * 60 * 60

    def __init__(self):
        self.secret = load_or_create_admin_secret()
        self.lock = threading.Lock()
        self.sessions: Dict[str, float] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {key: expiry for key, expiry in self.sessions.items()
                             if expiry > now}
            self.sessions[self._key(token)] = now + self.LIFETIME
        return token

    def valid(self, token: str) -> bool:
        if not token:
            return False
        try:
            key = self._key(token)
        except (UnicodeEncodeError, ValueError):
            return False
        with self.lock:
            expiry = self.sessions.get(key, 0)
            if expiry <= time.time():
                self.sessions.pop(key, None)
                return False
            return True

    def revoke(self, token: str) -> None:
        if not token:
            return
        try:
            key = self._key(token)
        except (UnicodeEncodeError, ValueError):
            return
        with self.lock:
            self.sessions.pop(key, None)


admin_sessions: Optional[AdminSessions] = None


class BridgeManager:
    """Manages active socket connections to Directory Server & Chat Nodes, and fanout to SSE clients."""

    def __init__(self, gateway_host: str = "127.0.0.1", gateway_port: int = 5000):
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.lock = threading.Lock()

        # SSE client queues for real-time pushing: queue.Queue per client
        self.sse_subscribers: Set[Any] = set()

        # Cached nodes info: name -> (host, port)
        self.node_endpoints: Dict[str, tuple] = {
            "Chat Server 1": ("127.0.0.1", int(os.environ.get("SOCKETROOM_NODE1_PORT", "5001"))),
            "Chat Server 2": ("127.0.0.1", int(os.environ.get("SOCKETROOM_NODE2_PORT", "5002"))),
        }

        # Background listener sockets on nodes to forward broadcasts to SSE
        self._node_sockets: Dict[str, socket.socket] = {}
        self._running = True
        self._start_node_listeners()

    def add_sse_subscriber(self, sub_queue) -> None:
        with self.lock:
            self.sse_subscribers.add(sub_queue)

    def remove_sse_subscriber(self, sub_queue) -> None:
        with self.lock:
            self.sse_subscribers.discard(sub_queue)

    def broadcast_sse(self, event_type: str, data: Any) -> None:
        payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        with self.lock:
            subscribers = list(self.sse_subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    def call_gateway(self, action: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect((self.gateway_host, self.gateway_port))
                send_packet(sock, {"action": action, "data": data})
                return recv_packet(sock)
        except Exception as e:
            logging.debug(f"Error calling Gateway {action}: {e}")
            return None

    def send_chat_to_node(self, node_name: str, room_id: str, user: str, initials: str, tone: str, body: str):
        """Return the persisted message only after the node confirms it."""
        endpoint = self.node_endpoints.get(node_name)
        if not endpoint:
            return None, "Máy chủ của phòng không hợp lệ."
        try:
            with socket.create_connection(endpoint, timeout=6.0) as sock:
                sock.settimeout(6.0)
                send_packet(sock, {
                    "action": ACTION_JOIN_ROOM,
                    "data": {"room_id": room_id, "user": user, "initials": initials, "tone": tone, "silent": True},
                })
                joined = recv_packet(sock)
                if not joined or joined.get("action") != ACTION_JOIN_ROOM_RESP or joined.get("status") != "ok":
                    return None, "Không thể vào phòng chat."
                send_packet(sock, {
                    "action": ACTION_CHAT_MESSAGE,
                    "data": {"room_id": room_id, "body": body},
                })
                while True:
                    reply = recv_packet(sock)
                    if not reply:
                        return None, "Máy chủ đã ngắt kết nối trước khi xác nhận tin nhắn."
                    if reply.get("action") == ACTION_CHAT_ACK:
                        if reply.get("status") == "ok":
                            return reply.get("data", {}).get("message"), None
                        return None, reply.get("message", "Không thể gửi tin nhắn.")
        except Exception as e:
            logging.error("Error sending chat to %s at %s: %s", node_name, endpoint, e)
            return None, "Không thể kết nối máy chủ chat."

    def _start_node_listeners(self) -> None:
        """Forward broadcasts for every room, including newly created rooms."""
        def listener(node_name: str, port: int):
            while self._running:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=4.0) as sock:
                        sock.settimeout(4.0)
                        joined_rooms = set()
                        next_sync = 0.0
                        while self._running:
                            now = time.monotonic()
                            if now >= next_sync:
                                rooms_resp = self.call_gateway(ACTION_LIST_ROOMS, {})
                                if rooms_resp and rooms_resp.get("status") == "ok":
                                    current_rooms = {
                                        room.get("id") for room in rooms_resp.get("data", {}).get("rooms", [])
                                        if room.get("server_node") == node_name
                                        and room.get("privacy") == "Công khai" and room.get("id")
                                    }
                                    for room_id in joined_rooms - current_rooms:
                                        send_packet(sock, {
                                            "action": ACTION_LEAVE_ROOM,
                                            "data": {"room_id": room_id},
                                        })
                                    joined_rooms.intersection_update(current_rooms)
                                    for room_id in current_rooms - joined_rooms:
                                        send_packet(sock, {
                                            "action": ACTION_JOIN_ROOM,
                                            "data": {
                                                "room_id": room_id,
                                                "user": "WebGateway",
                                                "initials": "WG",
                                                "tone": "minh",
                                                "silent": True,
                                            },
                                        })
                                        joined_rooms.add(room_id)
                                next_sync = time.monotonic() + 2.0
                            timeout = max(0.0, next_sync - time.monotonic())
                            readable, _, _ = select.select([sock], [], [], timeout)
                            if not readable:
                                continue
                            packet = recv_packet(sock)
                            if not packet:
                                break
                            action = packet.get("action")
                            data = packet.get("data", {})
                            if action == ACTION_CHAT_MESSAGE:
                                self.broadcast_sse("chat_message", data)
                            elif action == ACTION_USER_PRESENCE:
                                self.broadcast_sse("user_presence", data)
                            elif action == ACTION_ROOM_DELETED:
                                joined_rooms.discard(data.get("room_id"))
                                self.broadcast_sse("room_deleted", data)
                except Exception as e:
                    logging.debug("Node listener %s reconnecting: %s", node_name, e)
                if self._running:
                    time.sleep(2.0)

        for node_name, (_, port) in self.node_endpoints.items():
            threading.Thread(target=listener, args=(node_name, port), daemon=True).start()


bridge: Optional[BridgeManager] = None


class SocketRoomHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        # Mute standard noisy HTTP access logs
        if "/api/events" in str(args):
            return
        logging.debug(f"{self.client_address[0]} - {format % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self.path = "/index.html"
            return super().do_GET()

        if parsed.path == "/api/events":
            if os.environ.get("SOCKETROOM_PUBLIC_TUNNEL") == "1":
                self._send_json({"error": "SSE không khả dụng qua link công khai"}, status=404)
            else:
                self._handle_sse()
            return

        if parsed.path == "/api/admin/me":
            self._send_json({"admin": self._is_admin()})
            return

        if parsed.path == "/api/rooms":
            resp = bridge.call_gateway(ACTION_LIST_ROOMS, {})
            if not resp or resp.get("status") != "ok":
                self._send_json({"error": "Gateway không kết nối"}, status=502)
            else:
                self._send_json({"rooms": [r for r in resp.get("data", {}).get("rooms", [])
                                           if r.get("privacy") == "Công khai"]})
            return

        if parsed.path == "/api/health":
            resp = bridge.call_gateway(ACTION_LIST_ROOMS, {})
            self._send_json({"status": "ok"} if resp and resp.get("status") == "ok" else
                            {"error": "Gateway chưa sẵn sàng"}, status=200 if resp and resp.get("status") == "ok" else 503)
            return

        if parsed.path.startswith("/api/rooms/") and parsed.path.endswith("/messages"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4 and parts[2]:
                room_id = urllib.parse.unquote(parts[2])
                rooms_resp = bridge.call_gateway(ACTION_LIST_ROOMS, {})
                if not rooms_resp or rooms_resp.get("status") != "ok":
                    self._send_json({"error": "Gateway không kết nối"}, status=502)
                    return
                public_room = next((r for r in rooms_resp.get("data", {}).get("rooms", [])
                                    if r.get("id") == room_id and r.get("privacy") == "Công khai"), None)
                if not public_room:
                    self._send_json({"error": "Phòng không tồn tại hoặc không công khai"}, status=404)
                    return
                resp = bridge.call_gateway(ACTION_SYNC_HISTORY, {"room_id": room_id})
                if resp and resp.get("status") == "ok":
                    self._send_json(resp.get("data", {}))
                else:
                    self._send_json({"error": resp.get("message", "Không tìm thấy phòng") if resp else "Gateway không kết nối"},
                                    status=404 if resp else 502)
                return

        if parsed.path == "/api/infra/status":
            if not self._is_admin():
                self._send_json({"error": "Chức năng quản trị đã khóa"}, status=403)
                return
            resp = bridge.call_gateway(ACTION_INFRA_STATUS, {})
            self._send_json(resp.get("data", {}) if resp else {})
            return

        # Fallback to static files
        return super().do_GET()

    def _session_token(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        try:
            cookie = http.cookies.SimpleCookie()
            cookie.load(cookie_header)
            morsel = cookie.get(AdminSessions.COOKIE_NAME)
            return morsel.value if morsel else ""
        except http.cookies.CookieError:
            return ""

    def _is_admin(self) -> bool:
        return bool(admin_sessions and admin_sessions.valid(self._session_token()))

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == self.headers.get("Host", "").lower()

    def _admin_cookie(self, token: str, clear: bool = False) -> str:
        value = f"{AdminSessions.COOKIE_NAME}={token}; HttpOnly; Path=/; SameSite=Strict"
        value += "; Max-Age=0" if clear else f"; Max-Age={AdminSessions.LIFETIME}"
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            value += "; Secure"
        return value

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._same_origin():
            self._send_json({"error": "Yêu cầu khác nguồn bị từ chối"}, status=403)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Content-Length không hợp lệ"}, status=400)
            return
        if content_length < 0 or content_length > 16384:
            self._send_json({"error": "Dữ liệu gửi quá lớn"}, status=413)
            return
        try:
            post_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(post_data, dict):
                raise ValueError("JSON object required")
        except (UnicodeError, ValueError):
            self._send_json({"error": "Dữ liệu JSON không hợp lệ"}, status=400)
            return

        if parsed.path == "/api/admin/login":
            secret = post_data.get("secret")
            if not isinstance(secret, str) or not admin_sessions or not hmac.compare_digest(secret, admin_sessions.secret):
                self._send_json({"error": "Mã quản trị không đúng"}, status=401)
                return
            token = admin_sessions.create()
            self._send_json({"admin": True}, headers={"Set-Cookie": self._admin_cookie(token)})
            return

        if parsed.path == "/api/admin/logout":
            if admin_sessions:
                admin_sessions.revoke(self._session_token())
            self._send_json({"admin": False}, headers={"Set-Cookie": self._admin_cookie("", clear=True)})
            return

        if parsed.path == "/api/login":
            name = post_data.get("name", "")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
                self._send_json({"error": "Tên hiển thị phải có từ 1 đến 60 ký tự"}, status=400)
                return
            resp = bridge.call_gateway(ACTION_LOGIN, {"name": name.strip()})
            if resp and resp.get("status") == "ok":
                result = resp.get("data", {})
                result["rooms"] = [r for r in result.get("rooms", []) if r.get("privacy") == "Công khai"]
                self._send_json(result)
            else:
                self._send_json({"error": "Không thể kết nối Directory Server"}, status=500)
            return

        if parsed.path == "/api/rooms":
            resp = bridge.call_gateway(ACTION_CREATE_ROOM, post_data)
            if resp and resp.get("status") == "ok":
                bridge.broadcast_sse("room_created", resp.get("data", {}).get("room"))
                self._send_json(resp.get("data"))
            else:
                msg = resp.get("message") if resp else "Lỗi tạo phòng"
                self._send_json({"error": msg}, status=400)
            return

        if parsed.path.startswith("/api/rooms/") and parsed.path.endswith("/send"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 4 or not parts[2]:
                self._send_json({"error": "Đường dẫn phòng không hợp lệ"}, status=404)
                return
            room_id = urllib.parse.unquote(parts[2])
            user = post_data.get("user", "")
            body_text = post_data.get("body", "")
            if not isinstance(user, str) or not user.strip() or len(user) > 60:
                self._send_json({"error": "Tên hiển thị không hợp lệ"}, status=400)
                return
            if not isinstance(body_text, str) or not body_text.strip() or len(body_text.strip()) > 2000:
                self._send_json({"error": "Tin nhắn phải có từ 1 đến 2000 ký tự"}, status=400)
                return
            user = user.strip()
            body_text = body_text.strip()
            initials = "".join(part[0].upper() for part in user.split()[:2]) or "U"
            tone = post_data.get("tone", "minh")
            if tone not in {"minh", "lan", "tuan", "huy"}:
                tone = "minh"

            rooms_resp = bridge.call_gateway(ACTION_LIST_ROOMS, {})
            if not rooms_resp or rooms_resp.get("status") != "ok":
                self._send_json({"error": "Gateway không kết nối"}, status=502)
                return
            room = next((r for r in rooms_resp.get("data", {}).get("rooms", []) if r.get("id") == room_id), None)
            if not room or room.get("privacy") != "Công khai":
                self._send_json({"error": "Phòng không tồn tại hoặc không công khai"}, status=404)
                return

            message, error = bridge.send_chat_to_node(room["server_node"], room_id, user, initials, tone, body_text)
            if message:
                self._send_json({"status": "ok", "message": message})
            else:
                self._send_json({"error": error or "Không thể gửi tin nhắn"}, status=502)
            return

        if parsed.path == "/api/infra/toggle-node":
            if not self._is_admin():
                self._send_json({"error": "Chức năng quản trị đã khóa"}, status=403)
                return
            node_name = post_data.get("node_name", "Chat Server 2")
            offline = post_data.get("offline", True)
            resp = bridge.call_gateway(ACTION_SIMULATE_NODE, {"node_name": node_name, "offline": offline})
            bridge.broadcast_sse("infra_update", {"node_name": node_name, "offline": offline})
            self._send_json(resp or {"status": "error"})
            return

        self.send_error(404, "Endpoint not found")

    def do_DELETE(self):
        if not self._same_origin():
            self._send_json({"error": "Yêu cầu khác nguồn bị từ chối"}, status=403)
            return
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "rooms"] or not parts[2]:
            self._send_json({"error": "Đường dẫn phòng không hợp lệ"}, status=404)
            return
        if not self._is_admin():
            self._send_json({"error": "Cần đăng nhập quản trị để xóa phòng"}, status=403)
            return
        room_id = urllib.parse.unquote(parts[2])
        if "/" in room_id or "\\" in room_id:
            self._send_json({"error": "Mã phòng không hợp lệ"}, status=400)
            return
        resp = bridge.call_gateway(ACTION_DELETE_ROOM, {
            "room_id": room_id,
            "admin_secret": admin_sessions.secret,
        })
        if resp is None:
            self._send_json({"error": "Gateway không kết nối"}, status=502)
        elif resp.get("status") != "ok":
            status = 404 if resp.get("message") == "Room does not exist" else 403
            self._send_json({"error": resp.get("message", "Không thể xóa phòng")}, status=status)
        else:
            bridge.broadcast_sse("room_deleted", {"room_id": room_id})
            self._send_json({"deleted": True, "room_id": room_id})

    def _handle_sse(self):
        import queue

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = queue.Queue(maxsize=100)
        bridge.add_sse_subscriber(q)

        # Initial keepalive comment
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            while bridge._running:
                try:
                    payload = q.get(timeout=15.0)
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat comment to keep connection alive
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            bridge.remove_sse_subscriber(q)

    def _send_json(self, data: Any, status: int = 200, headers: Optional[Dict[str, str]] = None):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)


def run_web_server(host: str = "0.0.0.0", port: int = 8080, gateway_host: str = "127.0.0.1", gateway_port: int = 5000):
    global bridge, admin_sessions
    admin_sessions = AdminSessions()
    bridge = BridgeManager(gateway_host=gateway_host, gateway_port=gateway_port)

    server = http.server.ThreadingHTTPServer((host, port), SocketRoomHTTPHandler)
    logging.info(f"SocketRoom Web Interface & Bridge running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down Web Server...")
    finally:
        bridge._running = False
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Web Server & Socket Bridge")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen (default 8080)")
    parser.add_argument("--gateway-host", default="127.0.0.1", help="Directory Server Host")
    parser.add_argument("--gateway-port", type=int, default=5000, help="Directory Server Port")
    args = parser.parse_args()

    run_web_server(
        host=args.host,
        port=args.port,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port,
    )


if __name__ == "__main__":
    main()
