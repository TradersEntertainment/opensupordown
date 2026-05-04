from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import database
import pyth_client
import tracker_engine
import telegram_bot

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
        current_price = await pyth_client.get_latest_price(p['pyth_id'])
        p['current_price'] = current_price
        
        if current_price:
            ref = p['ref_price']
            p['diff_pct'] = ((current_price - ref) / ref) * 100
            p['is_winning'] = (p['direction'] == 'UP' and current_price > ref) or (p['direction'] == 'DOWN' and current_price < ref)
            
    return positions

@app.delete("/api/positions/{position_id}")
async def delete_position(position_id: int):
    await database.delete_position(position_id)
    return {"message": "Pozisyon silindi."}
