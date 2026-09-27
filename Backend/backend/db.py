# backend/db.py
import sqlite3

from backend.config import settings as _settings

DB_PATH = _settings.db_path


def get_connection():
    """
    Returns a SQLite connection with foreign keys enabled.
    Creates the data directory and database file if needed.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # dict-like rows
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


_FTS_TABLES = {
    # fts table: (source table, indexed columns)
    "messages_fts": ("messages", ("content",)),
    "memories_fts": ("memories", ("content", "tags")),
}


def fts_available(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    return row is not None


def _ensure_fts(cur) -> None:
    """Full-text indexes for message search and memory retrieval.

    External-content FTS5 tables mirror `messages`/`memories` via triggers,
    so search ranks by relevance instead of scanning with LIKE '%...%'.
    New indexes are backfilled once. If this SQLite lacks FTS5, callers fall
    back to LIKE.
    """
    for fts, (source, columns) in _FTS_TABLES.items():
        exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (fts,)
        ).fetchone()
        cols = ", ".join(columns)
        new_cols = ", ".join(f"new.{c}" for c in columns)
        old_cols = ", ".join(f"old.{c}" for c in columns)
        try:
            cur.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
                f"{cols}, content='{source}', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        except sqlite3.OperationalError:
            return  # no FTS5 in this build
        cur.executescript(
            f"""
            CREATE TRIGGER IF NOT EXISTS {fts}_ai AFTER INSERT ON {source} BEGIN
                INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_cols});
            END;
            CREATE TRIGGER IF NOT EXISTS {fts}_ad AFTER DELETE ON {source} BEGIN
                INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_cols});
            END;
            CREATE TRIGGER IF NOT EXISTS {fts}_au AFTER UPDATE ON {source} BEGIN
                INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_cols});
                INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_cols});
            END;
            """
        )
        if not exists:
            cur.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")


def init_db():
    """
    Create all core tables if they don't exist yet.
    Run this once on startup.
    """
    conn = get_connection()
    cur = conn.cursor()

    # WAL lets the chat request read while background summarizer/mood/telemetry
    # writes are in flight instead of serializing on the rollback journal.
    # The setting is persistent, so this is a no-op after the first boot.
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        pass

    # 1) Settings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # 2) Long-term memory
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT,
            role            TEXT NOT NULL,  -- 'user' or 'assistant' or 'system'
            content         TEXT NOT NULL,
            tags            TEXT,
            importance      INTEGER DEFAULT 0,
            embedding       BLOB,           -- optional for later
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at    DATETIME
        );
        """
    )

    # 3) Logs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            level       TEXT NOT NULL,  -- INFO, WARN, ERROR
            source      TEXT,           -- 'backend', 'electron', 'speech', etc.
            message     TEXT NOT NULL,
            payload_json TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # 4) Skills / plugins
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            slug        TEXT NOT NULL UNIQUE,
            description TEXT,
            enabled     INTEGER NOT NULL DEFAULT 1,
            config_json TEXT,
            path        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # B4 P3: idempotent migration for V8 DBs that pre-date the `path` column.
    try:
        cur.execute("ALTER TABLE skills ADD COLUMN path TEXT")
    except sqlite3.OperationalError:
        pass

    # 5) Conversation history
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT,
            project_id      INTEGER,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role            TEXT NOT NULL,   -- 'user' / 'assistant' / 'system'
            content         TEXT NOT NULL,
            meta_json       TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """
    )

    # Idempotent migration: `pinned` used to be added lazily on the first pin,
    # so search/pinned queries had to guard against the column not existing.
    try:
        cur.execute("ALTER TABLE messages ADD COLUMN pinned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Every chat turn reads recent messages for one conversation; without this
    # index each lookup is a full table scan that grows with total history.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
        "ON messages(conversation_id, id)"
    )
#-- Reflections: Sarah learning from interactions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            conversation_id INTEGER,
            message_id INTEGER,
            problem TEXT,
            improvement TEXT,
            new_rule TEXT,
            meta_json TEXT
        );
        """
    )

#-- Tasks: agentic task manager
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending', -- pending | in_progress | done | blocked
            priority INTEGER DEFAULT 0,
            context_json TEXT,
            result_summary TEXT
        );
        """
    )

    # 6) Projects: file/folder context storage
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            root_path TEXT,
            git_repo_path TEXT,
            current_branch TEXT,
            primary_language TEXT,
            framework TEXT,
            package_manager TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS project_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT,
            file_size INTEGER,
            content TEXT,
            is_current_file INTEGER DEFAULT 0,
            git_status TEXT,
            last_modified DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_files_project "
        "ON project_files(project_id)"
    )

    # 7) Git commits tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS git_commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            commit_hash TEXT NOT NULL,
            author TEXT,
            message TEXT,
            timestamp DATETIME,
            files_changed TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """
    )

    # 8) Project conversations junction table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS project_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            UNIQUE(project_id, conversation_id)
        );
        """
    )

    # 9) Conversation timezones
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_timezones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL UNIQUE,
            timezone TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """
    )

    _ensure_fts(cur)

    # mood_state (created lazily by backend.mood) has no FK to conversations,
    # so rows for deleted conversations used to accumulate. Prune them.
    try:
        cur.execute(
            "DELETE FROM mood_state WHERE conversation_id NOT IN (SELECT id FROM conversations)"
        )
    except sqlite3.OperationalError:
        pass  # table not created yet

    conn.commit()
    conn.close()
