"""Isolated public-chat and host-admin integration test for SocketRoom.

Copies source into a temporary project and uses independently chosen loopback ports.
It never starts or modifies the live SocketRoom directory.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from protocol import ACTION_DELETE_ROOM, recv_packet, send_packet
from database import Database

ROOT = Path(__file__).resolve().parent


def free_ports(count: int) -> list[int]:
    ports = set()
    while len(ports) < count:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            ports.add(sock.getsockname()[1])
    return list(ports)


class Client:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            response = self.opener.open(req, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {"raw": raw.decode("utf-8", errors="replace")}
            return response.status, data, response.headers


def expect(client: Client, method: str, path: str, status: int, payload=None):
    got, data, headers = client.request(method, path, payload)
    assert got == status, f"{method} {path}: expected {status}, got {got}: {data}"
    return data, headers


def wait_port(port: int, process: subprocess.Popen, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{process.args[1]} stopped before port {port} was ready")
        with socket.socket() as sock:
            sock.settimeout(.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(.1)
    raise RuntimeError(f"Port {port} did not become ready")


def launch(project: Path, ports: list[int], log):
    directory_port, node1_port, node2_port, web_port = ports
    environment = dict(
        os.environ,
        PYTHONUNBUFFERED="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        SOCKETROOM_DB_PATH=str(project / "isolated.db"),
        SOCKETROOM_NODE1_PORT=str(node1_port),
        SOCKETROOM_NODE2_PORT=str(node2_port),
    )
    services = [
        ("directory_server.py", ["--port", str(directory_port)], directory_port),
        ("chat_server.py", ["--node", "1", "--port", str(node1_port), "--dir-port", str(directory_port)], node1_port),
        ("chat_server.py", ["--node", "2", "--port", str(node2_port), "--dir-port", str(directory_port)], node2_port),
        ("web_server.py", ["--host", "127.0.0.1", "--port", str(web_port),
                           "--gateway-port", str(directory_port)], web_port),
    ]
    processes = []
    try:
        for script, args, port in services:
            proc = subprocess.Popen([sys.executable, str(project / script), *args],
                                    cwd=project, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT)
            processes.append(proc)
            wait_port(port, proc)
        client = Client(f"http://127.0.0.1:{web_port}")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if client.request("GET", "/api/health")[0] == 200:
                return processes
            time.sleep(.2)
        raise RuntimeError("Health endpoint did not become ready")
    except Exception:
        stop(processes)
        raise


def stop(processes):
    for proc in reversed(processes):
        if proc.poll() is None:
            proc.terminate()
    for proc in reversed(processes):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def gateway_delete(directory_port: int, room_id: str, secret: str | None = None):
    data = {"room_id": room_id}
    if secret is not None:
        data["admin_secret"] = secret
    with socket.create_connection(("127.0.0.1", directory_port), timeout=4) as sock:
        sock.settimeout(4)
        send_packet(sock, {"action": ACTION_DELETE_ROOM, "data": data})
        return recv_packet(sock)


def check_final_room_reseed(project: Path):
    db_path = project / "seed-delete.db"
    db = Database(str(db_path))
    room_ids = [room["id"] for room in db.get_rooms()]
    assert room_ids, "new DB did not seed its default rooms"
    for room_id in room_ids:
        assert db.delete_room(room_id) is not None
    assert db.get_rooms() == []
    assert Database(str(db_path)).get_rooms() == [], "deleted defaults reappeared after DB reopen"


def wait_secret(path: Path) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            return path.read_text(encoding="utf-8").strip()
        time.sleep(.1)
    raise AssertionError("Host admin secret was not written to local admin_secret.txt")


def run_browser(base: str, room_id: str, room_name: str, secret: str, project: Path):
    chrome_paths = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    chrome = next((path for path in chrome_paths if path.is_file()), None)
    node = shutil.which("node")
    if not chrome or not node:
        print("SKIP: Chrome/CDP UI test (Chrome or Node is unavailable)")
        return
    environment = dict(os.environ, SOCKETROOM_TEST_SECRET=secret)
    subprocess.run([node, str(ROOT / "test_full_version_browser.js"), base, room_id, room_name,
                    str(chrome), str(project / "chrome-profile")],
                   cwd=project, env=environment, check=True, timeout=90)


def main():
    ports = free_ports(4)
    with tempfile.TemporaryDirectory(prefix="socketroom-full-", dir=ROOT.parent) as temp:
        project = Path(temp).resolve()
        project.relative_to(ROOT.parent.resolve())
        for source in ROOT.glob("*.py"):
            if not source.name.startswith("test_"):
                shutil.copy2(source, project / source.name)
        shutil.copytree(ROOT / "static", project / "static")
        log_path = project / "services.log"
        with log_path.open("w", encoding="utf-8") as log:
            processes = []
            try:
                processes = launch(project, ports, log)
                base = f"http://127.0.0.1:{ports[3]}"
                guest = Client(base)
                admin = Client(base)
                assert expect(guest, "GET", "/api/admin/me", 200)[0]["admin"] is False
                assert expect(guest, "DELETE", "/api/rooms/python", 403)[0]["error"]
                assert expect(guest, "POST", "/api/admin/login", 401,
                              {"secret": "definitely-wrong"})[0]
                secret = wait_secret(project / "admin_secret.txt")
                page = urllib.request.urlopen(base + "/", timeout=8).read().decode("utf-8")
                assert secret not in page, "Host secret leaked to the guest HTML"
                assert guest.request("GET", "/admin_secret.txt")[0] == 404
                assert guest.request("GET", "/%2e%2e/admin_secret.txt")[0] == 404
                login_payload, _ = expect(guest, "POST", "/api/login", 200, {"name": "Guest"})
                assert secret not in json.dumps(login_payload, ensure_ascii=False)
                check_final_room_reseed(project)

                room, _ = expect(guest, "POST", "/api/rooms", 200,
                                 {"name": "delete-target", "description": "Deletion integration test", "server": "auto"})
                target_id = room["room"]["id"]
                sent, _ = expect(guest, "POST", f"/api/rooms/{target_id}/send", 200,
                                 {"user": "Guest", "body": "Must be removed with room"})
                assert sent["message"]["room_id"] == target_id
                survivor, _ = expect(guest, "POST", "/api/rooms", 200,
                                     {"name": "keep-target", "description": "Persistence integration test", "server": "auto"})
                survivor_id = survivor["room"]["id"]
                expect(guest, "POST", f"/api/rooms/{survivor_id}/send", 200,
                       {"user": "Guest", "body": "Must survive room deletion and restart"})
                assert expect(guest, "DELETE", f"/api/rooms/{target_id}", 403)[0]["error"]
                for rejected in (gateway_delete(ports[0], target_id), gateway_delete(ports[0], target_id, "wrong-secret")):
                    assert rejected["action"] == "DELETE_ROOM_RESP" and rejected["status"] == "error", rejected
                assert expect(guest, "GET", f"/api/rooms/{target_id}/messages", 200)[0]["messages"]

                login, headers = expect(admin, "POST", "/api/admin/login", 200, {"secret": secret})
                assert login.get("admin") is True
                cookie = headers.get("Set-Cookie", "").lower()
                assert "httponly" in cookie and "samesite=strict" in cookie, cookie
                assert expect(admin, "GET", "/api/admin/me", 200)[0]["admin"] is True
                assert expect(admin, "DELETE", "/api/rooms/unknown-room", 404)[0]
                deleted, _ = expect(admin, "DELETE", f"/api/rooms/{target_id}", 200)
                assert deleted.get("deleted") is True and deleted.get("room_id") == target_id
                assert expect(guest, "GET", f"/api/rooms/{target_id}/messages", 404)[0]
                listed, _ = expect(guest, "GET", "/api/rooms", 200)
                assert target_id not in [item["id"] for item in listed["rooms"]]
                assert survivor_id in [item["id"] for item in listed["rooms"]]
                with contextlib.closing(sqlite3.connect(project / "isolated.db")) as db:
                    assert db.execute("SELECT COUNT(*) FROM messages WHERE room_id=?", (target_id,)).fetchone()[0] == 0
                    assert db.execute("SELECT COUNT(*) FROM messages WHERE room_id=?", (survivor_id,)).fetchone()[0] == 1
                logout, _ = expect(admin, "POST", "/api/admin/logout", 200, {})
                assert logout.get("admin") is False
                assert expect(admin, "GET", "/api/admin/me", 200)[0]["admin"] is False
                assert expect(admin, "DELETE", f"/api/rooms/{survivor_id}", 403)[0]

                browser_room, _ = expect(guest, "POST", "/api/rooms", 200,
                                         {"name": "browser-test", "description": "Chrome UI integration test", "server": "auto"})
                run_browser(base, browser_room["room"]["id"], browser_room["room"]["name"], secret, project)

                stop(processes)
                processes = []
                processes = launch(project, ports, log)
                assert (project / "admin_secret.txt").read_text(encoding="utf-8").strip() == secret
                guest = Client(base)
                history, _ = expect(guest, "GET", f"/api/rooms/{survivor_id}/messages", 200)
                assert any(msg["body"] == "Must survive room deletion and restart" for msg in history["messages"])
                assert expect(guest, "GET", f"/api/rooms/{target_id}/messages", 404)[0]
                print("PASS: isolated ports/DB, admin cookie/login/logout, unauthorized deletion, "
                      "room-message cascade, browser UI, restart persistence")
            except Exception:
                log.flush()
                print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:])
                raise
            finally:
                stop(processes)


if __name__ == "__main__":
    main()

