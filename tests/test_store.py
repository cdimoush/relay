"""Tests for relay.store — SQLite CRUD, session lifecycle, message logging."""

import logging

import aiosqlite
import pytest

from relay.store import Store

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


# --- Session lifecycle ---


async def test_create_session(store):
    """create_session returns a Session with status='active'."""
    session = await store.create_session(chat_id=100)
    assert session.chat_id == 100
    assert session.status == "active"
    assert session.claude_session_id is None
    assert session.id  # non-empty


async def test_get_active_session(store):
    """get_active_session returns the most recent active session."""
    created = await store.create_session(chat_id=100)
    fetched = await store.get_active_session(chat_id=100)
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_active_session_returns_none_when_no_session(store):
    """get_active_session returns None for unknown chat_id."""
    result = await store.get_active_session(chat_id=999)
    assert result is None


async def test_touch_session(store):
    """touch_session updates last_active_at."""
    session = await store.create_session(chat_id=100)
    original_ts = session.last_active_at
    await store.touch_session(session.id)
    refreshed = await store.get_session(session.id)
    # last_active_at should be updated (or at least not earlier)
    assert refreshed.last_active_at >= original_ts


async def test_expire_session(store):
    """Expired sessions are not returned by get_active_session."""
    session = await store.create_session(chat_id=100)
    await store.expire_session(session.id)
    active = await store.get_active_session(chat_id=100)
    assert active is None
    # But the session still exists
    expired = await store.get_session(session.id)
    assert expired.status == "expired"


async def test_close_session(store):
    """close_session sets status='closed', distinct from expire."""
    session = await store.create_session(chat_id=100)
    await store.close_session(session.id)
    closed = await store.get_session(session.id)
    assert closed.status == "closed"
    active = await store.get_active_session(chat_id=100)
    assert active is None


async def test_update_session_claude_id(store):
    """update_session_claude_id persists the claude_session_id."""
    session = await store.create_session(chat_id=100)
    await store.update_session_claude_id(session.id, "claude-abc-123")
    refreshed = await store.get_session(session.id)
    assert refreshed.claude_session_id == "claude-abc-123"


async def test_new_session_after_expired(store):
    """A new session for the same chat_id does not conflict with an expired one."""
    s1 = await store.create_session(chat_id=100)
    await store.expire_session(s1.id)
    s2 = await store.create_session(chat_id=100)
    assert s2.id != s1.id
    assert s2.status == "active"
    active = await store.get_active_session(chat_id=100)
    assert active.id == s2.id


# --- Message CRUD ---


async def test_add_and_get_messages(store):
    """Messages are returned in chronological order."""
    session = await store.create_session(chat_id=100)
    await store.add_message(session.id, "user", "Hello")
    await store.add_message(session.id, "assistant", "Hi there")
    await store.add_message(session.id, "user", "How are you?")

    messages = await store.get_messages(session.id)
    assert len(messages) == 3
    assert messages[0].content == "Hello"
    assert messages[1].content == "Hi there"
    assert messages[2].content == "How are you?"
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


async def test_get_messages_limit(store):
    """get_messages respects the limit parameter."""
    session = await store.create_session(chat_id=100)
    for i in range(10):
        await store.add_message(session.id, "user", f"Message {i}")
    messages = await store.get_messages(session.id, limit=3)
    assert len(messages) == 3


async def test_count_messages(store):
    """count_messages returns the correct count."""
    session = await store.create_session(chat_id=100)
    assert await store.count_messages(session.id) == 0
    await store.add_message(session.id, "user", "one")
    await store.add_message(session.id, "assistant", "two")
    assert await store.count_messages(session.id) == 2


# --- Config state ---


async def test_get_state_returns_none_for_missing_key(store):
    """get_state returns None for a key that doesn't exist."""
    assert await store.get_state("nonexistent") is None


async def test_set_and_get_state(store):
    """set_state stores a value, get_state retrieves it."""
    await store.set_state("my_key", "my_value")
    assert await store.get_state("my_key") == "my_value"


async def test_set_state_upsert(store):
    """set_state overwrites an existing key."""
    await store.set_state("key", "v1")
    await store.set_state("key", "v2")
    assert await store.get_state("key") == "v2"


# --- Agent-namespaced session isolation ---


async def test_agent_name_default(store):
    """Sessions created without agent_name default to 'default'."""
    session = await store.create_session(chat_id=100)
    assert session.agent_name == "default"


async def test_agent_name_stored_and_retrieved(store):
    """Sessions store and return the agent_name."""
    session = await store.create_session(chat_id=100, agent_name="bot-alpha")
    assert session.agent_name == "bot-alpha"
    fetched = await store.get_session(session.id)
    assert fetched.agent_name == "bot-alpha"


async def test_agent_session_isolation(store):
    """Two agents with the same chat_id get completely separate sessions."""
    s_alpha = await store.create_session(chat_id=100, agent_name="alpha")
    s_beta = await store.create_session(chat_id=100, agent_name="beta")

    assert s_alpha.id != s_beta.id

    # Each agent sees only its own session
    active_alpha = await store.get_active_session(chat_id=100, agent_name="alpha")
    active_beta = await store.get_active_session(chat_id=100, agent_name="beta")
    assert active_alpha.id == s_alpha.id
    assert active_beta.id == s_beta.id


async def test_agent_session_isolation_expire(store):
    """Expiring one agent's session does not affect another agent's session."""
    s_alpha = await store.create_session(chat_id=100, agent_name="alpha")
    s_beta = await store.create_session(chat_id=100, agent_name="beta")

    await store.expire_session(s_alpha.id)

    assert await store.get_active_session(chat_id=100, agent_name="alpha") is None
    active_beta = await store.get_active_session(chat_id=100, agent_name="beta")
    assert active_beta.id == s_beta.id


async def test_agent_session_isolation_close(store):
    """Closing one agent's session does not affect another agent's session."""
    s_alpha = await store.create_session(chat_id=100, agent_name="alpha")
    s_beta = await store.create_session(chat_id=100, agent_name="beta")

    await store.close_session(s_alpha.id)

    assert await store.get_active_session(chat_id=100, agent_name="alpha") is None
    active_beta = await store.get_active_session(chat_id=100, agent_name="beta")
    assert active_beta.id == s_beta.id


async def test_migration_adds_agent_name_column(tmp_path):
    """An existing DB without agent_name column gets migrated on initialize."""
    db_path = str(tmp_path / "legacy.db")

    # Create a legacy DB without agent_name column
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE sessions ("
            "  id TEXT PRIMARY KEY,"
            "  chat_id INTEGER NOT NULL,"
            "  claude_session_id TEXT,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  last_active_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  status TEXT NOT NULL DEFAULT 'active'"
            ")"
        )
        await db.execute(
            "INSERT INTO sessions (id, chat_id, status, created_at, last_active_at) "
            "VALUES ('legacy-id', 42, 'active', datetime('now'), datetime('now'))"
        )
        await db.commit()

    # Now open via Store — migration should add agent_name
    s = Store(db_path)
    await s.initialize()

    # The legacy session should have agent_name='default'
    session = await s.get_session("legacy-id")
    assert session is not None
    assert session.agent_name == "default"
    assert session.chat_id == 42

    # get_active_session with default agent_name should find it
    active = await s.get_active_session(chat_id=42)
    assert active is not None
    assert active.id == "legacy-id"

    await s.close()


async def test_migration_idempotent(tmp_path):
    """Calling initialize twice does not fail (migration is idempotent)."""
    db_path = str(tmp_path / "idempotent.db")
    s = Store(db_path)
    await s.initialize()
    await s.close()

    # Second initialization on same DB should not raise
    s2 = Store(db_path)
    await s2.initialize()
    await s2.close()
