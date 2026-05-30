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
from telegram_bot import send_notification, call_groq_api

logger = logging.getLogger(__name__)

# User's Polymarket wallet address
TRACKED_WALLET = "0xab40bd6ef2ecb420c10d222f0cd6b1dd54d7b57d"
POLYMARKET_DATA_API = "https://data-api.polymarket.com"

# Profiles to track (Trades and Comments)
USER_PROFILES = {
    "0xab40bd6ef2ecb420c10d222f0cd6b1dd54d7b57d": {
        "name": "mrsdrfinance",
        "telegram": "@artniyetli"
    },
    "0xa1d57d329227c75b12b09f927fb3d6d6ef8f1343": {
        "name": "1kto1m",
        "telegram": "@rainingmann"
    }
}

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
    "mu-up-or-down": ("MU", "close"),
    "micron-up-or-down": ("MU", "close"),
}


def _match_slug_to_symbol(slug: str):
    """Try to match a Polymarket event slug to our internal symbol."""
    slug_lower = slug.lower()
    for slug_key, (symbol, bet_type) in SLUG_SYMBOL_MAP.items():
        if slug_key in slug_lower:
            return symbol, bet_type
    return None, None


def _parse_binary_market(slug: str, title: str, outcome: str):
    """
    Parses a binary market (Yes/No) like 'WTI Closes Above $80.00' or 'PLTR Closes Above $22'
    or monthly/weekly hit bets ('WTI hit in May', 'XAU hit in week 18').
    Returns: (symbol, direction, ref_price, bet_type) or (None, None, None, None)
    """
    slug_lower = slug.lower()
    title_lower = title.lower()
    outcome_upper = outcome.upper()
    
    # 1. Check if it's a binary market (outcomes should be YES or NO)
    if outcome_upper not in ('YES', 'NO'):
        return None, None, None, None
        
    # 2. Check if it's a closes-above / closes-below / hit / exceed / touch / week / month / price market
    is_binary = any(kw in slug_lower or kw in title_lower for kw in ('closes-above', 'closes-below', 'hit', 'close above', 'close below', 'touch', 'exceed', 'week', 'month', 'price'))
    if not is_binary:
        return None, None, None, None
        
    # 3. Identify the symbol
    symbol = None
    for slug_key, (sym, _) in SLUG_SYMBOL_MAP.items():
        # Get the asset name token (e.g. 'pltr' from 'pltr-up-or-down')
        prefix = slug_key.split('-')[0]
        if prefix in slug_lower:
            symbol = sym
            break
            
    if not symbol:
        return None, None, None, None
        
    # 4. Extract target price (ref_price) from title or slug
    # We want to search for the price target while ignoring week numbers (e.g. week 18) and years (e.g. 2026)
    
    # Heuristic A: Look for a number with a dollar sign like "$80.00", "$2400"
    match = re.search(r"\$([\d\.,]+)", title_lower)
    if match:
        ref_price_str = match.group(1).replace(',', '')
        try:
            ref_price = float(ref_price_str)
            # Filter out year numbers if they happen to have a dollar sign
            if ref_price not in (2025, 2026, 2027, 2028):
                return _build_binary_return(symbol, outcome_upper, slug_lower, title_lower, ref_price)
        except ValueError:
            pass
            
    # Heuristic B: Look for patterns like "above $80.00", "below 75.5", "hit 80", "exceed 80", "touch 80"
    match = re.search(r"(?:above|below|hit|exceed|touch)\s+\$?([\d\.]+)", title_lower)
    if not match:
        match = re.search(r"(?:above|below|hit|exceed|touch)-([\d\.]+)", slug_lower)
        
    if match:
        try:
            ref_price = float(match.group(1))
            return _build_binary_return(symbol, outcome_upper, slug_lower, title_lower, ref_price)
        except ValueError:
            pass
            
    # Heuristic C: Look for general price numbers by cleaning the text of week and year numbers
    cleaned_title = re.sub(r"(?:week|year|202\d)\s*\-?\d+", "", title_lower)
    match = re.search(r"\b([\d\.]+)\b", cleaned_title)
    if match:
        try:
            ref_price = float(match.group(1))
            if ref_price not in (2025, 2026, 2027, 2028):
                return _build_binary_return(symbol, outcome_upper, slug_lower, title_lower, ref_price)
        except ValueError:
            pass

    return None, None, None, None


def _build_binary_return(symbol, outcome_upper, slug_lower, title_lower, ref_price):
    is_below_market = 'below' in slug_lower or 'below' in title_lower
    if is_below_market:
        direction = 'NO' if outcome_upper == 'YES' else 'YES'
    else:
        direction = outcome_upper
    return symbol, direction, ref_price, 'close'




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


async def _add_position_from_poly(symbol: str, direction: str, bet_type: str, title: str, ref_price: float = None):
    """Resolves price and adds position to our tracking system."""
    pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
    if not pyth_id:
        logger.warning(f"Could not resolve Pyth ID for {symbol}, skipping auto-track")
        return False
    
    # Get reference price
    if ref_price is None:
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
    else:
        import time
        to_ts = int(time.time())
    
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
    
    is_up_bet = direction in ('UP', 'YES')
    is_winning = (is_up_bet and current_price and current_price > ref_price) or \
                 (not is_up_bet and current_price and current_price < ref_price)
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
            
            
            
            # Get already tracked symbols to avoid duplicates
            existing = await database.get_active_positions()
            existing_symbols = {(p['symbol'], p['direction']) for p in existing}
            
            for pos in positions:
                # Skip resolved/redeemable positions
                if pos.get('redeemable', False):
                    continue
                    
                # Skip expired positions (endDate < today), track all active ones
                end_date = pos.get('endDate', '')
                if end_date:
                    try:
                        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
                        today_dt = datetime.now(pytz.timezone('US/Eastern')).date()
                        if end_dt < today_dt:
                            continue  # Already expired, skip
                    except Exception:
                        pass  # If we can't parse date, still try to track it
                
                slug = pos.get('eventSlug', '') or pos.get('slug', '')
                outcome = pos.get('outcome', '')  # "Up", "Down", "Yes", "No"
                title = pos.get('title', slug)
                
                # 1. Try to match standard Up/Down markets
                symbol, bet_type = _match_slug_to_symbol(slug)
                ref_price_val = None
                
                if symbol:
                    direction = outcome.upper()  # "Up" -> "UP", "Down" -> "DOWN"
                    if direction not in ('UP', 'DOWN'):
                        symbol = None # Reset to try binary market parsing instead
                
                # 2. Try to match binary markets (Yes/No)
                if not symbol:
                    symbol, direction, ref_price_val, bet_type = _parse_binary_market(slug, title, outcome)
                    
                if not symbol:
                    continue
                    
                db_direction = f"OPEN_{direction}" if bet_type == 'open' else direction
                
                # Skip if already tracked
                if (symbol, db_direction) in existing_symbols:
                    continue
                
                logger.info(f"Auto-tracking new position: {symbol} {db_direction} from Polymarket")
                await _add_position_from_poly(symbol, direction, bet_type, title, ref_price=ref_price_val)
                
        except Exception as e:
            logger.error(f"Error in poly sync loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)


async def fetch_user_trades(wallet: str):
    """Fetch all trades for a user from the Polymarket Data API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{POLYMARKET_DATA_API}/trades",
                params={"user": wallet, "limit": 10},
                timeout=15.0
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Error fetching trades: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch trades for {wallet}: {e}")
    return []


def _is_crypto_trade(trade: dict) -> bool:
    """Detect if a trade is related to crypto."""
    title = (trade.get("title") or "").lower()
    slug = (trade.get("slug") or "").lower()
    event_slug = (trade.get("eventSlug") or "").lower()
    
    crypto_keywords = {
        "btc", "eth", "sol", "bitcoin", "ethereum", "solana", "crypto", "binance", 
        "coinbase", "doge", "pepe", "memecoin", "crypto", "coin", "cardano", "ripple", 
        "xrp", "shib", "uniswap", "layer2", "blockchain", "gavax", "avax", "link", 
        "chainlink", "fantom", "near", "polkadot"
    }
    
    for word in crypto_keywords:
        pattern = rf"\b{word}\b"
        if re.search(pattern, title) or word in slug or word in event_slug:
            return True
    return False


async def generate_friendly_advice(username: str, telegram_tag: str, trade: dict) -> str:
    """Generate a friendly, finance-focused AI comment for the trade."""
    side = trade.get("side", "BUY")
    title = trade.get("title", "")
    size = trade.get("size", 0)
    price = trade.get("price", 0.0)
    outcome = trade.get("outcome", "")
    
    prompt = (
        "You are a finance-savvy, friendly, and witty quantitative AI trading assistant and the voice of 'Sinyal Fabrikası'.\n"
        "A user from our group has just made a trade on Polymarket. Make a friendly, supportive, and insightful "
        "comment in Turkish, addressing them by their Telegram tag.\n\n"
        "Trade Details:\n"
        f"Trader (Polymarket Username): {username}\n"
        f"Trader (Telegram Tag): {telegram_tag}\n"
        f"Action: {side} (e.g. BUY/SELL)\n"
        f"Market Title: {title}\n"
        f"Size: {size} contracts\n"
        f"Price: ${price:.4f} per contract\n"
        f"Outcome Bet: {outcome}\n\n"
        "Instructions:\n"
        "1. Be friendly, encouraging, and slightly witty.\n"
        "2. Tag them using their Telegram tag (e.g. @artniyetli or @rainingmann).\n"
        "3. Give them some clever, short financial advice or a quick quantitative perspective about this trade.\n"
        "4. Keep the message concise (max 3-4 sentences) and format it beautifully with HTML tags allowed in Telegram (bold, italic, code).\n"
        "Strictly reply in Turkish."
    )
    
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"{username} just {side.lower()} {outcome} on {title}."}
    ]
    
    try:
        response = await call_groq_api(messages)
        return response
    except Exception as e:
        logger.error(f"Failed to generate AI advice with Groq: {e}")
        return (
            f"🔔 <b>Yeni İşlem:</b> {telegram_tag} ({username}), <b>{title}</b> pazarında "
            f"<b>{outcome}</b> yönünde <code>{size}</code> adet kontrat {side.lower()} etti! "
            f"Ortalama fiyat: ${price:.4f}. Bol şans dileriz! 🚀"
        )


async def sync_profile_trades():
    """Periodically check tracked user profiles for new trades and comment on them."""
    logger.info("Polymarket Profile Trades Tracker started.")
    
    while True:
        try:
            for wallet, profile in USER_PROFILES.items():
                username = profile["name"]
                telegram_tag = profile["telegram"]
                
                trades = await fetch_user_trades(wallet)
                if not trades:
                    continue
                    
                for trade in trades:
                    tx_hash = trade.get("transactionHash")
                    if not tx_hash:
                        continue
                        
                    # 1. Skip if already processed
                    if await database.is_trade_processed(tx_hash):
                        continue
                        
                    # 2. Skip crypto trades completely
                    if _is_crypto_trade(trade):
                        now_str = datetime.now().isoformat()
                        await database.mark_trade_processed(tx_hash, now_str)
                        continue
                        
                    # 3. Process traditional finance trade
                    logger.info(f"Processing traditional finance trade for {username}: {trade.get('title')}")
                    
                    # Generate AI comment
                    msg = await generate_friendly_advice(username, telegram_tag, trade)
                    
                    # Send notification to the group
                    await send_notification(msg)
                    
                    # Mark as processed
                    now_str = datetime.now().isoformat()
                    await database.mark_trade_processed(tx_hash, now_str)
                    
        except Exception as e:
            logger.error(f"Error in profile trades sync loop: {e}")
            
        # Check every 2 minutes
        await asyncio.sleep(120)


def start_sync_task():
    """Start the Polymarket sync background tasks."""
    asyncio.create_task(sync_positions_loop())
    asyncio.create_task(sync_profile_trades())
