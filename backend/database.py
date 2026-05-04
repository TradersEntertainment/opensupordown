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
                created_at TEXT NOT NULL
            )
        """)
        
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
        
        await db.commit()

# --- Positions ---

async def add_position(symbol: str, pyth_id: str, direction: str, ref_price: float, ref_timestamp: int, created_at: str):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            """INSERT INTO positions 
               (symbol, pyth_id, direction, ref_price, ref_timestamp, status, created_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, pyth_id, direction, ref_price, ref_timestamp, 'active', created_at)
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
