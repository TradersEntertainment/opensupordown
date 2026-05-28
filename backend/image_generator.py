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
    """Ensure Inter fonts are downloaded locally for high-quality rendering."""
    if not os.path.exists(FONT_DIR):
        os.makedirs(FONT_DIR)
        
    async def download_file(url, dest):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, follow_redirects=True, timeout=15.0)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
            logger.info(f"Downloaded font to {dest}")
        except Exception as e:
            logger.error(f"Failed to download font from {url}: {e}")

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

def generate_card_image(data: dict) -> bytes:
    """
    Generates a beautifully styled, high-res image of the asset opportunity card.
    Uses 2x supersampling (renders at 1200x1400, scales down to 600x700) for pristine anti-aliasing.
    """
    # ── Configuration & Dimensions ──
    scale = 2  # Supersampling factor
    width, height = 600 * scale, 700 * scale
    
    # Create canvas
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # ── Colors ──
    bg_color = (13, 19, 31, 255)         # Dark slate BG (#0D131F)
    card_border = (31, 41, 55, 255)      # Slate-800 (#1F2937)
    panel_bg = (22, 30, 46, 255)          # Nested panel dark BG (#161E2E)
    panel_border = (44, 58, 82, 255)      # Nested panel border (#2C3A52)
    
    # Green and Red accents
    green_accent = (16, 185, 129, 255)    # Emerald-500 (#10B981)
    green_bg = (16, 185, 129, 40)
    red_accent = (239, 68, 68, 255)       # Red-500 (#EF4444)
    red_bg = (239, 68, 68, 40)
    
    # Amber/Gold accents
    amber_accent = (245, 158, 11, 255)    # Amber-500 (#F59E0B)
    amber_bg = (245, 158, 11, 20)
    
    # Text colors
    text_white = (255, 255, 255, 255)
    text_gray = (156, 163, 175, 255)      # Gray-400 (#9CA3AF)
    text_gold = (251, 191, 36, 255)       # Amber-400 (#FBBF24)
    
    # Resolve direction variables
    is_up = data.get("direction") == "UP"
    dir_color = green_accent if is_up else red_accent
    dir_bg = green_bg if is_up else red_bg
    dir_text = "📈 YUKARI" if is_up else "📉 AŞAĞI"
    
    # Load fonts at 2x size
    f_reg, f_bold = get_fonts(13 * scale, 14 * scale)
    _, f_title = get_fonts(16 * scale, 24 * scale)      # 48px bold for Symbol
    _, f_subtitle = get_fonts(12 * scale, 16 * scale)   # Subtitles
    _, f_badge = get_fonts(10 * scale, 10 * scale)      # Badges
    
    # ── Draw Background Card ──
    draw_rounded_rect(draw, [10, 10, width-10, height-10], 16 * scale, bg_color, outline=card_border, width=2 * scale)
    
    # ── Top-Left Direction Pill ──
    # Draw a rounded rect for the direction indicator
    pill_w = 90 * scale
    pill_h = 24 * scale
    pill_x, pill_y = 30 * scale, 35 * scale
    draw_rounded_rect(draw, [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h], 6 * scale, dir_bg, outline=dir_color, width=1 * scale)
    # Direction Text
    draw.text((pill_x + 10 * scale, pill_y + 4 * scale), dir_text, font=f_badge, fill=dir_color)
    
    # ── Symbol Title ──
    symbol_name = data.get("symbol", "ASSET")
    draw.text((pill_x + pill_w + 15 * scale, pill_y - 4 * scale), symbol_name, font=f_title, fill=text_white)
    
    # ── Top-Right Badges ──
    badge_x = width - 30 * scale
    badge_y = 35 * scale
    
    # 1) 99c EMİR Badge
    if data.get("has_orders_at_99"):
        badge_w = 75 * scale
        badge_h = 20 * scale
        badge_x -= badge_w
        draw_rounded_rect(draw, [badge_x, badge_y + 2*scale, badge_x + badge_w, badge_y + 2*scale + badge_h], 10 * scale, (245, 158, 11, 255))
        draw.text((badge_x + 8 * scale, badge_y + 5 * scale), "📦 99¢ EMİR", font=f_badge, fill=(0, 0, 0, 255))
        badge_x -= 10 * scale  # Margin between badges
        
    # 2) İMKANSIZ Badge
    if data.get("is_impossible"):
        badge_w = 78 * scale
        badge_h = 20 * scale
        badge_x -= badge_w
        draw_rounded_rect(draw, [badge_x, badge_y + 2*scale, badge_x + badge_w, badge_y + 2*scale + badge_h], 10 * scale, (16, 185, 129, 255))
        draw.text((badge_x + 8 * scale, badge_y + 5 * scale), "💎 İMKANSIZ", font=f_badge, fill=(0, 0, 0, 255))
        
    # ── Change vs Yesterday ──
    diff_pct = data.get("diff_pct", 0.0)
    diff_sign = "+" if diff_pct > 0 else ""
    diff_str = f"Düne Göre Değişim: {diff_sign}{diff_pct:.2f}%"
    draw.text((30 * scale, 75 * scale), diff_str, font=f_subtitle, fill=dir_color)
    
    # ── Prices List (Middle Section) ──
    y_cursor = 115 * scale
    line_spacing = 28 * scale
    
    # 1) Anlık Pyth Fiyatı
    draw.text((30 * scale, y_cursor), "Anlık Pyth Fiyatı:", font=f_reg, fill=text_gray)
    curr_price_str = f"${data.get('current_price', 0.0):,.4f}"
    draw.text((width - 30 * scale - draw.textlength(curr_price_str, font=f_bold), y_cursor), curr_price_str, font=f_bold, fill=text_white)
    
    # 2) Dünkü Kapanış
    y_cursor += line_spacing
    draw.text((30 * scale, y_cursor), "Dünkü Kapanış:", font=f_reg, fill=text_gray)
    ref_price_str = f"${data.get('ref_price', 0.0):,.4f}"
    draw.text((width - 30 * scale - draw.textlength(ref_price_str, font=f_bold), y_cursor), ref_price_str, font=f_bold, fill=text_white)
    
    # 3) Polymarket Tahtası
    y_cursor += line_spacing
    draw.text((30 * scale, y_cursor), "Polymarket Tahtası:", font=f_reg, fill=text_gray)
    
    poly = data.get("poly", {})
    if poly.get("slug"):
        up_c = f"U: {round(poly.get('up_price', 0.0) * 100)}¢"
        down_c = f"D: {round(poly.get('down_price', 0.0) * 100)}¢"
        sep = " | "
        
        # Position calculations from right to left
        w_down = draw.textlength(down_c, font=f_bold)
        w_sep = draw.textlength(sep, font=f_reg)
        w_up = draw.textlength(up_c, font=f_bold)
        
        rx = width - 30 * scale
        
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
        draw.text((width - 30 * scale - draw.textlength("Pazar Yok", font=f_reg), y_cursor), "Pazar Yok", font=f_reg, fill=text_gray)

    # ─── Panel 1: Tarihsel Analiz (60 Gün) ───
    y_cursor += 38 * scale
    panel_h = 105 * scale
    draw_rounded_rect(draw, [30 * scale, y_cursor, width - 30 * scale, y_cursor + panel_h], 10 * scale, panel_bg, outline=panel_border, width=1 * scale)
    
    # Header inside Panel
    p_cursor = y_cursor + 12 * scale
    draw.text((45 * scale, p_cursor), "Tarihsel Analiz (60 Gün)", font=f_subtitle, fill=text_gray)
    
    # Confidence Stars (aligned right)
    hist = data.get("historical", {})
    stars = hist.get("confidence_stars", "⭐")
    draw.text((width - 45 * scale - draw.textlength(stars, font=f_subtitle), p_cursor), stars, font=f_subtitle, fill=text_gold)
    
    # Ters Dönüş Oranı
    p_cursor += 24 * scale
    draw.text((45 * scale, p_cursor), "Ters Dönüş Oranı:", font=f_reg, fill=text_gray)
    rev_count = hist.get("reversed_count", 0)
    total_days = hist.get("total_similar_days", 0)
    rev_rate = hist.get("reversal_rate", 0.0)
    rev_str = f"{rev_count}/{total_days} gün ({rev_rate:.1f}%)"
    
    # Select color depending on risk
    rev_color = green_accent if rev_count == 0 else (251, 191, 36, 255) if rev_count == 1 else red_accent
    draw.text((width - 45 * scale - draw.textlength(rev_str, font=f_bold), p_cursor), rev_str, font=f_bold, fill=rev_color)
    
    # En Kötü Senaryo
    p_cursor += 24 * scale
    draw.text((45 * scale, p_cursor), "En Kötü Senaryo:", font=f_reg, fill=text_gray)
    worst = hist.get("worst_case", 0.0)
    worst_str = f"%{worst:+.2f}" if worst != 0 else "Ters dönmemiş ✅"
    draw.text((width - 45 * scale - draw.textlength(worst_str, font=f_bold), p_cursor), worst_str, font=f_bold, fill=text_white)
    
    # ─── Panel 2: Emir Kitabı (CLOB) ───
    y_cursor += panel_h + 15 * scale
    panel2_h = 135 * scale
    
    # Use special border glow if it has 99c orders
    p2_border = amber_accent if data.get("has_orders_at_99") else panel_border
    p2_bg = amber_bg if data.get("has_orders_at_99") else panel_bg
    draw_rounded_rect(draw, [30 * scale, y_cursor, width - 30 * scale, y_cursor + panel2_h], 10 * scale, p2_bg, outline=p2_border, width=1 * scale)
    
    # Header inside Panel
    p2_cursor = y_cursor + 12 * scale
    draw.text((45 * scale, p2_cursor), "📦 Emir Kitabı", font=f_subtitle, fill=text_gold if data.get("has_orders_at_99") else text_gray)
    
    # "ALIM AKTİF" or "EMİR YOK" badge (aligned right)
    status_text = "ALIM AKTİF" if data.get("has_orders_at_99") else "EMİR YOK"
    status_color = text_gold if data.get("has_orders_at_99") else text_gray
    draw.text((width - 45 * scale - draw.textlength(status_text, font=f_badge), p2_cursor + 2*scale), status_text, font=f_badge, fill=status_color)
    
    # Rows
    if poly.get("best_ask") is not None:
        # En Ucuz Teklif
        p2_cursor += 24 * scale
        draw.text((45 * scale, p2_cursor), "En Ucuz Teklif (Ask):", font=f_reg, fill=text_gray)
        ask_str = f"{round(poly.get('best_ask', 0.0) * 100)}¢"
        draw.text((width - 45 * scale - draw.textlength(ask_str, font=f_bold), p2_cursor), ask_str, font=f_bold, fill=text_white)
        
        # Satış Emir Büyüklüğü
        p2_cursor += 24 * scale
        draw.text((45 * scale, p2_cursor), "Satış Emir Büyüklüğü:", font=f_reg, fill=text_gray)
        size_str = f"${round(poly.get('depth_at_best', 0.0)):,}"
        draw.text((width - 45 * scale - draw.textlength(size_str, font=f_bold), p2_cursor), size_str, font=f_bold, fill=text_white)
        
        # 99c'daki Emirler
        p2_cursor += 24 * scale
        draw.text((45 * scale, p2_cursor), "99¢'daki Emirler:", font=f_bold, fill=text_gold if poly.get("depth_at_99", 0) > 0 else text_gray)
        depth_99_str = f"${round(poly.get('depth_at_99', 0.0)):,}"
        draw.text((width - 45 * scale - draw.textlength(depth_99_str, font=f_bold), p2_cursor), depth_99_str, font=f_bold, fill=text_gold if poly.get("depth_at_99", 0) > 0 else text_white)
    else:
        # No orders
        p2_cursor += 45 * scale
        no_orders_str = "CLOB satış emri bulunmuyor"
        draw.text((width/2 - draw.textlength(no_orders_str, font=f_reg)/2, p2_cursor), no_orders_str, font=f_reg, fill=text_gray)

    # ─── Footer: Tavsiye & İşlem Yap Link ───
    y_cursor = height - 55 * scale
    
    # 1) Tavsiye text
    safe_outcome_price = poly.get("safe_outcome_price", 0.0)
    if safe_outcome_price > 0:
        profit_pct = ((1.0 - safe_outcome_price) / safe_outcome_price) * 100
        rec_price = round(safe_outcome_price * 100)
        advice_str = f"Tavsiye: {rec_price}¢ ➔ $1.00 (%{profit_pct:.1f} kâr)"
        draw.text((30 * scale, y_cursor), advice_str, font=f_subtitle, fill=text_white)
    
    # 2) "İşlem Yap" link on right
    link_str = "İşlem Yap ↗"
    draw.text((width - 30 * scale - draw.textlength(link_str, font=f_subtitle), y_cursor), link_str, font=f_subtitle, fill=(59, 130, 246, 255))
    
    # ── Supersampling scale down ──
    # Resize image down to original 600x700 with high-quality Lanczos resampling
    final_img = img.resize((600, 700), Image.Resampling.LANCZOS)
    
    # Convert PIL Image to bytes
    byte_io = io.BytesIO()
    final_img.save(byte_io, 'PNG', quality=95)
    byte_io.seek(0)
    
    return byte_io.getvalue()
