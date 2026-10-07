"""Start SocketRoom locally and publish a temporary Cloudflare Quick Tunnel link."""
import argparse
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TUNNEL = ROOT / "tools" / "cloudflared.exe"
LINK_FILE = ROOT / "public_link.txt"
PID_FILE = ROOT / "launcher.pid"
STATE_FILE = ROOT / "launcher_state.json"
SERVICES = [
    ("directory", "directory_server.py", []),
    ("chat1", "chat_server.py", ["--node", "1"]),
    ("chat2", "chat_server.py", ["--node", "2"]),
    ("web", "web_server.py", ["--host", "127.0.0.1"]),
]
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def wait_for_port(port, process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Dịch vụ cổng {port} đã dừng. Xem thư mục logs.")
        with socket.socket() as sock:
            sock.settimeout(.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(.2)
    raise RuntimeError(f"Dịch vụ cổng {port} không khởi động. Xem thư mục logs.")


def wait_for_health(process, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Web server đã dừng. Xem logs/web.log.")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.3)
    raise RuntimeError("Gateway chưa sẵn sàng. Xem thư mục logs.")


def atomic_write(path, text, encoding="utf-8"):
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding=encoding)
    os.replace(temporary, path)


def wait_for_public_health(link, processes, tunnel, timeout=180):
    """Publish only a link that reaches this chat application over HTTPS."""
    deadline = time.monotonic() + timeout
    last_error = "Chưa có phản hồi từ link công khai"
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in [*processes, tunnel]):
            raise RuntimeError("Một dịch vụ đã dừng khi kiểm tra link; xem logs.")
        try:
            request = urllib.request.Request(link + "/api/health",
                                             headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read(8192).decode("utf-8"))
                if response.status == 200 and isinstance(data, dict) and data.get("status") == "ok":
                    return
                last_error = f"HTTP {response.status}: ứng dụng chưa sẵn sàng"
        except (OSError, urllib.error.URLError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(.75)
    raise RuntimeError("Link công khai chưa truy cập được. " + last_error +
                       ". Xem logs/tunnel.log và kiểm tra Internet.")


def pipe_lines(process, output, log_path):
    try:
        with log_path.open("a", encoding="utf-8") as log:
            for line in iter(process.stdout.readline, ""):
                log.write(line)
                log.flush()
                output.put(line.rstrip())
    finally:
        output.put(None)


def main():
    parser = argparse.ArgumentParser(description="Start SocketRoom with a public share link")
    parser.add_argument("--local-only", action="store_true", help="Run the chat locally without opening an Internet tunnel")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("Gói chia sẻ này được chuẩn bị cho Windows.")
    if not args.local_only and not TUNNEL.is_file():
        raise RuntimeError("Thiếu tools/cloudflared.exe. Hãy giải nén đầy đủ gói SocketRoom.")
    for port in (5000, 5001, 5002, 8080):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Cổng {port} đang được dùng. Hãy đóng phiên SocketRoom cũ.")

    LINK_FILE.unlink(missing_ok=True)
    atomic_write(STATE_FILE, json.dumps({"pid": os.getpid(), "started_at": time.time(),
                                         "script": str(Path(__file__).resolve())}) + "\n")
    atomic_write(PID_FILE, str(os.getpid()) + "\n", encoding="ascii")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if not args.local_only:
        environment["SOCKETROOM_PUBLIC_TUNNEL"] = "1"
    processes = []
    log_files = []
    tunnel = None
    try:
        for (name, script, service_args), port in zip(SERVICES, (5000, 5001, 5002, 8080)):
            log = (log_dir / f"{name}.log").open("a", encoding="utf-8")
            log_files.append(log)
            process = subprocess.Popen([sys.executable, str(ROOT / script), *service_args], cwd=ROOT,
                                       stdout=log, stderr=subprocess.STDOUT, env=environment,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(process)
            wait_for_port(port, process)
        wait_for_health(processes[-1])
        print("SocketRoom đã chạy tại http://127.0.0.1:8080", flush=True)
        print("Mã quản trị nằm trong " + str(ROOT / "admin_secret.txt"), flush=True)
        if args.local_only:
            print("Chế độ thử cục bộ. Nhấn Ctrl+C để dừng.", flush=True)
            while True:
                for process in processes:
                    if process.poll() is not None:
                        raise RuntimeError("Một dịch vụ đã dừng; xem thư mục logs.")
                time.sleep(1)
        print("Đang tạo liên kết công khai...", flush=True)

        tunnel = subprocess.Popen([str(TUNNEL), "tunnel", "--url", "http://127.0.0.1:8080"],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1,
                                  env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        output = queue.Queue()
        threading.Thread(target=pipe_lines, args=(tunnel, output, log_dir / 'tunnel.log'), daemon=True).start()
        deadline = time.monotonic() + 45
        link = None
        while time.monotonic() < deadline and tunnel.poll() is None:
            try:
                line = output.get(timeout=.5)
            except queue.Empty:
                continue
            if line is None:
                break
            print(line, flush=True)
            match = URL_PATTERN.search(line)
            if match:
                link = match.group(0)
                break
        if not link:
            raise RuntimeError("Không tạo được link công khai. Kiểm tra Internet và log cloudflared.")

        print("Đang kiểm tra link từ Internet, vui lòng đợi...", flush=True)
        wait_for_public_health(link, processes, tunnel)
        atomic_write(LINK_FILE, link + "\n")
        print("\nLINK PHÒNG CHAT: " + link, flush=True)
        print("Link cũng được lưu trong public_link.txt. Giữ máy đang bật để mọi người truy cập.", flush=True)
        print("Dùng stop_public.bat để dừng chia sẻ.\n", flush=True)

        while True:
            for process in [*processes, tunnel]:
                if process.poll() is not None:
                    raise RuntimeError("Một dịch vụ đã dừng; xem thư mục logs.")
            try:
                while True:
                    line = output.get_nowait()
                    if line:
                        print(line, flush=True)
            except queue.Empty:
                pass
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nĐang dừng SocketRoom...", flush=True)
    finally:
        LINK_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
        STATE_FILE.unlink(missing_ok=True)
        for process in reversed([*processes, *([tunnel] if tunnel else [])]):
            if process.poll() is None:
                process.terminate()
        for process in reversed([*processes, *([tunnel] if tunnel else [])]):
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        for log in log_files:
            log.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("LỖI: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)

