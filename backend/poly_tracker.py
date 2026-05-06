"""
Polymarket Auto Position Tracker
Periodically checks the user's Polymarket wallet for active positions
and automatically adds matching ones to the tracking system.
"""
import asyncio
import httpx
import logging
import re
from datetime import datetime
import pytz
import database
import pyth_client
from telegram_bot import send_notification

logger = logging.getLogger(__name__)

# User's Polymarket wallet address
TRACKED_WALLET = "0xab40bd6ef2ecb420c10d222f0cd6b1dd54d7b57d"
POLYMARKET_DATA_API = "https://data-api.polymarket.com"

# Check every 5 minutes
CHECK_INTERVAL = 300

# Maps Polymarket slug keywords to our internal symbols
# Format: { slug_keyword: (symbol, bet_type) }
# "up-or-down" slugs → close bet, "opens-up-or-down" slugs → open bet
SLUG_SYMBOL_MAP = {
    "spx-up-or-down": ("SPY", "close"),
    "spy-up-or-down": ("SPY", "close"),
    "sp-500-up-or-down": ("SPY", "close"),
    "spx-opens-up-or-down": ("SPY", "open"),
    "sp-500-opens": ("SPY", "open"),
    "pltr-up-or-down": ("PLTR", "close"),
    "coin-up-or-down": ("COIN", "close"),
    "hood-up-or-down": ("HOOD", "close"),
    "tsla-up-or-down": ("TSLA", "close"),
    "nvda-up-or-down": ("NVDA", "close"),
    "aapl-up-or-down": ("AAPL", "close"),
    "amzn-up-or-down": ("AMZN", "close"),
    "meta-up-or-down": ("META", "close"),
    "googl-up-or-down": ("GOOGL", "close"),
    "msft-up-or-down": ("MSFT", "close"),
    "nflx-up-or-down": ("NFLX", "close"),
    "abnb-up-or-down": ("ABNB", "close"),
    "open-up-or-down": ("OPEN", "close"),
    "rklb-up-or-down": ("RKLB", "close"),
    "ewy-up-or-down": ("EWY", "close"),
    "wti-up-or-down": ("WTI", "close"),
    "gold-up-or-down": ("XAU", "close"),
    "xau-up-or-down": ("XAU", "close"),
    "silver-up-or-down": ("XAG", "close"),
    "xag-up-or-down": ("XAG", "close"),
    "natural-gas-up-or-down": ("NG", "close"),
    "russell-2000-up-or-down": ("RUT", "close"),
    "hang-seng-up-or-down": ("HSI", "close"),
    "dow-jones-up-or-down": ("DIA", "close"),
    "dax-up-or-down": ("DAX", "close"),
    "nikkei-up-or-down": ("NKY", "close"),
    "ftse-up-or-down": ("UKX", "close"),
    "nya-up-or-down": ("NYA", "close"),
    "bitcoin-up-or-down": ("BTC", "close"),
}


def _match_slug_to_symbol(slug: str):
    """Try to match a Polymarket event slug to our internal symbol."""
    slug_lower = slug.lower()
    for slug_key, (symbol, bet_type) in SLUG_SYMBOL_MAP.items():
        if slug_key in slug_lower:
            return symbol, bet_type
    return None, None


async def fetch_wallet_positions():
    """Fetch all positions for the tracked wallet from Polymarket Data API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{POLYMARKET_DATA_API}/positions",
                params={"user": TRACKED_WALLET},
                timeout=15.0
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch Polymarket positions: {e}")
        return []


async def _add_position_from_poly(symbol: str, direction: str, bet_type: str, title: str):
    """Resolves price and adds position to our tracking system."""
    pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
    if not pyth_id:
        logger.warning(f"Could not resolve Pyth ID for {symbol}, skipping auto-track")
        return False
    
    # Get reference price
    if bet_type == 'close':
        from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
        ref_price = await pyth_client.get_historical_candle_price(
            full_symbol, pyth_id, from_ts, to_ts, price_type='close'
        )
    else:
        from_ts, to_ts = pyth_client.get_previous_open_times(symbol)
        ref_price = await pyth_client.get_historical_candle_price(
            full_symbol, pyth_id, from_ts, to_ts, price_type='open'
        )
    
    if ref_price is None:
        logger.warning(f"Could not get reference price for {symbol}, skipping auto-track")
        return False
    
    # Build direction string
    db_direction = f"OPEN_{direction}" if bet_type == 'open' else direction
    
    now_str = datetime.now().isoformat()
    await database.add_position(
        symbol=symbol,
        pyth_id=pyth_id,
        direction=db_direction,
        ref_price=ref_price,
        ref_timestamp=to_ts,
        created_at=now_str
    )
    
    # Get current price for notification
    current_price = await pyth_client.get_active_price(symbol, pyth_id)
    diff_pct = 0.0
    if current_price:
        diff_pct = ((current_price - ref_price) / ref_price) * 100
    
    is_winning = (direction == 'UP' and current_price and current_price > ref_price) or \
                 (direction == 'DOWN' and current_price and current_price < ref_price)
    status = "KAZANIYOR 🟢" if is_winning else "KAYBEDİYOR 🔴"
    current_price_str = f"${current_price:.4f}" if current_price else "Bilinmiyor"
    
    msg = (
        f"🔄 <b>Otomatik Takip: {symbol} {db_direction}</b>\n"
        f"<i>({title})</i>\n\n"
        f"<b>Durum:</b> {status}\n"
        f"<b>Anlık:</b> {current_price_str}\n"
        f"<b>Fark:</b> %{diff_pct:.2f}\n"
        f"<b>Referans:</b> ${ref_price:.4f}"
    )
    await send_notification(msg)
    return True


async def sync_positions_loop():
    """Main loop: periodically check Polymarket and auto-add new positions."""
    logger.info("Polymarket Auto Position Tracker started.")
    
    while True:
        try:
            # Don't sync after market close — no point adding positions that expire immediately
            et_tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(et_tz)
            total_minutes = now_et.hour * 60 + now_et.minute
            is_weekend = now_et.weekday() >= 5
            # Stock market: 09:30-16:00 ET, but allow from 09:00 for pre-market
            # Don't sync after 16:00 ET (23:00 TR) for stocks
            if is_weekend or total_minutes >= 960 or total_minutes < 540:  # 960=16:00, 540=09:00
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            positions = await fetch_wallet_positions()
            if not positions:
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # Get today's date for filtering
            today_str = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
            
            # Get already tracked symbols to avoid duplicates
            existing = await database.get_active_positions()
            existing_symbols = {(p['symbol'], p['direction']) for p in existing}
            
            for pos in positions:
                # Skip resolved/redeemable positions
                if pos.get('redeemable', False):
                    continue
                    
                # Only today's bets
                end_date = pos.get('endDate', '')
                if end_date != today_str:
                    continue
                
                slug = pos.get('eventSlug', '') or pos.get('slug', '')
                outcome = pos.get('outcome', '')  # "Up" or "Down"
                
                # Try to match the slug to our known markets
                symbol, bet_type = _match_slug_to_symbol(slug)
                if not symbol:
                    continue
                
                direction = outcome.upper()  # "Up" -> "UP", "Down" -> "DOWN"
                if direction not in ('UP', 'DOWN'):
                    continue
                    
                db_direction = f"OPEN_{direction}" if bet_type == 'open' else direction
                
                # Skip if already tracked
                if (symbol, db_direction) in existing_symbols:
                    continue
                
                logger.info(f"Auto-tracking new position: {symbol} {db_direction} from Polymarket")
                title = pos.get('title', slug)
                await _add_position_from_poly(symbol, direction, bet_type, title)
                
        except Exception as e:
            logger.error(f"Error in poly sync loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)


def start_sync_task():
    """Start the Polymarket sync background task."""
    asyncio.create_task(sync_positions_loop())
