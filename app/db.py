import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PgConn:
    """Thin wrapper so call sites can use sqlite3-style conn.execute(...)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return cur

    def executemany(self, sql, seq_of_params):
        sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def executescript(self, sql):
        cur = self._raw.cursor()
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
        return cur

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def get_conn() -> PgConn:
    raw = psycopg2.connect(DATABASE_URL)
    return PgConn(raw)


SCHEMA = """
CREATE TABLE IF NOT EXISTS consoles (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    hourly_rate REAL NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'available',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS time_options (
    id SERIAL PRIMARY KEY,
    label TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS add_ons (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    console_type TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    day_key TEXT NOT NULL,
    console_id INTEGER NOT NULL REFERENCES consoles(id),
    console_name TEXT NOT NULL,
    console_type TEXT,
    customer_name TEXT NOT NULL,
    phone TEXT,
    game_name TEXT,
    rate_per_hour REAL NOT NULL,
    duration_minutes INTEGER NOT NULL,
    add_ons TEXT NOT NULL DEFAULT '[]',
    amount_due REAL NOT NULL,
    amount_paid REAL NOT NULL DEFAULT 0,
    payment_status TEXT NOT NULL DEFAULT 'unpaid',
    payment_method TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    actual_end_time TEXT,
    extensions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS qr_image (
    id INTEGER PRIMARY KEY DEFAULT 1,
    data BYTEA NOT NULL,
    content_type TEXT NOT NULL
)
"""

DEFAULT_CONSOLES = [
    ("PS5 - 1", "PS5", 100, 0),
    ("PS5 - 2", "PS5", 100, 1),
    ("PS4 - 1", "PS4", 100, 2),
    ("PS4 - 2", "PS4", 100, 3),
    ("Racing Wheel", "Racing Wheel", 100, 4),
]

DEFAULT_TIME_OPTIONS = [("30 mins", 30), ("45 mins", 45), ("1 hour", 60), ("1.5 hours", 90), ("2 hours", 120)]

DEFAULT_SETTINGS = {
    "cafeName": "RGS Gaming Zone",
    "currencySymbol": "₹",
    "paymentQrPath": "",
}


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()

        if conn.execute("SELECT COUNT(*) c FROM consoles").fetchone()["c"] == 0:
            ts = now_iso()
            conn.executemany(
                "INSERT INTO consoles (name, type, hourly_rate, sort_order, status, created_at) VALUES (?, ?, ?, ?, 'available', ?)",
                [(n, t, r, o, ts) for n, t, r, o in DEFAULT_CONSOLES],
            )

        if conn.execute("SELECT COUNT(*) c FROM time_options").fetchone()["c"] == 0:
            conn.executemany("INSERT INTO time_options (label, minutes) VALUES (?, ?)", DEFAULT_TIME_OPTIONS)

        if conn.execute("SELECT COUNT(*) c FROM add_ons").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO add_ons (name, amount, console_type) VALUES (?, ?, ?)",
                ("VR headset", 20, "Racing Wheel"),
            )

        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (k, v))

        conn.commit()
    finally:
        conn.close()
