# Relay v1.0: Implementation Specification

> This spec is the single source of truth for Phase B sub-agents. Each module section is self-sufficient — a sub-agent reading only its section (plus the shared conventions at the top) should know exactly what to build.

---

## Shared Conventions

### Python Version & Style
- Python 3.11+
- asyncio throughout (Relay is a single async process)
- Type hints on all public functions
- Docstrings on all public functions (one-liner is fine)
- No classes unless state management requires it (prefer module-level functions + dataclasses)
- Imports: stdlib first, third-party second, local third

### Package Layout
```
relay/
├── pyproject.toml
├── requirements.txt
├── relay.yaml                    # User config (gitignored in practice)
├── relay.yaml.example            # Checked-in template
├── relay.service                 # systemd unit file
├── relay.db                      # SQLite runtime (gitignored)
├── src/
│   └── relay/
│       ├── __init__.py           # version string only
│       ├── main.py               # entry point
│       ├── store.py              # SQLite operations
│       ├── config.py             # YAML config loading
│       ├── voice.py              # Voice transcription
│       ├── agent.py              # Claude subprocess management
│       ├── intake.py             # Message classifier/router
│       └── telegram.py           # Telegram adapter
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # shared fixtures
│   ├── test_store.py
│   ├── test_config.py
│   ├── test_voice.py
│   ├── test_agent.py
│   ├── test_intake.py
│   └── test_telegram.py
└── research/                     # existing, untouched
```

### Error Handling Philosophy
- Never crash the polling loop. Catch exceptions at the handler level, log them, send a user-friendly error to Telegram.
- Use Python's `logging` module. Logger name = module name (e.g., `logging.getLogger(__name__)`).
- All subprocess calls have timeouts. All I/O has timeouts.

### Key Environment Variables
| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token |
| `OPENAI_API_KEY` | Yes (for voice) | Used by vox / OpenAI fallback |

### Critical Subprocess Detail
When spawning Claude Code as a subprocess, **always** remove `CLAUDECODE` from the environment:
```python
env = os.environ.copy()
env.pop("CLAUDECODE", None)
```
Without this, Claude refuses to start with a "nested session" error.

### System Prompt Conventions (CLAUDE.md vs --system-prompt vs --append-system-prompt)
Claude Code has two distinct prompt injection flags, and the choice depends on whether the subprocess runs inside a project directory:

- **`agent.py`** uses `--append-system-prompt` and sets `cwd=agent_config.project_dir`. Claude Code automatically loads `CLAUDE.md` from the cwd, so the project's instructions are already present. The `--append-system-prompt` flag *adds* Relay-specific context (e.g., "you are being accessed via Telegram") on top of the project's own CLAUDE.md.
- **`intake.py`** uses `--system-prompt` and does **not** set a `cwd`. The intake classifier has no project context and no CLAUDE.md — it only needs the classification instructions. `--system-prompt` *replaces* the default system prompt entirely, which is correct for a standalone classifier with no project awareness.

Never mix these up: using `--system-prompt` in agent.py would override the project's CLAUDE.md instructions, and using `--append-system-prompt` in intake.py would append to a default prompt that is not relevant to classification.

---

## Module 1: store.py

**File path:** `src/relay/store.py`

### Purpose
SQLite database operations — session lifecycle CRUD, message logging, and config state. Foundation module with no dependencies on other Relay modules.

### Dependencies
- **External:** `aiosqlite` (async SQLite wrapper)
- **Internal:** None (leaf module)

### SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                          -- Python uuid4 hex string
    chat_id INTEGER NOT NULL,                     -- Telegram chat ID
    claude_session_id TEXT,                        -- Claude's session_id for --resume
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'active'          -- active | expired | closed
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,                             -- user | assistant | system
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);

CREATE TABLE IF NOT EXISTS config_state (
    key TEXT PRIMARY KEY,
    value TEXT                                      -- JSON blob for misc state
);
```

Database opened with WAL mode for concurrent read safety:
```python
async with aiosqlite.connect(db_path) as db:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
```

### Public Interface

```python
import aiosqlite
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Session:
    id: str
    chat_id: int
    claude_session_id: str | None
    created_at: str
    last_active_at: str
    status: str  # "active" | "expired" | "closed"


@dataclass
class Message:
    id: int
    session_id: str
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: str


class Store:
    """Async SQLite store for sessions and messages."""

    def __init__(self, db_path: str) -> None:
        """Initialize with path to SQLite database file."""
        ...

    async def initialize(self) -> None:
        """Create tables if they don't exist. Open connection. Enable WAL mode.
        Must be called once at startup before any other method."""
        ...

    async def close(self) -> None:
        """Close the database connection."""
        ...

    # --- Session operations ---

    async def create_session(self, chat_id: int) -> Session:
        """Create a new active session for the given chat_id.
        Generates a uuid4 id. Returns the created Session."""
        ...

    async def get_active_session(self, chat_id: int) -> Session | None:
        """Return the active session for chat_id, or None.
        A session is active if status='active'."""
        ...

    async def update_session_claude_id(self, session_id: str, claude_session_id: str) -> None:
        """Store Claude's session_id after the first agent call.
        Also updates last_active_at."""
        ...

    async def touch_session(self, session_id: str) -> None:
        """Update last_active_at to now. Called on every message."""
        ...

    async def expire_session(self, session_id: str) -> None:
        """Set status='expired'. Called when session_ttl exceeded."""
        ...

    async def close_session(self, session_id: str) -> None:
        """Set status='closed'. Called on explicit user reset ('start over')."""
        ...

    async def get_session(self, session_id: str) -> Session | None:
        """Fetch a session by its id. Returns None if not found."""
        ...

    # --- Message operations ---

    async def add_message(self, session_id: str, role: str, content: str) -> Message:
        """Log a message. role is 'user', 'assistant', or 'system'. Returns the created Message."""
        ...

    async def get_messages(self, session_id: str, limit: int = 50) -> list[Message]:
        """Return messages for a session, ordered by created_at ASC. Default limit 50."""
        ...

    async def count_messages(self, session_id: str) -> int:
        """Return total message count for a session."""
        ...

    # --- Config state ---

    async def get_state(self, key: str) -> str | None:
        """Get a config_state value by key. Returns None if not found."""
        ...

    async def set_state(self, key: str, value: str) -> None:
        """Upsert a config_state key/value pair."""
        ...
```

### Implementation Details
- Use a single `aiosqlite.Connection` held as `self._db`, opened in `initialize()`.
- All write operations use `await self._db.commit()` after execute.
- `create_session` generates `uuid.uuid4().hex` for the id.
- `get_active_session` queries: `SELECT * FROM sessions WHERE chat_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1`.
- `expire_session` and `close_session` are separate to distinguish automatic expiry from user-initiated reset.
- Row factory: set `db.row_factory = aiosqlite.Row` then convert rows to dataclasses in each method.

### Error Handling
- `initialize()` raises if it cannot create/open the database file (let it propagate — fatal at startup).
- All other methods catch `aiosqlite.Error`, log it, and re-raise as a plain `RuntimeError` with context.
- Never silently swallow database errors.

### Test Expectations
- Use an in-memory database (`:memory:`) or `tmp_path` fixture for isolation.
- Test session lifecycle: create → get_active → touch → expire. Verify expired sessions are not returned by `get_active_session`.
- Test close_session separately from expire_session.
- Test create_session + update_session_claude_id + verify the claude_session_id is persisted.
- Test message CRUD: add multiple messages, verify ordering and limit.
- Test config_state: set, get, upsert (overwrite existing key).
- Test that a new session for the same chat_id does not conflict with an expired one.

---

## Module 2: config.py

**File path:** `src/relay/config.py`

### Purpose
Load and validate `relay.yaml`. Provide a typed configuration object to all other modules. Supports environment variable substitution in YAML values.

### Dependencies
- **External:** `pyyaml`
- **Internal:** None (leaf module)

### relay.yaml Schema

```yaml
# relay.yaml
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}        # env var substitution
  allowed_users:                           # list of Telegram user IDs (integers)
    - 123456789

agent:
  name: "isaac"                            # human-readable label
  project_dir: "/home/ubuntu/isaac_research"  # absolute path, must exist
  allowed_tools:                           # passed to --allowedTools
    - "Read"
    - "Glob"
    - "Grep"
    - "Edit"
    - "Write"
    - "Bash"
    - "Agent"
  model: "sonnet"                          # passed to --model
  timeout: 900                             # subprocess timeout in seconds (default: 900)
  session_ttl: 14400                       # session expiry in seconds (default: 14400 = 4 hours)
  max_budget: 1.0                          # passed to --max-budget-usd (default: 1.0)
  append_system_prompt: |                  # appended via --append-system-prompt
    You are being accessed via Telegram on a mobile device.
    Keep responses concise and well-structured for small screens.
    Use short paragraphs and bullet points.

voice:
  backend: "vox"                           # "vox" or "openai"

storage:
  db_path: "relay.db"                      # relative to relay project root, or absolute
```

### Public Interface

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TelegramConfig:
    bot_token: str
    allowed_users: list[int]


@dataclass
class AgentConfig:
    name: str
    project_dir: str
    allowed_tools: list[str]
    model: str = "sonnet"
    timeout: int = 900
    session_ttl: int = 14400
    max_budget: float = 1.0
    append_system_prompt: str = ""


@dataclass
class VoiceConfig:
    backend: str = "vox"           # "vox" or "openai"


@dataclass
class StorageConfig:
    db_path: str = "relay.db"


@dataclass
class RelayConfig:
    telegram: TelegramConfig
    agent: AgentConfig
    voice: VoiceConfig
    storage: StorageConfig


def load_config(config_path: str = "relay.yaml") -> RelayConfig:
    """Load relay.yaml, substitute env vars, validate, return RelayConfig.

    Raises:
        FileNotFoundError: if config_path does not exist
        ValueError: if required fields are missing or invalid
    """
    ...
```

### Implementation Details
- Read the YAML file as a string first. Use `os.path.expandvars()` to substitute `${VAR}` patterns with environment variables before parsing with `yaml.safe_load()`.
- After parsing, validate:
  - `telegram.bot_token` is non-empty (after env substitution).
  - `telegram.allowed_users` is a non-empty list of integers.
  - `agent.project_dir` exists and is a directory (`Path(project_dir).is_dir()`).
  - `agent.allowed_tools` is a non-empty list of strings.
  - `voice.backend` is one of `"vox"`, `"openai"`.
- Apply defaults for optional fields before constructing dataclasses.
- `storage.db_path`: if relative, resolve it relative to the directory containing `relay.yaml` (not cwd).

### Error Handling
- `FileNotFoundError` if relay.yaml doesn't exist (let it propagate).
- `ValueError` with a clear message for each validation failure (e.g., `"agent.project_dir '/foo/bar' does not exist"`).
- `yaml.YAMLError` on malformed YAML (let it propagate with context).

### Test Expectations
- Test loading a valid config from a tmp_path YAML file.
- Test env var substitution: set an env var, reference it with `${VAR}`, verify it resolves.
- Test missing required fields raise `ValueError`.
- Test defaults are applied for optional fields.
- Test invalid `project_dir` (non-existent path) raises `ValueError`.
- Test relative `db_path` resolution.

---

## Module 3: voice.py

**File path:** `src/relay/voice.py`

### Purpose
Transcribe audio files (voice messages downloaded from Telegram) to text. Primary backend: `vox file` subprocess. Fallback: direct OpenAI Whisper API call.

### Dependencies
- **External:** `openai` (only if fallback is used)
- **Internal:** None (leaf module — receives a file path, returns text)

### Public Interface

```python
async def transcribe(audio_path: str, backend: str = "vox") -> str:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to audio file (OGG, WAV, MP3, M4A, WebM)
        backend: "vox" (default) or "openai"

    Returns:
        Transcribed text string. Never empty — raises on failure.

    Raises:
        TranscriptionError: if transcription fails for any reason.
    """
    ...


class TranscriptionError(Exception):
    """Raised when transcription fails."""
    pass
```

### Implementation Details

#### Vox Backend (Primary)
```python
async def _transcribe_vox(audio_path: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "vox", "file", audio_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TranscriptionError("Vox transcription timed out after 120 seconds")

    if proc.returncode != 0:
        raise TranscriptionError(f"Vox failed (exit {proc.returncode}): {stderr.decode().strip()}")

    text = stdout.decode().strip()
    if not text:
        raise TranscriptionError("Vox returned empty transcription")
    return text
```

- `vox file <path>` writes transcription text to stdout and UI/progress to stderr.
- Timeout: 120 seconds (Whisper API can be slow on long audio).
- If `vox` is not installed or not executable (`FileNotFoundError` or `PermissionError` from subprocess), auto-fall back to OpenAI.

#### OpenAI Backend (Fallback)
```python
async def _transcribe_openai(audio_path: str) -> str:
    """Direct OpenAI Whisper API call. Used when vox is not available."""
    import openai

    client = openai.AsyncOpenAI()  # uses OPENAI_API_KEY from env
    with open(audio_path, "rb") as f:
        response = await client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=f,
        )
    text = response.text.strip()
    if not text:
        raise TranscriptionError("OpenAI returned empty transcription")
    return text
```

- No audio chunking in v1.0. Telegram voice messages are typically under 1 minute. If a message exceeds Whisper's limit (~25 MB), the error is caught and reported.
- The `openai` import is lazy (inside the function) so it's not required if vox backend is used.

#### Backend Selection in `transcribe()`
```python
async def transcribe(audio_path: str, backend: str = "vox") -> str:
    if backend == "vox":
        try:
            return await _transcribe_vox(audio_path)
        except (FileNotFoundError, PermissionError):
            logger.warning("vox not available, falling back to openai backend")
            return await _transcribe_openai(audio_path)
    elif backend == "openai":
        return await _transcribe_openai(audio_path)
    else:
        raise ValueError(f"Unknown voice backend: {backend}")
```

### Error Handling
- All errors wrapped in `TranscriptionError` with a human-readable message.
- Vox not installed or not executable → automatic fallback to OpenAI (log a warning).
- OpenAI API errors → `TranscriptionError` with the API error message.
- Empty transcription → `TranscriptionError` (never return empty string).
- Timeout → `TranscriptionError` with timeout info.

### Test Expectations
- Mock `asyncio.create_subprocess_exec` for vox tests.
- Test vox success: mock stdout with text, verify returned text.
- Test vox failure: mock non-zero exit code, verify `TranscriptionError`.
- Test vox timeout: mock a process that never completes, verify `TranscriptionError`.
- Test vox not found: mock `FileNotFoundError`, verify fallback to OpenAI is attempted.
- Test vox not executable: mock `PermissionError`, verify fallback to OpenAI is attempted.
- Test OpenAI success: mock `openai.AsyncOpenAI`, verify returned text.
- Test OpenAI failure: mock API error, verify `TranscriptionError`.
- Test unknown backend raises `ValueError`.

---

## Module 4: agent.py

**File path:** `src/relay/agent.py`

### Purpose
Manage Claude Code subprocess lifecycle — spawn `claude -p` with `--resume` for session persistence, parse JSON output, handle timeouts and errors. This is the core integration point between Relay and Claude Code.

### Dependencies
- **External:** None (uses stdlib `asyncio`, `json`, `os`, `signal`)
- **Internal:** `store.py` (for session lookup and persistence)

### Public Interface

```python
from dataclasses import dataclass


@dataclass
class AgentResponse:
    text: str                      # The agent's response text (from JSON "result" field)
    session_id: str | None         # Claude's session_id (for --resume)
    is_error: bool                 # True if the response represents an error
    cost_usd: float                # total_cost_usd from JSON
    duration_ms: int               # duration_ms from JSON
    num_turns: int                 # num_turns from JSON


async def send_message(
    message: str,
    chat_id: int,
    store: "Store",
    agent_config: "AgentConfig",
) -> AgentResponse:
    """Send a message to the Claude agent and return the response.

    Handles the full session lifecycle:
    1. Look up active session for chat_id in store
    2. Check session_ttl — expire if stale, create new if needed
    3. Spawn claude subprocess with --resume if session exists
    4. Parse JSON response
    5. Store claude_session_id if this was the first call
    6. Log user message and assistant response to store
    7. Return AgentResponse

    Args:
        message: The user's message text
        chat_id: Telegram chat ID (used to look up session)
        store: Store instance for session/message persistence
        agent_config: AgentConfig with project_dir, model, timeout, etc.

    Returns:
        AgentResponse with the agent's text and metadata.
        On error, is_error=True and text contains the error description.
    """
    ...


async def reset_session(chat_id: int, store: "Store") -> str:
    """Close the current session for chat_id and return a confirmation message.

    Returns a string like "Session closed. Starting fresh next message."
    If no active session exists, returns "No active session to reset."
    """
    ...


async def get_session_info(chat_id: int, store: "Store") -> str:
    """Return human-readable session info for the given chat_id.

    Returns something like:
        "Active session: 2h 15m old, 12 messages"
    Or:
        "No active session."
    """
    ...
```

### Implementation Details

#### Subprocess Construction
```python
async def _run_claude(
    message: str,
    claude_session_id: str | None,
    agent_config: AgentConfig,
) -> AgentResponse:
    """Low-level: spawn claude subprocess, parse output, return response."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # CRITICAL: prevent nested session error

    cmd = ["claude", "-p", message, "--output-format", "json"]

    if claude_session_id:
        cmd.extend(["--resume", claude_session_id])

    # Tool permissions
    cmd.append("--allowedTools")
    cmd.extend(agent_config.allowed_tools)

    # Model
    cmd.extend(["--model", agent_config.model])

    # Budget safety net
    cmd.extend(["--max-budget-usd", str(agent_config.max_budget)])

    # Append system prompt for Relay-specific context
    if agent_config.append_system_prompt:
        cmd.extend(["--append-system-prompt", agent_config.append_system_prompt])

    # Skip interactive permission prompts
    cmd.append("--dangerously-skip-permissions")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_config.project_dir,    # Claude reads CLAUDE.md from here
        env=env,
        start_new_session=True,           # own process group for killpg
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=agent_config.timeout,
        )
    except asyncio.TimeoutError:
        try:
            # proc.pid == pgid because start_new_session=True makes the child the group leader
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        await proc.wait()
        return AgentResponse(
            text=f"The agent timed out after {agent_config.timeout // 60} minutes.",
            session_id=claude_session_id,
            is_error=True,
            cost_usd=0.0,
            duration_ms=agent_config.timeout * 1000,
            num_turns=0,
        )

    if proc.returncode != 0:
        error_text = stderr.decode().strip()
        # Handle expired/missing session: retry without --resume
        if "No conversation found" in error_text and claude_session_id:
            logger.warning("Session %s expired in Claude, starting fresh", claude_session_id)
            return await _run_claude(message, claude_session_id=None, agent_config=agent_config)
        return AgentResponse(
            text=f"Agent error: {error_text or 'unknown error'}",
            session_id=claude_session_id,
            is_error=True,
            cost_usd=0.0,
            duration_ms=0,
            num_turns=0,
        )

    # Parse JSON from stdout
    data = json.loads(stdout.decode())
    return AgentResponse(
        text=data.get("result", ""),
        session_id=data.get("session_id"),
        is_error=data.get("is_error", False),
        cost_usd=data.get("total_cost_usd", 0.0),
        duration_ms=data.get("duration_ms", 0),
        num_turns=data.get("num_turns", 0),
    )
```

#### Session Lifecycle in `send_message()`
1. Call `store.get_active_session(chat_id)`.
2. If session exists, check `last_active_at` against `agent_config.session_ttl`:
   - If `now - last_active_at > session_ttl`, call `store.expire_session()` and create a new session.
   - Otherwise, call `store.touch_session()`.
3. If no active session, call `store.create_session(chat_id)`.
4. Log the user message: `store.add_message(session_id, "user", message)`.
5. Call `_run_claude(message, session.claude_session_id, agent_config)`.
6. If this was the first call (no `claude_session_id` stored yet) and response has a `session_id`, call `store.update_session_claude_id()`.
7. Log the assistant message: `store.add_message(session_id, "assistant", response.text)`.
8. Return the `AgentResponse`.

### Error Handling
- Subprocess timeout → return `AgentResponse(is_error=True)` with timeout message. Kill the process group.
- Non-zero exit code → return `AgentResponse(is_error=True)` with stderr text.
- Invalid/expired Claude session → auto-retry without `--resume` (one retry only, no recursion loop — the retry passes `claude_session_id=None`).
- JSON parse error → return `AgentResponse(is_error=True)` with raw stdout preview.
- Store errors → log and re-raise (caller handles).

### Test Expectations
- Mock `asyncio.create_subprocess_exec` throughout.
- Test successful call: mock valid JSON stdout, verify `AgentResponse` fields.
- Test session resume: verify `--resume` is in the command when `claude_session_id` is set.
- Test first-call session_id storage: verify `store.update_session_claude_id` is called with the returned session_id.
- Test timeout: mock a process that exceeds timeout, verify `os.killpg` is called and `is_error=True`.
- Test expired session retry: mock first call with "No conversation found" stderr, verify retry without `--resume`.
- Test session TTL expiry: set a short TTL, verify old session is expired and new one created.
- Test `reset_session`: verify `store.close_session` is called.
- Test `get_session_info`: verify human-readable output with age and message count.

---

## Module 5: intake.py

**File path:** `src/relay/intake.py`

### Purpose
Classify incoming messages before they reach the agent. Determines whether a message should be forwarded to the agent, triggers a session reset, returns status info, or is dismissed as unclear. Uses a fast, cheap Claude call for classification.

### Dependencies
- **External:** None (uses stdlib for subprocess)
- **Internal:** `agent.py` (calls `send_message`, `reset_session`, `get_session_info`)

### Public Interface

```python
from dataclasses import dataclass


@dataclass
class IntakeResult:
    action: str             # "forward" | "new_session" | "status" | "unclear"
    cleaned_message: str    # The message to forward (may be cleaned up from original)


async def classify(message: str) -> IntakeResult:
    """Classify a user message into an action.

    Uses a lightweight Claude -p call with --output-format json.
    Fast and stateless — no session context.

    Args:
        message: The user's raw message text

    Returns:
        IntakeResult with the classification and cleaned message.
    """
    ...


async def handle_message(
    message: str,
    chat_id: int,
    store: "Store",
    agent_config: "AgentConfig",
) -> str:
    """Full intake pipeline: classify → route → return response text.

    This is the main entry point called by telegram.py for every text message.

    1. Classify the message
    2. If "forward" → call agent.send_message(), return agent's response
    3. If "new_session" → call agent.reset_session(), return confirmation
    4. If "status" → call agent.get_session_info(), return info
    5. If "unclear" → return a brief "I didn't understand" message

    Returns:
        The response text to send back to the user.
    """
    ...
```

### Implementation Details

#### Classifier Subprocess
```python
INTAKE_SYSTEM_PROMPT = """You are a message classifier for a Telegram-to-agent relay system.
Given a user message, classify it into one of these actions:
- "forward": The message is for the agent (default — most messages are this)
- "new_session": The user wants to start over / reset / new session / forget everything
- "status": The user wants to know session status / what's going on / how long
- "unclear": The message is gibberish, accidental, or completely unintelligible

Respond with JSON only: {"action": "forward"|"new_session"|"status"|"unclear", "cleaned_message": "..."}
The cleaned_message should be the original message, lightly cleaned up (fix obvious typos from voice, remove filler words) but preserving the user's intent. If action is not "forward", cleaned_message can be empty.

Bias heavily toward "forward" — when in doubt, forward to the agent."""


async def classify(message: str) -> IntakeResult:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        "claude", "-p", f"Classify this message:\n\n{message}",
        "--output-format", "json",
        "--system-prompt", INTAKE_SYSTEM_PROMPT,
        "--model", "haiku",
        "--max-budget-usd", "0.01",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        # On timeout, default to forward (don't block the user)
        return IntakeResult(action="forward", cleaned_message=message)

    if proc.returncode != 0:
        # On error, default to forward
        logger.warning("Intake classifier failed: %s", stderr.decode().strip())
        return IntakeResult(action="forward", cleaned_message=message)

    data = json.loads(stdout.decode())
    result_text = data.get("result", "{}")

    # Parse the inner JSON from the result field
    try:
        classification = json.loads(result_text)
    except json.JSONDecodeError:
        # If the model didn't return valid JSON, default to forward
        return IntakeResult(action="forward", cleaned_message=message)

    action = classification.get("action", "forward")
    cleaned = classification.get("cleaned_message", message)

    if action not in ("forward", "new_session", "status", "unclear"):
        action = "forward"

    return IntakeResult(action=action, cleaned_message=cleaned or message)
```

Key design decisions:
- Uses `--model haiku` for speed and cost (~500 tokens, ~$0.0001 per classification).
- Uses `--system-prompt` (not `--append-system-prompt`) since this has no project context.
- No `cwd` set — the classifier doesn't need project access.
- On any failure (timeout, error, bad JSON), defaults to `"forward"` — never block the user.
- Budget capped at $0.01 per classification call.

#### `handle_message()` Routing
```python
async def handle_message(message, chat_id, store, agent_config) -> str:
    result = await classify(message)

    if result.action == "forward":
        response = await agent.send_message(result.cleaned_message, chat_id, store, agent_config)
        return response.text

    elif result.action == "new_session":
        return await agent.reset_session(chat_id, store)

    elif result.action == "status":
        return await agent.get_session_info(chat_id, store)

    elif result.action == "unclear":
        return "I didn't quite catch that. Could you rephrase?"

    # Fallback (shouldn't reach here)
    response = await agent.send_message(message, chat_id, store, agent_config)
    return response.text
```

### Error Handling
- Classifier timeout (30s) → default to "forward" (log warning).
- Classifier subprocess failure → default to "forward" (log warning).
- Invalid JSON from classifier → default to "forward" (log warning).
- Agent errors are handled by `agent.send_message()` returning `AgentResponse(is_error=True)`.
- The intake router should NEVER prevent a message from being processed.

### Test Expectations
- Mock the classifier subprocess for all tests.
- Test "forward" classification: verify message is passed to `agent.send_message`.
- Test "new_session" classification: verify `agent.reset_session` is called.
- Test "status" classification: verify `agent.get_session_info` is called.
- Test "unclear" classification: verify the "didn't catch that" response.
- Test classifier timeout: verify default to "forward".
- Test classifier error: verify default to "forward".
- Test malformed JSON from classifier: verify default to "forward".
- Test `handle_message` end-to-end with mocked agent module.

---

## Module 6: telegram.py

**File path:** `src/relay/telegram.py`

### Purpose
Telegram bot adapter — handles polling, authorization, voice message download, response chunking, and wiring everything together. This is the outermost module that receives user input and sends responses.

### Dependencies
- **External:** `python-telegram-bot` (v21+)
- **Internal:** `config.py`, `store.py`, `voice.py`, `intake.py`

### Public Interface

```python
from telegram.ext import Application


async def start_bot(config: "RelayConfig", store: "Store") -> None:
    """Start the Telegram bot with long-polling.

    Sets up message handlers, initializes the bot, and runs polling.
    This function blocks until the bot is stopped (Ctrl+C / SIGTERM).

    Args:
        config: Full RelayConfig from config.py
        store: Initialized Store instance from store.py
    """
    ...
```

### Implementation Details

#### Bot Setup
```python
import tempfile
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

logger = logging.getLogger(__name__)

# Note: telegram.py uses module-level mutable globals for shared state across handlers.
# This is an intentional exception to the "no classes" convention — python-telegram-bot's
# handler registration model requires shared state, and module globals with a one-time
# init in start_bot() are the simplest approach without introducing a class.
_config: RelayConfig = None
_store: Store = None


async def start_bot(config: RelayConfig, store: Store) -> None:
    global _config, _store
    _config = config
    _store = store

    app = Application.builder().token(config.telegram.bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))

    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Block until stopped — caller manages shutdown
    # (main.py sets up signal handlers that call app.stop())
```

#### Authorization
```python
def _is_authorized(user_id: int) -> bool:
    """Check if a Telegram user ID is in the allowed list."""
    return user_id in _config.telegram.allowed_users


async def _check_auth(update: Update) -> bool:
    """Check authorization. Silently drop unauthorized messages. Returns True if authorized."""
    if not update.effective_user:
        return False
    if not _is_authorized(update.effective_user.id):
        logger.warning("Unauthorized message from user %s", update.effective_user.id)
        return False
    return True
```

#### Text Message Handler
```python
async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    if not await _check_auth(update):
        return

    chat_id = update.effective_chat.id
    message_text = update.message.text

    # Send "thinking" indicator
    await update.effective_chat.send_action("typing")

    try:
        response_text = await intake.handle_message(
            message_text, chat_id, _store, _config.agent,
        )
    except Exception as e:
        logger.exception("Error handling message")
        response_text = f"Something went wrong: {e}"

    await _send_chunked(update, response_text)
```

#### Voice Message Handler
```python
async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    if not await _check_auth(update):
        return

    chat_id = update.effective_chat.id

    # Download voice file to temp path
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

    try:
        # Transcribe
        transcript = await voice_module.transcribe(tmp_path, backend=_config.voice.backend)

        # Send "Heard: ..." preview to user
        preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
        await update.message.reply_text(f"Heard: {preview}")

        # Send typing indicator
        await update.effective_chat.send_action("typing")

        # Route through intake
        response_text = await intake.handle_message(
            transcript, chat_id, _store, _config.agent,
        )
    except voice_module.TranscriptionError as e:
        response_text = f"Couldn't transcribe your voice message: {e}"
    except Exception as e:
        logger.exception("Error handling voice message")
        response_text = f"Something went wrong: {e}"
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    await _send_chunked(update, response_text)
```

#### Response Chunking
```python
TELEGRAM_MAX_LENGTH = 4096


async def _send_chunked(update: Update, text: str) -> None:
    """Send a response, splitting into multiple messages if needed."""
    if not text:
        text = "(empty response)"

    chunks = [text[i:i + TELEGRAM_MAX_LENGTH] for i in range(0, len(text), TELEGRAM_MAX_LENGTH)]
    for chunk in chunks:
        await update.message.reply_text(chunk)
```

### Error Handling
- Unauthorized messages: silently dropped (logged at WARNING).
- Voice download failure: catch exception, send user-friendly error.
- Transcription failure: catch `TranscriptionError`, send specific error message.
- Agent/intake errors: catch broad `Exception`, send generic error, log traceback.
- Temp file cleanup: always in `finally` block.
- Never let an exception propagate to the polling loop (it would crash the bot).

### Test Expectations
- Mock `python-telegram-bot` Update, Message, Chat, User, Bot objects.
- Test `_is_authorized` with allowed and disallowed user IDs.
- Test `_handle_text` with authorized user: verify `intake.handle_message` is called.
- Test `_handle_text` with unauthorized user: verify message is dropped.
- Test `_handle_voice`: mock file download + transcribe, verify "Heard:" preview is sent.
- Test `_send_chunked` with short message (single chunk) and long message (multiple chunks).
- Test error handling: mock `intake.handle_message` to raise, verify error message is sent to user.

---

## Additional Spec: main.py (Entry Point)

**File path:** `src/relay/main.py`

### Purpose
Glue module — load config, initialize store, start the Telegram bot, handle graceful shutdown.

### Public Interface

```python
def main() -> None:
    """Entry point for Relay. Load config, init store, start bot."""
    ...
```

### Implementation Details

```python
import asyncio
import logging
import signal
import sys

from relay.config import load_config
from relay.store import Store
from relay import telegram


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    config = load_config()
    logger.info("Loaded config: agent=%s, project=%s", config.agent.name, config.agent.project_dir)

    store = Store(config.storage.db_path)

    async def _run():
        await store.initialize()

        # Handle SIGTERM (from systemd stop) by cancelling the main task.
        # SIGINT (Ctrl+C) is already handled by asyncio.run() which raises KeyboardInterrupt.
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()

        def _sigterm_handler():
            logger.info("Received SIGTERM, shutting down gracefully...")
            main_task.cancel()

        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

        try:
            await telegram.start_bot(config, store)
        except asyncio.CancelledError:
            logger.info("Main task cancelled, cleaning up...")
        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
```

---

## Additional Spec: relay.yaml (Example Configuration)

**File path:** `relay.yaml.example`

```yaml
# Relay v1.0 Configuration
# Copy to relay.yaml and edit values.
# Environment variables can be referenced with ${VAR_NAME}.

telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  allowed_users:
    - 123456789          # Your Telegram user ID

agent:
  name: "isaac"
  project_dir: "/home/ubuntu/isaac_research"
  allowed_tools:
    - "Read"
    - "Glob"
    - "Grep"
    - "Edit"
    - "Write"
    - "Bash"
    - "Agent"
  model: "sonnet"
  timeout: 900           # 15 minutes
  session_ttl: 14400     # 4 hours
  max_budget: 1.0        # $1.00 per agent call
  append_system_prompt: |
    You are being accessed via Telegram on a mobile device.
    Keep responses concise and well-structured for small screens.
    Use short paragraphs and bullet points.

voice:
  backend: "vox"         # "vox" or "openai"

storage:
  db_path: "relay.db"
```

---

## Additional Spec: pyproject.toml

**File path:** `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "relay"
version = "1.0.0"
description = "Telegram-to-Claude Code agent relay"
requires-python = ">=3.11"
dependencies = [
    "python-telegram-bot>=21.0",
    "aiosqlite>=0.19.0",
    "pyyaml>=6.0",
    "openai>=1.0",
]

[project.scripts]
relay = "relay.main:main"

[tool.setuptools.packages.find]
where = ["src"]
```

---

## Additional Spec: requirements.txt

**File path:** `requirements.txt`

```
python-telegram-bot>=21.0
aiosqlite>=0.19.0
pyyaml>=6.0
openai>=1.0
```

---

## Additional Spec: relay.service (systemd)

**File path:** `relay.service`

```ini
[Unit]
Description=Relay - Telegram to Claude Code Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/relay
ExecStart=/home/ubuntu/relay/.venv/bin/python -m relay.main
Restart=always
RestartSec=5
Environment=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=/home/ubuntu/relay/.env

[Install]
WantedBy=multi-user.target
```

The `.env` file (gitignored) contains:
```
TELEGRAM_BOT_TOKEN=your-bot-token-here
OPENAI_API_KEY=your-openai-key-here
```

---

## Additional Spec: tests/conftest.py

**File path:** `tests/conftest.py`

```python
import pytest
import pytest_asyncio
from relay.store import Store


@pytest_asyncio.fixture
async def store(tmp_path):
    """Provide an initialized Store with a temp database."""
    db_path = str(tmp_path / "test.db")
    s = Store(db_path)
    await s.initialize()
    yield s
    await s.close()
```

Test runner: `pytest` with `pytest-asyncio` for async test support.

Add to requirements or a separate `requirements-dev.txt`:
```
pytest>=7.0
pytest-asyncio>=0.21
```

---

## Module Dependency Graph

```
config.py ─────────────────────────────────────┐
store.py ──────────────────────────────────┐    │
voice.py ─────────────────────────────┐    │    │
                                      │    │    │
agent.py ◄────────────────────────────┼── store │
                                      │    │    │
intake.py ◄──────────────────────── agent  │    │
                                      │    │    │
telegram.py ◄──── intake, voice, store, config  │
                                      │    │    │
main.py ◄──────── telegram, store, config ──────┘
```

Build order for parallel implementation:
- **Tier 1 (no deps):** store.py, config.py, voice.py — fully parallel
- **Tier 2 (depends on store):** agent.py
- **Tier 3 (depends on agent):** intake.py
- **Tier 4 (depends on all):** telegram.py, main.py

---

## Summary Checklist

| Component | File | Status |
|---|---|---|
| Store | `src/relay/store.py` | Spec complete |
| Config | `src/relay/config.py` | Spec complete |
| Voice | `src/relay/voice.py` | Spec complete |
| Agent | `src/relay/agent.py` | Spec complete |
| Intake | `src/relay/intake.py` | Spec complete |
| Telegram | `src/relay/telegram.py` | Spec complete |
| Main | `src/relay/main.py` | Spec complete |
| relay.yaml | `relay.yaml.example` | Spec complete |
| SQLite schema | In store.py section | Spec complete |
| pyproject.toml | `pyproject.toml` | Spec complete |
| requirements.txt | `requirements.txt` | Spec complete |
| systemd service | `relay.service` | Spec complete |
| Test fixtures | `tests/conftest.py` | Spec complete |
