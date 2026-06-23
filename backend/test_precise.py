import asyncio
import sys
import os
import pytz
from datetime import datetime, timedelta
import httpx
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pyth_client

async def test_precise_close():
    full_symbol = "Equity.US.TSLA/USD"
    BENCHMARKS_URL = "https://benchmarks.pyth.network/v1"
    
    from_ts, to_ts = pyth_client.get_previous_close_times(full_symbol)
    
    url = f"{BENCHMARKS_URL}/shims/tradingview/history"
    params = {
        "symbol": full_symbol,
        "resolution": "D",
        "from": from_ts,
        "to": to_ts
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        
        last_valid_day_ts = data['t'][-1]
        last_day_utc = datetime.fromtimestamp(last_valid_day_ts, tz=pytz.utc)
        trading_date = last_day_utc.date()
        
        et_tz = pytz.timezone('US/Eastern')
        close_dt_et = et_tz.localize(datetime(
            trading_date.year,
            trading_date.month,
            trading_date.day,
            16, 0, 0
        ))
        
        close_start_ts = int((close_dt_et - timedelta(minutes=10)).timestamp())
        close_end_ts = int(close_dt_et.timestamp())
        
        print(f"1m window: {close_dt_et} -> start: {close_start_ts}, end: {close_end_ts}")
        
        url_1m = f"{BENCHMARKS_URL}/shims/tradingview/history"
        params_1m = {
            "symbol": full_symbol,
            "resolution": "1",
            "from": close_start_ts,
            "to": close_end_ts
        }
        
        resp_1m = await client.get(url_1m, params=params_1m)
        data_1m = resp_1m.json()
        if data_1m.get("s") == "ok" and "c" in data_1m:
            print("PRECISE CLOSE:", data_1m["c"][-1])

if __name__ == "__main__":
    asyncio.run(test_precise_close())
