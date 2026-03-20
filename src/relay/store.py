"""SQLite database operations — session lifecycle CRUD, message logging, and config state."""

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    claude_session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);

CREATE TABLE IF NOT EXISTS config_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class Session:
    """A chat session."""

    id: str
    chat_id: int
    claude_session_id: str | None
    created_at: str
    last_active_at: str
    status: str  # "active" | "expired" | "closed"
    agent_name: str = "default"
    platform: str = "telegram"


@dataclass
class Message:
    """A logged message within a session."""

    id: int
    session_id: str
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: str


def _row_to_session(row: aiosqlite.Row) -> Session:
    """Convert a database row to a Session dataclass."""
    # platform column may not exist in legacy databases pre-migration
    try:
        platform = row["platform"]
    except (IndexError, KeyError):
        platform = "telegram"
    return Session(
        id=row["id"],
        chat_id=row["chat_id"],
        claude_session_id=row["claude_session_id"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
        status=row["status"],
        agent_name=row["agent_name"],
        platform=platform,
    )


def _row_to_message(row: aiosqlite.Row) -> Message:
    """Convert a database row to a Message dataclass."""
    return Message(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
    )


def _utcnow() -> str:
    """Return current UTC time as an ISO-format string matching SQLite's datetime('now')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    """Async SQLite store for sessions and messages."""

    def __init__(self, db_path: str) -> None:
        """Initialize with path to SQLite database file."""
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create tables if they don't exist. Open connection. Enable WAL mode.

        Must be called once at startup before any other method.
        """
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        # Idempotent migration: add agent_name column if missing
        try:
            await self._db.execute(
                "ALTER TABLE sessions ADD COLUMN agent_name TEXT NOT NULL DEFAULT 'default'"
            )
            await self._db.commit()
            logger.info("Migrated sessions table: added agent_name column")
        except sqlite3.OperationalError:
            # Column already exists — nothing to do
            pass
        # Idempotent migration: add platform column if missing
        try:
            await self._db.execute(
                "ALTER TABLE sessions ADD COLUMN platform TEXT NOT NULL DEFAULT 'telegram'"
            )
            await self._db.commit()
            logger.info("Migrated sessions table: added platform column")
        except sqlite3.OperationalError:
            pass
        # Index for agent-scoped session lookups
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_agent_chat "
            "ON sessions(agent_name, chat_id)"
        )
        # Index for platform-aware session lookups
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_agent_chat_platform "
            "ON sessions(agent_name, chat_id, platform)"
        )
        await self._db.commit()
        logger.info("Store initialized: %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Store closed")

    # --- Session operations ---

    async def create_session(
        self, chat_id: int, agent_name: str = "default", platform: str = "telegram"
    ) -> Session:
        """Create a new active session for the given agent_name, chat_id, and platform.

        Generates a uuid4 id. Returns the created Session.
        """
        session_id = uuid.uuid4().hex
        now = _utcnow()
        try:
            await self._db.execute(
                "INSERT INTO sessions (id, chat_id, agent_name, platform, created_at, last_active_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (session_id, chat_id, agent_name, platform, now, now),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to create session for chat_id=%s: %s", chat_id, exc)
            raise RuntimeError(
                f"Failed to create session for chat_id={chat_id}"
            ) from exc

        return Session(
            id=session_id,
            chat_id=chat_id,
            claude_session_id=None,
            created_at=now,
            last_active_at=now,
            status="active",
            agent_name=agent_name,
            platform=platform,
        )

    async def get_active_session(
        self, chat_id: int, agent_name: str = "default", platform: str = "telegram"
    ) -> Session | None:
        """Return the active session for agent_name, chat_id, and platform, or None.

        A session is active if status='active'.
        """
        try:
            async with self._db.execute(
                "SELECT * FROM sessions WHERE agent_name = ? AND chat_id = ? AND platform = ? AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1",
                (agent_name, chat_id, platform),
            ) as cursor:
                row = await cursor.fetchone()
                return _row_to_session(row) if row else None
        except aiosqlite.Error as exc:
            logger.error(
                "Failed to get active session for chat_id=%s: %s", chat_id, exc
            )
            raise RuntimeError(
                f"Failed to get active session for chat_id={chat_id}"
            ) from exc

    async def update_session_claude_id(
        self, session_id: str, claude_session_id: str
    ) -> None:
        """Store Claude's session_id after the first agent call.

        Also updates last_active_at.
        """
        now = _utcnow()
        try:
            await self._db.execute(
                "UPDATE sessions SET claude_session_id = ?, last_active_at = ? WHERE id = ?",
                (claude_session_id, now, session_id),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error(
                "Failed to update claude_id for session=%s: %s", session_id, exc
            )
            raise RuntimeError(
                f"Failed to update claude_id for session={session_id}"
            ) from exc

    async def touch_session(self, session_id: str) -> None:
        """Update last_active_at to now. Called on every message."""
        now = _utcnow()
        try:
            await self._db.execute(
                "UPDATE sessions SET last_active_at = ? WHERE id = ?",
                (now, session_id),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to touch session=%s: %s", session_id, exc)
            raise RuntimeError(f"Failed to touch session={session_id}") from exc

    async def expire_session(self, session_id: str) -> None:
        """Set status='expired'. Called when session_ttl exceeded."""
        try:
            await self._db.execute(
                "UPDATE sessions SET status = 'expired' WHERE id = ?",
                (session_id,),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to expire session=%s: %s", session_id, exc)
            raise RuntimeError(f"Failed to expire session={session_id}") from exc

    async def close_session(self, session_id: str) -> None:
        """Set status='closed'. Called on explicit user reset ('start over')."""
        try:
            await self._db.execute(
                "UPDATE sessions SET status = 'closed' WHERE id = ?",
                (session_id,),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to close session=%s: %s", session_id, exc)
            raise RuntimeError(f"Failed to close session={session_id}") from exc

    async def get_session(self, session_id: str) -> Session | None:
        """Fetch a session by its id. Returns None if not found."""
        try:
            async with self._db.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return _row_to_session(row) if row else None
        except aiosqlite.Error as exc:
            logger.error("Failed to get session=%s: %s", session_id, exc)
            raise RuntimeError(f"Failed to get session={session_id}") from exc

    # --- Message operations ---

    async def add_message(self, session_id: str, role: str, content: str) -> Message:
        """Log a message. role is 'user', 'assistant', or 'system'. Returns the created Message."""
        now = _utcnow()
        try:
            async with self._db.execute(
                "INSERT INTO messages (session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            ) as cursor:
                msg_id = cursor.lastrowid
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to add message to session=%s: %s", session_id, exc)
            raise RuntimeError(
                f"Failed to add message to session={session_id}"
            ) from exc

        return Message(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
        )

    async def get_messages(self, session_id: str, limit: int = 50) -> list[Message]:
        """Return messages for a session, ordered by created_at ASC. Default limit 50."""
        try:
            async with self._db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [_row_to_message(row) for row in rows]
        except aiosqlite.Error as exc:
            logger.error("Failed to get messages for session=%s: %s", session_id, exc)
            raise RuntimeError(
                f"Failed to get messages for session={session_id}"
            ) from exc

    async def count_messages(self, session_id: str) -> int:
        """Return total message count for a session."""
        try:
            async with self._db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0]
        except aiosqlite.Error as exc:
            logger.error("Failed to count messages for session=%s: %s", session_id, exc)
            raise RuntimeError(
                f"Failed to count messages for session={session_id}"
            ) from exc

    # --- Config state ---

    async def get_state(self, key: str) -> str | None:
        """Get a config_state value by key. Returns None if not found."""
        try:
            async with self._db.execute(
                "SELECT value FROM config_state WHERE key = ?",
                (key,),
            ) as cursor:
                row = await cursor.fetchone()
                return row["value"] if row else None
        except aiosqlite.Error as exc:
            logger.error("Failed to get state key=%s: %s", key, exc)
            raise RuntimeError(f"Failed to get state key={key}") from exc

    async def set_state(self, key: str, value: str) -> None:
        """Upsert a config_state key/value pair."""
        try:
            await self._db.execute(
                "INSERT INTO config_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await self._db.commit()
        except aiosqlite.Error as exc:
            logger.error("Failed to set state key=%s: %s", key, exc)
            raise RuntimeError(f"Failed to set state key={key}") from exc
