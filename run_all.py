"""
SocketRoom Master Launcher
Runs the complete SocketRoom distributed cluster in one command:
- Directory Server (Gateway) on 127.0.0.1:5000
- Chat Server 1 (Node A) on 127.0.0.1:5001
- Chat Server 2 (Node B) on 127.0.0.1:5002
- Web Application & Socket Bridge on http://127.0.0.1:8080
- Optional: Automated demo clients ('Lan' and 'Tuấn')
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

if os.name == "nt":
    os.system("")

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = sys.executable


def stream_output(process, prefix, color):
    try:
        for line in iter(process.stdout.readline, ""):
            if line:
                print(f"{color}[{prefix}]{RESET} {line.strip()}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="SocketRoom Cluster Launcher")
    parser.add_argument("--with-bots", action="store_true", help="Automatically spawn Lan & Tuấn demo bots")
    parser.add_argument("--open-browser", action="store_true", default=True, help="Open browser automatically")
    parser.add_argument("--no-browser", action="store_false", dest="open_browser", help="Do not open browser")
    args = parser.parse_args()

    sub_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    print(f"\n{CYAN}{BOLD}==================================================================={RESET}")
    print(f"{CYAN}{BOLD}             KHỞI ĐỘNG HỆ THỐNG PHÂN TÁN SOCKETROOM               {RESET}")
    print(f"{CYAN}{BOLD}==================================================================={RESET}")
    print(f"{GREEN}✓ Python Runtime: {PYTHON_EXE}{RESET}")
    print(f"{GREEN}✓ Thư mục dự án: {SCRATCH_DIR}{RESET}\n")

    processes = []

    try:
        # 1. Directory Server (:5000)
        print(f"{BOLD}[1/4] Đang khởi động Directory Server (Gateway :5000)...{RESET}")
        p_dir = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRATCH_DIR, "directory_server.py"), "--port", "5000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=sub_env,
        )
        processes.append(("Directory Server", p_dir))
        threading.Thread(target=stream_output, args=(p_dir, "Gateway:5000", CYAN), daemon=True).start()
        time.sleep(1.2)

        # 2. Chat Server 1 (:5001)
        print(f"{BOLD}[2/4] Đang khởi động Chat Server 1 (Node A :5001)...{RESET}")
        p_node1 = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRATCH_DIR, "chat_server.py"), "--node", "1", "--port", "5001"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=sub_env,
        )
        processes.append(("Chat Server 1", p_node1))
        threading.Thread(target=stream_output, args=(p_node1, "Node-1:5001", GREEN), daemon=True).start()
        time.sleep(0.8)

        # 3. Chat Server 2 (:5002)
        print(f"{BOLD}[3/4] Đang khởi động Chat Server 2 (Node B :5002)...{RESET}")
        p_node2 = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRATCH_DIR, "chat_server.py"), "--node", "2", "--port", "5002"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=sub_env,
        )
        processes.append(("Chat Server 2", p_node2))
        threading.Thread(target=stream_output, args=(p_node2, "Node-2:5002", YELLOW), daemon=True).start()
        time.sleep(0.8)

        # 4. Web Application Server (:8080)
        print(f"{BOLD}[4/4] Đang khởi động Web Server & Socket Bridge (:8080)...{RESET}")
        p_web = subprocess.Popen(
            [PYTHON_EXE, os.path.join(SCRATCH_DIR, "web_server.py"), "--host", "127.0.0.1", "--port", "8080"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=sub_env,
        )
        processes.append(("Web Server", p_web))
        threading.Thread(target=stream_output, args=(p_web, "Web:8080", MAGENTA), daemon=True).start()
        time.sleep(1.0)

        # Optional: Demo bots
        if args.with_bots:
            print(f"{BOLD}[+] Đang khởi động Demo Clients ('Lan' và 'Tuấn')...{RESET}")
            p_bots = subprocess.Popen(
                [PYTHON_EXE, os.path.join(SCRATCH_DIR, "demo_clients.py")],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=sub_env,
            )
            processes.append(("Demo Bots", p_bots))
            threading.Thread(target=stream_output, args=(p_bots, "Bots", YELLOW), daemon=True).start()

        failed = [name for name, process in processes if process.poll() is not None]
        if failed:
            raise RuntimeError("Không thể khởi động: " + ", ".join(failed))
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=4) as response:
                if response.status != 200:
                    raise RuntimeError("Gateway chưa sẵn sàng")
        except Exception as exc:
            raise RuntimeError("Web/Gateway chưa sẵn sàng: " + str(exc)) from exc

        print(f"\n{GREEN}{BOLD}==================================================================={RESET}")
        print(f"{GREEN}{BOLD}  ✓ TẤT CẢ CÁC DỊCH VỤ ĐÃ KHỞI ĐỘNG THÀNH CÔNG!                     {RESET}")
        print(f"{GREEN}{BOLD}==================================================================={RESET}")
        print(f"  • {BOLD}Trình duyệt (trên máy bạn):{RESET}          {CYAN}http://127.0.0.1:8080{RESET}")
        print(f"  • {BOLD}Directory Server (Gateway):{RESET}          127.0.0.1:5000 (TCP)")
        print(f"  • {BOLD}Chat Server Node 1:{RESET}                 127.0.0.1:5001 (TCP)")
        print(f"  • {BOLD}Chat Server Node 2:{RESET}                 127.0.0.1:5002 (TCP)")
        print(f"  • {BOLD}Lưu trữ CSDL:{RESET}                       {SCRATCH_DIR}\\socketroom.db (SQLite)")
        print(f"  • {BOLD}Mã quản trị:{RESET}                        {SCRATCH_DIR}\\admin_secret.txt")
        print(f"-------------------------------------------------------------------")
        print(f"{YELLOW}Hướng dẫn chạy Client bổ sung (mở thêm cửa sổ Terminal):{RESET}")
        print(f"  {CYAN}python client_cli.py --name \"Huy\"{RESET}")
        print(f"  {CYAN}python client_cli.py --name \"Lan\"{RESET}")
        print(f"  {CYAN}python demo_clients.py{RESET}")
        print(f"-------------------------------------------------------------------")
        print(f"{DIM}Nhấn Ctrl+C để dừng toàn bộ hệ thống.{RESET}\n")

        if args.open_browser:
            try:
                webbrowser.open("http://127.0.0.1:8080")
            except Exception:
                pass

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}Đang dừng toàn bộ các tiến trình server...{RESET}")
    finally:
        for name, p in reversed(processes):
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print(f"{GREEN}✓ Đã dừng SocketRoom an toàn.{RESET}\n")


if __name__ == "__main__":
    main()
