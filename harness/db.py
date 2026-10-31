import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_objects(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS session_playbooks(session_id TEXT PRIMARY KEY,version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,customer_id TEXT,channel TEXT,external_id TEXT,mode TEXT,token_hash TEXT UNIQUE,verified INTEGER DEFAULT 0,status TEXT DEFAULT 'active',created_at TEXT);
CREATE TABLE IF NOT EXISTS identity_links(channel TEXT,external_id TEXT,customer_id TEXT,PRIMARY KEY(channel,external_id));
CREATE TABLE IF NOT EXISTS guest_customers(id TEXT PRIMARY KEY,channel TEXT NOT NULL,external_id TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(channel,external_id));
CREATE TABLE IF NOT EXISTS customer_profiles(id TEXT PRIMARY KEY,phone_e164 TEXT NOT NULL UNIQUE,telegram_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS phone_challenges(id TEXT PRIMARY KEY,customer_id TEXT,phone_e164 TEXT NOT NULL,telegram_target TEXT,code_hash TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER DEFAULT 0,consumed_at REAL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS phone_sessions(session_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,session_id TEXT,client_id TEXT,text TEXT,reply TEXT,result TEXT,created_at TEXT,UNIQUE(session_id,client_id));
CREATE TABLE IF NOT EXISTS facts(id TEXT,customer_id TEXT,key TEXT,value TEXT,unit TEXT,source_type TEXT,source_ref TEXT,channel TEXT,observed_at TEXT,valid_until TEXT,status TEXT,confidence REAL,PRIMARY KEY(id,customer_id));
CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY,session_id TEXT,tool TEXT,args TEXT,result TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS traces(turn_id TEXT PRIMARY KEY,session_id TEXT,data TEXT);
CREATE TABLE IF NOT EXISTS customer_sequences(customer_id TEXT PRIMARY KEY,next_seq INTEGER DEFAULT 0,processed_seq INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS memory_jobs(turn_id TEXT PRIMARY KEY,customer_id TEXT,seq INTEGER,payload TEXT,status TEXT DEFAULT 'pending',lease_until REAL DEFAULT 0,error TEXT,UNIQUE(customer_id,seq));
CREATE TABLE IF NOT EXISTS journey_events(id TEXT PRIMARY KEY,customer_id TEXT,type TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY,session_id TEXT,status TEXT,summary TEXT,receipt TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS callbacks(id TEXT PRIMARY KEY,customer_id TEXT,session_id TEXT,due_at TEXT,status TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS auth_attempts(identity TEXT,source TEXT,at REAL);
CREATE TABLE IF NOT EXISTS staff_sessions(token_hash TEXT PRIMARY KEY,csrf TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS provider_events(id TEXT PRIMARY KEY,channel TEXT,payload TEXT,status TEXT,received_at REAL,error TEXT);
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,channel TEXT,target TEXT,text TEXT,status TEXT,receipt TEXT,attempts INTEGER DEFAULT 0,reply_markup TEXT);
CREATE TABLE IF NOT EXISTS faults(tool TEXT PRIMARY KEY,mode TEXT,count INTEGER);
CREATE TABLE IF NOT EXISTS sales_improvements(run_id TEXT NOT NULL,action_id TEXT NOT NULL,status TEXT NOT NULL,script TEXT NOT NULL,notes TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(run_id,action_id));
"""


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        old_handoff = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='handoffs'").fetchone()
        if old_handoff and "session_id TEXT UNIQUE" in old_handoff[0]:
            self.backup()
            self.conn.executescript(
                "BEGIN IMMEDIATE; ALTER TABLE handoffs RENAME TO handoffs_v1; CREATE TABLE handoffs(id TEXT PRIMARY KEY,session_id TEXT,status TEXT,summary TEXT,receipt TEXT,created_at TEXT); INSERT INTO handoffs SELECT * FROM handoffs_v1; DROP TABLE handoffs_v1; COMMIT;"
            )
        self.conn.executescript(SCHEMA)
        if "reply_markup" not in {row["name"] for row in self.conn.execute("PRAGMA table_info(outbox)")}:
            self.conn.execute("ALTER TABLE outbox ADD COLUMN reply_markup TEXT")
        self.conn.execute("INSERT OR REPLACE INTO settings VALUES('schema_version','4')")
        self.conn.execute("INSERT OR IGNORE INTO settings VALUES('clock','\"2026-10-31\"')")
        self.conn.execute("INSERT OR IGNORE INTO settings VALUES('playbook','\"v1\"')")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql, args=()):
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args)

    def setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def backup(self):
        if self.path == ":memory:":
            return None
        import time

        dest = f"{self.path}.{time.time_ns()}.bak"
        with self.lock, sqlite3.connect(dest) as target:
            self.conn.backup(target)
        return dest
