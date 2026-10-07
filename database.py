"""
SocketRoom SQLite Database Module
Manages persistent storage for rooms, historical messages, and chat node registries.
Thread-safe with connection pooling/per-call connections.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

DB_FILE = os.environ.get("SOCKETROOM_DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "socketroom.db")
_lock = threading.RLock()


class Database:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Rooms table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    server_node TEXT NOT NULL,
                    privacy TEXT NOT NULL DEFAULT 'Công khai',
                    unread INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    sender_initials TEXT NOT NULL,
                    sender_tone TEXT NOT NULL,
                    time TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
                )
            """)

            # Nodes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    last_heartbeat REAL DEFAULT 0.0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()

            self.seed_defaults()

    def seed_defaults(self) -> None:
        """Seed only once, even if all rooms are later deleted."""
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT value FROM app_meta WHERE key = 'seeded_defaults'")
            if cursor.fetchone() is not None:
                conn.close()
                return

            cursor.execute("SELECT COUNT(*) FROM rooms")
            if cursor.fetchone()[0] == 0:
                # Seed Nodes
                nodes = [
                    ("node-1", "Chat Server 1", "127.0.0.1", 5001, 1, time.time()),
                    ("node-2", "Chat Server 2", "127.0.0.1", 5002, 1, time.time()),
                ]
                cursor.executemany("""
                    INSERT OR IGNORE INTO nodes (id, name, host, port, is_active, last_heartbeat)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, nodes)

                # Seed Rooms
                rooms = [
                    ("python", "python-cơ-bản", "Trao đổi Python và bài tập", "Chat Server 2", "Công khai", 0),
                    ("socket", "đồ-án-socket", "Thiết kế protocol và phân server", "Chat Server 1", "Công khai", 0),
                    ("study", "học-nhóm", "Lịch họp và phân công", "Chat Server 1", "Công khai", 0),
                    ("network", "mạng-máy-tính", "Ôn tập TCP/IP", "Chat Server 2", "Có mã tham gia", 0),
                ]
                cursor.executemany("""
                    INSERT INTO rooms (id, name, description, server_node, privacy, unread)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, rooms)

                # New public installations begin with empty chat history.
            cursor.execute("INSERT INTO app_meta (key, value) VALUES ('seeded_defaults', '1')")
            conn.commit()
            conn.close()

    def get_rooms(self) -> List[Dict[str, Any]]:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM rooms ORDER BY id ASC")
            rows = cursor.fetchall()
            rooms = [dict(row) for row in rows]
            conn.close()
            return rooms

    def get_room(self, room_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM rooms WHERE id = ? OR name = ?", (room_id, room_id))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None

    def create_room(self, room_id: str, name: str, description: str, server_node: str, privacy: str = "Công khai") -> bool:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO rooms (id, name, description, server_node, privacy, unread)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (room_id, name, description, server_node, privacy))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
            finally:
                conn.close()

    def delete_room(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Delete an exact room ID and its messages in one transaction."""
        with _lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                room = dict(row)
                conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
                conn.commit()
                return room
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_messages(self, room_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, room_id, sender_name AS user, sender_initials AS initials,
                       sender_tone AS tone, time, body, created_at
                FROM messages
                WHERE room_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (room_id, limit))
            rows = cursor.fetchall()
            msgs = [dict(row) for row in reversed(rows)]
            conn.close()
            return msgs

    def save_message(self, room_id: str, sender_name: str, sender_initials: str,
                     sender_tone: str, time_str: str, body: str) -> int:
        with _lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (room_id, sender_name, sender_initials, sender_tone, time, body)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (room_id, sender_name, sender_initials, sender_tone, time_str, body))
                msg_id = cursor.lastrowid
                conn.commit()
                return msg_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def update_node_heartbeat(self, node_name: str, host: str, port: int, is_active: bool = True) -> None:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            node_id = node_name.lower().replace(" ", "-")
            cursor.execute("""
                INSERT INTO nodes (id, name, host, port, is_active, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    host = excluded.host,
                    port = excluded.port,
                    is_active = excluded.is_active,
                    last_heartbeat = excluded.last_heartbeat
            """, (node_id, node_name, host, port, 1 if is_active else 0, time.time()))
            conn.commit()
            conn.close()

    def set_node_active(self, node_name: str, is_active: bool) -> None:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE nodes SET is_active = ? WHERE name = ?
            """, (1 if is_active else 0, node_name))
            conn.commit()
            conn.close()

    def get_nodes(self) -> List[Dict[str, Any]]:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM nodes ORDER BY port ASC")
            rows = cursor.fetchall()
            nodes = [dict(row) for row in rows]
            conn.close()
            return nodes

    def get_stats(self) -> Dict[str, Any]:
        with _lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM rooms")
            total_rooms = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM messages")
            total_messages = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM nodes WHERE is_active = 1")
            active_nodes = cursor.fetchone()[0]
            conn.close()
            return {
                "total_rooms": total_rooms,
                "total_messages": total_messages,
                "active_nodes": active_nodes
            }
