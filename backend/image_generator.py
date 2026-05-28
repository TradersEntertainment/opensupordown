import os
import httpx
import logging
from PIL import Image, ImageDraw, ImageFont
import io

logger = logging.getLogger(__name__)

# Font paths
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
REGULAR_FONT_PATH = os.path.join(FONT_DIR, "Roboto-Regular.ttf")
BOLD_FONT_PATH = os.path.join(FONT_DIR, "Roboto-Bold.ttf")

# URLs to fetch fonts (Google Fonts Roboto repository)
REGULAR_FONT_URL = "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Regular.ttf"
BOLD_FONT_URL = "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Bold.ttf"

def ensure_fonts():
    """Ensure Roboto fonts are downloaded locally for high-quality rendering."""
    if not os.path.exists(FONT_DIR):
        os.makedirs(FONT_DIR)
        
    # If not running in an async context, we download synchronously
    if not os.path.exists(REGULAR_FONT_PATH):
        logger.info("Regular font missing, downloading...")
        try:
            resp = httpx.get(REGULAR_FONT_URL, follow_redirects=True, timeout=15.0)
            resp.raise_for_status()
            with open(REGULAR_FONT_PATH, "wb") as f:
                f.write(resp.content)
        except Exception as e:
            logger.error(f"Sync download failed for regular font: {e}")
            
    if not os.path.exists(BOLD_FONT_PATH):
        logger.info("Bold font missing, downloading...")
        try:
            resp = httpx.get(BOLD_FONT_URL, follow_redirects=True, timeout=15.0)
            resp.raise_for_status()
            with open(BOLD_FONT_PATH, "wb") as f:
                f.write(resp.content)
        except Exception as e:
            logger.error(f"Sync download failed for bold font: {e}")

def get_fonts(regular_size: int, bold_size: int):
    """Load fonts or fallback to default if not found."""
    ensure_fonts()
    try:
        if os.path.exists(REGULAR_FONT_PATH) and os.path.exists(BOLD_FONT_PATH):
            font_reg = ImageFont.truetype(REGULAR_FONT_PATH, regular_size)
            font_bold = ImageFont.truetype(BOLD_FONT_PATH, bold_size)
            return font_reg, font_bold
    except Exception as e:
        logger.error(f"Error loading truetype fonts: {e}")
        
    # Fallback to default
    return ImageFont.load_default(), ImageFont.load_default()

def draw_rounded_rect(draw, coords, radius, color, outline=None, width=1):
    """Draw a smooth rounded rectangle."""
    draw.rounded_rectangle(coords, radius=radius, fill=color, outline=outline, width=width)

def generate_card_image(data: dict, lang: str = "tr") -> bytes:
    """
    Generates a beautifully styled, high-res image of the asset opportunity card.
    Uses 2x supersampling (renders at 1200x1400, scales down to 600x700) for pristine anti-aliasing.
    Uses RGB mode with fully opaque panels to prevent blending issues on Telegram backgrounds.
    Supports dynamic language translation ("tr" or "en").
    """
    # ── Configuration & Dimensions ──
    scale = 2  # Supersampling factor
    width, height = 600 * scale, 700 * scale
    
    # ── Colors ──
    bg_color = (13, 21, 37)              # Opaque Dark slate BG (#0D1525)
    card_border = (31, 41, 55)            # Slate-800 (#1F2937)
    panel_bg = (22, 32, 51)               # Opaque panel BG (#162033)
    panel_border = (44, 58, 82)           # Nested panel border (#2C3A52)
    
    # Green and Red accents
    green_accent = (16, 185, 129)         # Emerald-500 (#10B981)
    green_bg = (12, 45, 34)               # Solid dark green BG (no transparency)
    red_accent = (239, 68, 68)            # Red-500 (#EF4444)
    red_bg = (58, 18, 18)                 # Solid dark red BG (no transparency)
    
    # Amber/Gold accents
    amber_accent = (245, 158, 11)         # Amber-500 (#F59E0B)
    amber_bg = (40, 28, 12)               # Opaque dark gold/brown BG (no transparency)
    
    # Text colors
    text_white = (255, 255, 255)
    text_gray = (175, 185, 200)           # Light slate gray (#AFB9C8) for excellent readability
    text_gold = (251, 191, 36)            # Amber-400 (#FBBF24)
    
    # Create canvas in solid RGB mode (completely prevents transparency bugs in Telegram)
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    
    # Resolve direction variables
    is_up = data.get("direction") == "UP"
    dir_color = green_accent if is_up else red_accent
    dir_bg = green_bg if is_up else red_bg
    diff_pct = data.get("diff_pct", 0.0)
    diff_sign = "+" if diff_pct > 0 else ""
    
    poly = data.get("poly", {})
    
    # ── Dynamic Language Translations ──
    if lang == "en":
        dir_text = "BUY UP" if is_up else "BUY DOWN"
        t_change = f"Change vs Yesterday: {diff_sign}{diff_pct:.2f}%"
        t_curr_price = "Current Pyth Price:"
        t_ref_price = "Yesterday's Close:"
        t_poly_board = "Polymarket Board:"
        t_hist_title = "Historical Analysis (60 Days)"
        t_rev_rate = "Reversal Rate:"
        t_worst = "Worst Case Scenario:"
        t_no_rev = "Never reversed"
        t_clob_title = "ORDER BOOK"
        t_clob_active = "ACTIVE DEPTH"
        t_clob_empty = "NO ORDERS"
        t_best_ask = "Best Ask (Price):"
        t_ask_size = "Order Book Size:"
        t_depth_99 = "Asks at 99c:"
        t_no_orders = "No active CLOB sell orders"
        
        safe_outcome_price = poly.get("safe_outcome_price", 0.0)
        if safe_outcome_price > 0:
            profit_pct = ((1.0 - safe_outcome_price) / safe_outcome_price) * 100
            rec_price = round(safe_outcome_price * 100)
            t_advice = f"Advice: Buy {rec_price}c -> $1.00 ({profit_pct:.1f}% profit)"
        else:
            t_advice = ""
        t_trade = "Trade Now ->"
        t_badge_impos = "IMPOSSIBLE"
        t_badge_99c = "99c ASK"
    else:
        dir_text = "YUKARI" if is_up else "ASAGI"
        t_change = f"Dune Gore Degisim: {diff_sign}{diff_pct:.2f}%"
        t_curr_price = "Anlık Pyth Fiyatı:"
        t_ref_price = "Dünkü Kapanış:"
        t_poly_board = "Polymarket Tahtası:"
        t_hist_title = "Tarihsel Analiz (60 Gun)"
        t_rev_rate = "Ters Donus Orani:"
        t_worst = "En Kotu Senaryo:"
        t_no_rev = "Ters donmedi"
        t_clob_title = "EMIR KITABI"
        t_clob_active = "ALIM AKTIF"
        t_clob_empty = "EMIR YOK"
        t_best_ask = "En Ucuz Teklif (Ask):"
        t_ask_size = "Satis Emir Buyuklugu:"
        t_depth_99 = "99c'daki Emirler:"
        t_no_orders = "CLOB satis emri bulunmuyor"
        
        safe_outcome_price = poly.get("safe_outcome_price", 0.0)
        if safe_outcome_price > 0:
            profit_pct = ((1.0 - safe_outcome_price) / safe_outcome_price) * 100
            rec_price = round(safe_outcome_price * 100)
            t_advice = f"Tavsiye: {rec_price}c -> $1.00 (%{profit_pct:.1f} kar)"
        else:
            t_advice = ""
        t_trade = "Islem Yap ->"
        t_badge_impos = "IMKANSIZ"
        t_badge_99c = "99c EMIR"
        
    # Load fonts at 2x size
    f_reg, f_bold = get_fonts(13 * scale, 14 * scale)
    _, f_title = get_fonts(16 * scale, 24 * scale)      # 48px bold for Symbol
    _, f_subtitle = get_fonts(12 * scale, 16 * scale)   # Subtitles
    _, f_badge = get_fonts(10 * scale, 10 * scale)      # Badges
    
    # ── Draw Background Card Outline ──
    draw_rounded_rect(draw, [15, 15, width-15, height-15], 16 * scale, bg_color, outline=card_border, width=2 * scale)
    
    # ── Top-Left Direction Pill ──
    pill_w = 80 * scale
    pill_h = 24 * scale
    pill_x, pill_y = 35 * scale, 40 * scale
    draw_rounded_rect(draw, [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h], 6 * scale, dir_bg, outline=dir_color, width=1 * scale)
    w_dir_text = draw.textlength(dir_text, font=f_badge)
    draw.text((pill_x + (pill_w - w_dir_text)/2, pill_y + 4 * scale), dir_text, font=f_badge, fill=dir_color)
    
    # ── Symbol Title ──
    symbol_name = data.get("symbol", "ASSET")
    draw.text((pill_x + pill_w + 15 * scale, pill_y - 4 * scale), symbol_name, font=f_title, fill=text_white)
    
    # ── Top-Right Badges ──
    badge_x = width - 35 * scale
    badge_y = 40 * scale
    
    # 1) 99c EMİR Badge
    if data.get("has_orders_at_99"):
        badge_w = 72 * scale
        badge_h = 20 * scale
        badge_x -= badge_w
        draw_rounded_rect(draw, [badge_x, badge_y + 2*scale, badge_x + badge_w, badge_y + 2*scale + badge_h], 10 * scale, amber_accent)
        w_badge_txt = draw.textlength(t_badge_99c, font=f_badge)
        draw.text((badge_x + (badge_w - w_badge_txt)/2, badge_y + 5 * scale), t_badge_99c, font=f_badge, fill=(0, 0, 0))
        badge_x -= 10 * scale  # Margin between badges
        
    # 2) İMKANSIZ Badge
    if data.get("is_impossible"):
        badge_w = 78 * scale
        badge_h = 20 * scale
        badge_x -= badge_w
        draw_rounded_rect(draw, [badge_x, badge_y + 2*scale, badge_x + badge_w, badge_y + 2*scale + badge_h], 10 * scale, green_accent)
        w_badge_txt = draw.textlength(t_badge_impos, font=f_badge)
        draw.text((badge_x + (badge_w - w_badge_txt)/2, badge_y + 5 * scale), t_badge_impos, font=f_badge, fill=(0, 0, 0))
        
    # ── Change vs Yesterday ──
    draw.text((35 * scale, 80 * scale), t_change, font=f_subtitle, fill=dir_color)
    
    # ── Prices List (Middle Section) ──
    y_cursor = 120 * scale
    line_spacing = 28 * scale
    
    # 1) Anlık Pyth Fiyatı
    draw.text((35 * scale, y_cursor), t_curr_price, font=f_reg, fill=text_gray)
    curr_price_str = f"${data.get('current_price', 0.0):,.4f}"
    draw.text((width - 35 * scale - draw.textlength(curr_price_str, font=f_bold), y_cursor), curr_price_str, font=f_bold, fill=text_white)
    
    # 2) Dünkü Kapanış
    y_cursor += line_spacing
    draw.text((35 * scale, y_cursor), t_ref_price, font=f_reg, fill=text_gray)
    ref_price_str = f"${data.get('ref_price', 0.0):,.4f}"
    draw.text((width - 35 * scale - draw.textlength(ref_price_str, font=f_bold), y_cursor), ref_price_str, font=f_bold, fill=text_white)
    
    # 3) Polymarket Tahtası
    y_cursor += line_spacing
    draw.text((35 * scale, y_cursor), t_poly_board, font=f_reg, fill=text_gray)
    
    if poly.get("slug"):
        up_c = f"U: {round(poly.get('up_price', 0.0) * 100)}c"
        down_c = f"D: {round(poly.get('down_price', 0.0) * 100)}c"
        sep = " | "
        
        # Position calculations from right to left
        w_down = draw.textlength(down_c, font=f_bold)
        w_sep = draw.textlength(sep, font=f_reg)
        w_up = draw.textlength(up_c, font=f_bold)
        
        rx = width - 35 * scale
        
        # Draw Down Contract
        rx -= w_down
        draw.text((rx, y_cursor), down_c, font=f_bold, fill=red_accent if not is_up else text_white)
        # Draw Sep
        rx -= w_sep
        draw.text((rx, y_cursor), sep, font=f_reg, fill=panel_border)
        # Draw Up Contract
        rx -= w_up
        draw.text((rx, y_cursor), up_c, font=f_bold, fill=green_accent if is_up else text_white)
    else:
        no_market_str = "No Market" if lang == "en" else "Pazar Yok"
        draw.text((width - 35 * scale - draw.textlength(no_market_str, font=f_reg), y_cursor), no_market_str, font=f_reg, fill=text_gray)
 
    # ─── Panel 1: Tarihsel Analiz (60 Gün) ───
    y_cursor += 38 * scale
    panel_h = 105 * scale
    draw_rounded_rect(draw, [35 * scale, y_cursor, width - 35 * scale, y_cursor + panel_h], 10 * scale, panel_bg, outline=panel_border, width=1 * scale)
    
    # Header inside Panel
    p_cursor = y_cursor + 12 * scale
    draw.text((50 * scale, p_cursor), t_hist_title, font=f_subtitle, fill=text_gray)
    
    # Convert star emojis to standard filled star glyphs (★/☆)
    hist = data.get("historical", {})
    stars = hist.get("confidence_stars", "⭐")
    stars_count = stars.count("⭐")
    if stars_count == 0:
        stars_count = 4  # Default fallback
    stars_text = "★" * stars_count + "☆" * (5 - stars_count)
    
    draw.text((width - 50 * scale - draw.textlength(stars_text, font=f_subtitle), p_cursor), stars_text, font=f_subtitle, fill=text_gold)
    
    # Ters Dönüş Oranı
    p_cursor += 24 * scale
    draw.text((50 * scale, p_cursor), t_rev_rate, font=f_reg, fill=text_gray)
    rev_count = hist.get("reversed_count", 0)
    total_days = hist.get("total_similar_days", 0)
    rev_rate = hist.get("reversal_rate", 0.0)
    
    days_word = "days" if lang == "en" else "gun"
    rev_str = f"{rev_count}/{total_days} {days_word} ({rev_rate:.1f}%)"
    
    # Select color depending on risk
    rev_color = green_accent if rev_count == 0 else text_gold if rev_count == 1 else red_accent
    draw.text((width - 50 * scale - draw.textlength(rev_str, font=f_bold), p_cursor), rev_str, font=f_bold, fill=rev_color)
    
    # En Kötü Senaryo
    p_cursor += 24 * scale
    draw.text((50 * scale, p_cursor), t_worst, font=f_reg, fill=text_gray)
    worst = hist.get("worst_case", 0.0)
    worst_str = f"%{worst:+.2f}" if worst != 0 else t_no_rev
    draw.text((width - 50 * scale - draw.textlength(worst_str, font=f_bold), p_cursor), worst_str, font=f_bold, fill=text_white)
    
    # ─── Panel 2: Emir Kitabı (CLOB) ───
    y_cursor += panel_h + 15 * scale
    panel2_h = 135 * scale
    
    # Use solid amber background for orderbook if 99c orders are active, otherwise regular panel BG
    p2_border = amber_accent if data.get("has_orders_at_99") else panel_border
    p2_bg = amber_bg if data.get("has_orders_at_99") else panel_bg
    draw_rounded_rect(draw, [35 * scale, y_cursor, width - 35 * scale, y_cursor + panel2_h], 10 * scale, p2_bg, outline=p2_border, width=1 * scale)
    
    # Header inside Panel
    p2_cursor = y_cursor + 12 * scale
    draw.text((50 * scale, p2_cursor), t_clob_title, font=f_subtitle, fill=text_gold if data.get("has_orders_at_99") else text_gray)
    
    status_text = t_clob_active if data.get("has_orders_at_99") else t_clob_empty
    status_color = text_gold if data.get("has_orders_at_99") else text_gray
    draw.text((width - 50 * scale - draw.textlength(status_text, font=f_badge), p2_cursor + 2*scale), status_text, font=f_badge, fill=status_color)
    
    # Rows
    if poly.get("best_ask") is not None:
        # En Ucuz Teklif
        p2_cursor += 24 * scale
        draw.text((50 * scale, p2_cursor), t_best_ask, font=f_reg, fill=text_gray)
        ask_str = f"{round(poly.get('best_ask', 0.0) * 100)}c"
        draw.text((width - 50 * scale - draw.textlength(ask_str, font=f_bold), p2_cursor), ask_str, font=f_bold, fill=text_white)
        
        # Satış Emir Büyüklüğü
        p2_cursor += 24 * scale
        draw.text((50 * scale, p2_cursor), t_ask_size, font=f_reg, fill=text_gray)
        size_str = f"${round(poly.get('depth_at_best', 0.0)):,}"
        draw.text((width - 50 * scale - draw.textlength(size_str, font=f_bold), p2_cursor), size_str, font=f_bold, fill=text_white)
        
        # 99c'daki Emirler
        p2_cursor += 24 * scale
        draw.text((50 * scale, p2_cursor), t_depth_99, font=f_bold, fill=text_gold if poly.get("depth_at_99", 0) > 0 else text_gray)
        depth_99_str = f"${round(poly.get('depth_at_99', 0.0)):,}"
        draw.text((width - 50 * scale - draw.textlength(depth_99_str, font=f_bold), p2_cursor), depth_99_str, font=f_bold, fill=text_gold if poly.get("depth_at_99", 0) > 0 else text_white)
    else:
        # No orders
        p2_cursor += 45 * scale
        draw.text((width/2 - draw.textlength(t_no_orders, font=f_reg)/2, p2_cursor), t_no_orders, font=f_reg, fill=text_gray)
 
    # ─── Footer: Tavsiye & İşlem Yap Link ───
    y_cursor = height - 55 * scale
    
    # 1) Tavsiye text (using standard -> ASCII arrow)
    if t_advice:
        draw.text((35 * scale, y_cursor), t_advice, font=f_subtitle, fill=text_white)
    
    # 2) "İşlem Yap" link on right (using standard -> ASCII arrow)
    draw.text((width - 35 * scale - draw.textlength(t_trade, font=f_subtitle), y_cursor), t_trade, font=f_subtitle, fill=(59, 130, 246))
    
    # ── Supersampling scale down ──
    final_img = img.resize((600, 700), Image.Resampling.LANCZOS)
    
    # Convert PIL Image to bytes
    byte_io = io.BytesIO()
    final_img.save(byte_io, 'PNG')
    byte_io.seek(0)
    
    return byte_io.getvalue()
    
    # Convert PIL Image to bytes
    byte_io = io.BytesIO()
    final_img.save(byte_io, 'PNG')
    byte_io.seek(0)
    
    return byte_io.getvalue()
