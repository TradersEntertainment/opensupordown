"""
Finance Signal Scanner — "Safe Bet" Detector
Scans Polymarket Up/Down stocks near market close for nearly-certain outcomes.
Sends signals to a dedicated Telegram channel.

Strategy:
  - Last 1 hour before close (15:00-16:00 ET / 22:00-23:00 TR)
  - Check each stock's diff vs yesterday's close
  - Dynamically determine if that diff level is "safe" using 60-day history
  - Signal if historical reversal rate <= 1/total_days
  - Include Polymarket prices + orderbook depth
"""
import asyncio
import httpx
import json
import io
import logging
import os
import time as time_mod
from datetime import datetime, timedelta
import re

import pytz
from telegram import Bot

import pyth_client
import image_generator
import database

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────

SIGNAL_CHAT_ID = "-5130715061"  # Dedicated signal channel
ENGLISH_SIGNAL_CHAT_ID = "@polymarketfinance1kto1m" # Public english channel (15-min delayed)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Scan window: 15:00-16:00 ET (22:00-23:00 TR)
SCAN_START_MINUTES = 900   # 15:00 ET
SCAN_END_MINUTES = 1020     # 17:00 ET (24:00 TR) to cover commodities close
SCAN_INTERVAL = 60         # Every 60 seconds

# Stocks, commodities and indices to scan
# NKY, UKX, NYA, DAX removed - Pyth has no price feeds for these indices/ETFs
SCAN_WATCHLIST = [
    "SPY", "PLTR", "TSLA", "NVDA", "AAPL", "AMZN", "META", "GOOGL",
    "MSFT", "NFLX", "COIN", "HOOD", "ABNB", "RKLB", "EWY", "OPEN",
    "MU", "WTI", "XAU", "XAG", "HSI", "NG", "DIA", "RUT"
]

ASSET_FUZZY_MAP = {
    "PALANTIR": "PLTR",
    "TESLA": "TSLA",
    "NVIDIA": "NVDA",
    "APPLE": "AAPL",
    "AMAZON": "AMZN",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "MICROSOFT": "MSFT",
    "NETFLIX": "NFLX",
    "COINBASE": "COIN",
    "ROBINHOOD": "HOOD",
    "AIRBNB": "ABNB",
    "ROCKET LAB": "RKLB",
    "MICRON": "MU",
    "CRUDE OIL": "WTI",
    "GOLD": "XAU",
    "SILVER": "XAG",
    "NATURAL GAS": "NG",
    "HANG SENG": "HSI",
    "NIKKEI": "NKY",
    "FTSE": "UKX",
    "DOW JONES": "DIA",
    "RUSSELL": "RUT",
    "S&P 500": "SPY"
}

# Slug → symbol mapping for Polymarket event discovery
SLUG_TO_SYMBOL = {
    "spx-up-or-down": "SPY",
    "spy-up-or-down": "SPY",
    "sp-500-up-or-down": "SPY",
    "pltr-up-or-down": "PLTR",
    "palantir-up-or-down": "PLTR",
    "tsla-up-or-down": "TSLA",
    "tesla-up-or-down": "TSLA",
    "nvda-up-or-down": "NVDA",
    "nvidia-up-or-down": "NVDA",
    "aapl-up-or-down": "AAPL",
    "apple-up-or-down": "AAPL",
    "amzn-up-or-down": "AMZN",
    "amazon-up-or-down": "AMZN",
    "meta-up-or-down": "META",
    "googl-up-or-down": "GOOGL",
    "google-up-or-down": "GOOGL",
    "msft-up-or-down": "MSFT",
    "microsoft-up-or-down": "MSFT",
    "nflx-up-or-down": "NFLX",
    "netflix-up-or-down": "NFLX",
    "coin-up-or-down": "COIN",
    "coinbase-up-or-down": "COIN",
    "hood-up-or-down": "HOOD",
    "robinhood-up-or-down": "HOOD",
    "abnb-up-or-down": "ABNB",
    "airbnb-up-or-down": "ABNB",
    "rklb-up-or-down": "RKLB",
    "rocket-lab-up-or-down": "RKLB",
    "ewy-up-or-down": "EWY",
    "opendoor-up-or-down": "OPEN",
    "open-up-or-down": "OPEN",
    "wti-up-or-down": "WTI",
    "xauusd-up-or-down": "XAU",
    "xagusd-up-or-down": "XAG",
    "xau-up-or-down": "XAU",
    "xag-up-or-down": "XAG",
    "hsi-up-or-down": "HSI",
    "hang-seng-up-or-down": "HSI",
    "ng-up-or-down": "NG",
    "natural-gas-up-or-down": "NG",
    "nik-up-or-down": "NKY",
    "nikkei-225-up-or-down": "NKY",
    "ukx-up-or-down": "UKX",
    "ftse-100-up-or-down": "UKX",
    "djia-up-or-down": "DIA",
    "dow-jones-up-or-down": "DIA",
    "dax-up-or-down": "DAX",
    "rut-up-or-down": "RUT",
    "russell-2000-up-or-down": "RUT",
    "nya-up-or-down": "NYA",
    "mu-up-or-down": "MU",
    "micron-up-or-down": "MU",
}

# ─── State ──────────────────────────────────────────────────────────────────

_historical_cache = {}       # symbol -> [day_data, ...]
_cache_loaded = False
_cache_load_date = None      # Refresh cache once per day

_signals_sent_today = {}     # (symbol, direction) -> {diff_pct, time, was_safe}
_last_signal_scan_time = 0   # Used by the Telegram signal scanner loop interval

_poly_cache = {}             # symbol -> {up_price, down_price, up_token_id, ...}
_last_poly_time = 0

# Background scan cache for the dashboard/frontend
_cached_scan_results = []    # Latest scan results for /api/scan-results
_last_scan_time = None       # Timestamp of last background scan completion

# Signal bot (same token, different chat_id)
_signal_bot = None
if TELEGRAM_TOKEN:
    _signal_bot = Bot(token=TELEGRAM_TOKEN)


# ─── Signal Sender ──────────────────────────────────────────────────────────

def build_card_data(symbol, direction, current_price, ref_price, diff_pct, minutes_to_close, analysis, poly_info, book=None):
    best_cheap_price = None
    total_cheap_size = 0.0
    depth_at_99 = 0.0
    has_orders_at_99 = False
    
    if poly_info and book:
        cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
        if cheap_asks:
            best_cheap_price = min(cheap_asks.keys())
            total_cheap_size = sum(cheap_asks.values())
            
        depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
        has_orders_at_99 = depth_at_99 > 0 or (best_cheap_price is not None and best_cheap_price <= 0.99 and total_cheap_size > 0)

    is_impossible = analysis.get("reversed_count", 99) <= 1 and analysis.get("total_similar_days", 0) >= 2
    is_safe_bet = analysis.get("is_safe_bet", False)
    
    up_price = poly_info.get("up_price", 0.0) if poly_info else 0.0
    down_price = poly_info.get("down_price", 0.0) if poly_info else 0.0
    safe_price = up_price if direction == "UP" else down_price

    return {
        "symbol": symbol,
        "direction": direction,
        "current_price": current_price,
        "ref_price": ref_price,
        "diff_pct": diff_pct,
        "minutes_to_close": minutes_to_close,
        "is_impossible": is_impossible,
        "is_safe_bet": is_safe_bet,
        "has_orders_at_99": has_orders_at_99,
        "historical": {
            "confidence_stars": analysis.get("confidence_stars", "❓"),
            "confidence_label": analysis.get("confidence_label", "VERİ YOK"),
            "reversed_count": analysis.get("reversed_count", 0),
            "total_similar_days": analysis.get("total_similar_days", 0),
            "reversal_rate": analysis.get("reversal_rate", 100.0),
            "worst_case": analysis.get("worst_case", 0.0)
        },
        "poly": {
            "slug": poly_info.get("slug", "") if poly_info else "",
            "up_price": up_price,
            "down_price": down_price,
            "safe_outcome_price": safe_price,
            "best_ask": best_cheap_price,
            "depth_at_best": total_cheap_size,
            "depth_at_99": depth_at_99,
            "has_orders_at_99": has_orders_at_99
        }
    }

async def send_signal(message: str):
    """Send a signal to the dedicated signal Telegram channel."""
    if not _signal_bot or not SIGNAL_CHAT_ID:
        logger.warning(f"Signal bot not configured. Missed: {message[:100]}")
        return
    try:
        await _signal_bot.send_message(
            chat_id=SIGNAL_CHAT_ID, text=message, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send signal: {e}")

async def send_signal_with_photo(message: str, photo_bytes: bytes):
    """Send a signal with a beautifully rendered card photo to the dedicated signal channel."""
    if not _signal_bot or not SIGNAL_CHAT_ID:
        logger.warning(f"Signal bot not configured. Missed photo: {message[:100]}")
        return
    try:
        photo_file = io.BytesIO(photo_bytes)
        photo_file.name = "opportunity_card.png"
        await _signal_bot.send_photo(
            chat_id=SIGNAL_CHAT_ID,
            photo=photo_file,
            caption=message,
            parse_mode="HTML"
        )
        logger.info("Signal card photo successfully sent to Telegram!")
    except Exception as e:
        logger.error(f"Failed to send signal with photo: {e}. Falling back to text-only signal...")
        try:
            await send_signal(message)
        except Exception as e2:
            logger.error(f"Fallback text signal failed as well: {e2}")

async def send_english_delayed_task(card_data: dict, scan_type: str):
    """Wait 15 minutes, then construct the English message and send it to the English channel."""
    logger.info(f"Scheduled delayed English signal for {card_data['symbol']} {card_data['direction']} in 15 minutes...")
    await asyncio.sleep(900)  # 15 minutes delay
    
    try:
        symbol = card_data["symbol"]
        direction = card_data["direction"]
        current_price = card_data["current_price"]
        ref_price = card_data["ref_price"]
        diff_pct = card_data["diff_pct"]
        minutes_to_close = card_data["minutes_to_close"]
        
        hours_left = minutes_to_close // 60
        mins_left = minutes_to_close % 60
        
        hist = card_data["historical"]
        poly = card_data["poly"]
        
        # Translate confidence label
        tr_label = hist.get("confidence_label", "VERİ YOK")
        label_map = {
            "ÇOK GÜVENLİ": "VERY SAFE",
            "GÜVENLİ": "SAFE",
            "GÜVENLİ (az veri)": "SAFE (low data)",
            "ORTA": "MODERATE",
            "ORTA (az veri)": "MODERATE (low data)",
            "ORTA (çok az veri)": "MODERATE (very low data)",
            "RİSKLİ": "RISKY",
            "TEHLİKELİ": "DANGEROUS",
            "YETERSİZ VERİ": "INSUFFICIENT DATA",
            "VERİ YOK": "NO DATA"
        }
        confidence_label_en = label_map.get(tr_label, tr_label)
        
        # Worst-case description
        worst = hist.get("worst_case", 0.0)
        worst_case_desc = f"{worst:+.2f}%" if worst != 0 else "Never reversed"
        
        # Polymarket Pricing (No orderbook details for the free channel)
        poly_price_str = ""
        profit_str = ""
        slug_link = ""
        
        up_p = poly.get("up_price", 0.0)
        down_p = poly.get("down_price", 0.0)
        safe_price = poly.get("safe_outcome_price", 0.0)
        slug = poly.get("slug", "")
        
        if slug:
            slug_link = f"https://polymarket.com/event/{slug}"
            poly_price_str = f"Up {up_p*100:.0f}¢ / Down {down_p*100:.0f}¢"
            
            if safe_price > 0:
                profit_pct = ((1.0 - safe_price) / safe_price) * 100
                profit_str = f"Buy <b>{direction}</b> ({safe_price*100:.0f}¢ ➔ $1.00 = {profit_pct:.1f}% expected yield)"
                
        # Format the English Telegram message (Cleaned: no CLOB Order Book)
        title_prefix = "🎯 <b>SAFE BET SIGNAL (15-MIN DELAYED)</b>"
        if scan_type == "hourly":
            title_prefix = "🎯 <b>HOURLY SCANNER - SAFE BET SIGNAL (15-MIN DELAYED)</b>"
        elif scan_type == "manual":
            title_prefix = "🎯 <b>ON-DEMAND SCAN - SAFE BET SIGNAL (15-MIN DELAYED)</b>"
            
        msg_en = (
            f"{title_prefix}\n\n"
            f"📊 <b>{symbol} {direction}</b>\n"
            f"<b>Price:</b> ${current_price:.2f} ({diff_pct:+.2f}%)\n"
            f"<b>Yesterday's Close:</b> ${ref_price:.2f}\n"
            f"<b>Time to Close:</b> {hours_left}h {mins_left}m\n\n"
            f"📈 <b>Historical Analysis (Last 60 Days):</b>\n"
            f"• At similar levels: {hist.get('reversed_count')}/{hist.get('total_similar_days')} reversals ➔ {hist.get('reversal_rate'):.1f}%\n"
            f"• Worst-case scenario: {worst_case_desc}\n\n"
        )
        
        if poly_price_str:
            msg_en += (
                f"💰 <b>Polymarket:</b> {poly_price_str}\n"
                f"💵 <b>Recommendation:</b> {profit_str}\n\n"
            )
            
        if slug_link:
            msg_en += f"🔗 <a href='{slug_link}'>Trade on Polymarket ↗</a>\n\n"
            
        msg_en += (
            f"<b>{hist.get('confidence_stars')} {confidence_label_en}</b>"
        )
        
        # Strip out order book details for the free channel image
        card_data_en = dict(card_data)
        card_data_en["has_orders_at_99"] = False
        if "poly" in card_data_en:
            card_data_en["poly"] = dict(card_data_en["poly"])
            card_data_en["poly"]["best_ask"] = None
            card_data_en["poly"]["depth_at_best"] = 0.0
            card_data_en["poly"]["depth_at_99"] = 0.0
            card_data_en["poly"]["has_orders_at_99"] = False
            
        # Render the English card image (Cleaned)
        photo_bytes_en = image_generator.generate_card_image(card_data_en, lang="en")
        
        # Send delayed signal to English Telegram channel
        if not _signal_bot or not ENGLISH_SIGNAL_CHAT_ID:
            logger.warning("English Telegram Bot/Channel not configured.")
            return
            
        photo_file = io.BytesIO(photo_bytes_en)
        photo_file.name = "opportunity_card_en.png"
        
        await _signal_bot.send_photo(
            chat_id=ENGLISH_SIGNAL_CHAT_ID,
            photo=photo_file,
            caption=msg_en,
            parse_mode="HTML"
        )
        logger.info(f"Delayed English signal successfully sent to {ENGLISH_SIGNAL_CHAT_ID}!")
        
    except Exception as e:
        logger.error(f"Failed to send delayed English signal: {e}")


# ─── Historical Data Loading ───────────────────────────────────────────────

async def load_historical_data():
    """
    Load 60 days of hourly candle data from Pyth for all watchlist stocks.
    Builds per-stock reversal analysis database.
    Called on startup and refreshed daily.
    """
    global _historical_cache, _cache_loaded, _cache_load_date

    logger.info("Signal Scanner: Loading 60-day historical data...")

    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)

    # Go back 90 calendar days to guarantee ~60 trading days
    from_dt = now_et - timedelta(days=90)
    from_ts = int(from_dt.timestamp())
    to_ts = int(now_et.timestamp())

    loaded_count = 0

    for symbol in SCAN_WATCHLIST:
        try:
            # Check SQLite database cache first
            try:
                cached_data, updated_at = await database.get_historical_cache(symbol)
                if cached_data and updated_at:
                    up_dt = datetime.fromisoformat(updated_at)
                    # If the cache is less than 12 hours old, we can reuse it
                    if (datetime.now() - up_dt).total_seconds() < 12 * 3600:
                        _historical_cache[symbol] = cached_data
                        loaded_count += 1
                        logger.info(f"Loaded {symbol} history from DB cache (updated {updated_at})")
                        continue
            except Exception as cache_err:
                logger.error(f"Error reading historical cache from DB for {symbol}: {cache_err}")

            full_symbol = pyth_client.SYMBOL_MAP.get(symbol.upper())
            if symbol.upper() == "WTI":
                et_tz2 = pytz.timezone('US/Eastern')
                full_symbol = pyth_client.get_wti_active_contract(datetime.now(et_tz2))
            elif symbol.upper() == "NG":
                et_tz2 = pytz.timezone('US/Eastern')
                full_symbol = pyth_client.get_ng_active_contract(datetime.now(et_tz2))
            if not full_symbol:
                logger.warning(f"No Pyth mapping for {symbol}, skipping")
                continue

            data = await pyth_client.get_tv_history_raw(
                full_symbol=full_symbol,
                resolution="60",
                from_ts=from_ts,
                to_ts=to_ts,
                max_retries=5
            )

            if not data or data.get("s") != "ok" or "t" not in data:
                logger.warning(f"No history for {symbol}: {data.get('s') if data else 'No Data'}")
                # Wait before next symbol even on failure
                await asyncio.sleep(1.0)
                continue

            timestamps = data["t"]
            opens_arr = data["o"]
            closes_arr = data["c"]
            highs_arr = data["h"]
            lows_arr = data["l"]

            # Group candles by trading day
            daily_candles = {}
            for i, ts in enumerate(timestamps):
                dt = datetime.fromtimestamp(ts, tz=et_tz)
                date_str = dt.strftime("%Y-%m-%d")
                if date_str not in daily_candles:
                    daily_candles[date_str] = []
                daily_candles[date_str].append({
                    "hour": dt.hour,
                    "minute": dt.minute,
                    "open": opens_arr[i],
                    "high": highs_arr[i],
                    "low": lows_arr[i],
                    "close": closes_arr[i],
                })

            # Build per-day analysis data
            sorted_dates = sorted(daily_candles.keys())
            analysis_data = []

            for idx in range(1, len(sorted_dates)):
                prev_date = sorted_dates[idx - 1]
                curr_date = sorted_dates[idx]
                prev_candles = daily_candles[prev_date]
                curr_candles = daily_candles[curr_date]

                if not prev_candles or not curr_candles:
                    continue

                prev_close = prev_candles[-1]["close"]
                final_close = curr_candles[-1]["close"]

                if prev_close == 0:
                    continue

                final_diff_pct = ((final_close - prev_close) / prev_close) * 100
                final_direction = "UP" if final_diff_pct > 0 else "DOWN"

                # Snapshots at each hour (using candle OPEN = price at that hour)
                snapshots = []
                for candle in curr_candles:
                    price = candle["open"]
                    diff = ((price - prev_close) / prev_close) * 100
                    is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
                    close_mins = 1020 if is_commodity else 960
                    mtc = max(0, close_mins - (candle["hour"] * 60 + candle["minute"]))
                    snapshots.append({
                        "hour": candle["hour"],
                        "price": price,
                        "diff_pct": diff,
                        "direction": "UP" if diff > 0 else "DOWN",
                        "minutes_to_close": mtc,
                    })

                analysis_data.append({
                    "date": curr_date,
                    "prev_close": prev_close,
                    "final_close": final_close,
                    "final_diff_pct": final_diff_pct,
                    "final_direction": final_direction,
                    "snapshots": snapshots,
                })

            _historical_cache[symbol] = analysis_data
            loaded_count += 1
            logger.info(f"  {symbol}: {len(analysis_data)} trading days")

            # Save successfully loaded data to SQLite database cache
            try:
                await database.save_historical_cache(symbol, analysis_data)
            except Exception as cache_err:
                logger.error(f"Error saving historical cache to DB for {symbol}: {cache_err}")

            await asyncio.sleep(3.0)  # Rate-limit friendly: must be slow for Pyth TV API when hitting network

        except Exception as e:
            logger.error(f"Error loading history for {symbol}: {e}")

    _cache_loaded = True
    _cache_load_date = now_et.strftime("%Y-%m-%d")
    logger.info(f"Historical data loaded: {loaded_count}/{len(SCAN_WATCHLIST)} symbols")


# ─── Reversal Analysis ─────────────────────────────────────────────────────

def analyze_reversal_risk(
    symbol: str, current_diff_pct: float, minutes_to_close: int
) -> dict:
    """
    Dynamically analyze historical reversal probability.
    No fixed threshold — uses the stock's own history to determine safety.

    Signal condition: at current level or above, reversed <= 1 time in 60 days.
    """
    if symbol not in _historical_cache:
        return {"is_safe_bet": False, "reason": "no_data"}

    data = _historical_cache[symbol]
    current_dir = "UP" if current_diff_pct > 0 else "DOWN"
    abs_diff = abs(current_diff_pct)

    # Too close to call
    if abs_diff < 0.1:
        return {"is_safe_bet": False, "reason": "too_close"}

    # Two levels of analysis:
    # 1) "at_level": days where stock was at current level OR HIGHER (conservative)
    # 2) "similar": days where stock was at ≥50% of current level (more data points)
    at_level = []
    similar = []

    for day in data:
        best_match = None
        for snap in day["snapshots"]:
            if snap["direction"] != current_dir:
                continue
            # Match time: within ±30 min window (for hourly data)
            if abs(snap["minutes_to_close"] - minutes_to_close) > 30:
                continue
            if best_match is None or abs(snap["minutes_to_close"] - minutes_to_close) < abs(best_match["minutes_to_close"] - minutes_to_close):
                best_match = snap

        if best_match is None:
            continue

        snap_abs = abs(best_match["diff_pct"])
        reversed_flag = day["final_direction"] != current_dir

        entry = {
            "date": day["date"],
            "snap_diff": best_match["diff_pct"],
            "snap_min": best_match["minutes_to_close"],
            "final_diff": day["final_diff_pct"],
            "reversed": reversed_flag,
        }

        # At current level or higher (90% tolerance)
        if snap_abs >= abs_diff * 0.9:
            at_level.append(entry)

        # Similar level (≥50% of current)
        if snap_abs >= abs_diff * 0.5:
            similar.append(entry)

    total_at = len(at_level)
    rev_at = sum(1 for e in at_level if e["reversed"])

    total_sim = len(similar)
    rev_sim = sum(1 for e in similar if e["reversed"])

    # Use "at_level" if we have enough data, otherwise fall back to "similar"
    if total_at >= 3:
        total, reversed_count = total_at, rev_at
    else:
        total, reversed_count = total_sim, rev_sim

    reversal_rate = (reversed_count / total * 100) if total > 0 else 100.0

    # Worst-case reversal
    worst_case = 0.0
    all_reversed = [e for e in (at_level + similar) if e["reversed"]]
    if all_reversed:
        if current_dir == "UP":
            worst_case = min(e["final_diff"] for e in all_reversed)
        else:
            worst_case = max(e["final_diff"] for e in all_reversed)

    # Confidence determination — signal fires if reversed <= 1
    is_safe = False
    if total >= 5:
        if reversed_count == 0:
            stars, label = "⭐⭐⭐⭐⭐", "ÇOK GÜVENLİ"
            is_safe = True
        elif reversed_count == 1:
            stars, label = "⭐⭐⭐⭐", "GÜVENLİ"
            is_safe = True
        elif reversal_rate <= 5:
            stars, label = "⭐⭐⭐", "ORTA"
            is_safe = False
        else:
            stars, label = "⭐⭐", "RİSKLİ"
            is_safe = False
    elif total >= 2:
        if reversed_count == 0:
            stars, label = "⭐⭐⭐⭐", "GÜVENLİ (az veri)"
            is_safe = True
        elif reversed_count <= 1:
            stars, label = "⭐⭐⭐", "ORTA (az veri)"
            is_safe = True
        else:
            stars, label = "⭐⭐", "YETERSİZ VERİ"
            is_safe = False
    elif total == 1:
        if reversed_count == 0:
            stars, label = "⭐⭐⭐", "ORTA (çok az veri)"
            is_safe = True
        else:
            stars, label = "⭐", "TEHLİKELİ"
            is_safe = False
    else:
        stars, label = "❓", "VERİ YOK"
        is_safe = False

    return {
        "total_similar_days": total,
        "reversed_count": reversed_count,
        "reversal_rate": reversal_rate,
        "worst_case": worst_case,
        "confidence_stars": stars,
        "confidence_label": label,
        "is_safe_bet": is_safe,
        "at_level": {"total": total_at, "reversed": rev_at},
        "similar": {"total": total_sim, "reversed": rev_sim},
    }


# ─── Polymarket Integration ────────────────────────────────────────────────

async def fetch_polymarket_events() -> dict:
    """
    Fetch active binary events from Polymarket Gamma API by utilizing the new fetch_active_binary_markets.
    Returns: {symbol: {"up_price": ..., "down_price": ..., "up_token_id": ..., "down_token_id": ..., "slug": ...}}
    """
    global _poly_cache, _last_poly_time

    now = time_mod.time()
    if now - _last_poly_time < 30 and _poly_cache:
        return _poly_cache

    try:
        results = {}
        markets = await fetch_active_binary_markets()
        
        for market in markets:
            question = market.get("question", "")
            symbol, threshold, m_type = parse_market_question(question)
            if not symbol or threshold is None or not m_type:
                continue
                
            if symbol not in results:
                outcomes = json.loads(market.get("outcomes", "[]"))
                prices = json.loads(market.get("outcomePrices", "[]"))
                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    try:
                        tokens = json.loads(tokens)
                    except:
                        tokens = []
                        
                up_price = down_price = 0.0
                up_token = down_token = ""
                
                for i, out in enumerate(outcomes):
                    out_upper = out.upper()
                    if out_upper in ("YES", "UP"):
                        up_price = float(prices[i]) if i < len(prices) else 0.0
                        up_token = tokens[i] if i < len(tokens) else ""
                    elif out_upper in ("NO", "DOWN"):
                        down_price = float(prices[i]) if i < len(prices) else 0.0
                        down_token = tokens[i] if i < len(tokens) else ""
                        
                results[symbol] = {
                    "up_price": up_price,
                    "down_price": down_price,
                    "up_token_id": up_token,
                    "down_token_id": down_token,
                    "slug": market.get("slug", ""),
                    "threshold": threshold,
                    "type": m_type
                }
                
        _poly_cache = results
        _last_poly_time = now
        return results

    except Exception as e:
        logger.error(f"Error fetching Polymarket events: {e}")
        return _poly_cache



async def fetch_orderbook_depth(token_id: str) -> dict:
    """
    Fetch orderbook from Polymarket CLOB API.
    Returns: {"asks": {price: total_size}, "bids": {price: total_size}}
    """
    if not token_id:
        return {"asks": {}, "bids": {}}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            book = resp.json()

        asks, bids = {}, {}
        for entry in book.get("asks", []):
            p = round(float(entry.get("price", 0)), 2)
            s = float(entry.get("size", 0))
            asks[p] = asks.get(p, 0) + s

        for entry in book.get("bids", []):
            p = round(float(entry.get("price", 0)), 2)
            s = float(entry.get("size", 0))
            bids[p] = bids.get(p, 0) + s

        return {"asks": asks, "bids": bids}

    except Exception as e:
        logger.error(f"Error fetching orderbook: {e}")
        return {"asks": {}, "bids": {}}


# ─── Main Scanner ──────────────────────────────────────────────────────────

async def scan_for_signals():
    """
    Core scanning logic. For each watchlist stock:
    1. Get current price vs yesterday's close
    2. Analyze historical reversal risk (DYNAMIC threshold per stock)
    3. Check Polymarket prices + orderbook depth at 99¢+
    4. If safe (reversed <= 1 in history) → send signal
    """
    global _signals_sent_today

    if not _cache_loaded:
        return

    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    # Fetch Polymarket data (cached 30s)
    poly_data = await fetch_polymarket_events()

    for symbol in SCAN_WATCHLIST:
        try:
            # Skip stocks if they are closed (after 4:00 PM ET / 960 mins)
            is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
            if not is_commodity and total_minutes >= 960:
                continue

            # Calculate individual minutes to close
            close_minutes = 1020 if is_commodity else 960
            minutes_to_close = max(0, close_minutes - total_minutes)
            pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
            if not pyth_id:
                continue

            current_price = await pyth_client.get_active_price(symbol, pyth_id)
            if not current_price:
                continue

            from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
            ref_price = await pyth_client.get_historical_candle_price(
                full_symbol, pyth_id, from_ts, to_ts, price_type="close"
            )
            if not ref_price or ref_price == 0:
                continue

            diff_pct = ((current_price - ref_price) / ref_price) * 100
            abs_diff = abs(diff_pct)
            direction = "UP" if diff_pct > 0 else "DOWN"

            # ── Historical analysis (dynamic threshold) ──
            analysis = analyze_reversal_risk(symbol, diff_pct, minutes_to_close)

            if not analysis.get("is_safe_bet"):
                continue

            # ── Polymarket info ──
            poly_price_str = ""
            order_str = ""
            profit_str = ""
            book = None

            poly_info = poly_data.get(symbol)
            if poly_info:
                up_p = poly_info["up_price"]
                down_p = poly_info["down_price"]
                poly_price_str = (
                    f"\n<b>💰 Polymarket:</b> Up {up_p*100:.0f}¢ / Down {down_p*100:.0f}¢"
                )

                safe_price = up_p if direction == "UP" else down_p
                safe_token = (
                    poly_info.get("up_token_id")
                    if direction == "UP"
                    else poly_info.get("down_token_id")
                )

                if safe_price > 0:
                    profit_pct = ((1.0 - safe_price) / safe_price) * 100
                    profit_str = (
                        f"\n<b>💵 Tavsiye:</b> {direction} al "
                        f"({safe_price*100:.0f}¢ → $1.00 = %{profit_pct:.1f} kâr)"
                    )

                # Orderbook depth at high prices
                if safe_token:
                    book = await fetch_orderbook_depth(safe_token)
                    high_asks = {
                        p: s
                        for p, s in book.get("asks", {}).items()
                        if p >= 0.90
                    }
                    if high_asks:
                        best_ask = min(high_asks.keys())
                        total_size = sum(high_asks.values())
                        order_str = (
                            f"\n<b>📦 Emir Derinliği:</b> "
                            f"{best_ask*100:.0f}¢'dan ${total_size:,.0f} alınabilir"
                        )

            # ── Generate the Opportunity Card Image ──
            photo_bytes = None
            try:
                card_data = build_card_data(
                    symbol=symbol,
                    direction=direction,
                    current_price=current_price,
                    ref_price=ref_price,
                    diff_pct=diff_pct,
                    minutes_to_close=minutes_to_close,
                    analysis=analysis,
                    poly_info=poly_info,
                    book=book
                )
                photo_bytes = image_generator.generate_card_image(card_data)
            except Exception as e:
                logger.error(f"Error generating card image in scan_for_signals: {e}")

            # ── Signal decision ──
            key = (symbol, direction)
            now_ts = time_mod.time()

            if key in _signals_sent_today:
                # Already sent a signal for this symbol and direction today. Skip to avoid duplicates on Telegram.
                continue

            # ── NEW SIGNAL ──
            rev = analysis
            a_lev = rev["at_level"]
            a_sim = rev["similar"]

            hist_lines = []
            if a_lev["total"] > 0:
                r_pct = (a_lev["reversed"] / a_lev["total"] * 100) if a_lev["total"] else 0
                hist_lines.append(
                    f"• %{abs_diff:.1f}+ ve ≤{minutes_to_close + 30}dk kala: "
                    f"{a_lev['reversed']}/{a_lev['total']} kez ters döndü → %{r_pct:.1f}"
                )
            if a_sim["total"] > 0 and a_sim["total"] != a_lev["total"]:
                r_pct = (a_sim["reversed"] / a_sim["total"] * 100) if a_sim["total"] else 0
                hist_lines.append(
                    f"• %{abs_diff * 0.5:.1f}+ benzer seviye: "
                    f"{a_sim['reversed']}/{a_sim['total']} kez ters döndü → %{r_pct:.1f}"
                )
            if rev["worst_case"] != 0:
                hist_lines.append(f"• En kötü senaryo: %{rev['worst_case']:+.2f}")
            else:
                hist_lines.append("• En kötü senaryo: hiç ters dönmemiş ✅")

            hist_str = "\n".join(hist_lines)

            msg = (
                f"🎯 <b>GÜVENLİ BAHİS SİNYALİ</b>\n\n"
                f"📊 <b>{symbol} {direction}</b>\n"
                f"<b>Anlık:</b> ${current_price:.2f} ({diff_pct:+.2f}%)\n"
                f"<b>Kapanışa:</b> {minutes_to_close}dk"
                f"{poly_price_str}{order_str}\n\n"
                f"📈 <b>Tarihsel Analiz (son 60 gün):</b>\n"
                f"{hist_str}\n\n"
                f"<b>{rev['confidence_stars']} {rev['confidence_label']}</b>"
                f"{profit_str}"
            )
            if photo_bytes:
                await send_signal_with_photo(msg, photo_bytes)
            else:
                await send_signal(msg)

            _signals_sent_today[key] = {
                "diff_pct": diff_pct,
                "time": now_ts,
                "was_safe": True,
            }
            logger.info(f"Signal sent: {symbol} {direction} ({diff_pct:+.2f}%)")

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")

    # Also scan for guaranteed bets
    await scan_guaranteed_bets()


# ─── Background Loop ───────────────────────────────────────────────────────

async def signal_scanner_loop():
    """Background loop for the signal scanner."""
    global _signals_sent_today, _last_signal_scan_time, _cache_load_date

    logger.info("Signal Scanner starting...")

    # Initial load
    await load_historical_data()

    if _cache_loaded:
        lines = [f"  • {sym}: {len(days)} gün" for sym, days in _historical_cache.items()]
        await send_signal(
            "🤖 <b>Finance Signal Scanner aktif!</b>\n"
            f"📊 {len(_historical_cache)} hisse için 60 günlük veri yüklendi.\n"
            "⏰ Tarama: 22:00-23:00 TR (kapanışa son 1 saat)\n"
            "📡 Her 1 dakikada bir tarama.\n\n"
            "<b>Yüklenen hisseler:</b>\n" + "\n".join(lines)
        )

    while True:
        try:
            et_tz = pytz.timezone("US/Eastern")
            now_et = datetime.now(et_tz)
            total_minutes = now_et.hour * 60 + now_et.minute
            today_str = now_et.strftime("%Y-%m-%d")

            # Refresh historical data once per day (before scan window)
            if _cache_load_date != today_str and total_minutes >= SCAN_START_MINUTES - 30:
                logger.info("Refreshing daily historical data...")
                await load_historical_data()

            # Reset daily signals after market close
            if total_minutes > SCAN_END_MINUTES + 10 and _signals_sent_today:
                _signals_sent_today = {}

            # Weekdays only
            if now_et.weekday() >= 5:
                await asyncio.sleep(60)
                continue

            # Active window: 15:00-16:00 ET
            if not (SCAN_START_MINUTES <= total_minutes < SCAN_END_MINUTES):
                await asyncio.sleep(30)
                continue

            # Scan interval
            now_ts = time_mod.time()
            if now_ts - _last_signal_scan_time < SCAN_INTERVAL:
                await asyncio.sleep(5)
                continue

            _last_signal_scan_time = now_ts
            logger.info(f"Signal scan running... ({now_et.strftime('%H:%M')} ET, {minutes_to_close(total_minutes)}dk kala)")
            await scan_for_signals()

        except Exception as e:
            logger.error(f"Error in signal scanner loop: {e}")

        await asyncio.sleep(5)


def minutes_to_close(total_minutes: int) -> int:
    """Helper: calculate minutes until market close."""
    return max(0, SCAN_END_MINUTES - total_minutes)


def start_signal_scanner():
    """Start the signal scanner background task."""
    asyncio.create_task(signal_scanner_loop())
    asyncio.create_task(hourly_scanner_loop())
    asyncio.create_task(background_scan_loop())


_last_hourly_scan_hour = -1


# ─── Background Dashboard Scan Loop ───────────────────────────────────────

def get_cached_results() -> dict:
    """Return the latest cached scan results for the /api/scan-results endpoint."""
    return {
        "results": _cached_scan_results,
        "scan_time": _last_scan_time,
    }


async def background_scan_loop():
    """
    Background loop that continuously scans all watchlist symbols sequentially,
    caching results in _cached_scan_results for the frontend to poll via /api/scan-results.
    Runs every 60s during market hours, every 120s otherwise.
    Scans symbols ONE BY ONE with small delays to avoid Pyth API 429 rate limits.
    """
    global _cached_scan_results, _last_scan_time, _historical_cache, _cache_loaded

    logger.info("Background scan loop starting... waiting for historical data to load.")

    # Wait for initial history load
    while not _cache_loaded:
        await asyncio.sleep(5)

    logger.info("Background scan loop active. Will scan sequentially to avoid rate limits.")

    while True:
        try:
            et_tz = pytz.timezone("US/Eastern")
            now_et = datetime.now(et_tz)
            total_minutes = now_et.hour * 60 + now_et.minute

            # Active session: 4:00 AM - 8:00 PM ET on weekdays (Pre-market + Regular + Post-market)
            is_active_session = (
                now_et.weekday() < 5
                and 240 <= total_minutes < 1200
            )
            scan_interval = 30 if is_active_session else 120

            logger.info(f"Background scan starting... ({'active session' if is_active_session else 'off hours'}, interval={scan_interval}s)")

            # Determine time context (same logic as run_manual_scan)
            is_off_hours = False
            off_hours_reason = ""

            if now_et.weekday() >= 5:
                minutes_to_close_val = 60
                is_off_hours = True
                off_hours_reason = "Hafta sonu nedeniyle ABD piyasaları kapalıdır. Test edebilmeniz amacıyla analiz kapanışa 1 saat (60 dk) kala şeklinde simüle edilmiştir."
            elif total_minutes < 570:
                minutes_to_close_val = 360
                is_off_hours = True
                off_hours_reason = "ABD piyasaları henüz açılmamıştır (Pre-market). Risk analizi tüm işlem gününü kapsayacak şekilde açılış mumu (360 dk kala) referans alınarak yapılmıştır."
            elif total_minutes >= 960:
                minutes_to_close_val = 60
                is_off_hours = True
                off_hours_reason = "ABD piyasaları kapanmıştır. Test edebilmeniz amacıyla analiz kapanışa 1 saat (60 dk) kala şeklinde simüle edilmiştir."
            else:
                minutes_to_close_val = max(0, 960 - total_minutes)
                is_off_hours = False
                off_hours_reason = ""

            # Fetch Polymarket active events
            poly_data = await fetch_polymarket_events()

            scan_results = []

            # Scan symbols SEQUENTIALLY (one by one) to avoid 429 rate limits
            for symbol in SCAN_WATCHLIST:
                try:
                    pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
                    if not pyth_id:
                        continue

                    current_price = await pyth_client.get_active_price(symbol, pyth_id)
                    if not current_price:
                        await asyncio.sleep(0.5)
                        continue

                    from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
                    ref_price = await pyth_client.get_historical_candle_price(
                        full_symbol, pyth_id, from_ts, to_ts, price_type="close"
                    )
                    if not ref_price or ref_price == 0:
                        await asyncio.sleep(0.5)
                        continue

                    diff_pct = ((current_price - ref_price) / ref_price) * 100
                    direction = "UP" if diff_pct > 0 else "DOWN"

                    # Calculate actual minutes to close for this specific symbol
                    is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
                    close_mins = 1020 if is_commodity else 960
                    real_minutes_to_close = max(0, close_mins - total_minutes)

                    if is_off_hours:
                        if total_minutes < 570 and now_et.weekday() < 5:
                            symbol_minutes_to_close = real_minutes_to_close
                            analysis_minutes = min(360 if not is_commodity else 420, real_minutes_to_close)
                        else:
                            symbol_minutes_to_close = minutes_to_close_val
                            analysis_minutes = minutes_to_close_val
                    else:
                        symbol_minutes_to_close = real_minutes_to_close
                        analysis_minutes = real_minutes_to_close

                    # Reversal analysis
                    analysis = analyze_reversal_risk(symbol, diff_pct, analysis_minutes)

                    # Polymarket details
                    up_price = 0.0
                    down_price = 0.0
                    safe_price = 0.0
                    poly_slug = ""
                    best_cheap_price = None
                    total_cheap_size = 0.0
                    depth_at_99 = 0.0
                    has_orders_at_99 = False

                    book = None
                    poly_info = poly_data.get(symbol)
                    if poly_info:
                        up_price = poly_info.get("up_price", 0.0)
                        down_price = poly_info.get("down_price", 0.0)
                        poly_slug = poly_info.get("slug", "")
                        safe_price = up_price if direction == "UP" else down_price
                        safe_token = (
                            poly_info.get("up_token_id")
                            if direction == "UP"
                            else poly_info.get("down_token_id")
                        )

                        if safe_token:
                            book = await fetch_orderbook_depth(safe_token)

                            cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
                            if cheap_asks:
                                best_cheap_price = min(cheap_asks.keys())
                                total_cheap_size = sum(cheap_asks.values())

                            depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
                            has_orders_at_99 = depth_at_99 > 0 or (best_cheap_price is not None and best_cheap_price <= 0.99 and total_cheap_size > 0)

                    is_impossible = analysis.get("reversed_count", 99) <= 1 and analysis.get("total_similar_days", 0) >= 2
                    is_safe_bet = analysis.get("is_safe_bet", False)

                    scan_results.append({
                        "symbol": symbol,
                        "direction": direction,
                        "current_price": current_price,
                        "ref_price": ref_price,
                        "diff_pct": diff_pct,
                        "minutes_to_close": symbol_minutes_to_close,
                        "is_off_hours": is_off_hours,
                        "off_hours_reason": off_hours_reason,
                        "historical": {
                            "total_similar_days": analysis.get("total_similar_days", 0),
                            "reversed_count": analysis.get("reversed_count", 0),
                            "reversal_rate": analysis.get("reversal_rate", 100.0),
                            "worst_case": analysis.get("worst_case", 0.0),
                            "confidence_stars": analysis.get("confidence_stars", "❓"),
                            "confidence_label": analysis.get("confidence_label", "VERİ YOK"),
                        },
                        "poly": {
                            "slug": poly_slug,
                            "up_price": up_price,
                            "down_price": down_price,
                            "safe_outcome_price": safe_price,
                            "best_ask": best_cheap_price,
                            "depth_at_best": total_cheap_size if best_cheap_price else 0.0,
                            "depth_at_99": depth_at_99,
                            "has_orders_at_99": has_orders_at_99
                        },
                        "is_safe_bet": is_safe_bet,
                        "is_impossible": is_impossible,
                        "has_orders_at_99": has_orders_at_99
                    })

                except Exception as e:
                    logger.error(f"Background scan error for {symbol}: {e}")

                # Small delay between each symbol to avoid 429 rate limits
                await asyncio.sleep(0.5)

            # Sort results: safe bets with orders at top, then by diff_pct
            def sort_key(item):
                is_safe = item["is_safe_bet"]
                is_imp = item["is_impossible"]
                has_99 = item["poly"]["has_orders_at_99"]
                abs_diff = abs(item["diff_pct"])

                score = 0
                if is_safe or is_imp:
                    score += 1000
                if has_99:
                    score += 500

                return (score, abs_diff)

            scan_results.sort(key=sort_key, reverse=True)

            # Update the global cache
            _cached_scan_results = scan_results
            _last_scan_time = datetime.now(et_tz).isoformat()

            logger.info(f"Background scan complete: {len(scan_results)} symbols cached.")

        except Exception as e:
            logger.error(f"Error in background scan loop: {e}")

        await asyncio.sleep(scan_interval)


async def refresh_orderbook_only() -> list:
    """
    Refresh only the Polymarket orderbook data for the existing cached scan results.
    This is fast since it only hits the Polymarket API (not Pyth).
    Re-fetches poly events and orderbook depth for each cached result's token IDs.
    """
    global _cached_scan_results, _last_scan_time

    if not _cached_scan_results:
        return []

    # Re-fetch Polymarket active events (force refresh by resetting cache timer)
    global _last_poly_time
    _last_poly_time = 0
    poly_data = await fetch_polymarket_events()

    updated_results = []

    for result in _cached_scan_results:
        try:
            symbol = result["symbol"]
            direction = result["direction"]

            # Start with the existing result (keeps price/historical data intact)
            updated = dict(result)

            poly_info = poly_data.get(symbol)
            if poly_info:
                up_price = poly_info.get("up_price", 0.0)
                down_price = poly_info.get("down_price", 0.0)
                poly_slug = poly_info.get("slug", "")
                safe_price = up_price if direction == "UP" else down_price
                safe_token = (
                    poly_info.get("up_token_id")
                    if direction == "UP"
                    else poly_info.get("down_token_id")
                )

                best_cheap_price = None
                total_cheap_size = 0.0
                depth_at_99 = 0.0
                has_orders_at_99 = False

                if safe_token:
                    book = await fetch_orderbook_depth(safe_token)

                    cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
                    if cheap_asks:
                        best_cheap_price = min(cheap_asks.keys())
                        total_cheap_size = sum(cheap_asks.values())

                    depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
                    has_orders_at_99 = depth_at_99 > 0 or (best_cheap_price is not None and best_cheap_price <= 0.99 and total_cheap_size > 0)

                updated["poly"] = {
                    "slug": poly_slug,
                    "up_price": up_price,
                    "down_price": down_price,
                    "safe_outcome_price": safe_price,
                    "best_ask": best_cheap_price,
                    "depth_at_best": total_cheap_size if best_cheap_price else 0.0,
                    "depth_at_99": depth_at_99,
                    "has_orders_at_99": has_orders_at_99
                }
                updated["has_orders_at_99"] = has_orders_at_99

            updated_results.append(updated)

        except Exception as e:
            logger.error(f"Error refreshing orderbook for {result.get('symbol')}: {e}")
            updated_results.append(result)  # Keep the old data on error

    # Update the global cache with refreshed poly data
    _cached_scan_results = updated_results
    et_tz = pytz.timezone("US/Eastern")
    _last_scan_time = datetime.now(et_tz).isoformat()

    logger.info(f"Orderbook refresh complete for {len(updated_results)} symbols.")
    return updated_results


async def hourly_scanner_loop():
    """Background loop to run a general scan every hour during the trading session."""
    global _last_hourly_scan_hour, _cache_loaded

    logger.info("Hourly Signal Scanner starting...")

    # Wait for initial history load if needed
    while not _cache_loaded:
        await asyncio.sleep(5)

    while True:
        try:
            et_tz = pytz.timezone("US/Eastern")
            now_et = datetime.now(et_tz)
            total_minutes = now_et.hour * 60 + now_et.minute
            today_weekday = now_et.weekday()

            # Weekdays only
            if today_weekday >= 5:
                await asyncio.sleep(60)
                continue

            # Regular trading hours (9:30 AM to 4:00 PM ET)
            # We want to scan at exactly 10:00, 11:00, 12:00, 13:00, 14:00, 15:00 ET
            if 570 <= total_minutes < 960:
                # Trigger on the hour (e.g. minute == 0)
                if now_et.minute == 0 and _last_hourly_scan_hour != now_et.hour:
                    _last_hourly_scan_hour = now_et.hour
                    logger.info(f"Running hourly signal scan for {now_et.hour}:00 ET...")
                    await run_hourly_scan(total_minutes)
                    
            # Check every 30 seconds
            await asyncio.sleep(30)

        except Exception as e:
            logger.error(f"Error in hourly scanner loop: {e}")
            await asyncio.sleep(10)


async def run_hourly_scan(total_minutes: int):
    """
    Perform an hourly scan of all watchlist stocks and send detailed Telegram alerts
    for any 'impossible' (safe bet) opportunities found.
    """
    # 1. Fetch active Polymarket events
    poly_data = await fetch_polymarket_events()

    minutes_to_close_val = max(0, 960 - total_minutes)
    hours_left = minutes_to_close_val // 60
    mins_left = minutes_to_close_val % 60

    for symbol in SCAN_WATCHLIST:
        try:
            pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
            if not pyth_id:
                continue

            current_price = await pyth_client.get_active_price(symbol, pyth_id)
            if not current_price:
                continue

            from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
            ref_price = await pyth_client.get_historical_candle_price(
                full_symbol, pyth_id, from_ts, to_ts, price_type="close"
            )
            if not ref_price or ref_price == 0:
                continue

            diff_pct = ((current_price - ref_price) / ref_price) * 100
            abs_diff = abs(diff_pct)
            direction = "UP" if diff_pct > 0 else "DOWN"

            # Reversal analysis
            analysis = analyze_reversal_risk(symbol, diff_pct, minutes_to_close_val)

            # Check if it qualifies as an "impossible" or safe bet opportunity
            is_impossible = analysis.get("reversed_count", 99) <= 1 and analysis.get("total_similar_days", 0) >= 2
            is_safe_bet = analysis.get("is_safe_bet", False)

            if not (is_impossible or is_safe_bet):
                continue

            # Skip if we already sent a Telegram signal for this asset today to avoid duplicates
            key = (symbol, direction)
            if key in _signals_sent_today:
                continue

            # Fetch Polymarket pricing + orderbook depth
            poly_price_str = ""
            order_str = ""
            profit_str = ""
            slug_link = ""

            book = None
            poly_info = poly_data.get(symbol)
            if poly_info:
                up_p = poly_info["up_price"]
                down_p = poly_info["down_price"]
                poly_slug = poly_info["slug"]
                slug_link = f"https://polymarket.com/event/{poly_slug}"

                poly_price_str = f"Up {up_p*100:.0f}¢ / Down {down_p*100:.0f}¢"

                safe_price = up_p if direction == "UP" else down_p
                safe_token = (
                    poly_info.get("up_token_id")
                    if direction == "UP"
                    else poly_info.get("down_token_id")
                )

                if safe_price > 0:
                    profit_pct = ((1.0 - safe_price) / safe_price) * 100
                    profit_str = f"<b>{direction}</b> kontratı al ({safe_price*100:.0f}¢ ➔ $1.00 = %{profit_pct:.1f} tahmini kâr)"

                # Orderbook depth
                if safe_token:
                    book = await fetch_orderbook_depth(safe_token)
                    
                    cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
                    if cheap_asks:
                        best_ask = min(cheap_asks.keys())
                        total_size = sum(cheap_asks.values())
                        order_str = f"{best_ask*100:.0f}¢ fiyattan ${total_size:,.0f} alım yapılabilir"
                        
                        # Add specific depth at 99c if available
                        depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
                        if depth_at_99 > 0:
                            order_str += f" (99¢'da ${depth_at_99:,.0f} aktif satıcı)"
                    else:
                        order_str = "Satış emri bulunmuyor"

            # Format historical analysis text
            hist_str = f"• Son 60 günde %{abs_diff:.1f}+ seviyesinde: {analysis['reversed_count']}/{analysis['total_similar_days']} kez ters döndü ➔ %{analysis['reversal_rate']:.1f}\n"
            if analysis["worst_case"] != 0:
                hist_str += f"• En kötü kapanış senaryosu: %{analysis['worst_case']:+.2f}"
            else:
                hist_str += "• En kötü kapanış senaryosu: Hiç ters dönmemiş ✅"

            # ── Construct Telegram Alert Message ──
            msg = (
                f"🎯 <b>SAATLİK SİNYAL SCANNER - GÜVENLİ BAHİS SİNYALİ</b>\n\n"
                f"📊 <b>{symbol} {direction}</b>\n"
                f"<b>Anlık Fiyat:</b> ${current_price:.2f} ({diff_pct:+.2f}%)\n"
                f"<b>Dünkü Kapanış:</b> ${ref_price:.2f}\n"
                f"<b>Kapanışa Kalan Süre:</b> {hours_left} saat {mins_left} dakika\n\n"
                f"📈 <b>Tarihsel Analiz (son 60 gün):</b>\n"
                f"{hist_str}\n\n"
            )

            if poly_price_str:
                msg += (
                    f"💰 <b>Polymarket:</b> {poly_price_str}\n"
                    f"💵 <b>Tavsiye:</b> {profit_str}\n"
                    f"📦 <b>Emir Kitabı (CLOB):</b> {order_str}\n\n"
                )

            if slug_link:
                msg += f"🔗 <a href='{slug_link}'>Polymarket'te İşlem Yap ↗</a>\n"
                
            msg += (
                f"🖥️ <a href='https://upordownwebsite.up.railway.app/'>Canlı Panel Takip ↗</a>\n\n"
                f"<b>{analysis['confidence_stars']} {analysis['confidence_label']}</b>"
            )

            # Generate the card image
            photo_bytes = None
            try:
                card_data = build_card_data(
                    symbol=symbol,
                    direction=direction,
                    current_price=current_price,
                    ref_price=ref_price,
                    diff_pct=diff_pct,
                    minutes_to_close=minutes_to_close_val,
                    analysis=analysis,
                    poly_info=poly_info,
                    book=book
                )
                photo_bytes = image_generator.generate_card_image(card_data)
            except Exception as e:
                logger.error(f"Error generating card image in run_hourly_scan: {e}")

            # Send signal to dedicated Telegram signal channel
            if photo_bytes:
                await send_signal_with_photo(msg, photo_bytes)
            else:
                await send_signal(msg)
            
            # Register in sent list to prevent duplicate alerts today
            _signals_sent_today[key] = {
                "diff_pct": diff_pct,
                "time": time_mod.time(),
                "was_safe": True,
            }
            logger.info(f"Hourly signal sent to Telegram: {symbol} {direction}")
            
            # Schedule delayed English signal for public channel
            asyncio.create_task(send_english_delayed_task(card_data, "hourly"))

        except Exception as e:
            logger.error(f"Error scanning {symbol} during hourly scan: {e}")

    # Also scan for guaranteed bets hourly
    await scan_guaranteed_bets()



async def run_manual_scan() -> list:
    """
    Run an on-demand scan of all watchlist stocks and return their detailed status,
    historical risk analysis, and Polymarket orderbook details.
    """
    global _historical_cache, _cache_loaded

    # 1. Load history if not loaded yet
    if not _cache_loaded:
        await load_historical_data()

    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute

    is_off_hours = False
    off_hours_reason = ""

    if now_et.weekday() >= 5:
        # Weekend: US markets are closed. Default to 60 mins to close for testing
        minutes_to_close_val = 60
        is_off_hours = True
        off_hours_reason = "Hafta sonu nedeniyle ABD piyasaları kapalıdır. Test edebilmeniz amacıyla analiz kapanışa 1 saat (60 dk) kala şeklinde simüle edilmiştir."
    elif total_minutes < 570:
        # Pre-market (before 9:30 AM ET): US markets are not open yet.
        # Analyze risk for the entire trading day (360 minutes to close / opening candle).
        minutes_to_close_val = 360
        is_off_hours = True
        off_hours_reason = "ABD piyasaları henüz açılmamıştır (Pre-market). Risk analizi tüm işlem gününü kapsayacak şekilde açılış mumu (360 dk kala) referans alınarak yapılmıştır."
    elif total_minutes >= 960:
        # After-hours (after 4:00 PM ET): US markets are closed.
        # Default to 60 mins to close for testing
        minutes_to_close_val = 60
        is_off_hours = True
        off_hours_reason = "ABD piyasaları kapanmıştır. Test edebilmeniz amacıyla analiz kapanışa 1 saat (60 dk) kala şeklinde simüle edilmiştir."
    else:
        # Regular trading hours
        minutes_to_close_val = max(0, 960 - total_minutes)
        is_off_hours = False
        off_hours_reason = ""


    # 2. Fetch Polymarket active events
    poly_data = await fetch_polymarket_events()

    async def scan_single_symbol(symbol: str) -> dict:
        try:
            pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
            if not pyth_id:
                return None

            current_price = await pyth_client.get_active_price(symbol, pyth_id)
            if not current_price:
                return None

            from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
            ref_price = await pyth_client.get_historical_candle_price(
                full_symbol, pyth_id, from_ts, to_ts, price_type="close"
            )
            if not ref_price or ref_price == 0:
                return None

            diff_pct = ((current_price - ref_price) / ref_price) * 100
            direction = "UP" if diff_pct > 0 else "DOWN"

            # Calculate actual minutes to close for this specific symbol
            is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
            
            close_mins = 1020 if is_commodity else 960
            real_minutes_to_close = max(0, close_mins - total_minutes)
            
            if is_off_hours:
                # If pre-market on a trading day, use real time left for display, but cap risk analysis to opening candle (360/420 mins)
                if total_minutes < 570 and now_et.weekday() < 5:
                    symbol_minutes_to_close = real_minutes_to_close
                    analysis_minutes = min(360 if not is_commodity else 420, real_minutes_to_close)
                else:
                    # Weekend or After-hours default simulation
                    symbol_minutes_to_close = minutes_to_close_val
                    analysis_minutes = minutes_to_close_val
            else:
                symbol_minutes_to_close = real_minutes_to_close
                analysis_minutes = real_minutes_to_close

            # Reversal analysis (using capped session minutes to match historical trading hours)
            analysis = analyze_reversal_risk(symbol, diff_pct, analysis_minutes)

            # Polymarket details
            up_price = 0.0
            down_price = 0.0
            safe_price = 0.0
            poly_slug = ""
            best_cheap_price = None
            total_cheap_size = 0.0
            depth_at_99 = 0.0
            has_orders_at_99 = False

            book = None
            poly_info = poly_data.get(symbol)
            if poly_info:
                up_price = poly_info.get("up_price", 0.0)
                down_price = poly_info.get("down_price", 0.0)
                poly_slug = poly_info.get("slug", "")
                safe_price = up_price if direction == "UP" else down_price
                safe_token = (
                    poly_info.get("up_token_id")
                    if direction == "UP"
                    else poly_info.get("down_token_id")
                )

                if safe_token:
                    book = await fetch_orderbook_depth(safe_token)
                    
                    # Look for asks between 90c and 99c
                    cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
                    if cheap_asks:
                        best_cheap_price = min(cheap_asks.keys())
                        total_cheap_size = sum(cheap_asks.values())
                        
                    # Specifically depth at 99c
                    depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
                    has_orders_at_99 = depth_at_99 > 0 or (best_cheap_price is not None and best_cheap_price <= 0.99 and total_cheap_size > 0)

            # A bet is "impossible" if historical reversal count <= 1 and we have at least 2 similar days
            is_impossible = analysis.get("reversed_count", 99) <= 1 and analysis.get("total_similar_days", 0) >= 2

            # Let's count it as a "safe bet" if it is marked as safe in analysis
            is_safe_bet = analysis.get("is_safe_bet", False)

            # If the opportunity is safe/impossible, let's send a Telegram signal if not sent recently
            if False:  # Disabled inside manual scan to prevent Telegram spam when loading the webpage dashboard. Background scanner handles alerts.
                key = (symbol, direction)
                now_ts = time_mod.time()
                should_send = True
                
                # Deduplication guard: only send if not sent today to prevent duplicate alerts
                if key in _signals_sent_today:
                    should_send = False
                
                if should_send:
                    # Construct manual signal Telegram message
                    hours_left = symbol_minutes_to_close // 60
                    mins_left = symbol_minutes_to_close % 60
                    
                    poly_price_str = ""
                    order_str = ""
                    profit_str = ""
                    slug_link = ""
                    
                    if poly_info:
                        up_p = poly_info.get("up_price", 0.0)
                        down_p = poly_info.get("down_price", 0.0)
                        poly_slug = poly_info.get("slug", "")
                        slug_link = f"https://polymarket.com/event/{poly_slug}"
                        
                        poly_price_str = f"Up {up_p*100:.0f}¢ / Down {down_p*100:.0f}¢"
                        
                        if safe_price > 0:
                            profit_pct = ((1.0 - safe_price) / safe_price) * 100
                            profit_str = f"<b>{direction}</b> kontratı al ({safe_price*100:.0f}¢ ➔ $1.00 = %{profit_pct:.1f} tahmini kâr)"
                            
                        if best_cheap_price is not None:
                            order_str = f"{best_cheap_price*100:.0f}¢ fiyattan ${total_cheap_size:,.0f} alım yapılabilir"
                            if depth_at_99 > 0:
                                order_str += f" (99¢'da ${depth_at_99:,.0f} aktif satıcı)"
                        else:
                            order_str = "Satış emri bulunmuyor"
                            
                    hist_str = f"• Son 60 günde %{abs(diff_pct):.1f}+ seviyesinde: {analysis.get('reversed_count')}/{analysis.get('total_similar_days')} kez ters döndü ➔ %{analysis.get('reversal_rate'):.1f}\n"
                    if analysis.get("worst_case") != 0:
                        hist_str += f"• En kötü kapanış senaryosu: %{analysis.get('worst_case'):+.2f}"
                    else:
                        hist_str += "• En kötü kapanış senaryosu: Hiç ters dönmemiş ✅"
                        
                    msg = (
                        f"🎯 <b>ANLIK TARAMA - GÜVENLİ BAHİS SİNYALİ</b>\n\n"
                        f"📊 <b>{symbol} {direction}</b>\n"
                        f"<b>Anlık Fiyat:</b> ${current_price:.2f} ({diff_pct:+.2f}%)\n"
                        f"<b>Dünkü Kapanış:</b> ${ref_price:.2f}\n"
                        f"<b>Kapanışa Kalan Süre:</b> {hours_left} saat {mins_left} dakika\n\n"
                        f"📈 <b>Tarihsel Analiz (son 60 gün):</b>\n"
                        f"{hist_str}\n\n"
                    )
                    
                    if poly_price_str:
                        msg += (
                            f"💰 <b>Polymarket:</b> {poly_price_str}\n"
                            f"💵 <b>Tavsiye:</b> {profit_str}\n"
                            f"📦 <b>Emir Kitabı (CLOB):</b> {order_str}\n\n"
                        )
                        
                    if slug_link:
                        msg += f"🔗 <a href='{slug_link}'>Polymarket'te İşlem Yap ↗</a>\n"
                        
                    msg += (
                        f"🖥️ <a href='https://upordownwebsite.up.railway.app/'>Canlı Panel Takip ↗</a>\n\n"
                        f"<b>{analysis.get('confidence_stars')} {analysis.get('confidence_label')}</b>"
                    )
                    
                    # Generate the card image
                    photo_bytes = None
                    try:
                        card_data = build_card_data(
                            symbol=symbol,
                            direction=direction,
                            current_price=current_price,
                            ref_price=ref_price,
                            diff_pct=diff_pct,
                            minutes_to_close=symbol_minutes_to_close,
                            analysis=analysis,
                            poly_info=poly_info,
                            book=book
                        )
                        photo_bytes = image_generator.generate_card_image(card_data)
                    except Exception as e:
                        logger.error(f"Error generating card image in manual scan: {e}")

                    # Send manual signal with photo to Telegram
                    if photo_bytes:
                        await send_signal_with_photo(msg, photo_bytes)
                    else:
                        await send_signal(msg)

                    _signals_sent_today[key] = {
                        "diff_pct": diff_pct,
                        "time": now_ts,
                        "was_safe": True
                    }
                    logger.info(f"Manual scan signal sent to Telegram: {symbol} {direction}")

            return {
                "symbol": symbol,
                "direction": direction,
                "current_price": current_price,
                "ref_price": ref_price,
                "diff_pct": diff_pct,
                "minutes_to_close": symbol_minutes_to_close,
                "is_off_hours": is_off_hours,
                "off_hours_reason": off_hours_reason,
                "historical": {
                    "total_similar_days": analysis.get("total_similar_days", 0),
                    "reversed_count": analysis.get("reversed_count", 0),
                    "reversal_rate": analysis.get("reversal_rate", 100.0),
                    "worst_case": analysis.get("worst_case", 0.0),
                    "confidence_stars": analysis.get("confidence_stars", "❓"),
                    "confidence_label": analysis.get("confidence_label", "VERİ YOK"),
                },
                "poly": {
                    "slug": poly_slug,
                    "up_price": up_price,
                    "down_price": down_price,
                    "safe_outcome_price": safe_price,
                    "best_ask": best_cheap_price,
                    "depth_at_best": total_cheap_size if best_cheap_price else 0.0,
                    "depth_at_99": depth_at_99,
                    "has_orders_at_99": has_orders_at_99
                },
                "is_safe_bet": is_safe_bet,
                "is_impossible": is_impossible,
                "has_orders_at_99": has_orders_at_99
            }
        except Exception as e:
            logger.error(f"Error scanning single symbol {symbol} manually: {e}")
            return None

    # Run with limited parallelism - Pyth TV API is very strict, 2 concurrent symbols max
    sem = asyncio.Semaphore(2)
    
    async def sem_scan(sym):
        async with sem:
            return await scan_single_symbol(sym)
            
    tasks = [sem_scan(sym) for sym in SCAN_WATCHLIST]
    scanned_results = await asyncio.gather(*tasks)

    # Filter out None results
    filtered_results = [r for r in scanned_results if r is not None]
    
    # Sort: put "impossible" / "safe bets" with orders at 99c at the very top,
    # then other safe bets, then sorting by diff_pct descending
    def sort_key(item):
        is_safe = item["is_safe_bet"]
        is_imp = item["is_impossible"]
        has_99 = item["poly"]["has_orders_at_99"]
        abs_diff = abs(item["diff_pct"])
        
        score = 0
        if is_safe or is_imp:
            score += 1000
        if has_99:
            score += 500
        
        return (score, abs_diff)

    filtered_results.sort(key=sort_key, reverse=True)
    
    # Guaranteed bet scanner is handled automatically in the background.
    # Disabled inside manual scan to prevent Telegram spam when loading the webpage dashboard.
    # asyncio.create_task(scan_guaranteed_bets())
    
    return filtered_results


def parse_market_question(question: str) -> tuple[str, float, str]:
    """
    Parses question text to extract asset symbol, threshold, and market type.
    Returns: (symbol, threshold, type) or (None, None, None)
    """
    q = question.upper()
    
    # Identify Asset
    matched_symbol = None
    # 1. Direct watchlist symbols
    for sym in SCAN_WATCHLIST:
        if sym in q:
            matched_symbol = sym
            break
            
    # 2. Fuzzy mapping of common names
    if not matched_symbol:
        for name, sym in ASSET_FUZZY_MAP.items():
            if name in q:
                matched_symbol = sym
                break
                
    if not matched_symbol:
        return None, None, None
        
    # Identify Threshold
    threshold = None
    q_clean = q.replace(",", "")
    
    # Match decimal numbers immediately following trigger words (HIT, TOUCH, ABOVE, BELOW, EXCEED, LEVEL)
    # Allows optional parenthesis (e.g. "hit (HIGH) $150") and optional dollar sign
    m_type = None
    match = re.search(r"(HIT|TOUCH|ABOVE|BELOW|EXCEED|LEVEL)\s*(?:\([A-Z]+\)\s*)?\$?(\d+(?:\.\d+)?)\s*(K)?", q_clean)
    if match:
        try:
            val = float(match.group(2))
            k_suffix = match.group(3)
            if k_suffix == "K":
                val *= 1000
            threshold = val
        except ValueError:
            pass
    elif "UP OR DOWN" in q_clean:
        threshold = -1.0
        m_type = "UP_DOWN"
            
    if threshold is None:
        return None, None, None
        
    # Identify Market Type if not already set
    if not m_type:
        if "HIT" in q or "TOUCH" in q:
            m_type = "HIT"
        elif "CLOSES ABOVE" in q or "CLOSE ABOVE" in q or "EXCEED" in q or "ABOVE" in q:
            m_type = "CLOSES_ABOVE"
        elif "CLOSES BELOW" in q or "CLOSE BELOW" in q or "BELOW" in q:
            m_type = "CLOSES_BELOW"
            
    if not m_type:
        m_type = "HIT"
        
    return matched_symbol, threshold, m_type

async def fetch_active_binary_markets() -> list:
    """
    Fetch exact daily Up/Down binary markets using predictably generated slugs based on today's date.
    Bypasses Gamma API's unreliable public-search entirely.
    """
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    
    # Month name full (lowercase), Day (no zero padding), Year
    month_str = now_et.strftime("%B").lower()
    day_str = str(now_et.day)
    year_str = now_et.strftime("%Y")
    date_suffix = f"-on-{month_str}-{day_str}-{year_str}"
    
    slugs = []
    for symbol in SCAN_WATCHLIST:
        prefix = symbol.lower()
        if symbol == "XAU":
            prefix = "xauusd"
        elif symbol == "XAG":
            prefix = "xagusd"
        elif symbol == "DIA":
            prefix = "djia"
            
        slugs.append(f"{prefix}-up-or-down{date_suffix}")
        
        # Add SPX variants for SPY
        if symbol == "SPY":
            slugs.append(f"spx-up-or-down{date_suffix}")
            slugs.append(f"spx-opens-up-or-down{date_suffix}")
            
    all_markets = []
    seen_market_ids = set()
    
    sem = asyncio.Semaphore(10)
    
    async def fetch_slug(client, slug):
        url = f"{GAMMA_API}/events"
        params = {"slug": slug}
        try:
            async with sem:
                resp = await client.get(url, params=params, timeout=10.0)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.error(f"Error fetching slug {slug}: {e}")
        return []

    async with httpx.AsyncClient() as client:
        tasks = [fetch_slug(client, slug) for slug in slugs]
        results = await asyncio.gather(*tasks)
        
        for events in results:
            for ev in events:
                markets = ev.get("markets", [])
                for m in markets:
                    m_id = m.get("id")
                    if m_id and m_id not in seen_market_ids:
                        if m.get("active") and not m.get("closed"):
                            seen_market_ids.add(m_id)
                            m["event_slug"] = ev.get("slug", "")
                            all_markets.append(m)
                            
    return all_markets

def analyze_move_risk(symbol: str, required_move: float, direction: str, minutes_to_close: int) -> dict:
    """
    Calculate the historical probability of 'symbol' moving by 'required_move' or more
    in the remaining 'minutes_to_close' time in the last 60 days.
    """
    if symbol not in _historical_cache:
        return {
            "reversed_count": 99,
            "total_similar_days": 0,
            "reversal_rate": 100.0,
            "max_reversal_move": "Veri yok",
            "confidence_stars": "❓",
            "confidence_label": "VERİ YOK",
        }

    data = _historical_cache[symbol]
    total = 0
    reversed_count = 0
    max_reversal_move = 0.0

    for day in data:
        best_match = None
        for snap in day["snapshots"]:
            if abs(snap["minutes_to_close"] - minutes_to_close) <= 30:
                if best_match is None or abs(snap["minutes_to_close"] - minutes_to_close) < abs(best_match["minutes_to_close"] - minutes_to_close):
                    best_match = snap

        if best_match is None:
            continue

        total += 1
        change = day["final_close"] - best_match["price"]
        
        if direction == "UP":
            if change >= required_move:
                reversed_count += 1
            if change > max_reversal_move:
                max_reversal_move = change
        else:
            if change <= -required_move:
                reversed_count += 1
            if -change > max_reversal_move:
                max_reversal_move = -change

    reversal_rate = (reversed_count / total * 100) if total > 0 else 100.0

    # Confidence stars
    if total >= 5:
        if reversed_count == 0:
            stars, label = "⭐⭐⭐⭐⭐", "ÇOK GÜVENLİ"
        elif reversed_count == 1:
            stars, label = "⭐⭐⭐⭐", "GÜVENLİ"
        elif reversal_rate <= 5:
            stars, label = "⭐⭐⭐", "ORTA"
        else:
            stars, label = "⭐⭐", "RİSKLİ"
    else:
        stars, label = "❓", "YETERSİZ VERİ"

    is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
    if is_commodity:
        max_move_desc = f"+${max_reversal_move:.2f}" if direction == "UP" else f"-${max_reversal_move:.2f}"
    else:
        # Stock index or Equity
        max_move_desc = f"+{max_reversal_move:.2f}%" if direction == "UP" else f"-{max_reversal_move:.2f}%"

    return {
        "total_similar_days": total,
        "reversed_count": reversed_count,
        "reversal_rate": reversal_rate,
        "max_reversal_move": max_move_desc,
        "confidence_stars": stars,
        "confidence_label": label,
    }

async def scan_guaranteed_bets():
    """
    Scans all active binary markets on Polymarket (hit, closes-above, etc.).
    Uses historical risk analysis to detect highly certain/impossible outcomes.
    Sends alerts to Telegram signal channels.
    """
    logger.info("🏆 Starting Guaranteed Bet Scanner...")
    
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    
    # Fetch active binary markets
    markets = await fetch_active_binary_markets()
    logger.info(f"Discovered {len(markets)} active binary markets on Polymarket.")
    
    for market in markets:
        try:
            question = market.get("question", "")
            symbol, threshold, m_type = parse_market_question(question)
            if not symbol or threshold is None or not m_type:
                continue
                
            pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
            if not pyth_id:
                continue
                
            current_price = await pyth_client.get_active_price(symbol, pyth_id)
            if not current_price:
                continue
                
            # --- WTI Rollover Alpha Front-run Engine ---
            wti_alpha_active = False
            wti_alpha_details = ""
            original_price = current_price
            
            if symbol == "WTI":
                try:
                    alpha_info = await pyth_client.get_wti_rollover_alpha_info()
                    if alpha_info.get("has_alpha"):
                        end_date_str = market.get("endDate") or market.get("endDateIso")
                        if end_date_str:
                            end_date_str = end_date_str.replace("Z", "+00:00")
                            end_dt = datetime.fromisoformat(end_date_str).astimezone(et_tz)
                            
                            rollover_time = alpha_info.get("rollover_time")
                            if end_dt > rollover_time:
                                # Rollover occurs before market resolves! Resolves on NEXT contract!
                                wti_alpha_active = True
                                current_price = alpha_info.get("next_price")
                                spread = alpha_info.get("spread")
                                sign = "+" if spread >= 0 else ""
                                wti_alpha_details = (
                                    f"\n\n🔥 <b>ROLLOVER ALPHA MOTORU AKTİF!</b>\n"
                                    f"• Aktif Vade ({alpha_info.get('active_symbol')}): ${alpha_info.get('active_price'):.2f}\n"
                                    f"• Sonraki Vade ({alpha_info.get('next_symbol')}): ${alpha_info.get('next_price'):.2f}\n"
                                    f"• <b>Fiyat Farkı (Spread):</b> {sign}${spread:.2f}\n"
                                    f"• Kontrat Geçişine: {alpha_info.get('hours_left'):.1f} saat\n"
                                    f"• <i>Analiz bir sonraki vadedeki ${current_price:.2f} referans alınarak yapılmıştır. Oranlar fırlayacaktır!</i> 🚀"
                                )
                                logger.info(f"🚨 WTI ROLLOVER ALPHA ACTIVE: Front-running spread {spread:.2f} using next price {current_price} instead of {original_price}")
                except Exception as alpha_err:
                    logger.error(f"Error executing WTI Rollover Alpha: {alpha_err}")
                
            end_date_str = market.get("endDate") or market.get("endDateIso")
            if not end_date_str:
                continue
                
            # Parse ISO date string
            end_date_str = end_date_str.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(end_date_str).astimezone(et_tz)
            
            time_left = end_dt - now_et
            minutes_left = int(time_left.total_seconds() / 60)
            
            if minutes_left <= 0:
                continue
                
            # Skip if time remaining is too far in the future (> 31 days)
            if minutes_left > 44640:
                continue
                
            safe_outcome = None
            reversal_direction = None
            required_move = 0.0
            
            # Hit Bet analysis
            if m_type == "HIT":
                if threshold > current_price:
                    required_move = threshold - current_price
                    reversal_direction = "UP"
                    safe_outcome = "No"
                else:
                    required_move = current_price - threshold
                    reversal_direction = "DOWN"
                    safe_outcome = "No"
                    
            # Closes Above analysis
            elif m_type == "CLOSES_ABOVE":
                if current_price > threshold:
                    required_move = current_price - threshold
                    reversal_direction = "DOWN"
                    safe_outcome = "Yes"
                else:
                    required_move = threshold - current_price
                    reversal_direction = "UP"
                    safe_outcome = "No"
                    
            # Closes Below analysis
            elif m_type == "CLOSES_BELOW":
                if current_price < threshold:
                    required_move = threshold - current_price
                    reversal_direction = "UP"
                    safe_outcome = "Yes"
                else:
                    required_move = current_price - threshold
                    reversal_direction = "DOWN"
                    safe_outcome = "No"
                    
            if required_move < 0.05:
                continue
                
            pct_move = (required_move / current_price) * 100
            is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
            
            if is_commodity:
                move_desc = f"+${required_move:.2f} (+{pct_move:.2f}%)" if reversal_direction == "UP" else f"-${required_move:.2f} (-{pct_move:.2f}%)"
            else:
                move_desc = f"+{required_move:.2f}%" if reversal_direction == "UP" else f"-{required_move:.2f}%"
                
            # Historical move risk analysis
            analysis = analyze_move_risk(symbol, required_move, reversal_direction, minutes_left)
            
            is_impossible = analysis.get("reversed_count", 99) <= 1 and analysis.get("total_similar_days", 0) >= 2
            if not is_impossible:
                continue
                
            # Check Polymarket pricing + orderbook depth
            outcomes = json.loads(market.get("outcomes", "[]"))
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            
            safe_idx = None
            for idx, o in enumerate(outcomes):
                if o.lower() == safe_outcome.lower():
                    safe_idx = idx
                    break
                    
            if safe_idx is None or safe_idx >= len(outcome_prices):
                continue
                
            safe_price = float(outcome_prices[safe_idx])
            safe_token = token_ids[safe_idx] if len(token_ids) > safe_idx else None
            
            # Yield check (between 90c and 97c)
            if not (0.90 <= safe_price <= 0.97):
                continue
                
            order_str = ""
            best_ask = None
            total_size = 0.0
            
            if safe_token:
                book = await fetch_orderbook_depth(safe_token)
                cheap_asks = {p: s for p, s in book.get("asks", {}).items() if 0.90 <= p <= 0.99}
                if cheap_asks:
                    best_ask = min(cheap_asks.keys())
                    total_size = sum(cheap_asks.values())
                    order_str = f"{best_ask*100:.0f}¢ fiyattan ${total_size:,.0f} alım yapılabilir"
                    
                    depth_at_99 = book.get("asks", {}).get(0.99, 0.0)
                    if depth_at_99 > 0:
                        order_str += f" (99¢'da ${depth_at_99:,.0f} aktif satıcı)"
                else:
                    order_str = "Satış emri bulunmuyor"
                    
            profit_pct = ((1.0 - safe_price) / safe_price) * 100
            
            # Deduplication
            slug = market.get("slug", "")
            key = (slug, safe_outcome, "guaranteed_bet")
            if key in _signals_sent_today:
                continue
                
            now_ts = time_mod.time()
            
            # Time left formatting
            days_left = minutes_left // 1440
            hrs_left = (minutes_left % 1440) // 60
            mins_left = minutes_left % 60
            
            time_left_str = ""
            if days_left > 0:
                time_left_str += f"{days_left} gün "
            time_left_str += f"{hrs_left} saat {mins_left} dakika"
            
            # Format and send Telegram Alert!
            price_line = f"<b>{symbol} Anlık Fiyat:</b> ${current_price:.2f}\n"
            if wti_alpha_active:
                price_line = f"<b>WTI Eski Vade Fiyatı:</b> ${original_price:.2f}\n<b>WTI Yeni Vade Referans Fiyatı:</b> ${current_price:.2f}\n"
                
            msg = (
                f"🏆 <b>SİNYAL FABRİKASI - GARANTİ BAHİS SİNYALİ</b>\n\n"
                f"📊 <b>{symbol} {m_type.replace('_', ' ')} Bahsi</b>\n"
                f"<b>Pazar:</b> {question}\n"
                f"{price_line}"
                f"<b>Hedef Seviye:</b> ${threshold:.2f}\n"
                f"<b>Gerekli Risk Hareketi:</b> {move_desc}\n"
                f"<b>Kapanışa Kalan Süre:</b> {time_left_str}\n\n"
                f"📈 <b>Tarihsel Analiz (son 60 gün):</b>\n"
                f"• {symbol} son 60 günde bu zaman diliminde {move_desc} kadar ters yönde "
                f"<b>{analysis['reversed_count']}/{analysis['total_similar_days']} kez</b> hareket etti (İhtimal: %{analysis['reversal_rate']:.1f})\n"
                f"• En büyük ters hareket: {analysis['max_reversal_move']}\n\n"
                f"💰 <b>Polymarket Safe Outcome:</b> '{safe_outcome}' ({safe_price*100:.1f}¢ ➔ $1.00 = %{profit_pct:.1f} tahmini kâr)\n"
                f"📦 <b>Emir Kitabı (CLOB):</b> {order_str}\n\n"
                f"🔗 <a href='https://polymarket.com/event/{market.get('event_slug', slug)}'>Polymarket'te İşlem Yap ↗</a>\n"
                f"🖥️ <a href='https://upordownwebsite.up.railway.app/'>Canlı Panel Takip ↗</a>\n\n"
                f"<b>{analysis['confidence_stars']} {analysis['confidence_label']}</b>"
                f"{wti_alpha_details}"
            )
            
            await send_signal(msg)
            _signals_sent_today[key] = {
                "time": now_ts
            }
            logger.info(f"🏆 Guaranteed bet signal sent: {question} -> {safe_outcome} at {safe_price}")
            
        except Exception as e:
            logger.error(f"Error scanning market {market.get('slug')}: {e}")


