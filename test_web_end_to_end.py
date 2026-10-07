"""Full HTTP and socket smoke test using a disposable project copy."""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8080"


def request(path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": "application/json"} if body is not None else {},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def wait_ready(processes):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"Server stopped unexpectedly: {process.args}")
        try:
            if request("/api/health")[0] == 200:
                return
        except Exception:
            pass
        time.sleep(.2)
    raise RuntimeError("Timed out waiting for web and gateway servers")


def main():
    for port in (5000, 5001, 5002, 8080):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is already occupied")
    with tempfile.TemporaryDirectory(prefix="socketroom-e2e-") as temp:
        project = Path(temp)
        for file in ROOT.glob("*.py"):
            if file.name != Path(__file__).name:
                shutil.copy2(file, project / file.name)
        shutil.copytree(ROOT / "static", project / "static")
        logs = open(project / "server.log", "w", encoding="utf-8")
        processes = []
        try:
            env = dict(__import__("os").environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1")
            for script, args in [
                ("directory_server.py", []),
                ("chat_server.py", ["--node", "1"]),
                ("chat_server.py", ["--node", "2"]),
                ("web_server.py", []),
            ]:
                proc = subprocess.Popen([sys.executable, str(project / script), *args], cwd=project,
                                        stdout=logs, stderr=subprocess.STDOUT, env=env)
                processes.append(proc)
                time.sleep(.35)
            wait_ready(processes)
            status, login = request("/api/login", {"name": "An"})
            assert status == 200 and login["user"]["name"] == "An", (status, login)
            assert "network" not in [r["id"] for r in login["rooms"]], login
            assert "python" in [r["id"] for r in login["rooms"]], login
            status, _ = request("/api/login", {"name": "Bình"})
            assert status == 200
            status, history = request("/api/rooms/python/messages")
            assert status == 200 and all(m["room_id"] == "python" for m in history["messages"])
            status, sent_a = request("/api/rooms/python/send", {"user": "An", "body": "Tin thử từ An"})
            assert status == 200 and sent_a["message"]["id"] and sent_a["message"]["room_id"] == "python", (status, sent_a)
            status, sent_b = request("/api/rooms/python/send", {"user": "Bình", "body": "Bình đã nhận"})
            assert status == 200 and sent_b["message"]["id"] > sent_a["message"]["id"], (status, sent_b)
            status, history = request("/api/rooms/python/messages")
            assert status == 200 and [m["body"] for m in history["messages"][-2:]] == ["Tin thử từ An", "Bình đã nhận"], history
            status, created = request("/api/rooms", {"name": "phong-moi", "description": "Thử phòng mới", "server": "auto"})
            assert status == 200 and created["room"]["id"], (status, created)
            room_id = created["room"]["id"]
            assert room_id in [r["id"] for r in request("/api/rooms")[1]["rooms"]]
            public_mode = env.get("SOCKETROOM_PUBLIC_TUNNEL") == "1"
            if public_mode:
                assert request("/api/events")[0] == 404
                status, sent_new = request(f"/api/rooms/{room_id}/send", {"user": "An", "body": "Tin trong phòng mới"})
                assert status == 200 and sent_new["message"]["room_id"] == room_id, (status, sent_new)
            else:
                time.sleep(2.3)
                with urllib.request.urlopen(BASE + "/api/events", timeout=6) as events:
                    assert events.readline().startswith(b": connected")
                    status, sent_new = request(f"/api/rooms/{room_id}/send", {"user": "An", "body": "Tin trong phòng mới"})
                    assert status == 200 and sent_new["message"]["room_id"] == room_id, (status, sent_new)
                    found = False
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        line = events.readline()
                        if line.startswith(b"data: ") and str(sent_new["message"]["id"]).encode() in line:
                            found = True
                            break
                    assert found, "New-room SSE broadcast was not delivered"
            status, history = request(f"/api/rooms/{room_id}/messages")
            assert status == 200 and history["messages"][-1]["body"] == "Tin trong phòng mới"
            assert request("/api/rooms/network/messages")[0] == 404
            assert request("/api/rooms/network/send", {"user": "An", "body": "blocked"})[0] == 404
            assert request("/api/rooms/does-not-exist/send", {"user": "An", "body": "blocked"})[0] == 404
            assert request("/api/infra/status")[0] == 403
            assert request("/api/infra/toggle-node", {"offline": True})[0] == 403
            assert request("/api/rooms/python/send", {"user": "An", "body": "x" * 2001})[0] == 400
            with urllib.request.urlopen(BASE + "/?room=" + room_id, timeout=5) as page:
                assert page.status == 200 and b"socketroom-preview" in page.read()
            print("PASS: login, two senders, persisted history, new room, private/admin routes, validation, deep link; " + ("public polling mode" if public_mode else "local SSE mode"))
        except Exception:
            logs.flush()
            print((project / "server.log").read_text(encoding="utf-8", errors="replace")[-10000:])
            raise
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
            for proc in reversed(processes):
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
            logs.close()


if __name__ == "__main__":
    main()
