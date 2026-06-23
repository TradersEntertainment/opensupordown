import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pyth_client

async def main():
    from_ts, to_ts = pyth_client.get_previous_close_times("TSLA")
    print(f"from_ts: {from_ts}, to_ts: {to_ts}")
    price = await pyth_client.get_historical_candle_price(
        full_symbol="Equity.US.TSLA/USD",
        pyth_id="Equity.US.TSLA/USD",
        from_ts=from_ts,
        to_ts=to_ts,
        price_type="close"
    )
    print(f"Pyth Close Price: {price}")

if __name__ == "__main__":
    asyncio.run(main())
