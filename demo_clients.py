"""
SocketRoom Dual Automated Demo Clients
Spawns two clients ('Lan' and 'Tuấn') that connect over real TCP sockets,
exchange messages in real time, and respond to other active users (e.g., 'Minh Anh').
"""

import argparse
import random
import sys
import threading
import time
from typing import Any, Dict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from socket_client import SocketRoomClient

RESPONSES = [
    "Framing 4 byte hoạt động rất tốt, không bị dính gói (TCP stream chunking) nữa rồi!",
    "SQLite đang lưu mọi tin nhắn vào socketroom.db, reload lại trang vẫn còn nguyên.",
    "Bạn thử mở thêm một tab trình duyệt hoặc một cửa sổ terminal khác để xem tin nhắn xuất hiện tức thì nhé.",
    "Chat Server 1 và 2 đều gửi heartbeat về Directory Server port 5000 mỗi 3.5 giây.",
    "Nếu ngắt Server 2 từ nút 'Hạ tầng', Directory Server sẽ tự động điều phối phòng mới sang Server 1.",
    "Kiến trúc phân tán này giúp chịu tải tốt hơn nhiều so với việc dồn toàn bộ room vào 1 server đơn lẻ.",
]


def run_demo(gateway_host: str = "127.0.0.1", gateway_port: int = 5000, room_id: str = "python", auto_chat: bool = True):
    print(f"\n[DEMO] Khởi tạo 2 client tự động ('Lan' và 'Tuấn')...")

    client_lan = SocketRoomClient("Lan", gateway_host, gateway_port, initials="LN", tone="lan")
    client_tuan = SocketRoomClient("Tuấn", gateway_host, gateway_port, initials="T", tone="tuan")

    # 1. Login both clients
    login_lan = client_lan.login()
    login_tuan = client_tuan.login()

    if not login_lan or not login_tuan:
        print("[DEMO ERROR] Không thể đăng nhập vào Directory Server! Hãy chắc chắn server đang chạy.")
        return

    print("[DEMO] ✓ Lan và Tuấn đã đăng nhập Directory Server thành công!")

    # Determine node for room
    rooms = client_lan.get_rooms()
    target_room = next((r for r in rooms if r["id"] == room_id), None)
    if not target_room:
        target_room = rooms[0] if rooms else None

    if not target_room:
        print("[DEMO ERROR] Không tìm thấy phòng chat!")
        return

    node_name = target_room["server_node"]
    node_port = 5002 if "2" in node_name else 5001

    print(f"[DEMO] Tham gia phòng #{target_room['name']} trên {node_name} (port {node_port})...")

    # Setup callback for Lan to reply when Minh Anh or external user chats
    def lan_on_message(data: Dict[str, Any]):
        sender = data.get("user")
        body = data.get("body", "")
        print(f"[TCP Event -> Lan nhận được từ {sender}]: {body}")

        if sender not in ("Lan", "Tuấn", "Hệ thống"):
            # Reply after a short delay
            def _reply():
                time.sleep(1.8)
                reply_text = f"Chào {sender}! {random.choice(RESPONSES)}"
                print(f"[TCP Event -> Lan gửi phản hồi]: {reply_text}")
                client_lan.send_chat_message(reply_text)

            threading.Thread(target=_reply, daemon=True).start()

    def tuan_on_message(data: Dict[str, Any]):
        sender = data.get("user")
        body = data.get("body", "")
        print(f"[TCP Event -> Tuấn nhận được từ {sender}]: {body}")

    client_lan.on_message = lan_on_message
    client_tuan.on_message = tuan_on_message

    # Join room on the node
    client_lan.join_room(target_room["id"], node_host="127.0.0.1", node_port=node_port)
    time.sleep(0.5)
    client_tuan.join_room(target_room["id"], node_host="127.0.0.1", node_port=node_port)
    time.sleep(0.5)

    print(f"[DEMO] ✓ Cả hai client đã kết nối Socket TCP tới {node_name} và vào phòng #{target_room['name']}!")

    if auto_chat:
        print("[DEMO] Bắt đầu phiên trao đổi mẫu giữa Lan và Tuấn qua Socket...")
        time.sleep(1.0)
        client_lan.send_chat_message("Chào Tuấn, client socket bên bạn kết nối ổn định không?")
        time.sleep(2.0)
        client_tuan.send_chat_message("Chào Lan! Kết nối TCP rất mượt, latency < 5ms.")
        time.sleep(2.0)
        client_lan.send_chat_message("Tuyệt vời, sẵn sàng đón các client khác tham gia rồi.")

    print(f"\n[DEMO] Hai client ('Lan' và 'Tuấn') đang trực tuyến và lắng nghe trên socket.")
    print("[DEMO] Nhấn Ctrl+C để dừng demo clients.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[DEMO] Đang ngắt kết nối các client...")
        client_lan.disconnect_node()
        client_tuan.disconnect_node()
        print("[DEMO] Đã dừng.")


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Automated Demo Clients")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway host")
    parser.add_argument("--port", type=int, default=5000, help="Gateway port")
    parser.add_argument("--room", default="python", help="Room id to join")
    parser.add_argument("--no-initial-chat", action="store_true", help="Skip initial conversation")
    args = parser.parse_args()

    run_demo(
        gateway_host=args.host,
        gateway_port=args.port,
        room_id=args.room,
        auto_chat=not args.no_initial_chat,
    )


if __name__ == "__main__":
    main()
