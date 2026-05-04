import os
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import logging
import database
import pyth_client
from datetime import datetime

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot_instance = None
if TELEGRAM_TOKEN:
    bot_instance = Bot(token=TELEGRAM_TOKEN)

async def send_notification(message: str):
    """Send a message to the configured chat."""
    if not bot_instance or not CHAT_ID:
        logger.warning(f"Telegram not configured. Missed message: {message}")
        return
    try:
        await bot_instance.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send telegram notification: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 <b>Poly Up/Down Tracker'a Hoş Geldiniz!</b>\n\n"
        "Komutlar:\n"
        "<b>Günlük Bahisler (Dünkü Kapanışa Göre):</b>\n"
        "<code>/up SEMBOL</code> - örn: /up PLTR\n"
        "<code>/down SEMBOL</code> - örn: /down WTI\n\n"
        "<b>Açılış Bahisleri (Bugünkü Açılışa Göre):</b>\n"
        "<code>/open_up SEMBOL</code> - Opens Up pozisyonu\n"
        "<code>/open_down SEMBOL</code> - Opens Down pozisyonu\n\n"
        "<b>Yönetim:</b>\n"
        "<code>/status</code> - Tüm aktif pozisyonları listele\n"
        "<code>/remove</code> - Pozisyon sil (liste gelir, numarasını yaz)"
    )
    await update.message.reply_html(msg)

async def handle_position(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str, bet_type: str = 'close'):
    if not context.args:
        prefix = "/open_" if bet_type == 'open' else "/"
        await update.message.reply_text(f"Kullanım: {prefix}{direction.lower()} SEMBOL (örn: {prefix}{direction.lower()} PLTR)")
        return
        
    symbol_input = context.args[0].upper()
    
    await update.message.reply_text(f"⏳ {symbol_input} için veriler Pyth'den çekiliyor...")
    
    # Resolve Pyth ID
    pyth_id, full_symbol = pyth_client.get_pyth_id(symbol_input)
    if not pyth_id:
        await update.message.reply_text(f"❌ '{symbol_input}' Pyth sisteminde bulunamadı. Lütfen tam sembolü kontrol edin.")
        return
        
    # Get reference price (yesterday's close OR today's open)
    if bet_type == 'close':
        from_ts, to_ts = pyth_client.get_previous_close_times(symbol_input)
        ref_price = await pyth_client.get_historical_candle_price(full_symbol, pyth_id, from_ts, to_ts, price_type='close')
        time_desc = "Dünkü 15:59 ET Kapanış"
    else:
        from_ts, to_ts = pyth_client.get_previous_open_times(symbol_input)
        ref_price = await pyth_client.get_historical_candle_price(full_symbol, pyth_id, from_ts, to_ts, price_type='open')
        time_desc = "Bugünkü 09:30 ET Açılış"
        
    if ref_price is None:
        await update.message.reply_text(f"❌ {full_symbol} için {time_desc} mumu Pyth'den çekilemedi! Daha sonra tekrar deneyin.")
        return
        
    # Get current price
    current_price = await pyth_client.get_latest_price(pyth_id)
    
    # Save to database
    now_str = datetime.now().isoformat()
    db_direction = f"OPEN_{direction}" if bet_type == 'open' else direction
    
    await database.add_position(
        symbol=symbol_input,
        pyth_id=pyth_id,
        direction=db_direction,
        ref_price=ref_price,
        ref_timestamp=to_ts,
        created_at=now_str
    )
    
    diff_pct = 0.0
    if current_price:
        diff_pct = ((current_price - ref_price) / ref_price) * 100
        
    status_icon = "✅" if (direction == 'UP' and current_price > ref_price) or (direction == 'DOWN' and current_price < ref_price) else "⚠️"
    
    current_price_str = f"${current_price:.4f}" if current_price else "Bilinmiyor"
    msg = (
        f"🎯 <b>Pozisyon Eklendi: {symbol_input} {db_direction}</b>\n\n"
        f"<b>Anlık Fiyat:</b> {current_price_str}\n"
        f"<b>Fark:</b> %{diff_pct:.2f} {status_icon}\n"
        f"<b>Referans ({time_desc}):</b> ${ref_price:.4f}\n\n"
        f"<i>Kapanışa yaklaştığında ayarlanan yüzdelere göre uyarılacaksınız.</i>"
    )
    await update.message.reply_html(msg)

async def up_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_position(update, context, "UP", bet_type='close')

async def down_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_position(update, context, "DOWN", bet_type='close')
    
async def open_up_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_position(update, context, "UP", bet_type='open')

async def open_down_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_position(update, context, "DOWN", bet_type='open')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = await database.get_active_positions()
    if not positions:
        await update.message.reply_text("Şu an aktif takip edilen pozisyonunuz yok.")
        return
        
    lines = ["📊 <b>Aktif Pozisyonlar</b>\n"]
    for p in positions:
        current_price = await pyth_client.get_latest_price(p['pyth_id'])
        if not current_price:
            lines.append(f"• {p['symbol']} {p['direction']} (Anlık fiyat alınamadı)")
            continue
            
        ref = p['ref_price']
        diff_pct = ((current_price - ref) / ref) * 100
        
        # db stores OPEN_UP, OPEN_DOWN, UP, DOWN
        is_up_bet = 'UP' in p['direction']
        is_winning = (is_up_bet and current_price > ref) or (not is_up_bet and current_price < ref)
        icon = "🟢" if is_winning else "🔴"
        
        lines.append(f"{icon} <b>{p['symbol']} {p['direction']}</b> | Ref: ${ref:.2f} | Anlık: ${current_price:.2f} (Fark: %{diff_pct:.2f})")
        
    await update.message.reply_html("\n".join(lines))

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = await database.get_active_positions()
    if not positions:
        await update.message.reply_text("Silinecek aktif pozisyon yok.")
        return

    if context.args:
        try:
            pos_id = int(context.args[0])
            await database.delete_position(pos_id)
            await update.message.reply_text(f"✅ #{pos_id} numaralı pozisyon silindi.")
            return
        except (ValueError, Exception) as e:
            await update.message.reply_text(f"Hata: {e}")
            return

    # List positions to choose from
    lines = ["🗑️ <b>Hangi pozisyonu silmek istiyorsunuz?</b>\n"]
    lines.append("<code>/remove [ID]</code> yazarak silebilirsiniz:\n")
    for p in positions:
        lines.append(f"• <code>/remove {p['id']}</code> → {p['symbol']} {p['direction']} (Ref: ${p['ref_price']:.4f})")
    await update.message.reply_html("\n".join(lines))

def setup_application() -> Application:
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN not set. Polling won't start.")
        return None

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("up", up_command))
    app.add_handler(CommandHandler("down", down_command))
    app.add_handler(CommandHandler("open_up", open_up_command))
    app.add_handler(CommandHandler("open_down", open_down_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("remove", remove_command))

    return app
