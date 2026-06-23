import asyncio
import os
import sys
import httpx
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
import pyth_client

async def test_symbol(symbol):
    full_symbol = pyth_client.SYMBOL_MAP.get(symbol.upper())
    if not full_symbol:
        print(f"{symbol}: No Pyth mapping")
        return
        
    from_dt = datetime.now() - timedelta(days=90)
    from_ts = int(from_dt.timestamp())
    to_ts = int(datetime.now().timestamp())
    
    url = f"{pyth_client.BENCHMARKS_URL}/shims/tradingview/history"
    params = {
        "symbol": full_symbol,
        "resolution": "60",
        "from": from_ts,
        "to": to_ts,
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            print(f"{symbol} ({full_symbol}): Status {resp.status_code}")
            if resp.status_code != 200:
                print(f"  Error body: {resp.text[:200]}")
                return
            data = resp.json()
            if data.get("s") == "ok" and "t" in data:
                print(f"  Success: {len(data['t'])} candles found.")
            else:
                print(f"  API response status: {data.get('s')}. Response keys: {list(data.keys())}")
    except Exception as e:
        print(f"{symbol} Exception: {e}")

async def main():
    symbols = ["PLTR", "TSLA", "NVDA", "AAPL", "AMZN", "META", "EWY"]
    for s in symbols:
        await test_symbol(s)
        await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
