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

def is_market_open() -> bool:
    """
    Returns True if at least one tracked market is likely open.
    Stocks: 09:30-16:00 ET (Mon-Fri)
    Commodities: 18:00 ET Sun - 17:00 ET Fri (essentially always open on weekdays)
    We use a simple check: if it's a weekday and between 09:00-17:30 ET, consider open.
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    if now_et.weekday() >= 5:  # Saturday or Sunday
        return False
    # 9:00 AM to 17:30 PM ET covers both stocks and commodities
    total_minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60) <= total_minutes <= (17 * 60 + 30)

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

            msg = (
                f"🏁 <b>MARKET KAPANDI: {symbol} {p['direction']}</b>\n\n"
                f"<b>Sonuç:</b> {result_str}\n"
                f"<b>Referans:</b> ${ref:.4f}\n"
                f"<b>Kapanış:</b> ${f'{current_price:.4f}' if current_price else 'Bilinmiyor'}\n"
            )
            await send_notification(msg)
            await database.close_position(p['id'])

async def check_prices_loop():
    logger.info("Starting Up/Down Tracker Engine...")
    await send_notification(
        "🚀 <b>Poly Up/Down Tracker başlatıldı!</b>\n"
        "Komutlar: /up, /down, /open_up, /open_down, /status, /remove"
    )

    while True:
        try:
            # If market is closed, sleep longer to save Railway compute & API quota
            if not is_market_open():
                await asyncio.sleep(SLEEP_INTERVAL_CLOSED)
                continue

            positions = await database.get_active_positions()
            if not positions:
                await asyncio.sleep(SLEEP_INTERVAL)
                continue

            await _check_auto_expire(positions)

            positions = await database.get_active_positions()
            if not positions:
                await asyncio.sleep(SLEEP_INTERVAL)
                continue

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
                            f"<b>Referans:</b> ${ref:.4f}\n"
                            f"<b>Anlık:</b> ${current_price:.4f}\n"
                            f"<b>Fark:</b> %{abs_diff_pct:.3f}"
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
