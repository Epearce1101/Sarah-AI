import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "sarah.db")

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, "r") as f:
            sql = f.read()
        self.conn.executescript(sql)
        self.conn.commit()

    def query(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        self.conn.commit()
        return cur

    def set_setting(self, key, value):
        self.query("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def get_setting(self, key, default=None):
        cur = self.query("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def save_memory(self, category, content):
        self.query("INSERT INTO memory (category, content) VALUES (?, ?)", (category, content))

    def get_memory(self, category=None):
        if category:
            cur = self.query("SELECT * FROM memory WHERE category=? ORDER BY id DESC", (category,))
        else:
            cur = self.query("SELECT * FROM memory ORDER BY id DESC")
        return cur.fetchall()

    def log(self, level, message, context=""):
        self.query("INSERT INTO logs (level, message, context) VALUES (?, ?, ?)",
                   (level, message, context))
