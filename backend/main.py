from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
from datetime import datetime
import database
import pyth_client
import tracker_engine
import telegram_bot
import poly_tracker
import signal_scanner

app = FastAPI(title="Poly Up/Down Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reference to the telegram application to start/stop it properly
tg_app = None

@app.on_event("startup")
async def startup_event():
    await database.init_db()
    await pyth_client.init_feeds_cache()
    
    # Start tracker loop
    tracker_engine.start_background_task()
    
    # Start Polymarket auto position sync
    poly_tracker.start_sync_task()
    
    # Start Finance Signal Scanner
    signal_scanner.start_signal_scanner()
    
    # Start Telegram bot
    global tg_app
    tg_app = telegram_bot.setup_application()
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()

@app.on_event("shutdown")
async def shutdown_event():
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

# --- API Endpoints for Dashboard ---

class SettingsUpdate(BaseModel):
    warning_zone_pct: float
    step_pct: float

@app.get("/api/settings")
async def get_settings():
    return await database.get_settings()

@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    await database.update_settings(req.warning_zone_pct, req.step_pct)
    return {"message": "Ayarlar güncellendi."}

@app.get("/api/positions")
async def get_positions():
    positions = await database.get_active_positions()
    
    # Inject current prices for the dashboard
    for p in positions:
        current_price = await pyth_client.get_active_price(p['symbol'], p['pyth_id'])
        p['current_price'] = current_price
        
        if current_price:
            ref = p['ref_price']
            p['diff_pct'] = ((current_price - ref) / ref) * 100
            is_up_bet = 'UP' in p['direction'] or 'YES' in p['direction']
            p['is_winning'] = (is_up_bet and current_price > ref) or (not is_up_bet and current_price < ref)
            
    return positions

class PositionCreate(BaseModel):
    symbol: str
    direction: str
    bet_type: str

@app.post("/api/positions")
async def create_position(req: PositionCreate):
    symbol_input = req.symbol.upper()
    direction = req.direction.upper()
    bet_type = req.bet_type.lower()
    
    # Resolve Pyth ID
    pyth_id, full_symbol = pyth_client.get_pyth_id(symbol_input)
    if not pyth_id:
        raise HTTPException(status_code=400, detail=f"'{symbol_input}' Pyth sisteminde bulunamadı. Lütfen tam sembolü kontrol edin.")
        
    # Get reference price
    if bet_type == 'close':
        from_ts, to_ts = pyth_client.get_previous_close_times(symbol_input)
        ref_price = await pyth_client.get_historical_candle_price(full_symbol, pyth_id, from_ts, to_ts, price_type='close')
        time_desc = "Dünkü 15:59 ET Kapanış"
    else:
        from_ts, to_ts = pyth_client.get_previous_open_times(symbol_input)
        ref_price = await pyth_client.get_historical_candle_price(full_symbol, pyth_id, from_ts, to_ts, price_type='open')
        time_desc = "Bugünkü 09:30 ET Açılış"
        
    if ref_price is None:
        raise HTTPException(status_code=400, detail=f"{full_symbol} için {time_desc} mumu Pyth'den çekilemedi! Piyasa kapalı olabilir veya henüz veri işlenmemiş olabilir.")
        
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
        f"🎯 <b>Web'den Pozisyon Eklendi: {symbol_input} {db_direction}</b>\n\n"
        f"<b>Anlık Fiyat:</b> {current_price_str}\n"
        f"<b>Fark:</b> %{diff_pct:.2f} {status_icon}\n"
        f"<b>Referans ({time_desc}):</b> ${ref_price:.4f}"
    )
    
    if tg_app:
        await telegram_bot.send_notification(msg)
        
    return {"message": "Pozisyon eklendi."}

@app.delete("/api/positions/{position_id}")
async def delete_position(position_id: int):
    await database.delete_position(position_id)
    return {"message": "Pozisyon silindi."}


@app.get("/api/scan-now")
async def run_scan_now():
    try:
        results = await signal_scanner.run_manual_scan()
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

