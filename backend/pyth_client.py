import httpx
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

# Base URLs
HERMES_URL = "https://hermes.pyth.network/v2"
BENCHMARKS_URL = "https://benchmarks.pyth.network/v1"

# Hardcoded symbol mapping for common assets to avoid needing a full cache initially
# Users can add more via dashboard/telegram if needed
SYMBOL_MAP = {
    "SPX": "Equity.US.SPY/USD", # Per user instruction, use SPY for SPX
    "SPY": "Equity.US.SPY/USD",
    "PLTR": "Equity.US.PLTR/USD",
    "AAPL": "Equity.US.AAPL/USD",
    "TSLA": "Equity.US.TSLA/USD",
    "WTI": "Commodities.WTI/USD", # Generic WTI, might need specific contract logic later
    "GOLD": "Metal.XAU/USD",
    "XAU": "Metal.XAU/USD",
    "SILVER": "Metal.XAG/USD",
    "XAG": "Metal.XAG/USD",
    "BTC": "Crypto.BTC/USD"
}

# Cache for resolved IDs
pyth_id_cache = {}

async def init_feeds_cache():
    """Fetches all price feeds from hermes to memorize symbol to ID mapping."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{HERMES_URL}/price_feeds", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            for feed in data:
                feed_id = feed.get("id")
                attrs = feed.get("attributes", {})
                symbol = attrs.get("symbol")
                if feed_id and symbol:
                    pyth_id_cache[symbol] = feed_id
        logger.info(f"Successfully cached {len(pyth_id_cache)} Pyth feeds.")
    except Exception as e:
        logger.error(f"Failed to fetch pyth feeds cache: {e}")

def get_pyth_id(symbol_name: str) -> str:
    """Resolve a common name like 'PLTR' or 'WTI' to a Pyth ID."""
    symbol_name = symbol_name.upper()
    pyth_symbol = SYMBOL_MAP.get(symbol_name, symbol_name) # Fallback to input if not in map
    
    # Check cache
    if pyth_symbol in pyth_id_cache:
        return pyth_id_cache[pyth_symbol], pyth_symbol
    
    # If not in exact map, try fuzzy search in cache
    for sym, pid in pyth_id_cache.items():
        if symbol_name in sym.upper():
            return pid, sym
            
    return None, None

def get_previous_close_times(symbol: str) -> tuple[int, int]:
    """
    Calculates the 'from' and 'to' Unix timestamps for the previous trading day's final 1-minute candle.
    Stocks: 15:59 ET - 16:00 ET (22:59 - 23:00 TR)
    Commodities: 16:59 ET - 17:00 ET (23:59 - 24:00 TR)
    Returns (from_ts, to_ts)
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    # Determine close hour based on asset type
    is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
    close_hour = 17 if is_commodity else 16
    
    # Start checking from yesterday
    target_date = now_et - timedelta(days=1)
    
    # If target_date is Sunday (6) or Saturday (5), go back to Friday
    while target_date.weekday() >= 5:
        target_date -= timedelta(days=1)
        
    # We want the 1-minute candle BEFORE the close_hour. 
    # e.g., if close is 16:00, we want the 15:59:00 to 16:00:00 candle.
    candle_start_dt = et_tz.localize(datetime(
        target_date.year, 
        target_date.month, 
        target_date.day, 
        close_hour - 1, 59, 0
    ))
    
    candle_end_dt = et_tz.localize(datetime(
        target_date.year, 
        target_date.month, 
        target_date.day, 
        close_hour, 0, 0
    ))
    
    return int(candle_start_dt.timestamp()), int(candle_end_dt.timestamp())

def get_previous_open_times(symbol: str) -> tuple[int, int]:
    """
    Calculates the 'from' and 'to' Unix timestamps for the current/previous trading day's 09:30 ET 1-minute candle.
    Stocks: 09:30 ET - 09:31 ET (16:30 - 16:31 TR)
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    target_date = now_et
    # If before 09:30 ET today, or if it's weekend, go back to previous trading day
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30) or now_et.weekday() >= 5:
        target_date -= timedelta(days=1)
        while target_date.weekday() >= 5:
            target_date -= timedelta(days=1)
            
    candle_start_dt = et_tz.localize(datetime(
        target_date.year, 
        target_date.month, 
        target_date.day, 
        9, 30, 0
    ))
    
    candle_end_dt = et_tz.localize(datetime(
        target_date.year, 
        target_date.month, 
        target_date.day, 
        9, 31, 0
    ))
    
    return int(candle_start_dt.timestamp()), int(candle_end_dt.timestamp())

async def get_historical_candle_price(full_symbol: str, from_ts: int, to_ts: int, price_type: str = 'close') -> float:
    """
    Fetches the exact 'Close' or 'Open' price of the 1-minute candle from Pyth's TV history API.
    """
    url = f"{BENCHMARKS_URL}/shims/tradingview/history"
    params = {
        "symbol": full_symbol,
        "resolution": "1", 
        "from": from_ts,
        "to": to_ts
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            
            # TV API response: { "s": "ok", "t": [...], "c": [...], "o": [...] }
            target_key = "c" if price_type == 'close' else "o"
            
            if data.get("s") == "ok" and target_key in data and len(data[target_key]) > 0:
                price = data[target_key][0]
                return float(price)
            else:
                logger.error(f"No TV candle data found for {full_symbol} between {from_ts} and {to_ts}. Response: {data}")
                return None
                
    except Exception as e:
        logger.error(f"Error fetching historical TV candle: {e}")
        return None

async def get_latest_price(pyth_id: str) -> float:
    """Fetches the real-time latest price from Hermes."""
    clean_id = pyth_id if not pyth_id.startswith('0x') else pyth_id[2:]
    url = f"{HERMES_URL}/updates/price/latest"
    params = {"ids[]": clean_id}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            
            for item in data.get("parsed", []):
                item_id = item.get("id")
                if clean_id in item_id:
                    price_info = item.get("price", {})
                    price_str = price_info.get("price")
                    expo_str = price_info.get("expo")
                    if price_str and expo_str:
                        return float(price_str) * (10 ** int(expo_str))
                        
            return None
    except Exception as e:
        logger.error(f"Error fetching latest price: {e}")
        return None
