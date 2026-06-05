import aiosqlite
import logging

logger = logging.getLogger(__name__)

DB_FILE = "updown_tracker.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        # Positions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                pyth_id TEXT NOT NULL,
                direction TEXT NOT NULL, -- 'UP' or 'DOWN'
                ref_price REAL NOT NULL, -- Previous Close Price
                ref_timestamp INTEGER NOT NULL, -- Timestamp of the reference candle
                status TEXT DEFAULT 'active', -- 'active' or 'closed'
                last_warning_distance REAL DEFAULT 999.0, -- Track progressive warnings
                created_at TEXT NOT NULL,
                title TEXT DEFAULT ''
            )
        """)
        
        # Try to add title column if table already exists without it
        try:
            await db.execute("ALTER TABLE positions ADD COLUMN title TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass # Column already exists
        
        # Settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                warning_zone_pct REAL DEFAULT 1.0, -- Alert when price is within 1% of reference
                step_pct REAL DEFAULT 0.1 -- Send progressive alerts every 0.1%
            )
        """)
        
        # Insert default settings if not exists
        await db.execute("""
            INSERT OR IGNORE INTO settings (id, warning_zone_pct, step_pct) 
            VALUES (1, 1.0, 0.1)
        """)
        
        # Processed trades/transactions to avoid duplicate notifications
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_trades (
                tx_hash TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)

        # Tracked trades with AI commentary
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_trades (
                tx_hash TEXT PRIMARY KEY,
                username TEXT,
                telegram_tag TEXT,
                title TEXT,
                side TEXT,
                size REAL,
                price REAL,
                outcome TEXT,
                ai_comment TEXT,
                analysis_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        await db.commit()

# --- Positions ---

async def add_position(symbol: str, pyth_id: str, direction: str, ref_price: float, ref_timestamp: int, created_at: str, title: str = ""):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            """INSERT INTO positions 
               (symbol, pyth_id, direction, ref_price, ref_timestamp, status, created_at, title) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, pyth_id, direction, ref_price, ref_timestamp, 'active', created_at, title)
        )
        await db.commit()
        return cursor.lastrowid

async def get_active_positions():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM positions WHERE status = 'active'") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_warning_distance(position_id: int, distance_pct: float):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE positions SET last_warning_distance = ? WHERE id = ?",
            (distance_pct, position_id)
        )
        await db.commit()

async def close_position(position_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE positions SET status = 'closed' WHERE id = ?", (position_id,))
        await db.commit()

async def delete_position(position_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        await db.commit()

async def update_position_title_if_empty(symbol: str, direction: str, title: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE positions SET title = ? WHERE symbol = ? AND direction = ? AND (title IS NULL OR title = '') AND status = 'active'",
            (title, symbol, direction)
        )
        await db.commit()

# --- Settings ---

async def get_settings():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT warning_zone_pct, step_pct FROM settings WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {"warning_zone_pct": 1.0, "step_pct": 0.1}

async def update_settings(warning_zone_pct: float, step_pct: float):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE settings SET warning_zone_pct = ?, step_pct = ? WHERE id = 1",
            (warning_zone_pct, step_pct)
        )
        await db.commit()

# --- Processed Trades ---

async def is_trade_processed(tx_hash: str) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT 1 FROM processed_trades WHERE tx_hash = ?", (tx_hash,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def mark_trade_processed(tx_hash: str, processed_at: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO processed_trades (tx_hash, processed_at) VALUES (?, ?)",
            (tx_hash, processed_at)
        )
        await db.commit()

# --- Tracked Trades with AI Commentary ---

async def add_tracked_trade(tx_hash: str, username: str, telegram_tag: str, title: str, side: str, size: float, price: float, outcome: str, ai_comment: str, analysis_json: str, created_at: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """INSERT OR IGNORE INTO tracked_trades 
               (tx_hash, username, telegram_tag, title, side, size, price, outcome, ai_comment, analysis_json, created_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_hash, username, telegram_tag, title, side, size, price, outcome, ai_comment, analysis_json, created_at)
        )
        await db.commit()

async def get_tracked_trades(limit: int = 50):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tracked_trades ORDER BY created_at DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
