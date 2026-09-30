# backend/db.py
import sqlite3
from pathlib import Path

# SARAH_AI_V11_Backend_Full/backend/db.py
BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"
DB_PATH = DATA_DIR / "sarah_ai_v9.db"  # filename kept from V9 so saved memory carries over


def get_connection():
    """
    Returns a SQLite connection with foreign keys enabled.
    Creates the data directory and database file if needed.
    """
    DATA_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # dict-like rows
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    """
    Create all core tables if they don't exist yet.
    Run this once on startup.
    """
    conn = get_connection()
    cur = conn.cursor()

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
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # 5) Conversation history
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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


        
    conn.commit()
    conn.close()
