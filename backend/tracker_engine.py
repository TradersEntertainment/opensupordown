import asyncio
import logging
from datetime import datetime
import pytz
import database
import pyth_client
from telegram_bot import send_notification

logger = logging.getLogger(__name__)

SLEEP_INTERVAL = 5  # seconds when market is open
SLEEP_INTERVAL_CLOSED = 60  # seconds when market is closed (saves API calls)

# --- Pre-market Opens Tracker State ---
_last_premarket_msg_time = 0
_last_premarket_price = None

# --- Closing Summary State ---
_last_summary_time = 0


def is_market_completely_closed() -> bool:
    """
    True only when ALL markets are fully closed.
    - Weekdays before 09:00 ET (16:00 TR): pre-market not yet started
    - Weekdays after 17:30 ET (00:30 TR): commodity market closed
    - Saturday: all day
    - Sunday before 18:00 ET (01:00 TR): commodity market closed
    NOTE: 09:00 ET (16:00 TR) is intentionally included as active
    so Opens Up/Down bets are tracked before market opens at 09:30 ET.
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    weekday = now_et.weekday()  # 0=Mon, 6=Sun
    total_minutes = now_et.hour * 60 + now_et.minute

    if weekday == 5:  # Saturday: fully closed
        return True
    if weekday == 6:  # Sunday: closed until 18:00 ET
        return total_minutes < (18 * 60)
    # Weekdays: active 09:00 ET (pre-market) to 17:30 ET
    return total_minutes < (9 * 60) or total_minutes > (17 * 60 + 30)


async def _check_auto_expire(positions):
    """Closes positions if their market close time has passed."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)

    for p in positions:
        symbol = p['symbol'].upper()
        is_commodity = any(c in symbol for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
        close_hour = 17 if is_commodity else 16

        if now_et.hour >= close_hour:
            logger.info(f"Auto-closing position {p['id']} ({symbol})")

            current_price = await pyth_client.get_latest_price(p['pyth_id'])
            ref = p['ref_price']
            is_win = False
            if current_price:
                is_up_bet = 'UP' in p['direction']
                is_win = (is_up_bet and current_price > ref) or (not is_up_bet and current_price < ref)

            result_str = "KAZANDI 🟢" if is_win else "KAYBETTİ 🔴"
            current_price_str = f"${current_price:.4f}" if current_price else "Bilinmiyor"

            msg = (
                f"🏁 <b>MARKET KAPANDI: {symbol} {p['direction']}</b>\n\n"
                f"<b>Sonuç:</b> {result_str}\n"
                f"<b>Kapanış:</b> {current_price_str}\n"
                f"<b>Referans:</b> ${ref:.4f}"
            )
            await send_notification(msg)
            await database.close_position(p['id'])


async def _send_closing_summary(positions):
    """
    Send a summary of all active positions every 5 minutes 
    during the last 30 minutes before market close.
    Stocks: 15:30-16:00 ET (22:30-23:00 TR)
    Commodities: 16:30-17:00 ET (23:30-24:00 TR)
    """
    global _last_summary_time
    import time
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    
    # Check if we're in any closing window
    # Stocks close window: 15:30-16:00 ET (930-960 min)
    # Commodities close window: 16:30-17:00 ET (990-1020 min)
    in_stock_close = 930 <= total_minutes < 960
    in_commodity_close = 990 <= total_minutes < 1020
    
    if not (in_stock_close or in_commodity_close):
        return
    
    now_ts = time.time()
    # Send every 5 minutes (300 seconds)
    if now_ts - _last_summary_time < 300:
        return
    
    if not positions:
        return
    
    _last_summary_time = now_ts
    
    lines = [f"📊 <b>Kapanış Yaklaşıyor — Durum Özeti</b>\n"]
    minutes_to_close_stock = max(0, 960 - total_minutes)
    minutes_to_close_commodity = max(0, 1020 - total_minutes)
    
    for p in positions:
        current_price = await pyth_client.get_latest_price(p['pyth_id'])
        if not current_price:
            continue
        
        ref = p['ref_price']
        diff_pct = ((current_price - ref) / ref) * 100
        is_up_bet = 'UP' in p['direction']
        is_winning = (is_up_bet and current_price > ref) or (not is_up_bet and current_price < ref)
        icon = "🟢" if is_winning else "🔴"
        
        symbol = p['symbol']
        is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
        mins_left = minutes_to_close_commodity if is_commodity else minutes_to_close_stock
        
        lines.append(
            f"{icon} <b>{symbol} {p['direction']}</b>\n"
            f"   Anlık: ${current_price:.4f} | Fark: %{diff_pct:.2f} | Kapanışa: {mins_left}dk"
        )
    
    if len(lines) > 1:
        await send_notification("\n".join(lines))


async def _check_premarket_opens():
    """
    SPX Opens Up/Down Pre-Market Tracker.
    Active between 09:15-09:30 ET (16:15-16:30 TR) on weekdays.
    Sends notifications every 3 minutes OR on 0.1% change.
    Uses yesterday's SPY close as reference.
    """
    global _last_premarket_msg_time, _last_premarket_price
    import time
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    
    # Only active 09:15-09:30 ET on weekdays
    if now_et.weekday() >= 5:
        return
    if not (555 <= total_minutes < 570):  # 09:15 = 555, 09:30 = 570
        return
    
    # Resolve SPY
    pyth_id, full_symbol = pyth_client.get_pyth_id("SPY")
    if not pyth_id:
        return
    
    current_price = await pyth_client.get_latest_price(pyth_id)
    if not current_price:
        return
    
    # Get yesterday's close as reference
    from_ts, to_ts = pyth_client.get_previous_close_times("SPY")
    ref_price = await pyth_client.get_historical_candle_price(
        full_symbol, pyth_id, from_ts, to_ts, price_type='close'
    )
    if not ref_price:
        return
    
    diff_pct = ((current_price - ref_price) / ref_price) * 100
    
    now_ts = time.time()
    should_send = False
    
    # Send every 3 minutes (180 seconds)
    if now_ts - _last_premarket_msg_time >= 180:
        should_send = True
    
    # Send on 0.1% change from last notified price
    if _last_premarket_price is not None:
        price_change_pct = abs((current_price - _last_premarket_price) / _last_premarket_price) * 100
        if price_change_pct >= 0.1:
            should_send = True
    else:
        should_send = True  # First message
    
    if not should_send:
        return
    
    _last_premarket_msg_time = now_ts
    _last_premarket_price = current_price
    
    minutes_to_open = 570 - total_minutes  # 09:30 = 570
    direction_guess = "Yukarı Açılacak 📈" if diff_pct > 0 else "Aşağı Açılacak 📉"
    
    msg = (
        f"🔔 <b>SPX OPENS: {direction_guess}</b>\n\n"
        f"<b>Anlık SPY:</b> ${current_price:.4f}\n"
        f"<b>Fark:</b> %{diff_pct:+.3f}\n"
        f"<b>Ref (Dünkü Kapanış):</b> ${ref_price:.4f}\n"
        f"<b>Açılışa:</b> {minutes_to_open} dakika"
    )
    await send_notification(msg)


async def check_prices_loop():
    logger.info("Starting Up/Down Tracker Engine...")
    await send_notification(
        "🚀 <b>Poly Up/Down Tracker başlatıldı!</b>\n"
        "Komutlar: /up, /down, /open_up, /open_down, /status, /remove"
    )

    while True:
        try:
            # Always check pre-market opens (runs only in the right time window)
            await _check_premarket_opens()
            
            positions = await database.get_active_positions()

            # Cost optimization: sleep long ONLY when no active bets AND market is fully closed.
            # If user has any active position, always poll at full speed (5s) regardless of time.
            if not positions:
                sleep_time = SLEEP_INTERVAL_CLOSED if is_market_completely_closed() else SLEEP_INTERVAL
                await asyncio.sleep(sleep_time)
                continue

            await _check_auto_expire(positions)

            positions = await database.get_active_positions()
            if not positions:
                await asyncio.sleep(SLEEP_INTERVAL)
                continue

            # Check for closing summary
            await _send_closing_summary(positions)

            settings = await database.get_settings()
            warning_zone_pct = settings['warning_zone_pct']
            step_pct = settings['step_pct']

            for p in positions:
                current_price = await pyth_client.get_latest_price(p['pyth_id'])
                if not current_price:
                    continue

                ref = p['ref_price']
                abs_diff_pct = abs((current_price - ref) / ref) * 100

                if abs_diff_pct <= warning_zone_pct:
                    current_step_idx = int(abs_diff_pct / step_pct)
                    current_threshold = round(current_step_idx * step_pct, 4)

                    last_warning = p['last_warning_distance']

                    if current_threshold < last_warning:
                        await database.update_warning_distance(p['id'], current_threshold)

                        direction = p['direction']
                        is_up_bet = 'UP' in direction
                        is_losing = (is_up_bet and current_price <= ref) or (not is_up_bet and current_price >= ref)

                        status_word = "KAYBEDİYOR 🔴" if is_losing else "KAZANIYOR 🟢"
                        urgency = "🚨" if abs_diff_pct <= step_pct else "⚠️"

                        msg = (
                            f"{urgency} <b>TEHLİKE: {p['symbol']} {direction}</b>\n\n"
                            f"<b>Durum:</b> {status_word}\n"
                            f"<b>Anlık:</b> ${current_price:.4f}\n"
                            f"<b>Fark:</b> %{abs_diff_pct:.3f}\n"
                            f"<b>Referans:</b> ${ref:.4f}"
                        )
                        await send_notification(msg)
                else:
                    if p['last_warning_distance'] < warning_zone_pct:
                        await database.update_warning_distance(p['id'], 999.0)

        except Exception as e:
            logger.error(f"Error in tracker loop: {e}")

        await asyncio.sleep(SLEEP_INTERVAL)

def start_background_task():
    asyncio.create_task(check_prices_loop())
