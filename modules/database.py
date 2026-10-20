import sqlite3
import os
import threading

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'trading_bot.db')
_local = threading.local()

class SQLiteCursorWrapper:
    """Wrapper to make SQLite cursor compatible with Postgres-style %s placeholders"""
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        # Convert %s placeholder to ? for sqlite
        query = query.replace('%s', '?')
        # Handle PostgreSQL INTERVAL '...' syntax in queries
        if "INTERVAL '12 hours'" in query:
            query = query.replace("NOW() - INTERVAL '12 hours'", "datetime('now', '-12 hours')")
        if "INTERVAL '24 hours'" in query:
            query = query.replace("NOW() - INTERVAL '24 hours'", "datetime('now', '-24 hours')")
        if "NOW()" in query:
            query = query.replace("NOW()", "datetime('now')")
        if "CURRENT_DATE" in query:
            query = query.replace("CURRENT_DATE", "date('now')")

        if params is not None:
            return self._cursor.execute(query, params)
        return self._cursor.execute(query)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()

class SQLiteConnWrapper:
    """Wrapper to provide cursor with dict-like or standard row access"""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, cursor_factory=None):
        if cursor_factory is not None:
            self._conn.row_factory = sqlite3.Row
        else:
            self._conn.row_factory = None
        return SQLiteCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

def get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None:
        conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
        _local.conn = SQLiteConnWrapper(conn)
    return _local.conn

def release_conn(conn):
    # For SQLite thread-local connections, keep open per-thread
    pass

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                timeframe TEXT,
                pattern TEXT,
                entry_price REAL,
                sl_price REAL,
                tp1 REAL, tp2 REAL, tp3 REAL,
                rr REAL,
                status TEXT DEFAULT 'Waiting Entry',
                reason TEXT,
                tech_score INTEGER,
                quant_score INTEGER,
                deriv_score INTEGER,
                smc_score INTEGER DEFAULT 0,
                z_score REAL DEFAULT 0,
                zeta_score REAL DEFAULT 0,
                obi REAL DEFAULT 0,
                basis REAL,
                btc_bias TEXT,
                tech_reasons TEXT,
                quant_reasons TEXT,
                deriv_reasons TEXT,
                smc_reasons TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                entry_hit_at TIMESTAMP,
                closed_at TIMESTAMP,
                exit_price REAL,
                message_id TEXT,
                channel_id TEXT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key_name TEXT PRIMARY KEY,
                value_text TEXT
            );
        """)

        conn.commit()
        print(f"[OK] SQLite Database Ready: {DB_FILE}")
    except Exception as e:
        print(f"[ERROR] SQLite Init Error: {e}")
    finally:
        conn.close()

def get_active_signals():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, timeframe 
            FROM trades 
            WHERE status NOT LIKE '%Closed%' 
            AND status NOT LIKE '%Cancelled%'
            AND status NOT LIKE '%Stop Loss%'
        """)
        return {(r[0], r[1]) for r in cur.fetchall()}
    except Exception as e:
        print(f"⚠️ Error fetching active signals: {e}")
        return set()
    finally:
        release_conn(conn)