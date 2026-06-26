import asyncio
import sys
import os
import pytz
from datetime import datetime, timedelta
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pyth_client

def get_previous_close_times_fixed(symbol: str) -> tuple[int, int]:
    now_utc = datetime.now(pytz.utc)
    from_dt = now_utc - timedelta(days=7)
    
    # Exclude today's candle (which starts at 00:00:00 UTC today)
    to_dt = datetime(
        now_utc.year, 
        now_utc.month, 
        now_utc.day, 
        0, 0, 0,
        tzinfo=pytz.utc
    ) - timedelta(seconds=1) # 23:59:59 yesterday UTC
    
    return int(from_dt.timestamp()), int(to_dt.timestamp())

async def main():
    await pyth_client.init_feeds_cache()
    pyth_id, full_symbol = pyth_client.get_pyth_id("TSLA")
    from_ts, to_ts = get_previous_close_times_fixed("TSLA")
    print(f"pyth_id: {pyth_id}, from_ts: {from_ts}, to_ts: {to_ts}")
    price = await pyth_client.get_historical_candle_price(
        full_symbol=full_symbol,
        pyth_id=pyth_id,
        from_ts=from_ts,
        to_ts=to_ts,
        price_type="close"
    )
    print(f"Fixed Pyth Close Price: {price}")

if __name__ == "__main__":
    asyncio.run(main())
