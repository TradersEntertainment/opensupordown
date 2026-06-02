import os
import json
import logging
import re
from datetime import datetime
import pytz
import httpx
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import database
import pyth_client
import signal_scanner

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

bot_instance = None
if TELEGRAM_TOKEN:
    bot_instance = Bot(token=TELEGRAM_TOKEN)

def clean_telegram_html(text: str) -> str:
    """
    Sanitizes LLM outputs to comply with Telegram's strict HTML parser.
    Converts markdown bold/italic/code tags to HTML tags, and converts markdown links to HTML or raw bold.
    """
    if not text:
        return text

    # 1. Convert markdown bold **text** or __text__ to <b>text</b>
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.*?)__", r"<b>\1</b>", text)

    # 2. Convert markdown italic *text* or _text_ to <i>text</i>
    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_(.*?)_", r"<i>\1</i>", text)

    # 3. Convert markdown code `text` to <code>text</code>
    text = re.sub(r"`(.*?)`", r"<code>\1</code>", text)

    # 4. Resolve markdown links like [label](url)
    def replace_markdown_link(match):
        label = match.group(1)
        url = match.group(2)
        if url.startswith("http"):
            return f'<a href="{url}">{label}</a>'
        elif url.startswith("tg://"):
            return f'<b>{label}</b>'  # Keep bold instead of raw tg links
        else:
            return label

    text = re.sub(r"\[(.*?)\]\((.*?)\)", replace_markdown_link, text)

    return text

async def send_notification(message: str):
    """Send a message to the configured chat."""
    if not bot_instance or not CHAT_ID:
        logger.warning(f"Telegram not configured. Missed message: {message}")
        return
    try:
        clean_message = clean_telegram_html(message)
        await bot_instance.send_message(chat_id=CHAT_ID, text=clean_message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send telegram notification: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 <b>Sinyal Fabrikası Yapay Zeka Asistanı Aktif!</b>\n\n"
        "Bana gruptan normal sohbet eder gibi soru sorabilirsiniz! Beni etiketlemeniz (mention) veya doğrudan yazmanız yeterlidir.\n\n"
        "<b>Örnek Sorular:</b>\n"
        "• <i>@bot_kullanici_adi bana PLTR 25.50 hedefini analiz et kapanışa kadar ne olur?</i>\n"
        "• <i>WTI 80 hits bahsinde ne kadar risk var?</i>\n"
        "• <i>Altın (XAU) 2400 close imkansız mı?</i>\n\n"
        "<b>Yönetim Komutları:</b>\n"
        "<code>/status</code> - Aktif pozisyonları listele\n"
        "<code>/remove</code> - Pozisyon sil"
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
        
    # Get reference price (always use previous trading day's close as baseline)
    from_ts, to_ts = pyth_client.get_previous_close_times(symbol_input)
    ref_price = await pyth_client.get_historical_candle_price(full_symbol, pyth_id, from_ts, to_ts, price_type='close')
    time_desc = "Dünkü 15:59 ET Kapanış"
        
    if ref_price is None:
        await update.message.reply_text(f"❌ {full_symbol} için {time_desc} mumu Pyth'den çekilemedi! Daha sonra tekrar deneyin.")
        return
        
    # Get current price
    current_price = await pyth_client.get_active_price(symbol_input, pyth_id)
    
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
        
        is_up_bet = 'UP' in p['direction'] or 'YES' in p['direction']
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


# ─── AI Chat & Market Analysis Integration (Groq API) ──────────────────────

async def call_groq_api(messages: list, model: str = "llama-3.3-70b-versatile") -> str:
    """Helper: Calls Groq API using HTTPX to avoid heavy dependencies."""
    if not GROQ_API_KEY:
        return "Groq API Anahtarı bulunamadı. Lütfen Railway ortam değişkenlerine GROQ_API_KEY ekleyin."
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.3
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=20.0)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"Groq API Error: {resp.status_code} - {resp.text}")
                return "Yapay zeka servisi şu an meşgul. Lütfen daha sonra tekrar deneyin."
    except Exception as e:
        logger.error(f"Failed to call Groq API: {e}")
        return "Bağlantı hatası oluştu."

async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main AI handler. Triggered on any text messages in groups (if mentioned) 
    or private chat. Utilizes Groq to determine user intent, and if analysis 
    is requested, runs real-time Pyth/CME calculations before returning response.
    """
    msg_text = update.message.text
    chat_type = update.message.chat.type
    
    # In groups/supergroups, only reply if mentioned (bot user tag is present)
    bot_user = context.bot.username
    is_mentioned = bot_user and f"@{bot_user}" in msg_text
    
    if chat_type in ["group", "supergroup"] and not is_mentioned:
        return
        
    # Remove bot mention from text to clean it
    clean_text = msg_text
    if bot_user:
        clean_text = re.sub(rf"@{bot_user}\s*", "", clean_text, flags=re.IGNORECASE).strip()
        
    # 1. Step 1: Groq parsing intent to extract symbol, threshold and action
    intent_system_prompt = (
        "You are an expert financial analysis router. Your job is to analyze the user's natural language input "
        "and determine if they are asking for a market price/risk analysis of a stock/commodity/crypto in our watchlist.\n\n"
        f"Watchlist: {', '.join(signal_scanner.SCAN_WATCHLIST)}\n\n"
        "SYNONYM MAPPINGS (Map these common terms to their respective watchlist symbols):\n"
        "- 'PETROL', 'CRUDE OIL', 'OIL', 'WTI' -> 'WTI'\n"
        "- 'ALTIN', 'GOLD', 'XAU' -> 'XAU'\n"
        "- 'GÜMÜŞ', 'SILVER', 'XAG' -> 'XAG'\n"
        "- 'DOĞAL GAZ', 'DOĞALGAZ', 'GAS', 'NG' -> 'NG'\n"
        "- 'PALANTIR', 'PLTR' -> 'PLTR'\n"
        "- 'TESLA', 'TSLA' -> 'TSLA'\n"
        "- 'NVIDIA', 'NVDA' -> 'NVDA'\n"
        "- 'APPLE', 'AAPL' -> 'AAPL'\n"
        "- 'AMAZON', 'AMZN' -> 'AMZN'\n"
        "- 'GOOGLE', 'ALPHABET', 'GOOGL' -> 'GOOGL'\n"
        "- 'MICROSOFT', 'MSFT' -> 'MSFT'\n"
        "- 'NETFLIX', 'NFLX' -> 'NFLX'\n"
        "- 'COINBASE', 'COIN' -> 'COIN'\n"
        "- 'ROBINHOOD', 'HOOD' -> 'HOOD'\n"
        "- 'AIRBNB', 'ABNB' -> 'ABNB'\n"
        "- 'ROCKET LAB', 'RKLB' -> 'RKLB'\n"
        "- 'MICRON', 'MU' -> 'MU'\n"
        "- 'S&P 500', 'SPY', 'SPX' -> 'SPY'\n"
        "- 'DOW JONES', 'DIA', 'DOW' -> 'DIA'\n"
        "- 'RUSSELL', 'RUT' -> 'RUT'\n"
        "- 'NASDAQ', 'QQQ' -> 'SPY'\n\n"
        "You must respond ONLY with a raw JSON object containing these keys:\n"
        "- 'action': 'analyze' if they ask to track, analyze, or check risk of a symbol from watchlist (including synonyms). Otherwise 'chat'.\n"
        "- 'symbol': The matched uppercase symbol from watchlist (e.g. 'PLTR', 'WTI', 'XAU'). Null if not found.\n"
        "- 'threshold': The target price target/barrier they mentioned (e.g. 25.5, 80.0, 95.0). Null if not found.\n"
        "- 'direction': 'UP' if target price is above current market, 'DOWN' if below. Or null.\n"
        "- 'reply': A brief chat fallback reply in Turkish if action is 'chat'. Null if action is 'analyze'.\n\n"
        "Strictly return ONLY the JSON block. No explanation, no backticks."
    )
    
    intent_messages = [
        {"role": "system", "content": intent_system_prompt},
        {"role": "user", "content": clean_text}
    ]
    
    await update.message.chat.send_action("typing")
    intent_response = await call_groq_api(intent_messages, model="llama-3.1-8b-instant")
    
    # Strip any potential backticks or markdown wrapper
    intent_response = intent_response.strip().replace("```json", "").replace("```", "").strip()
    
    try:
        intent = json.loads(intent_response)
    except Exception as e:
        logger.error(f"Failed to parse Groq intent JSON: {intent_response}. Error: {e}")
        intent = {"action": "chat", "reply": "Sorunuzu tam olarak anlayamadım, lütfen tekrar dener misiniz?"}
        
    # 2. Step 2: Handle intent actions
    if intent.get("action") == "analyze" and intent.get("symbol"):
        symbol = intent.get("symbol")
        threshold = intent.get("threshold")
        
        # Run real-time Pyth + Risk calculations
        pyth_id, full_symbol = pyth_client.get_pyth_id(symbol)
        if not pyth_id:
            await update.message.reply_text(f"❌ '{symbol}' Pyth sisteminde aktif feed olarak bulunamadı.")
            return
            
        current_price = await pyth_client.get_active_price(symbol, pyth_id)
        if not current_price:
            await update.message.reply_text(f"❌ {symbol} için Pyth canlı fiyatı şu an alınamıyor.")
            return
            
        # Determine remaining minutes to market close (standard stock/commodity schedule)
        et_tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(et_tz)
        total_minutes = now_et.hour * 60 + now_et.minute
        
        is_commodity = any(c in symbol for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
        close_mins = 1020 if is_commodity else 960
        minutes_left = max(0, close_mins - total_minutes)
        
        # Handle off-hours simulation if closed
        is_off_hours = False
        if now_et.weekday() >= 5 or total_minutes >= close_mins or total_minutes < 570:
            minutes_left = 60 # simulate 1h for testing
            is_off_hours = True
            
        # Reconstruct analysis variables
        if threshold is None:
            # Fallback target if not specified (e.g. current +/- 1.5%)
            threshold = current_price * (1.015 if now_et.second % 2 == 0 else 0.985)
            
        required_move = abs(current_price - threshold)
        direction = "UP" if threshold > current_price else "DOWN"
        
        # Calculate historical move risk
        analysis = signal_scanner.analyze_move_risk(symbol, required_move, direction, minutes_left)
        
        # Fetch 7/24 basis calibration if WTI/Gold
        basis_spread_desc = ""
        if symbol == "WTI":
            basis_spread_desc = f" (Basis spread: ${pyth_client._wti_binance_basis:+.4f})"
        elif symbol in ["XAU", "GOLD"]:
            basis_spread_desc = f" (Basis spread: ${pyth_client._xau_binance_basis:+.2f})"
            
        # 3. Step 3: Second call to Groq to generate a beautiful natural response loaded with real stats
        stats_prompt = (
            "You are the expert quantitative trader and the voice of 'Sinyal Fabrikası' (Signal Factory).\n"
            "Generate an engaging, professional, and slightly witty response in Turkish to the user's request. "
            "Address them directly. You must explain the risk calculation using the live data provided below.\n\n"
            "OUR TRADING PHILOSOPHY: 'Impatience Premium Harvesting' (Sabırsızlık Primi Hasadı) / 'Safe Betting'\n"
            "- We buy extremely high-probability outcomes priced at 90¢ to 97¢ (which yields a 3% to 11% expected return/yield in hours or days).\n"
            "- While the market prices the contract at 95¢ (implying a 5% chance of failure), our 60-day quantitative analysis proves that the real statistical risk of reversal is virtually zero (less than 1-in-1000 or <0.1%).\n"
            "- This is an elite arbitrage of harvesting the premium left behind by impatient/irrational retail traders. Ordinary assets take years to yield 5%, we do it in a single day with statistical certainty!\n\n"
            "Live Data Details:\n"
            f"- Varlık (Symbol): {symbol}\n"
            f"- Canlı Pyth Fiyatı: ${current_price:.4f}{basis_spread_desc}\n"
            f"- Kullanıcının Hedef Seviyesi (Threshold): ${threshold:.4f}\n"
            f"- Kapanışa Kalan Süre: {minutes_left} dakika (Simüle edilmiş: {is_off_hours})\n"
            f"- Gerekli Yön: {direction} (Fiyatın buraya gitmesi gerekiyor)\n"
            f"- Tarihsel Analiz Son 60 Gün: {analysis['reversed_count']}/{analysis['total_similar_days']} kez ters yönde hareket gerçekleşti (%{analysis['reversal_rate']:.1f})\n"
            f"- En büyük ters hareket: {analysis['max_reversal_move']}\n"
            f"- Güven Derecesi: {analysis['confidence_label']} ({analysis['confidence_stars']})\n\n"
            "CRITICAL RULES:\n"
            "1. NO MONOTONOUS OPENINGS: Do NOT start the message with repetitive cliché words like 'tebrikler!', 'Tebrikler!' or 'Sabırsızlık Primi Hasadı strategy is perfect!'. "
            "Instead, use diverse, high-class financial openings like:\n"
            "   - 'Harika bir analiz talebi...'\n"
            "   - 'Tahtaya yakından bakalım...'\n"
            "   - 'Risk avcısı yine sahnede...'\n"
            "   - 'Matematiksel kesinlik kokusu alıyorum...'\n"
            "   - 'Güzel bir soru, verileri masaya yatıralım...'\n"
            "2. LANGUAGE QUALITY: Use pure, elite, high-end Turkish financial jargon. Strictly forbid mixing English, Vietnamese, or other foreign words (e.g. do not say 'cự', 'oportunite', 'together', 'chance', 'risk-free'). Use correct Turkish equivalents (e.g. 'aşırı', 'fırsat', 'birlikte', 'olasılık', 'risksiz').\n"
            "3. VOLATILITY ESTIMATE (DURATION QUESTIONS): If the user asks how long it will take (e.g., 'tahmini kaç gün sürer?'), quantitatively estimate the duration using the asset's typical daily volatility range (e.g. WTI typically moves $1.50-$2.50 per day, SPY moves 0.8%-1.5% per day, Gold moves $20-$40 per day). Explain how many days a move of this magnitude would typically require under normal and volatile conditions. Be quantitative and highly precise!\n"
            "4. STYLE & LENGTH: Keep the response concise (max 4-5 sentences). Format it beautifully with HTML tags allowed in Telegram (<b>, <i>, <code>, <u>, <a>)."
        )
        
        answer_messages = [
            {"role": "system", "content": stats_prompt},
            {"role": "user", "content": clean_text}
        ]
        
        response_text = await call_groq_api(answer_messages)
        await update.message.reply_html(clean_telegram_html(response_text))
        
    else:
        # Standard chat reply using Llama 3
        chat_prompt = (
            "You are the quantitative trading bot named 'Sinyal Fabrikası'. "
            "Reply to the user in a friendly, finance-savvy, and helpful manner in Turkish. "
            "Keep the reply concise (max 3-4 sentences) and encourage them to ask for stock or commodity analyses. "
            "You can use HTML tags (bold, italic)."
        )
        
        chat_messages = [
            {"role": "system", "content": chat_prompt},
            {"role": "user", "content": clean_text}
        ]
        
        response_text = await call_groq_api(chat_messages)
        await update.message.reply_html(clean_telegram_html(response_text))


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

    # Catch-all MessageHandler for AI Sohbet & Analysis
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))

    return app
