import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import signal_scanner
import pyth_client

async def main():
    await pyth_client.init_feeds_cache()
    results = await signal_scanner.run_manual_scan()
    print(f"Results count: {len(results)}")
    for r in results:
        print(r['symbol'], r.get('poly', {}).get('up_price'))

if __name__ == "__main__":
    asyncio.run(main())
