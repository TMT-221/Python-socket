import tempfile
import threading
import unittest
from unittest.mock import patch

from directory_server import DirectoryServer
from protocol import ACTION_CREATE_ROOM, ACTION_LOG_MESSAGE, ACTION_SIMULATE_NODE, ACTION_SYNC_HISTORY


class DirectoryFixTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = DirectoryServer(db_path=self.temp_dir.name + "/test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def dispatch(self, action, data):
        return self.server._dispatch(action, data, None, ("127.0.0.1", 12345))

    def test_simulate_node_completes_without_deadlock(self):
        result = []
        worker = threading.Thread(
            target=lambda: result.append(self.dispatch(ACTION_SIMULATE_NODE, {
                "node_name": "Chat Server 2", "offline": True
            })),
            daemon=True,
        )
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive(), "node toggle deadlocked")
        self.assertEqual(result[0]["status"], "ok")
        self.assertFalse(result[0]["is_active"])

    def test_expired_heartbeat_completes_without_deadlock(self):
        self.server.nodes["Chat Server 1"]["last_heartbeat"] = 0
        self.server._running = True
        with patch("directory_server.time.sleep", side_effect=lambda _: setattr(self.server, "_running", False)):
            worker = threading.Thread(target=self.server._health_monitor, daemon=True)
            worker.start()
            worker.join(2)
        self.assertFalse(worker.is_alive(), "health monitor deadlocked")
        self.assertFalse(self.server.nodes["Chat Server 1"]["is_active"])

    def test_unknown_room_cannot_receive_or_log_messages(self):
        history = self.dispatch(ACTION_SYNC_HISTORY, {"room_id": "missing"})
        saved = self.dispatch(ACTION_LOG_MESSAGE, {
            "room_id": "missing", "user": "Test", "body": "hello"
        })
        self.assertEqual(history["status"], "error")
        self.assertEqual(saved["status"], "error")
        self.assertEqual(self.server.db.get_messages("missing"), [])

    def test_rapid_room_creation_uses_distinct_ids(self):
        with patch("directory_server.time.time", return_value=1234567890.123):
            first = self.dispatch(ACTION_CREATE_ROOM, {
                "name": "room-a", "description": "A"
            })
            second = self.dispatch(ACTION_CREATE_ROOM, {
                "name": "room-b", "description": "B"
            })
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertNotEqual(first["data"]["room"]["id"], second["data"]["room"]["id"])


if __name__ == "__main__":
    unittest.main()