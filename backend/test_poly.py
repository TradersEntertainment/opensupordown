import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import signal_scanner

async def main():
    print("Fetching polymarket events...")
    data = await signal_scanner.fetch_polymarket_events()
    for symbol, info in data.items():
        print(f"{symbol}: Up {info['up_price']} / Down {info['down_price']}")
    print(f"Total mapped symbols: {len(data)}")

if __name__ == "__main__":
    asyncio.run(main())
