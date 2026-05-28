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
import logging
import os
import time as time_mod
from datetime import datetime, timedelta

import pytz
from telegram import Bot

import pyth_client

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────

SIGNAL_CHAT_ID = "-5130715061"  # Dedicated signal channel
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Scan window: 15:00-16:00 ET (22:00-23:00 TR)
SCAN_START_MINUTES = 900   # 15:00 ET
SCAN_END_MINUTES = 960     # 16:00 ET
SCAN_INTERVAL = 60         # Every 60 seconds

# Stocks to scan
SCAN_WATCHLIST = [
    "SPY", "PLTR", "TSLA", "NVDA", "AAPL", "AMZN", "META", "GOOGL",
    "MSFT", "NFLX", "COIN", "HOOD", "ABNB", "RKLB", "EWY", "OPEN",
]

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
}

# ─── State ──────────────────────────────────────────────────────────────────

_historical_cache = {}       # symbol -> [day_data, ...]
_cache_loaded = False
_cache_load_date = None      # Refresh cache once per day

_signals_sent_today = {}     # (symbol, direction) -> {diff_pct, time, was_safe}
_last_scan_time = 0

_poly_cache = {}             # symbol -> {up_price, down_price, up_token_id, ...}
_last_poly_time = 0

# Signal bot (same token, different chat_id)
_signal_bot = None
if TELEGRAM_TOKEN:
    _signal_bot = Bot(token=TELEGRAM_TOKEN)


# ─── Signal Sender ──────────────────────────────────────────────────────────

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
            full_symbol = pyth_client.SYMBOL_MAP.get(symbol.upper())
            if not full_symbol:
                logger.warning(f"No Pyth mapping for {symbol}, skipping")
                continue

            url = f"{pyth_client.BENCHMARKS_URL}/shims/tradingview/history"
            params = {
                "symbol": full_symbol,
                "resolution": "60",  # Hourly candles
                "from": from_ts,
                "to": to_ts,
            }

            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, timeout=15.0)
                resp.raise_for_status()
                data = resp.json()

            if data.get("s") != "ok" or "t" not in data:
                logger.warning(f"No history for {symbol}: {data.get('s')}")
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
                    mtc = max(0, 960 - (candle["hour"] * 60 + candle["minute"]))
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

            await asyncio.sleep(0.15)  # Rate-limit friendly

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
    Fetch active Up/Down events from Polymarket Gamma API by dynamically generating slugs.
    Returns: {symbol: {up_price, down_price, up_token_id, down_token_id, slug}}
    """
    global _poly_cache, _last_poly_time

    now = time_mod.time()
    if now - _last_poly_time < 30 and _poly_cache:
        return _poly_cache

    try:
        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)
        
        # Generate the standard monthly format (e.g. "may-28-2026")
        month_name = now_et.strftime("%B").lower()
        day = now_et.day
        year = now_et.year
        
        results = {}

        async def fetch_single_symbol(symbol: str):
            ticker = symbol.lower()
            slug = f"{ticker}-up-or-down-on-{month_name}-{day}-{year}"
            
            slugs_to_try = [slug]
            if symbol == "SPY":
                slugs_to_try.append(f"spx-up-or-down-on-{month_name}-{day}-{year}")
                slugs_to_try.append(f"sp-500-up-or-down-on-{month_name}-{day}-{year}")
            elif symbol == "OPEN":
                slugs_to_try.append(f"opendoor-up-or-down-on-{month_name}-{day}-{year}")

            async with httpx.AsyncClient() as client:
                for s in slugs_to_try:
                    url = f"{GAMMA_API}/events/slug/{s}"
                    try:
                        resp = await client.get(url, timeout=5.0)
                        if resp.status_code != 200:
                            continue
                            
                        event = resp.json()
                        markets = event.get("markets", [])
                        if not markets:
                            continue

                        market = markets[0]
                        outcomes = json.loads(market.get("outcomes", "[]"))
                        prices = json.loads(market.get("outcomePrices", "[]"))
                        token_ids = json.loads(market.get("clobTokenIds", "[]"))

                        if len(outcomes) < 2 or len(prices) < 2:
                            continue

                        up_idx = down_idx = None
                        for i, o in enumerate(outcomes):
                            if o.lower() == "up":
                                up_idx = i
                            elif o.lower() == "down":
                                down_idx = i

                        if up_idx is None or down_idx is None:
                            continue

                        results[symbol] = {
                            "up_price": float(prices[up_idx]),
                            "down_price": float(prices[down_idx]),
                            "up_token_id": token_ids[up_idx] if len(token_ids) > up_idx else None,
                            "down_token_id": token_ids[down_idx] if len(token_ids) > down_idx else None,
                            "slug": s,
                        }
                        break # Found successfully, stop trying other slugs for this symbol
                    except Exception as e:
                        logger.debug(f"Error fetching slug {s}: {e}")

        # Fetch all symbols in parallel
        tasks = [fetch_single_symbol(symbol) for symbol in SCAN_WATCHLIST]
        await asyncio.gather(*tasks)

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
    minutes_to_close = max(0, SCAN_END_MINUTES - total_minutes)

    # Fetch Polymarket data (cached 30s)
    poly_data = await fetch_polymarket_events()

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

            # ── Historical analysis (dynamic threshold) ──
            analysis = analyze_reversal_risk(symbol, diff_pct, minutes_to_close)

            if not analysis.get("is_safe_bet"):
                # Warn if a previous signal degraded
                key = (symbol, direction)
                if key in _signals_sent_today and _signals_sent_today[key].get("was_safe"):
                    warn = (
                        f"🚨 <b>SİNYAL TEHLİKEDE: {symbol}</b>\n\n"
                        f"<b>Önceki:</b> %{_signals_sent_today[key]['diff_pct']:+.2f}\n"
                        f"<b>Şimdi:</b> %{diff_pct:+.2f}\n"
                        f"<b>Anlık:</b> ${current_price:.2f}\n"
                        f"<b>Kapanışa:</b> {minutes_to_close}dk\n"
                        f"⚠️ Güven düşürüldü — dikkat!"
                    )
                    await send_signal(warn)
                    _signals_sent_today[key]["was_safe"] = False
                continue

            # ── Polymarket info ──
            poly_price_str = ""
            order_str = ""
            profit_str = ""

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

            # ── Signal decision ──
            key = (symbol, direction)
            now_ts = time_mod.time()

            if key in _signals_sent_today:
                prev = _signals_sent_today[key]
                # Send update only if diff changed ≥0.3% or 10+ min passed
                if (
                    abs(abs_diff - abs(prev["diff_pct"])) < 0.3
                    and now_ts - prev["time"] < 600
                ):
                    continue

                # ── UPDATE signal ──
                update_msg = (
                    f"🔄 <b>SİNYAL GÜNCELLEMESİ: {symbol} {direction}</b>\n\n"
                    f"<b>Önceki:</b> %{prev['diff_pct']:+.2f} → "
                    f"<b>Şimdi:</b> %{diff_pct:+.2f}\n"
                    f"<b>Anlık:</b> ${current_price:.2f}\n"
                    f"<b>Kapanışa:</b> {minutes_to_close}dk"
                    f"{poly_price_str}{order_str}\n"
                    f"<b>Güven:</b> {analysis['confidence_stars']} hâlâ geçerli"
                )
                await send_signal(update_msg)
                _signals_sent_today[key] = {
                    "diff_pct": diff_pct,
                    "time": now_ts,
                    "was_safe": True,
                }
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
            await send_signal(msg)

            _signals_sent_today[key] = {
                "diff_pct": diff_pct,
                "time": now_ts,
                "was_safe": True,
            }
            logger.info(f"Signal sent: {symbol} {direction} ({diff_pct:+.2f}%)")

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")


# ─── Background Loop ───────────────────────────────────────────────────────

async def signal_scanner_loop():
    """Background loop for the signal scanner."""
    global _signals_sent_today, _last_scan_time, _cache_load_date

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
            if now_ts - _last_scan_time < SCAN_INTERVAL:
                await asyncio.sleep(5)
                continue

            _last_scan_time = now_ts
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


_last_hourly_scan_hour = -1


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

            # Fetch Polymarket pricing + orderbook depth
            poly_price_str = ""
            order_str = ""
            profit_str = ""
            slug_link = ""

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
                f"🖥️ <a href='https://opensupordown.railway.app'>Canlı Panel Takip ↗</a>\n\n"
                f"<b>{analysis['confidence_stars']} {analysis['confidence_label']}</b>"
            )

            # Send signal to dedicated Telegram signal channel
            await send_signal(msg)
            logger.info(f"Hourly signal sent to Telegram: {symbol} {direction}")

        except Exception as e:
            logger.error(f"Error scanning {symbol} during hourly scan: {e}")



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

            # Reversal analysis
            analysis = analyze_reversal_risk(symbol, diff_pct, minutes_to_close_val)

            # Polymarket details
            up_price = 0.0
            down_price = 0.0
            safe_price = 0.0
            poly_slug = ""
            best_cheap_price = None
            total_cheap_size = 0.0
            depth_at_99 = 0.0
            has_orders_at_99 = False

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

            return {
                "symbol": symbol,
                "direction": direction,
                "current_price": current_price,
                "ref_price": ref_price,
                "diff_pct": diff_pct,
                "minutes_to_close": minutes_to_close_val,
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

    # Run in parallel using asyncio.gather
    tasks = [scan_single_symbol(symbol) for symbol in SCAN_WATCHLIST]
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
    return filtered_results

