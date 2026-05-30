import httpx
import logging
from datetime import datetime, timedelta, date
import pytz

logger = logging.getLogger(__name__)

# Base URLs
HERMES_URL = "https://hermes.pyth.network/v2"
BENCHMARKS_URL = "https://benchmarks.pyth.network/v1"

# CME WTI month codes mapping
CME_MONTH_CODES = {
    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
}

def is_cme_business_day(d: date) -> bool:
    """Check if date is a business day (not weekend or major CME holiday)."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    # Standard major CME holidays
    if (d.month == 1 and d.day == 1):  # New Year
        return False
    if (d.month == 7 and d.day == 4):  # Independence Day
        return False
    if (d.month == 12 and d.day == 25): # Christmas
        return False
    return True

def get_wti_contract_ltd(delivery_year: int, delivery_month: int) -> date:
    """
    Returns the Last Trading Day (LTD) for WTI Crude Oil (CL) futures contract.
    LTD is three business days prior to the 25th calendar day of the month preceding
    the delivery month (four business days if the 25th is not a business day).
    """
    # Preceding month calculation
    prec_month = delivery_month - 1
    prec_year = delivery_year
    if prec_month == 0:
        prec_month = 12
        prec_year -= 1
        
    ref_date = date(prec_year, prec_month, 25)
    needed_days = 3 if is_cme_business_day(ref_date) else 4
    
    curr = ref_date
    days_found = 0
    while days_found < needed_days:
        curr -= timedelta(days=1)
        if is_cme_business_day(curr):
            days_found += 1
            
    return curr

def get_wti_rollover_datetime(delivery_year: int, delivery_month: int) -> datetime:
    """
    Returns the rollover datetime when this contract stops being the active month.
    Rollover occurs at the start of the second trading session prior to LTD's session.
    This is 2 business days prior to LTD, at 6:00 PM ET on the preceding calendar day.
    """
    ltd = get_wti_contract_ltd(delivery_year, delivery_month)
    
    curr = ltd
    days_found = 0
    while days_found < 2:
        curr -= timedelta(days=1)
        if is_cme_business_day(curr):
            days_found += 1
            
    # Rollover starts at 6:00 PM ET on the calendar day prior to `curr`
    rollover_day = curr - timedelta(days=1)
    et_tz = pytz.timezone('US/Eastern')
    return et_tz.localize(datetime(rollover_day.year, rollover_day.month, rollover_day.day, 18, 0, 0))

def get_wti_active_contract(dt: datetime) -> str:
    """
    Returns the active CME WTI futures contract symbol (e.g. 'WTIN6/USD')
    for a given ET datetime.
    """
    et_tz = pytz.timezone('US/Eastern')
    if dt.tzinfo is None:
        dt = et_tz.localize(dt)
    else:
        dt = dt.astimezone(et_tz)
        
    # Generate candidate delivery months around dt: from dt.month - 1 to dt.month + 3
    candidates = []
    for offset in range(-1, 4):
        y = dt.year
        m = dt.month + offset
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
            
        try:
            rollover_time = get_wti_rollover_datetime(y, m)
            candidates.append({
                "year": y,
                "month": m,
                "rollover": rollover_time
            })
        except Exception as e:
            logger.error(f"Error calculating candidate rollover for delivery {y}-{m}: {e}")
            
    candidates.sort(key=lambda x: x["rollover"])
    
    # The active contract at dt is the one with the smallest rollover_datetime that is > dt.
    active_cand = None
    for cand in candidates:
        if cand["rollover"] > dt:
            active_cand = cand
            break
            
    if active_cand is None:
        # Fallback to the last candidate
        active_cand = candidates[-1]
        
    cme_code = CME_MONTH_CODES.get(active_cand["month"])
    year_digit = str(active_cand["year"])[-1]
    
    return f"Commodities.WTI{cme_code}{year_digit}/USD"

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
    "MU": "Equity.US.MU/USD",
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
    
    if symbol_name == "WTI":
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        pyth_symbol = get_wti_active_contract(now_et)
        logger.info(f"Resolved dynamic WTI active contract: {pyth_symbol}")
    else:
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
async def get_binance_perpetual_price(symbol: str) -> float:
    """
    Fetches 7/24 real-time perpetual futures prices from Binance API
    for WTI (CLUSDT) and Gold (XAUTUSDT) when official CME markets are closed.
    """
    symbol_up = symbol.upper()
    binance_symbol = None
    if symbol_up == "WTI":
        binance_symbol = "CLUSDT"
    elif symbol_up in ["XAU", "GOLD"]:
        binance_symbol = "XAUTUSDT"
    elif symbol_up in ["XAG", "SILVER"]:
        binance_symbol = "XAGUSDT"
        
    if not binance_symbol:
        return None
        
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={binance_symbol}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                price_str = data.get("price")
                if price_str:
                    price = float(price_str)
                    logger.info(f"Fetched 7/24 Binance Perpetual price for {symbol_up} ({binance_symbol}): ${price:.2f}")
                    return price
    except Exception as e:
        logger.debug(f"Failed to fetch Binance price for {symbol_up}: {e}")
        
    return None

# Live 7/24 Basis Spread Calibration state
_wti_binance_basis = 0.0
_xau_binance_basis = 0.0

async def get_active_price(symbol: str, default_pyth_id: str) -> float:
    """
    Smart price fetcher that prefers .PRE feeds for stocks during pre-market,
    and fallbacks to calibrated 7/24 Binance Perpetual prices for Commodities (WTI/Gold) 
    when official CME markets are closed (using basis spread adjustments).
    """
    global _wti_binance_basis, _xau_binance_basis
    
    symbol_up = symbol.upper()
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    is_commodity = any(c in symbol_up for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
    
    # 1. 7/24 Commodity Alpha check (Binance perpetuals during weekends or off-hours daily breaks)
    if is_commodity:
        is_weekend = False
        weekday = now_et.weekday()
        hour = now_et.hour
        
        if weekday == 4 and hour >= 17:  # Friday after 5 PM ET
            is_weekend = True
        elif weekday == 5:  # Saturday
            is_weekend = True
        elif weekday == 6 and hour < 18:  # Sunday before 6 PM ET
            is_weekend = True
            
        is_daily_break = (hour == 17)  # Daily CME break (5 PM to 6 PM ET)
        
        if is_weekend or is_daily_break:
            binance_price = await get_binance_perpetual_price(symbol_up)
            if binance_price:
                # Apply calibrated basis spread to reconstruct realistic CME price
                basis = _wti_binance_basis if symbol_up == "WTI" else _xau_binance_basis
                adjusted_price = binance_price - basis
                logger.info(f"Commodity {symbol_up} market closed. Live Binance: ${binance_price:.2f}, Basis: ${basis:+.4f} -> Real Adjusted CME Price: ${adjusted_price:.2f}")
                return adjusted_price
                
    # 2. Pre-market stock logic
    total_minutes = now_et.hour * 60 + now_et.minute
    is_premarket = 240 <= total_minutes < 570
    
    if is_premarket and not is_commodity:
        regular_pyth_symbol = SYMBOL_MAP.get(symbol_up, f"Equity.US.{symbol_up}/USD")
        if regular_pyth_symbol.startswith("Equity.US."):
            pre_symbol = f"{regular_pyth_symbol}.PRE"
            pre_id = pyth_id_cache.get(pre_symbol)
            if pre_id:
                price = await get_latest_price(pre_id)
                if price:
                    return price
                    
    # 3. Fetch default active price (official market open)
    official_price = await get_latest_price(default_pyth_id)
    
    # Live Calibration of Basis Spread while CME official market is open
    if official_price and is_commodity:
        try:
            binance_price = await get_binance_perpetual_price(symbol_up)
            if binance_price:
                basis = binance_price - official_price
                if symbol_up == "WTI":
                    _wti_binance_basis = basis
                else:
                    _xau_binance_basis = basis
                logger.debug(f"Calibrated {symbol_up} live Basis Spread: ${basis:+.4f} (Binance: ${binance_price:.2f} vs CME: ${official_price:.2f})")
        except Exception as e:
            logger.debug(f"Failed to calibrate live basis spread: {e}")
            
    return official_price

async def get_wti_rollover_alpha_info() -> dict:
    """
    Analyzes active WTI contract vs next contract to find price differences (spreads) 
    that present massive trading opportunities on Polymarket before rollover occurs.
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    active_symbol = get_wti_active_contract(now_et)
    active_id = pyth_id_cache.get(active_symbol)
    
    # Check 28 days in the future to find next month's active contract
    future_dt = now_et + timedelta(days=28)
    next_symbol = get_wti_active_contract(future_dt)
    next_id = pyth_id_cache.get(next_symbol)
    
    if not active_id or not next_id or active_symbol == next_symbol:
        return {"has_alpha": False, "reason": "No rollover near or next contract not cached"}
        
    # Find active rollover time
    active_rollover_time = None
    for offset in range(-1, 4):
        y = now_et.year
        m = now_et.month + offset
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        try:
            rollover_time = get_wti_rollover_datetime(y, m)
            cme_code = CME_MONTH_CODES.get(m)
            year_digit = str(y)[-1]
            cand_symbol = f"Commodities.WTI{cme_code}{year_digit}/USD"
            if cand_symbol == active_symbol:
                active_rollover_time = rollover_time
                break
        except:
            pass
            
    if not active_rollover_time:
        return {"has_alpha": False, "reason": "Could not calculate active contract rollover time"}
        
    time_left = active_rollover_time - now_et
    hours_left = time_left.total_seconds() / 3600
    
    # Active scanning when rollover is within 5 days (120 hours)
    if hours_left > 120 or hours_left < 0:
        return {"has_alpha": False, "reason": f"Rollover too far ({hours_left:.1f} hours left)"}
        
    active_price = await get_latest_price(active_id)
    next_price = await get_latest_price(next_id)
    
    if not active_price or not next_price:
        return {"has_alpha": False, "reason": "Could not fetch prices for active or next contract"}
        
    spread = next_price - active_price
    has_alpha = abs(spread) >= 0.20
    
    return {
        "has_alpha": has_alpha,
        "hours_left": hours_left,
        "active_symbol": active_symbol.split('.')[-1].split('/')[0],
        "next_symbol": next_symbol.split('.')[-1].split('/')[0],
        "active_price": active_price,
        "next_price": next_price,
        "spread": spread,
        "rollover_time": active_rollover_time
    }
