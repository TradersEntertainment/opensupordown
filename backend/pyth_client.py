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
    "SPX": "Equity.US.SPY/USD", 
    "SPY": "Equity.US.SPY/USD",
    "PLTR": "Equity.US.PLTR/USD",
    "AAPL": "Equity.US.AAPL/USD",
    "TSLA": "Equity.US.TSLA/USD",
    "AMZN": "Equity.US.AMZN/USD",
    "NVDA": "Equity.US.NVDA/USD",
    "HOOD": "Equity.US.HOOD/USD",
    "META": "Equity.US.META/USD",
    "GOOGL": "Equity.US.GOOGL/USD",
    "ABNB": "Equity.US.ABNB/USD",
    "OPEN": "Equity.US.OPEN/USD",
    "MSFT": "Equity.US.MSFT/USD",
    "COIN": "Equity.US.COIN/USD",
    "NFLX": "Equity.US.NFLX/USD",
    "RKLB": "Equity.US.RKLB/USD",
    "EWY": "Equity.US.EWY/USD",
    "WTI": "Commodities.USOILSPOT",
    "GOLD": "Metal.XAU/USD",
    "XAU": "Metal.XAU/USD",
    "XAUUSD": "Metal.XAU/USD",
    "SILVER": "Metal.XAG/USD",
    "XAG": "Metal.XAG/USD",
    "XAGUSD": "Metal.XAG/USD",
    "NG": "Crypto.NG/USD",
    "RUT": "Index.US.RUT/USD",
    "HSI": "Index.HK.HSI/HKD",
    "DIA": "Index.US.DJI/USD",
    "DAX": "Index.EU.DAX/EUR",
    "NKY": "Index.JP.NI225/JPY",
    "UKX": "Index.GB.FTSE/GBP",
    "NYA": "Index.US.NYA/USD",
    "BTC": "Crypto.BTC/USD"
}

# Cache for resolved IDs
pyth_id_cache = {}

# In-memory cache for historical candle prices to avoid 429 rate limiting
_historical_price_cache = {}

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
        
    # We want ONLY the 15:59 (or 16:59) candle.
    # Using 15:59:00 to 15:59:59 ensures Pyth returns exactly 1 candle.
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
        close_hour - 1, 59, 59
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
        9, 30, 59
    ))
    
    return int(candle_start_dt.timestamp()), int(candle_end_dt.timestamp())

async def get_historical_candle_price(full_symbol: str, pyth_id: str, from_ts: int, to_ts: int, price_type: str = 'close') -> float:
    """
    Fetches the exact 'Close' or 'Open' price of the 1-minute candle from Pyth's TV history API.
    Falls back to Hermes historical API if TV shim fails.
    Uses in-memory cache to prevent 429 rate limiting.
    """
    cache_key = (full_symbol, from_ts, to_ts, price_type)
    if cache_key in _historical_price_cache:
        cached_price = _historical_price_cache[cache_key]
        if cached_price is not None:
            logger.info(f"Using cached historical price for {full_symbol}: {cached_price}")
            return cached_price

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
                price = data[target_key][-1] if price_type == 'close' else data[target_key][0]
                res_price = float(price)
                _historical_price_cache[cache_key] = res_price
                return res_price
            else:
                logger.warning(f"No TV candle data found for {full_symbol} between {from_ts} and {to_ts}. Response: {data.get('s')}. Falling back to Hermes history API...")
                clean_id = pyth_id if not pyth_id.startswith('0x') else pyth_id[2:]
                # If we want close, we want the price at the end of the minute (to_ts)
                # If we want open, we want the price at the start of the minute (from_ts)
                target_ts = to_ts if price_type == 'close' else from_ts
                fallback_url = f"{HERMES_URL}/updates/price/{target_ts}"
                fb_params = {"ids[]": clean_id, "parsed": "true"}
                fb_resp = await client.get(fallback_url, params=fb_params, timeout=5.0)
                if fb_resp.status_code == 200:
                    fb_data = fb_resp.json()
                    for item in fb_data.get("parsed", []):
                        if clean_id in item.get("id", ""):
                            price_info = item.get("price", {})
                            p_str = price_info.get("price")
                            e_str = price_info.get("expo")
                            if p_str and e_str:
                                res_price = float(p_str) * (10 ** int(e_str))
                                _historical_price_cache[cache_key] = res_price
                                return res_price
                                
                logger.error(f"Fallback to Hermes History failed for {full_symbol} at {target_ts}")
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
async def get_active_price(symbol: str, default_pyth_id: str) -> float:
    """
    Smart price fetcher that prefers .PRE feeds for stocks during pre-market hours.
    ET 04:00-09:30 (TR 11:00-16:30)
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    is_premarket = 240 <= total_minutes < 570
    
    symbol_up = symbol.upper()
    is_commodity = any(c in symbol_up for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER", "NG"])
    
    if is_premarket and not is_commodity:
        # Try to find a .PRE feed for this stock
        regular_pyth_symbol = SYMBOL_MAP.get(symbol_up, f"Equity.US.{symbol_up}/USD")
        if regular_pyth_symbol.startswith("Equity.US."):
            pre_symbol = f"{regular_pyth_symbol}.PRE"
            pre_id = pyth_id_cache.get(pre_symbol)
            if pre_id:
                price = await get_latest_price(pre_id)
                if price:
                    return price
                    
    # Fallback to default ID
    return await get_latest_price(default_pyth_id)
