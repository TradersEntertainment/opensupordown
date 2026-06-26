import asyncio
import logging
from datetime import datetime, timedelta
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

# --- Pre-market Movers Scanner State ---
_last_scanner_time = 0
_scanner_alerted = set()  # Set of symbols already alerted today
PREMARKET_ALERT_THRESHOLD = 2.0  # Alert at 2%+ move
PREMARKET_SCAN_INTERVAL = 180  # Check every 3 minutes

# Stocks to scan in pre-market (ones that have Polymarket Up/Down bets)
PREMARKET_WATCHLIST = [
    "SPY", "PLTR", "TSLA", "NVDA", "AAPL", "AMZN", "META", "GOOGL",
    "MSFT", "NFLX", "COIN", "HOOD", "ABNB", "RKLB", "EWY", "MU"
]

# --- Market Open Result State ---
_open_result_sent_today = None

# --- Direction Flip Detection State ---
_position_flip_state = {}
FLIP_STABLE_MINUTES = 3    # Direction must be stable for at least 3 min
FLIP_MIN_PCT = 0.1          # Minimum 0.1% in new direction to trigger
FLIP_COOLDOWN = 300         # 5 min cooldown between alerts per position


def is_market_completely_closed() -> bool:
    """
    True only when ALL markets are fully closed.
    - Weekdays before 04:00 ET (11:00 TR): pre-market not yet started
    - Weekdays after 17:30 ET (00:30 TR): commodity market closed
    - Saturday: all day
    - Sunday before 18:00 ET (01:00 TR): commodity market closed
    NOTE: 04:00 ET is used so premarket movers scanner can run.
    """
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    weekday = now_et.weekday()  # 0=Mon, 6=Sun
    total_minutes = now_et.hour * 60 + now_et.minute

    if weekday == 5:  # Saturday: fully closed
        return True
    if weekday == 6:  # Sunday: closed until 18:00 ET
        return total_minutes < (18 * 60)
    # Weekdays: active 04:00 ET (pre-market scanner) to 17:30 ET
    return total_minutes < (4 * 60) or total_minutes > (17 * 60 + 30)


async def is_position_winning(p, price) -> bool:
    """Helper to determine if a position is currently winning relative to the reference/target level."""
    symbol = p['symbol'].upper()
    direction = p['direction'].upper()
    ref = p['ref_price']
    
    if direction in ('YES', 'NO'):
        # Binary hit bet
        try:
            created_dt = datetime.fromisoformat(p['created_at'])
            et_tz = pytz.timezone('US/Eastern')
            if created_dt.tzinfo is None:
                created_dt = et_tz.localize(created_dt)
            created_ts = int(created_dt.timestamp())
        except:
            created_ts = 0
            
        creation_price = None
        if created_ts > 0:
            try:
                _, full_symbol = pyth_client.get_pyth_id(symbol)
                creation_price = await pyth_client.get_historical_candle_price(
                    full_symbol or symbol, p['pyth_id'], created_ts - 120, created_ts + 120, price_type='close'
                )
            except:
                pass
        is_low_bet = None
        title_lower = p.get('title', '').lower() if p.get('title') else ''
        if any(kw in title_lower for kw in ['(low)', 'dip', 'below', 'drop', 'under', 'down']):
            is_low_bet = True
        elif any(kw in title_lower for kw in ['(high)', 'exceed', 'above', 'rise', 'climb', 'up']):
            is_low_bet = False
            
        if is_low_bet is None:
            if not creation_price:
                creation_price = price
            is_low_bet = creation_price > ref
        
        if is_low_bet:
            if direction == 'YES':
                # YES wins if price touches or goes below ref
                return price <= ref
            else: # NO
                # NO wins if price stays above ref
                return price > ref
        else: # HIGH bet
            if direction == 'YES':
                # YES wins if price touches or goes above ref
                return price >= ref
            else: # NO
                # NO wins if price stays below ref
                return price < ref
    else:
        # Standard open/close bets
        is_up_bet = 'UP' in direction
        if is_up_bet:
            return price > ref
        else:
            return price < ref


async def is_up_seeking_position(p, price) -> bool:
    """Helper to determine if a position is up-seeking (meaning it wants price to go up/above ref)."""
    symbol = p['symbol'].upper()
    direction = p['direction'].upper()
    ref = p['ref_price']
    
    if direction in ('YES', 'NO'):
        # Binary hit bet
        try:
            created_dt = datetime.fromisoformat(p['created_at'])
            et_tz = pytz.timezone('US/Eastern')
            if created_dt.tzinfo is None:
                created_dt = et_tz.localize(created_dt)
            created_ts = int(created_dt.timestamp())
        except:
            created_ts = 0
            
        creation_price = None
        if created_ts > 0:
            try:
                _, full_symbol = pyth_client.get_pyth_id(symbol)
                creation_price = await pyth_client.get_historical_candle_price(
                    full_symbol or symbol, p['pyth_id'], created_ts - 120, created_ts + 120, price_type='close'
                )
            except:
                pass
        if not creation_price:
            creation_price = price
            
        is_low_bet = None
        title_lower = p.get('title', '').lower() if p.get('title') else ''
        if any(kw in title_lower for kw in ['(low)', 'dip', 'below', 'drop', 'under', 'down']):
            is_low_bet = True
        elif any(kw in title_lower for kw in ['(high)', 'exceed', 'above', 'rise', 'climb', 'up']):
            is_low_bet = False
            
        if is_low_bet is None:
            is_low_bet = creation_price > ref
            
        if is_low_bet:
            return direction == 'NO'
        else:
            return direction == 'YES'
    else:
        return 'UP' in direction


async def _check_auto_expire(positions):
    """Closes positions if their resolution time has passed (9:30 AM ET for open bets, market close for close bets)."""
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)

    for p in positions:
        symbol = p['symbol'].upper()
        direction = p['direction'].upper()
        is_open_bet = 'OPEN_' in direction

        # Parse creation time to US/Eastern
        try:
            created_dt = datetime.fromisoformat(p['created_at'])
            if created_dt.tzinfo is None:
                # System local time assumed, localize and convert to ET
                created_dt = created_dt.astimezone(et_tz)
            else:
                created_dt = created_dt.astimezone(et_tz)
        except Exception as e:
            logger.error(f"Error parsing created_at for position {p['id']}: {e}")
            created_dt = now_et - timedelta(days=1)  # Fallback to allow resolution

        if is_open_bet:
            # Open bets resolve at 9:30 AM ET (16:30 TR)
            today_open_et = et_tz.localize(datetime(now_et.year, now_et.month, now_et.day, 9, 30, 0))
            # Resolve if current time is past 9:30 AM ET today, AND position was created before today's 9:30 AM ET
            if now_et >= today_open_et and created_dt < today_open_et:
                logger.info(f"Auto-resolving open bet position {p['id']} ({symbol} {direction})")
                
                # Fetch opening price
                open_price = None
                try:
                    from_ts, to_ts = pyth_client.get_previous_open_times(symbol)
                    _, full_symbol = pyth_client.get_pyth_id(symbol)
                    open_price = await pyth_client.get_historical_candle_price(
                        full_symbol or symbol, p['pyth_id'], from_ts, to_ts, price_type='open'
                    )
                except Exception as ex:
                    logger.error(f"Error fetching historical open price for {symbol}: {ex}")

                if not open_price:
                    open_price = await pyth_client.get_active_price(symbol, p['pyth_id'])

                ref = p['ref_price']
                is_win = False
                if open_price:
                    is_win = await is_position_winning(p, open_price)

                result_str = "KAZANDI 🟢" if is_win else "KAYBETTİ 🔴"
                open_price_str = f"${open_price:.4f}" if open_price else "Bilinmiyor"

                msg = (
                    f"🏁 <b>MARKET AÇILDI: {symbol} {direction}</b>\n\n"
                    f"<b>Sonuç:</b> {result_str}\n"
                    f"<b>Açılış:</b> {open_price_str}\n"
                    f"<b>Referans (Dünkü Kapanış):</b> ${ref:.4f}"
                )
                await send_notification(msg)
                await database.close_position(p['id'])
        else:
            # Regular close bets resolve at market close (16:00 ET for stocks, 17:00 ET for commodities)
            is_commodity = any(c in symbol for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
            close_hour = 17 if is_commodity else 16

            if now_et.hour >= close_hour:
                logger.info(f"Auto-closing position {p['id']} ({symbol})")

                current_price = await pyth_client.get_active_price(symbol, p['pyth_id'])
                ref = p['ref_price']
                is_win = False
                if current_price:
                    is_win = await is_position_winning(p, current_price)

                result_str = "KAZANDI 🟢" if is_win else "KAYBETTİ 🔴"
                current_price_str = f"${current_price:.4f}" if current_price else "Bilinmiyor"

                msg = (
                    f"🏁 <b>MARKET KAPANDI: {symbol} {direction}</b>\n\n"
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
    
    minutes_to_close_stock = max(0, 960 - total_minutes)
    minutes_to_close_commodity = max(0, 1020 - total_minutes)
    
    # Collect position data first so we can sort by danger level
    pos_data = []
    for p in positions:
        current_price = await pyth_client.get_active_price(p['symbol'], p['pyth_id'])
        if not current_price:
            continue
        
        ref = p['ref_price']
        diff_pct = ((current_price - ref) / ref) * 100
        is_winning = await is_position_winning(p, current_price)
        
        symbol = p['symbol']
        is_commodity = any(c in symbol.upper() for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
        mins_left = minutes_to_close_commodity if is_commodity else minutes_to_close_stock
        
        pos_data.append({
            'symbol': symbol,
            'direction': p['direction'],
            'current_price': current_price,
            'diff_pct': diff_pct,
            'is_winning': is_winning,
            'mins_left': mins_left,
            'abs_diff': abs(diff_pct)
        })
    
    if not pos_data:
        return
    
    # Sort by danger: losing positions first, then by smallest abs diff (closest to flipping)
    pos_data.sort(key=lambda x: (x['is_winning'], x['abs_diff']))
    
    lines = [f"📊 <b>Kapanış Yaklaşıyor — Durum Özeti</b>\n"]
    for d in pos_data:
        icon = "🟢" if d['is_winning'] else "🔴"
        lines.append(
            f"{icon} <b>{d['symbol']} {d['direction']}</b>\n"
            f"   Anlık: ${d['current_price']:.4f} | Fark: %{d['diff_pct']:.2f} | Kapanışa: {d['mins_left']}dk"
        )
    
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
    
    # Only active 09:15-09:29 ET on weekdays (cut off before 09:29 ET / 16:29 TR to prevent notifications at open)
    if now_et.weekday() >= 5:
        return
    if not (555 <= total_minutes < 569):  # 09:15 = 555, 09:29 = 569
        return
    
    # Resolve SPY
    regular_id, _ = pyth_client.get_pyth_id("SPY")
    if not regular_id:
        return
        
    current_price = await pyth_client.get_active_price("SPY", regular_id)
    if not current_price:
        return
    
    # Get yesterday's close as reference (always from regular feed for history)
    from_ts, to_ts = pyth_client.get_previous_close_times("SPY")
    ref_price = await pyth_client.get_historical_candle_price(
        "Equity.US.SPY/USD", regular_id, from_ts, to_ts, price_type='close'
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


async def _check_market_open_result():
    """
    At exactly 09:30 ET (16:30 TR), send a one-time notification
    about whether SPX opened UP or DOWN.
    """
    global _open_result_sent_today
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    
    # Only at 09:30-09:32 ET on weekdays (2-min window to catch it)
    if now_et.weekday() >= 5:
        return
    if not (570 <= total_minutes < 572):  # 09:30 = 570, 09:32 = 572
        return
    
    today_str = now_et.strftime('%Y-%m-%d')
    if _open_result_sent_today == today_str:
        return  # Already sent today
    
    # Get SPY current price
    regular_id, _ = pyth_client.get_pyth_id("SPY")
    if not regular_id:
        return
    
    current_price = await pyth_client.get_active_price("SPY", regular_id)
    if not current_price:
        return
    
    # Get yesterday's close as reference
    from_ts, to_ts = pyth_client.get_previous_close_times("SPY")
    ref_price = await pyth_client.get_historical_candle_price(
        "Equity.US.SPY/USD", regular_id, from_ts, to_ts, price_type='close'
    )
    if not ref_price:
        return
    
    diff_pct = ((current_price - ref_price) / ref_price) * 100
    
    if diff_pct > 0:
        result = "YUKARI AÇILDI 📈🟢"
    else:
        result = "AŞAĞI AÇILDI 📉🔴"
    
    _open_result_sent_today = today_str
    
    msg = (
        f"🔔 <b>SPX {result}</b>\n\n"
        f"<b>Açılış Fiyatı:</b> ${current_price:.4f}\n"
        f"<b>Fark:</b> %{diff_pct:+.3f}\n"
        f"<b>Dünkü Kapanış:</b> ${ref_price:.4f}"
    )
    await send_notification(msg)


_position_price_history = {}
_last_tail_risk_alert_time = {}

async def _check_tail_risk_reversal(p, current_price):
    """
    Detect sudden tail-risk reversals in the last 30 minutes of the trading session.
    If price moves sharply against a winning position in a 5-minute window, alert.
    """
    global _position_price_history, _last_tail_risk_alert_time
    import time
    
    if not current_price:
        return
        
    pos_id = p['id']
    symbol = p['symbol'].upper()
    direction = p['direction']
    ref = p['ref_price']
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    
    is_commodity = any(c in symbol for c in ["WTI", "XAU", "XAG", "GOLD", "SILVER"])
    close_mins = 1020 if is_commodity else 960
    
    # Active only in the last 30 minutes before market close
    if not (close_mins - 30 <= total_minutes < close_mins):
        return
        
    minutes_left = close_mins - total_minutes
    now_ts = time.time()
    
    # Initialize history list
    if pos_id not in _position_price_history:
        _position_price_history[pos_id] = []
        
    history = _position_price_history[pos_id]
    
    # 1. Clean up history older than 5 minutes (300 seconds)
    history = [h for h in history if now_ts - h[0] <= 300]
    history.append((now_ts, current_price))
    _position_price_history[pos_id] = history
    
    # Need at least 1 minute of data points to compare
    if len(history) < 5 or (now_ts - history[0][0] < 60):
        return
        
    # Get reference window
    oldest_price = history[0][1]
    
    # Cooldown check (cooldown = 3 minutes)
    last_alert = _last_tail_risk_alert_time.get(pos_id, 0)
    if now_ts - last_alert < 180:
        return
        
    is_winning = await is_position_winning(p, current_price)
    
    # Only alert on active winning positions (which they comfortably hold as "safe bets")
    if not is_winning:
        return
        
    # Calculate price change in the 5-minute sliding window
    change_pct = ((current_price - oldest_price) / oldest_price) * 100
    
    # Alert conditions:
    # - If UP bet and price dropped sharply (change_pct is negative and abs(change_pct) >= 0.15%)
    # - If DOWN bet and price rose sharply (change_pct is positive and change_pct >= 0.15%)
    is_danger = False
    move_desc = ""
    
    is_up_seeking_bet = await is_up_seeking_position(p, current_price)
    if is_up_seeking_bet and change_pct <= -0.15:
        is_danger = True
        move_desc = f"son 5 dakikada %{abs(change_pct):.3f} oranında sert düştü 📉"
    elif not is_up_seeking_bet and change_pct >= 0.15:
        is_danger = True
        move_desc = f"son 5 dakikada %{change_pct:.3f} oranında sert yükseldi 📈"
        
    if is_danger:
        _last_tail_risk_alert_time[pos_id] = now_ts
        
        diff_pct = ((current_price - ref) / ref) * 100
        
        msg = (
            f"⚡ <b>ANİ ANOMALİ ALARMI: {symbol} {direction}</b>\n\n"
            f"⚠️ Rahat giden pozisyonda, market kapanışına <b>{minutes_left} dakika kala</b> beklenmedik sert bir ters hareket yaşandı!\n\n"
            f"<b>Hareket Durumu:</b> {move_desc}\n"
            f"<b>Anlık Fiyat:</b> ${current_price:.4f}\n"
            f"<b>Referansa Kalan Fark:</b> %{diff_pct:+.3f} (Duyarlılık kritik!)\n"
            f"<b>Referans Seviye:</b> ${ref:.4f}\n\n"
            f"<i>💡 Pozisyonunuz hâlâ kazanıyor ancak son saniyelerdeki bu oynaklık riski artırıyor, önlem almanız gerekebilir!</i>"
        )
        await send_notification(msg)


async def _check_direction_flip(p, current_price):
    """
    Detect rapid direction changes for a position.
    If price was stably UP (or DOWN) vs reference for 3+ minutes
    and then flips to the other direction by at least 0.1%, alert.
    """
    global _position_flip_state
    import time
    
    if not current_price:
        return
    
    pos_id = p['id']
    ref = p['ref_price']
    diff_pct = ((current_price - ref) / ref) * 100
    now_ts = time.time()
    
    # Determine current direction relative to ref
    current_dir = 'UP' if diff_pct > 0 else 'DOWN'
    
    if pos_id not in _position_flip_state:
        _position_flip_state[pos_id] = {
            'direction': current_dir,
            'since': now_ts,
            'last_alert': 0,
            'peak_pct': diff_pct
        }
        return
    
    state = _position_flip_state[pos_id]
    
    # Same direction: just track the peak
    if current_dir == state['direction']:
        if current_dir == 'UP' and diff_pct > state.get('peak_pct', 0):
            state['peak_pct'] = diff_pct
        elif current_dir == 'DOWN' and diff_pct < state.get('peak_pct', 0):
            state['peak_pct'] = diff_pct
        return
    
    # Direction changed!
    prev_dir = state['direction']
    stable_duration = (now_ts - state['since']) / 60  # minutes
    
    # Previous direction must have been stable for at least FLIP_STABLE_MINUTES
    if stable_duration < FLIP_STABLE_MINUTES:
        state['direction'] = current_dir
        state['since'] = now_ts
        state['peak_pct'] = diff_pct
        return
    
    # Previous peak must have been meaningful (at least FLIP_MIN_PCT)
    if abs(state.get('peak_pct', 0)) < FLIP_MIN_PCT:
        state['direction'] = current_dir
        state['since'] = now_ts
        state['peak_pct'] = diff_pct
        return
    
    # New direction must be at least FLIP_MIN_PCT
    if abs(diff_pct) < FLIP_MIN_PCT:
        return  # Don't update state yet — wait for it to commit or bounce back
    
    # Cooldown check
    if now_ts - state['last_alert'] < FLIP_COOLDOWN:
        state['direction'] = current_dir
        state['since'] = now_ts
        state['peak_pct'] = diff_pct
        return
    
    # SEND ALERT!
    swing_pct = abs(diff_pct - state.get('peak_pct', 0))
    flip_emoji = "🔄📉" if current_dir == 'DOWN' else "🔄📈"
    prev_peak = state.get('peak_pct', 0)
    
    msg = (
        f"{flip_emoji} <b>HIZLI YÖN DEĞİŞİMİ: {p['symbol']}</b>\n\n"
        f"<b>Önceki:</b> {'Yukarı ↑' if prev_dir == 'UP' else 'Aşağı ↓'} (%{prev_peak:+.3f}, {stable_duration:.0f}dk boyunca)\n"
        f"<b>Şimdi:</b> {'Yukarı ↑' if current_dir == 'UP' else 'Aşağı ↓'} (%{diff_pct:+.3f})\n"
        f"<b>Anlık:</b> ${current_price:.4f}\n"
        f"<b>Referans:</b> ${ref:.4f}\n"
        f"<b>Toplam Swing:</b> %{swing_pct:.3f}\n\n"
        f"<i>💡 {prev_dir} → {current_dir} hızlı dönüş — pozisyon fırsatı!</i>"
    )
    await send_notification(msg)
    
    state['direction'] = current_dir
    state['since'] = now_ts
    state['last_alert'] = now_ts
    state['peak_pct'] = diff_pct


async def _check_premarket_movers():
    """
    Pre-market movers scanner.
    Active between 04:00-09:30 ET (11:00-16:30 TR) on weekdays.
    Scans all watchlist stocks and alerts when any moves 2%+ vs yesterday's close.
    Uses .PRE Pyth feeds where available, falls back to regular feeds.
    """
    global _last_scanner_time, _scanner_alerted
    import time
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    total_minutes = now_et.hour * 60 + now_et.minute
    
    # Only active 04:00-09:30 ET on weekdays
    if now_et.weekday() >= 5:
        return
    if not (240 <= total_minutes < 570):  # 04:00=240, 09:30=570
        # Reset alerts at end of day
        if _scanner_alerted:
            _scanner_alerted = set()
        return
    
    now_ts = time.time()
    if now_ts - _last_scanner_time < PREMARKET_SCAN_INTERVAL:
        return
    _last_scanner_time = now_ts
    
    movers = []
    
    for symbol in PREMARKET_WATCHLIST:
        try:
            pre_symbol = f"Equity.US.{symbol}/USD.PRE"
            regular_symbol = f"Equity.US.{symbol}/USD"
            
            pre_id = pyth_client.pyth_id_cache.get(pre_symbol)
            regular_id = pyth_client.pyth_id_cache.get(regular_symbol)
            active_id = regular_id or pre_id
            
            if not active_id:
                continue
            
            # Use get_active_price to benefit from Yahoo Finance real-time pre-market fallback for stocks
            current_price = await pyth_client.get_active_price(symbol, active_id)
            if not current_price:
                continue
            
            # Get yesterday's close
            pyth_id_for_history = regular_id or pre_id
            from_ts, to_ts = pyth_client.get_previous_close_times(symbol)
            ref_price = await pyth_client.get_historical_candle_price(
                regular_symbol, pyth_id_for_history, from_ts, to_ts, price_type='close'
            )
            
            if not ref_price:
                continue
            
            diff_pct = ((current_price - ref_price) / ref_price) * 100
            
            if abs(diff_pct) >= PREMARKET_ALERT_THRESHOLD:
                movers.append((symbol, current_price, ref_price, diff_pct))
                
        except Exception as e:
            logger.error(f"Error scanning {symbol} premarket: {e}")
            continue
    
    if not movers:
        return
    
    # Filter out already-alerted symbols (only alert once per day per symbol)
    new_movers = [(s, cp, rp, dp) for s, cp, rp, dp in movers if s not in _scanner_alerted]
    
    if not new_movers:
        return
    
    # Mark as alerted
    for s, _, _, _ in new_movers:
        _scanner_alerted.add(s)
    
    # Build message
    lines = ["🚀 <b>PREMARKET HAREKET TESPİT EDİLDİ!</b>\n"]
    minutes_to_open = max(0, 570 - total_minutes)
    
    for symbol, cp, rp, dp in sorted(new_movers, key=lambda x: abs(x[3]), reverse=True):
        direction = "📈" if dp > 0 else "📉"
        lines.append(
            f"{direction} <b>{symbol}</b>: ${cp:.2f} ({dp:+.2f}%)\n"
            f"   Dünkü Kapanış: ${rp:.2f}"
        )
    
    lines.append(f"\n⏰ Açılışa {minutes_to_open} dakika")
    lines.append("\n<i>💡 Polymarket'te Up pozisyonu almak için iyi fırsat olabilir!</i>")
    
    await send_notification("\n".join(lines))


async def get_historical_min_max(symbol: str, pyth_id: str, from_ts: int, to_ts: int) -> tuple[float, float]:
    """Fetches the lowest and highest price reached using Pyth history API."""
    import httpx
    _, full_symbol = pyth_client.get_pyth_id(symbol)
    url = f"{pyth_client.BENCHMARKS_URL}/shims/tradingview/history"
    params = {
        "symbol": full_symbol or symbol,
        "resolution": "60", # 1-hour resolution is lightweight and fast
        "from": from_ts,
        "to": to_ts
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("s") == "ok" and "l" in data and "h" in data:
                    lows = [float(x) for x in data["l"]]
                    highs = [float(x) for x in data["h"]]
                    if lows and highs:
                        return min(lows), max(highs)
    except Exception as e:
        logger.error(f"Error fetching historical min/max for {symbol}: {e}")
    return None, None

async def check_and_resolve_hit_bet(p, current_price) -> bool:
    """Checks if a binary hit bet (YES/NO) has met its target level during its lifetime."""
    symbol = p['symbol'].upper()
    pyth_id = p['pyth_id']
    ref = p['ref_price']
    direction = p['direction'].upper() # 'YES' or 'NO'
    
    try:
        created_dt = datetime.fromisoformat(p['created_at'])
        et_tz = pytz.timezone('US/Eastern')
        if created_dt.tzinfo is None:
            created_dt = et_tz.localize(created_dt)
        created_ts = int(created_dt.timestamp())
    except Exception as e:
        logger.error(f"Error parsing created_at for hit bet {p['id']}: {e}")
        return False
        
    import time
    current_ts = int(time.time())
    
    # 1. Fetch creation price to check if this is hit LOW or hit HIGH bet
    # If symbol is WTI, use dynamic active contract
    _, full_symbol = pyth_client.get_pyth_id(symbol)
    creation_price = await pyth_client.get_historical_candle_price(
        full_symbol or symbol, pyth_id, created_ts - 120, created_ts + 120, price_type='close'
    )
    is_low_bet = None
    title_lower = p.get('title', '').lower() if p.get('title') else ''
    if any(kw in title_lower for kw in ['(low)', 'dip', 'below', 'drop', 'under', 'down']):
        is_low_bet = True
    elif any(kw in title_lower for kw in ['(high)', 'exceed', 'above', 'rise', 'climb', 'up']):
        is_low_bet = False
        
    if is_low_bet is None:
        if not creation_price:
            creation_price = current_price
        is_low_bet = creation_price > ref
    
    # 2. Get the lowest and highest price reached since creation
    low_reached, high_reached = await get_historical_min_max(symbol, pyth_id, created_ts, current_ts)
    
    # Include current price in the min/max checks
    if low_reached is None: low_reached = current_price
    else: low_reached = min(low_reached, current_price)
    
    if high_reached is None: high_reached = current_price
    else: high_reached = max(high_reached, current_price)
    
    # 3. Check if target was hit
    has_hit = False
    if is_low_bet:
        if low_reached <= ref:
            has_hit = True
    else:
        if high_reached >= ref:
            has_hit = True
            
    # 4. Resolve the position if target was hit
    if has_hit:
        is_win = (direction == 'YES')
        result_str = "KAZANDI 🟢" if is_win else "KAYBETTİ 🔴"
        hit_price_reached = low_reached if is_low_bet else high_reached
        
        msg = (
            f"🏁 <b>BİNARY HEDEF VURULDU: {symbol} {direction}</b>\n\n"
            f"<b>Sonuç:</b> {result_str}\n"
            f"<b>Hedef Seviye:</b> ${ref:.4f}\n"
            f"<b>Görülen Uç Fiyat:</b> ${hit_price_reached:.4f}\n"
            f"<b>Açıklama:</b> Fiyat hedef seviyeye başarıyla temas etti!"
        )
        await send_notification(msg)
        await database.close_position(p['id'])
        return True
        
    return False


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
            
            # Check if market just opened (SPX UP or DOWN result)
            await _check_market_open_result()
            
            # Scan for premarket movers (2%+ moves)
            await _check_premarket_movers()
            
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
                current_price = await pyth_client.get_active_price(p['symbol'], p['pyth_id'])
                if not current_price:
                    continue

                # Check binary hit bets (YES/NO) resolution
                if p['direction'].upper() in ('YES', 'NO'):
                    resolved = await check_and_resolve_hit_bet(p, current_price)
                    if resolved:
                        continue

                # Check for rapid direction flips (UP→DOWN or DOWN→UP)
                await _check_direction_flip(p, current_price)

                # Check for sudden tail-risk reversals in the last 30 minutes of the session
                await _check_tail_risk_reversal(p, current_price)

                ref = p['ref_price']
                abs_diff_pct = abs((current_price - ref) / ref) * 100

                if abs_diff_pct <= warning_zone_pct:
                    current_step_idx = int(abs_diff_pct / step_pct)
                    current_threshold = round(current_step_idx * step_pct, 4)

                    last_warning = p['last_warning_distance']

                    if current_threshold < last_warning:
                        await database.update_warning_distance(p['id'], current_threshold)

                        direction = p['direction']
                        is_losing = not (await is_position_winning(p, current_price))

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
