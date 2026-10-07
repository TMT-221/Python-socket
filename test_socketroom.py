"""
SocketRoom Automated Test Suite
Verifies:
1. 4-byte length prefix TCP framing & JSON packet serialization
2. Database operations & message persistence in SQLite
3. Directory Server & Chat Server node clustering
4. Multi-client real-time messaging exchange & broadcast
"""

import os
import socket
import tempfile
import threading
import time
import unittest
from typing import Any, Dict

from database import Database
from directory_server import DirectoryServer
from chat_server import ChatServerNode
from protocol import (
    ACTION_CHAT_MESSAGE,
    ACTION_JOIN_ROOM,
    ProtocolError,
    recv_packet,
    send_packet,
)
from socket_client import SocketRoomClient


class TestProtocolFraming(unittest.TestCase):
    def test_send_and_receive_packet(self):
        # Create loopback socket pair
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.connect(("127.0.0.1", port))
        conn, _ = server_sock.accept()

        try:
            sample_data = {
                "action": "TEST_PACKET",
                "content": "Kiểm tra framing 4 byte với tiếng Việt có dấu: 🚀 Đồ án socket mạng máy tính!",
                "number": 12345,
                "nested": {"key": "value"}
            }

            # Send from client
            send_packet(client_sock, sample_data)

            # Receive on server
            received = recv_packet(conn)
            self.assertEqual(received, sample_data)
        finally:
            client_sock.close()
            conn.close()
            server_sock.close()


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = Database(self.temp_db.name)

    def tearDown(self):
        try:
            os.remove(self.temp_db.name)
        except Exception:
            pass

    def test_defaults_and_message_persistence(self):
        rooms = self.db.get_rooms()
        self.assertGreaterEqual(len(rooms), 4)

        # Create new room
        ok = self.db.create_room("test-room", "test-room", "Test Desc", "Chat Server 1")
        self.assertTrue(ok)

        # Save message
        msg_id = self.db.save_message("test-room", "Minh Anh", "MA", "minh", "10:00", "Hello Test")
        self.assertIsInstance(msg_id, int)

        # Retrieve messages
        msgs = self.db.get_messages("test-room")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["user"], "Minh Anh")
        self.assertEqual(msgs[0]["body"], "Hello Test")


class TestMultiClientCluster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        cls.temp_db.close()

        # Ports for test cluster
        cls.dir_port = 5700
        cls.node_port = 5701

        # 1. Start test Directory Server
        cls.dir_server = DirectoryServer(port=cls.dir_port, db_path=cls.temp_db.name)
        cls.dir_thread = threading.Thread(target=cls.dir_server.start, daemon=True)
        cls.dir_thread.start()
        time.sleep(0.8)

        # 2. Start test Chat Node
        cls.node_server = ChatServerNode(
            name="Chat Server 1",
            port=cls.node_port,
            dir_port=cls.dir_port,
        )
        cls.node_thread = threading.Thread(target=cls.node_server.start, daemon=True)
        cls.node_thread.start()
        time.sleep(0.8)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.node_server.stop()
            cls.dir_server.stop()
            os.remove(cls.temp_db.name)
        except Exception:
            pass

    def test_multi_client_exchange(self):
        client_a = SocketRoomClient("Lan", "127.0.0.1", self.dir_port, initials="LN", tone="lan")
        client_b = SocketRoomClient("Tuấn", "127.0.0.1", self.dir_port, initials="T", tone="tuan")

        # Login both
        res_a = client_a.login()
        res_b = client_b.login()
        self.assertIsNotNone(res_a)
        self.assertIsNotNone(res_b)

        received_by_b = []
        event_b = threading.Event()

        def on_msg_b(data: Dict[str, Any]):
            received_by_b.append(data)
            if data.get("body") == "Thử nghiệm gửi nhận realtime":
                event_b.set()

        client_b.on_message = on_msg_b

        # Both join room on node 5701
        ok_a = client_a.join_room("python", node_host="127.0.0.1", node_port=self.node_port)
        ok_b = client_b.join_room("python", node_host="127.0.0.1", node_port=self.node_port)
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        time.sleep(0.5)

        # Client A sends a chat message
        sent = client_a.send_chat_message("Thử nghiệm gửi nhận realtime")
        self.assertTrue(sent)

        # Wait for Client B to receive
        success = event_b.wait(timeout=3.0)
        self.assertTrue(success, "Client B did not receive the broadcast message within timeout")
        self.assertEqual(received_by_b[-1]["user"], "Lan")
        self.assertEqual(received_by_b[-1]["body"], "Thử nghiệm gửi nhận realtime")

        # Clean up
        client_a.disconnect_node()
        client_b.disconnect_node()


if __name__ == "__main__":
    unittest.main()
