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

def get_ng_active_contract(dt: datetime) -> str:
    """
    Returns the active CME Natural Gas (NG/NGD) futures contract symbol.
    NG uses the same CME month codes and a similar rollover structure to WTI.
    The LTD for NG is 3 business days prior to the first day of the delivery month.
    We approximate by using a rollover 4 business days before the 1st of the delivery month.
    """
    et_tz = pytz.timezone('US/Eastern')
    if dt.tzinfo is None:
        dt = et_tz.localize(dt)
    else:
        dt = dt.astimezone(et_tz)
        
    # Generate candidate delivery months
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
        
        # Approximate rollover: 4 business days before 1st of delivery month
        first_of_month = date(y, m, 1)
        curr = first_of_month
        days_found = 0
        while days_found < 4:
            curr -= timedelta(days=1)
            if curr.weekday() < 5:  # Simple weekday check
                days_found += 1
        
        rollover_dt = et_tz.localize(datetime(curr.year, curr.month, curr.day, 18, 0, 0))
        candidates.append({"year": y, "month": m, "rollover": rollover_dt})
    
    candidates.sort(key=lambda x: x["rollover"])
    
    active_cand = None
    for cand in candidates:
        if cand["rollover"] > dt:
            active_cand = cand
            break
    
    if active_cand is None:
        active_cand = candidates[-1]
    
    cme_code = CME_MONTH_CODES.get(active_cand["month"])
    year_digit = str(active_cand["year"])[-1]
    
    return f"Commodities.NGD{cme_code}{year_digit}/USD"

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
    # NG is resolved dynamically like WTI (futures contract)
    # Indices mapped to their liquid ETF equivalents available on Pyth
    "DIA": "Equity.US.DIA/USD",
    "RUT": "Equity.US.IWM/USD",
    "HSI": "Equity.US.EWH/USD",
    "DAX": "Equity.US.EWG/USD",
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
    elif symbol_name == "NG":
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        pyth_symbol = get_ng_active_contract(now_et)
        logger.info(f"Resolved dynamic NG active contract: {pyth_symbol}")
    else:
        pyth_symbol = SYMBOL_MAP.get(symbol_name)
        if not pyth_symbol:
            logger.warning(f"Symbol {symbol_name} not found in SYMBOL_MAP, skipping.")
            return None, None
    
    # Check exact match in pyth_id_cache (populated by init_feeds_cache)
    if pyth_symbol in pyth_id_cache:
        return pyth_id_cache[pyth_symbol], pyth_symbol
    
    # No fuzzy matching - exact matches only to prevent NG->CPNG, HSI->HSIC, NVDA->NVDAX type bugs
    logger.warning(f"Pyth ID not found in cache for resolved symbol {pyth_symbol} (from {symbol_name})")
    return None, None

def get_previous_close_times(symbol: str) -> tuple[int, int]:
    """
    Calculates the 'from' and 'to' Unix timestamps to find the previous trading day's daily candle.
    By looking back 7 days and strictly cutting off at yesterday 23:59:59 UTC, we skip
    today's live candle, weekends, and market holidays.
    """
    # Use UTC to strictly align with Pyth's 00:00:00 UTC daily candle timestamps
    now_utc = datetime.now(pytz.utc)
    
    # Align to midnight of today UTC
    today_midnight = datetime(
        now_utc.year, 
        now_utc.month, 
        now_utc.day, 
        0, 0, 0,
        tzinfo=pytz.utc
    )
    
    # Span the last 7 days starting from midnight
    from_dt = today_midnight - timedelta(days=7)
    
    # We want to strictly exclude TODAY's daily candle (which starts at 00:00:00 UTC today).
    # So we set 'to_dt' to exactly 1 second before today's UTC midnight.
    to_dt = today_midnight - timedelta(seconds=1)
    
    return int(from_dt.timestamp()), int(to_dt.timestamp())

def get_previous_open_times(symbol: str) -> tuple[int, int]:
    """
    Returns the same window as get_previous_close_times, for symmetry if needed.
    """
    return get_previous_close_times(symbol)

import asyncio
import time

# Global rate limit state for Pyth TV History API
_tv_api_sem = asyncio.Semaphore(1)
_tv_api_backoff_until = 0.0

async def get_yahoo_history_raw(symbol: str, interval: str = "1h", range_str: str = "90d") -> dict:
    """
    Queries Yahoo Finance's chart API for historical data and maps it to Pyth TV History format.
    """
    symbol_up = symbol.upper()
    yahoo_symbol = symbol_up
    if symbol_up == "HSI":
        yahoo_symbol = "EWH"
    elif symbol_up == "RUT":
        yahoo_symbol = "IWM"
    elif symbol_up == "DAX":
        yahoo_symbol = "EWG"
        
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval={interval}&range={range_str}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                res_data = resp.json()
                if "chart" in res_data and "result" in res_data["chart"] and res_data["chart"]["result"]:
                    result = res_data["chart"]["result"][0]
                    timestamps = result.get("timestamp", [])
                    quote = result.get("indicators", {}).get("quote", [{}])[0]
                    
                    opens = quote.get("open", [])
                    highs = quote.get("high", [])
                    lows = quote.get("low", [])
                    closes = quote.get("close", [])
                    
                    valid_t = []
                    valid_o = []
                    valid_h = []
                    valid_l = []
                    valid_c = []
                    
                    for i in range(len(timestamps)):
                        if (i < len(opens) and opens[i] is not None and 
                            i < len(highs) and highs[i] is not None and 
                            i < len(lows) and lows[i] is not None and 
                            i < len(closes) and closes[i] is not None):
                            valid_t.append(timestamps[i])
                            valid_o.append(float(opens[i]))
                            valid_h.append(float(highs[i]))
                            valid_l.append(float(lows[i]))
                            valid_c.append(float(closes[i]))
                            
                    return {
                        "s": "ok",
                        "t": valid_t,
                        "o": valid_o,
                        "h": valid_h,
                        "l": valid_l,
                        "c": valid_c
                    }
    except Exception as e:
        logger.warning(f"Failed to fetch Yahoo history for {symbol_up}: {e}")
    return None

async def get_tv_history_raw(full_symbol: str, resolution: str, from_ts: int, to_ts: int, max_retries: int = 5, direct_pyth: bool = False) -> dict:
    """
    Low-level helper to query Pyth's TV History API.
    Handles concurrency via semaphore, global rate limit backoff, and retries.
    For daily resolution (resolution == "D"), we query Pyth TV History API first to match
    Polymarket's resolution source, and only fallback to Yahoo Finance if Pyth fails or returns 400/404 or s != "ok".
    For other resolutions (e.g. resolution == "60"), we route Stock/ETF symbols directly to Yahoo Finance to avoid rate limits.
    """
    is_equity = full_symbol.startswith("Equity.")
    ticker = None
    if is_equity:
        parts = full_symbol.split(".")
        if len(parts) >= 3:
            ticker = parts[2].split("/")[0]

    # Route stocks/ETFs directly to Yahoo if not requesting daily resolution and direct_pyth is False
    if is_equity and ticker and resolution != "D" and not direct_pyth:
        if resolution == "60":
            interval = "1h"
            range_str = "90d"
        elif resolution == "1":
            interval = "1m"
            range_str = "7d"
        elif resolution == "5":
            interval = "5m"
            range_str = "30d"
        elif resolution == "15":
            interval = "15m"
            range_str = "30d"
        elif resolution == "30":
            interval = "30m"
            range_str = "30d"
        else:
            interval = "1d"
            range_str = "30d"
        logger.info(f"Routing {resolution}-minute/intraday history query for {full_symbol} to Yahoo Finance (ticker: {ticker}, interval: {interval})")
        yahoo_data = await get_yahoo_history_raw(ticker, interval, range_str)
        if yahoo_data:
            return yahoo_data
        logger.warning(f"Yahoo Finance history failed for {ticker}, falling back to Pyth TV History...")

    # Query Pyth TV History API
    global _tv_api_backoff_until
    url = f"{BENCHMARKS_URL}/shims/tradingview/history"
    params = {
        "symbol": full_symbol,
        "resolution": resolution,
        "from": from_ts,
        "to": to_ts
    }
    
    pyth_data = None
    for attempt in range(max_retries):
        try:
            async with _tv_api_sem:
                # If there's an active backoff, wait for it inside the semaphore
                now = time.time()
                if now < _tv_api_backoff_until:
                    sleep_time = _tv_api_backoff_until - now
                    logger.warning(f"TV API in backoff. Waiting {sleep_time:.1f}s before request for {full_symbol}...")
                    await asyncio.sleep(sleep_time)
                
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, params=params, timeout=15.0)
                
                # Check for rate limiting
                if resp.status_code == 429:
                    delay = 3.0 * (attempt + 1)
                    _tv_api_backoff_until = time.time() + delay
                    logger.warning(f"Rate limited (429) on TV History for {full_symbol}. Backing off for {delay:.1f}s...")
                    # Delay inside the semaphore so no other concurrent requests are sent
                    await asyncio.sleep(delay)
                    continue
                
                resp.raise_for_status()
                data = resp.json()
                
                # Success: add a small mandatory cooldown to prevent burst limit
                await asyncio.sleep(0.5)
                
                if data and data.get("s") == "ok":
                    pyth_data = data
                    break
                else:
                    logger.warning(f"Pyth TV History API returned non-ok status: {data.get('s')} for {full_symbol}")
                    break  # Treat non-ok status as failure, no need to retry
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code in [400, 404]:
                logger.warning(f"TV History API returned {e.response.status_code} for {full_symbol} (invalid symbol). Skipping retries.")
                break
            if attempt < max_retries - 1:
                delay = 2.0 * (attempt + 1)
                logger.warning(f"HTTPStatusError {e.response.status_code} for {full_symbol} (Attempt {attempt+1}/{max_retries}). Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Failed to query TV History for {full_symbol} due to HTTPStatusError: {e}")
        except Exception as e:
            if attempt < max_retries - 1:
                delay = 2.0 * (attempt + 1)
                logger.warning(f"Error querying TV History for {full_symbol} (Attempt {attempt+1}/{max_retries}): {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Failed to query TV History for {full_symbol} after {max_retries} attempts: {e}")

    if pyth_data:
        return pyth_data

    # Fallback to Yahoo if Pyth TV History failed/returned non-ok and this is a daily request for a Stock/ETF
    if is_equity and ticker and resolution == "D" and not direct_pyth:
        logger.warning(f"Pyth TV History daily fetch failed or returned error for {full_symbol}. Falling back to Yahoo Finance daily...")
        yahoo_data = await get_yahoo_history_raw(ticker, "1d", "30d")
        if yahoo_data:
            return yahoo_data

async def get_historical_candle_price(full_symbol: str, pyth_id: str, from_ts: int, to_ts: int, price_type: str = 'close') -> float:
    """
    Fetches the exact 'Close' or 'Open' price of the candle.
    For Stock/ETF symbols (starting with 'Equity.'), retrieves the exact price published by Pyth
    at 16:00:00 ET (for close) or 9:30:00 ET (for open) via Hermes historical API.
    Otherwise, falls back to Pyth's TV history API or Yahoo Finance.
    Uses in-memory cache to prevent 429 rate limiting.
    """
    cache_key = (full_symbol, from_ts, to_ts, price_type)
    if cache_key in _historical_price_cache:
        cached_price = _historical_price_cache[cache_key]
        if cached_price is not None:
            return cached_price

    is_equity = full_symbol.startswith("Equity.")
    clean_id = pyth_id if not pyth_id.startswith('0x') else pyth_id[2:]
    
    if is_equity:
        try:
            et_tz = pytz.timezone('US/Eastern')
            dt_et = datetime.fromtimestamp(to_ts, et_tz)
            target_date = dt_et.date()
            ticker = full_symbol.split(".")[2].split("/")[0] if len(full_symbol.split(".")) >= 3 else None
            
            if price_type == 'close' and ticker:
                # 1. Fetch Yahoo daily close to use as reference
                yahoo_close = None
                try:
                    yahoo_data = await get_yahoo_history_raw(ticker, "1d", "5d")
                    if yahoo_data and yahoo_data.get("s") == "ok":
                        y_times = yahoo_data.get("t", [])
                        y_closes = yahoo_data.get("c", [])
                        for idx, y_ts in enumerate(y_times):
                            y_date = datetime.fromtimestamp(y_ts, et_tz).date()
                            if y_date == target_date:
                                yahoo_close = float(y_closes[idx])
                                break
                except Exception as e:
                    logger.warning(f"Failed to fetch Yahoo daily close reference for {ticker}: {e}")
                
                # Query 1-minute Pyth candles around 16:00:00 ET
                target_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=16, minute=0, second=0)
                target_dt = et_tz.localize(target_dt)
                target_ts = int(target_dt.timestamp())
                
                pyth_success = False
                candidates = []
                try:
                    logger.info(f"Querying Pyth 1-minute candles for {full_symbol} close around {target_dt}")
                    pyth_data = await get_tv_history_raw(
                        full_symbol=full_symbol,
                        resolution="1",
                        from_ts=target_ts - 300,
                        to_ts=target_ts + 300,
                        direct_pyth=True
                    )
                    if pyth_data and pyth_data.get("s") == "ok":
                        t_arr = pyth_data.get("t", [])
                        o_arr = pyth_data.get("o", [])
                        h_arr = pyth_data.get("h", [])
                        l_arr = pyth_data.get("l", [])
                        c_arr = pyth_data.get("c", [])
                        
                        t_1559 = target_ts - 60
                        if t_1559 in t_arr:
                            idx_1559 = t_arr.index(t_1559)
                            candidates.append(float(c_arr[idx_1559]))
                        if target_ts in t_arr:
                            idx_1600 = t_arr.index(target_ts)
                            candidates.append(float(o_arr[idx_1600]))
                            candidates.append(float(h_arr[idx_1600]))
                            candidates.append(float(l_arr[idx_1600]))
                            candidates.append(float(c_arr[idx_1600]))
                        
                        if candidates:
                            pyth_success = True
                except Exception as e:
                    logger.warning(f"Error querying Pyth 1-minute candles for {full_symbol}: {e}")
                
                # If Pyth 1-minute candidates are found, resolve the final price from them
                if pyth_success and candidates:
                    if yahoo_close is not None:
                        selected_price = min(candidates, key=lambda x: abs(x - yahoo_close))
                        logger.info(f"Resolved {full_symbol} close using Pyth 1-minute closest rule: {selected_price} (Yahoo reference: {yahoo_close})")
                    else:
                        selected_price = candidates[0]
                        logger.warning(f"Resolved {full_symbol} close using Pyth 1-minute default (no Yahoo reference): {selected_price}")
                    _historical_price_cache[cache_key] = selected_price
                    return selected_price
                
                # 2. Try Pyth Daily TV history as a secondary Pyth fallback (if 1-minute candles failed or were rate-limited)
                try:
                    logger.warning(f"Pyth 1-minute candles unavailable for {full_symbol}. Trying Pyth daily TV history...")
                    daily_data = await get_tv_history_raw(full_symbol, resolution="D", from_ts=from_ts, to_ts=to_ts, direct_pyth=True)
                    if daily_data and daily_data.get("s") == "ok" and "c" in daily_data:
                        d_times = daily_data.get("t", [])
                        d_closes = daily_data.get("c", [])
                        for idx, d_ts in enumerate(d_times):
                            d_date = datetime.fromtimestamp(d_ts, et_tz).date()
                            if d_date == target_date:
                                res_val = float(d_closes[idx])
                                logger.info(f"Resolved {full_symbol} close using Pyth daily TV close: {res_val}")
                                _historical_price_cache[cache_key] = res_val
                                return res_val
                except Exception as e:
                    logger.warning(f"Error querying Pyth daily TV history for {full_symbol}: {e}")
                    
                # 3. Yahoo 5-minute bar close fallback (as final fallback for completely unsupported assets like EWY)
                try:
                    logger.info(f"Pyth TV history failed for {full_symbol}. Trying Yahoo 5-minute bar close fallback for {ticker}...")
                    yahoo_5m = await get_yahoo_history_raw(ticker, interval="5m", range_str="5d")
                    if yahoo_5m and yahoo_5m.get("s") == "ok":
                        bar_ts = target_ts - 300  # 15:55 ET bar
                        timestamps = yahoo_5m.get("t", [])
                        closes = yahoo_5m.get("c", [])
                        if bar_ts in timestamps:
                            idx = timestamps.index(bar_ts)
                            res_price = float(closes[idx])
                            logger.info(f"Resolved {full_symbol} close using Yahoo 5-minute bar close: {res_price}")
                            _historical_price_cache[cache_key] = res_price
                            return res_price
                except Exception as e:
                    logger.warning(f"Error querying Yahoo 5-minute bar close fallback for {ticker}: {e}")

            elif price_type == 'open' and clean_id:
                # Keep direct Hermes lookup for market open
                target_dt = dt_et.replace(hour=9, minute=30, second=0, microsecond=0)
                target_ts = int(target_dt.timestamp())
                logger.info(f"Fetching exact Hermes historical price for open of {full_symbol} at {target_dt}")
                url = f"{HERMES_URL}/updates/price/{target_ts}"
                params = {"ids[]": clean_id, "parsed": "true"}
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, params=params, timeout=10.0)
                    if resp.status_code == 200:
                        fb_data = resp.json()
                        for item in fb_data.get("parsed", []):
                            if clean_id in item.get("id", ""):
                                price_info = item.get("price", {})
                                p_str = price_info.get("price")
                                e_str = price_info.get("expo")
                                if p_str and e_str:
                                    res_price = float(p_str) * (10 ** int(e_str))
                                    _historical_price_cache[cache_key] = res_price
                                    return res_price
        except Exception as e:
            logger.warning(f"Error resolving equity historical price for {full_symbol}: {e}")

    # Fallback to TV History daily resolution
    data = await get_tv_history_raw(full_symbol, resolution="D", from_ts=from_ts, to_ts=to_ts)
    
    # TV API response: { "s": "ok", "t": [...], "c": [...], "o": [...] }
    target_key = "c" if price_type == 'close' else "o"
    
    if data and data.get("s") == "ok" and target_key in data and len(data[target_key]) > 0:
        timestamps = data.get("t", [])
        prices = data[target_key]
        
        valid_price = None
        # Iterate backwards to find the latest candle that is <= to_ts (to exclude today's partial candle)
        for i in range(len(timestamps) - 1, -1, -1):
            if timestamps[i] <= to_ts:
                valid_price = float(prices[i])
                break
                
        if valid_price is not None:
            _historical_price_cache[cache_key] = valid_price
            return valid_price
    
    # Fallback to Hermes at the raw to_ts/from_ts
    logger.warning(f"No TV candle data found for {full_symbol} between {from_ts} and {to_ts}. Falling back to Hermes raw timestamp history API...")
    target_ts = to_ts if price_type == 'close' else from_ts
    fallback_url = f"{HERMES_URL}/updates/price/{target_ts}"
    fb_params = {"ids[]": clean_id, "parsed": "true"}
    
    try:
        async with httpx.AsyncClient() as client:
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
            logger.error(f"Fallback to Hermes History failed for {full_symbol} at {target_ts} (Status: {fb_resp.status_code})")
    except Exception as e:
        logger.error(f"Error in Hermes fallback for {full_symbol}: {e}")
        
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

async def get_yahoo_live_price(symbol: str) -> float:
    """
    Fetches the real-time live price (including pre/post-market and regular hours) of a stock/ETF from Yahoo Finance's chart API.
    """
    symbol_up = symbol.upper()
    yahoo_symbol = symbol_up
    if symbol_up == "HSI":
        yahoo_symbol = "EWH"
    elif symbol_up == "RUT":
        yahoo_symbol = "IWM"
    elif symbol_up == "DAX":
        yahoo_symbol = "EWG"
        
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?includePrePost=true&interval=1m&range=1d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                if "chart" in data and "result" in data["chart"] and data["chart"]["result"]:
                    chart_result = data["chart"]["result"][0]
                    indicators = chart_result.get("indicators", {}).get("quote", [{}])[0]
                    close_prices = indicators.get("close", [])
                    if close_prices:
                        # Find the last non-None close price
                        last_close = next((c for c in reversed(close_prices) if c is not None), None)
                        if last_close is not None:
                            logger.info(f"Fetched live Yahoo price for {symbol_up} ({yahoo_symbol}): ${last_close:.2f}")
                            return float(last_close)
    except Exception as e:
        logger.warning(f"Failed to fetch Yahoo price for {symbol_up}: {e}")
    return None

async def get_active_price(symbol: str, default_pyth_id: str) -> float:
    """
    Smart price fetcher that prefers Yahoo Finance for all stock/ETF symbols 24/7,
    and fallbacks to calibrated 7/24 Binance Perpetual prices for Commodities (WTI/Gold) 
    when official CME markets are closed (using basis spread adjustments).
    """
    global _wti_binance_basis, _xau_binance_basis
    
    symbol_up = symbol.upper()
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    is_commodity = any(c in symbol_up for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER", "NG"])
    
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
                
    # 2. Live stock/ETF price from Yahoo Finance
    if not is_commodity:
        yahoo_price = await get_yahoo_live_price(symbol_up)
        if yahoo_price:
            return yahoo_price
                    
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
