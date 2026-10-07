"""
SocketRoom Interactive CLI Client
Allows pair programming, live multi-client testing, and room navigation
directly from the terminal.
"""

import argparse
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from socket_client import SocketRoomClient

# ANSI colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Enable VT100 colors and UTF-8 on Windows
if os.name == "nt":
    os.system("")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class CLIClient:
    def __init__(self, username: str, gateway_host: str = "127.0.0.1", gateway_port: int = 5000, tone: str = "lan"):
        self.username = username
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.tone = tone

        self.client = SocketRoomClient(
            username=username,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            tone=tone,
        )

        self.rooms: List[Dict[str, Any]] = []
        self.current_room: Optional[Dict[str, Any]] = None
        # Chat node name -> TCP port, as reported by the Directory Server at login.
        self.node_ports: Dict[str, int] = {}

    def on_incoming_message(self, data: Dict[str, Any]) -> None:
        room_id = data.get("room_id")
        if room_id and (not self.current_room or room_id != self.current_room.get("id")):
            return
        sender = data.get("user", "Ẩn danh")
        time_str = data.get("time", "")
        body = data.get("body", "")

        # Format sender color based on name
        color = CYAN if sender == self.username else (MAGENTA if sender == "Lan" else (BLUE if sender == "Tuấn" else GREEN))
        print(f"\r{DIM}[{time_str}]{RESET} {color}{BOLD}{sender}:{RESET} {body}\n> ", end="", flush=True)

    def on_presence_event(self, data: Dict[str, Any]) -> None:
        if "user" in data and data.get("status") == "joined":
            user = data.get("user")
            if user not in (self.username, "WebGateway"):
                print(f"\r{DIM}[Thông báo]{RESET} {YELLOW}{user}{RESET} vừa vào phòng.\n> ", end="", flush=True)

    def on_error_event(self, message: str) -> None:
        print(f"\r{RED}[Lỗi] {message}{RESET}\n> ", end="", flush=True)

    def on_room_deleted(self, data: Dict[str, Any]) -> None:
        room_id = data.get("room_id")
        self.rooms = [room for room in self.rooms if room.get("id") != room_id]
        if self.current_room and self.current_room.get("id") == room_id:
            self.current_room = None
            print("\r[Thông báo] Phòng hiện tại đã bị xóa. Dùng /rooms để xem phòng còn lại.\n> ", end="", flush=True)

    def run(self) -> None:
        print(f"\n{CYAN}{BOLD}=== SocketRoom CLI Client ==={RESET}")
        print(f"Đang kết nối Directory Server ({self.gateway_host}:{self.gateway_port})...")

        login_res = self.client.login()
        if not login_res:
            print(f"{RED}Không thể kết nối Directory Server tại {self.gateway_host}:{self.gateway_port}. Hãy kiểm tra xem server đã chạy chưa!{RESET}")
            return

        print(f"{GREEN}✓ Đăng nhập thành công với tên {BOLD}{self.username}{RESET}!")
        self.rooms = login_res.get("rooms", [])
        self.node_ports = {
            node.get("name"): node.get("port")
            for node in login_res.get("nodes", [])
            if node.get("name") and isinstance(node.get("port"), int)
        }

        self.client.on_message = self.on_incoming_message
        self.client.on_presence = self.on_presence_event
        self.client.on_room_deleted = self.on_room_deleted
        self.client.on_error = self.on_error_event

        self.list_rooms()

        # Auto join first room or 'python'
        default_room = next((r for r in self.rooms if r["id"] == "python"), self.rooms[0] if self.rooms else None)
        if default_room:
            self.switch_room(default_room["id"])

        self.print_help()

        # Input loop
        while True:
            try:
                user_input = input("> ").strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    parts = user_input.split(maxsplit=2)
                    cmd = parts[0].lower()

                    if cmd in ("/exit", "/quit"):
                        print(f"{YELLOW}Đang ngắt kết nối... Tạm biệt!{RESET}")
                        self.client.disconnect_node()
                        break
                    elif cmd in ("/help", "/?"):
                        self.print_help()
                    elif cmd == "/rooms":
                        self.list_rooms()
                    elif cmd == "/join":
                        if len(parts) > 1:
                            self.switch_room(parts[1])
                        else:
                            print(f"{YELLOW}Cách dùng: /join <mã_phòng> (ví dụ: /join python){RESET}")
                    elif cmd == "/create":
                        if len(parts) > 1:
                            name = parts[1]
                            desc = parts[2] if len(parts) > 2 else f"Phòng {name}"
                            self.create_room(name, desc)
                        else:
                            print(f"{YELLOW}Cách dùng: /create <tên_phòng> [mô tả]{RESET}")
                    elif cmd == "/history":
                        if self.current_room:
                            self.show_history(self.current_room["id"])
                    else:
                        print(f"{RED}Lệnh không hợp lệ. Gõ /help để xem danh sách lệnh.{RESET}")
                else:
                    if not self.current_room:
                        print(f"{YELLOW}Bạn chưa vào phòng nào. Dùng /join <mã_phòng>{RESET}")
                        continue
                    self.client.send_chat_message(user_input)
            except (KeyboardInterrupt, EOFError):
                print(f"\n{YELLOW}Tạm biệt!{RESET}")
                self.client.disconnect_node()
                break

    def list_rooms(self) -> None:
        self.rooms = self.client.get_rooms()
        print(f"\n{BOLD}Danh sách phòng chat:{RESET}")
        print(f"{'Mã':<12} {'Tên phòng':<22} {'Máy chủ phục vụ':<16} {'Mô tả'}")
        print("-" * 75)
        for r in self.rooms:
            mark = f"{GREEN}* {RESET}" if self.current_room and self.current_room["id"] == r["id"] else "  "
            print(f"{mark}{r['id']:<10} #{r['name']:<21} {r['server_node']:<16} {r['description']}")
        print()

    def switch_room(self, room_id: str) -> None:
        target = next((r for r in self.rooms if r["id"] == room_id or r["name"] == room_id), None)
        if not target:
            # Refresh rooms and check again
            self.rooms = self.client.get_rooms()
            target = next((r for r in self.rooms if r["id"] == room_id or r["name"] == room_id), None)
            if not target:
                print(f"{RED}Không tìm thấy phòng '{room_id}'{RESET}")
                return

        node_name = target["server_node"]
        node_port = self.node_ports.get(node_name) or (5002 if "2" in node_name else 5001)

        print(f"\n{YELLOW}Đang chuyển sang #{target['name']} ({node_name} : {node_port})...{RESET}")
        ok = self.client.join_room(target["id"], node_host="127.0.0.1", node_port=node_port)
        if ok:
            self.current_room = target
            print(f"{GREEN}✓ Đã kết nối và vào #{target['name']}!{RESET}")
            self.show_history(target["id"])
        else:
            print(f"{RED}Không thể kết nối tới {node_name} (: {node_port})! Server có thể đang gián đoạn.{RESET}")

    def show_history(self, room_id: str) -> None:
        history = self.client.get_history(room_id)
        if history:
            print(f"{DIM}--- Lịch sử tin nhắn gần đây ({len(history)} tin từ SQLite) ---{RESET}")
            for msg in history:
                u = msg.get("user", "")
                t = msg.get("time", "")
                b = msg.get("body", "")
                c = CYAN if u == self.username else (MAGENTA if u == "Lan" else (BLUE if u == "Tuấn" else GREEN))
                print(f"{DIM}[{t}]{RESET} {c}{u}:{RESET} {b}")
            print(f"{DIM}-------------------------------------------------------{RESET}\n")

    def create_room(self, name: str, description: str) -> None:
        try:
            room = self.client.create_room(name, description, server="auto")
            if room:
                print(f"{GREEN}✓ Tạo phòng #{room['name']} thành công trên {room['server_node']}!{RESET}")
                self.list_rooms()
                self.switch_room(room["id"])
        except Exception as e:
            print(f"{RED}Tạo phòng thất bại: {e}{RESET}")

    def print_help(self) -> None:
        print(f"{DIM}Lệnh hỗ trợ: /rooms (xem phòng) | /join <mã> (vào phòng) | /create <tên> (tạo) | /history | /quit{RESET}")


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Terminal Client")
    parser.add_argument("--name", default="Minh Anh", help="Display name (default 'Minh Anh')")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway Host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Gateway Port (default 5000)")
    parser.add_argument("--tone", default="minh", help="Color tone (minh, lan, tuan, huy)")
    args = parser.parse_args()

    client = CLIClient(username=args.name, gateway_host=args.host, gateway_port=args.port, tone=args.tone)
    client.run()


if __name__ == "__main__":
    main()
