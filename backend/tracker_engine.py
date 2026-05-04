import asyncio
import logging
from datetime import datetime
import pytz
import database
import pyth_client
from telegram_bot import send_notification

logger = logging.getLogger(__name__)

SLEEP_INTERVAL = 5 # seconds

async def _check_auto_expire(positions):
    """Closes positions if their market close time has passed."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    for p in positions:
        symbol = p['symbol'].upper()
        is_commodity = any(c in symbol for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
        close_hour = 17 if is_commodity else 16
        
        # If the current hour is >= close_hour, the market has closed for the day
        if now_et.hour >= close_hour:
            logger.info(f"Auto-closing position {p['id']} ({symbol}) as market closed at {close_hour}:00 ET")
            
            # Final price check to report outcome
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
                f"<b>Kapanış:</b> ${current_price:.4f if current_price else 'Bilinmiyor'}\n"
            )
            await send_notification(msg)
            await database.close_position(p['id'])

async def check_prices_loop():
    logger.info("Starting Up/Down Tracker Engine...")
    
    # Send startup message
    await send_notification("🚀 <b>Poly Up/Down Tracker başlatıldı!</b>\nTelegram'dan /up, /down, /open_up komutlarıyla pozisyon alabilirsiniz.")
    
    while True:
        try:
            positions = await database.get_active_positions()
            if not positions:
                await asyncio.sleep(SLEEP_INTERVAL)
                continue
                
            # 1. Check for auto-expirations (market close)
            await _check_auto_expire(positions)
            
            # Refresh positions list after expiration
            positions = await database.get_active_positions()
            if not positions:
                await asyncio.sleep(SLEEP_INTERVAL)
                continue
                
            # Get Settings (warning zone and step size)
            settings = await database.get_settings()
            warning_zone_pct = settings['warning_zone_pct'] # e.g. 1.0%
            step_pct = settings['step_pct'] # e.g. 0.1%
            
            for p in positions:
                current_price = await pyth_client.get_latest_price(p['pyth_id'])
                if not current_price:
                    continue
                    
                ref = p['ref_price']
                
                # Calculate distance in percentage
                abs_diff_pct = abs((current_price - ref) / ref) * 100
                
                # Is it in the warning zone?
                if abs_diff_pct <= warning_zone_pct:
                    current_step_idx = int(abs_diff_pct / step_pct) 
                    current_threshold = current_step_idx * step_pct
                    
                    last_warning = p['last_warning_distance']
                    
                    if current_threshold < last_warning:
                        await database.update_warning_distance(p['id'], current_threshold)
                        
                        direction = p['direction']
                        is_up_bet = 'UP' in direction
                        is_losing = (is_up_bet and current_price <= ref) or (not is_up_bet and current_price >= ref)
                        
                        status_word = "KAYBEDİYOR 🔴" if is_losing else "KAZANIYOR 🟢"
                        urgency = "⚠️" if abs_diff_pct > warning_zone_pct / 2 else "🚨"
                        
                        msg = (
                            f"{urgency} <b>TEHLİKE UYARISI: {p['symbol']} {direction}</b> {urgency}\n\n"
                            f"Fiyat referans çizgisine çok yaklaştı!\n"
                            f"<b>Mevcut Durum:</b> {status_word}\n"
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
