import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
import pytz
import pyth_client
import asyncio

async def test_rollovers():
    et_tz = pytz.timezone('US/Eastern')
    
    # Let's initialize cache first so WTI symbols are resolved
    await pyth_client.init_feeds_cache()
    
    test_cases = [
        # In May 2026: WTIM6 LTD is May 20th. Rollover starts May 17th at 6:00 PM ET.
        datetime(2026, 5, 10, 12, 0, tzinfo=et_tz), # Should be June delivery contract (WTIM6)
        datetime(2026, 5, 17, 17, 59, tzinfo=et_tz), # Still WTIM6
        datetime(2026, 5, 17, 18, 1, tzinfo=et_tz), # Rolled to WTIN6 (July contract)
        datetime(2026, 5, 25, 12, 0, tzinfo=et_tz), # Should be WTIN6
        
        # In June 2026: WTIN6 LTD is June 22nd. Rollover starts June 17th at 6:00 PM ET.
        datetime(2026, 6, 10, 12, 0, tzinfo=et_tz), # WTIN6
        datetime(2026, 6, 17, 17, 59, tzinfo=et_tz), # WTIN6
        datetime(2026, 6, 17, 18, 1, tzinfo=et_tz), # Rolled to WTIQ6 (August contract)
        datetime(2026, 6, 25, 12, 0, tzinfo=et_tz), # WTIQ6
    ]
    
    print("--- TESTING CME WTI CONTRACT RESOLUTIONS ---")
    for tc in test_cases:
        res = pyth_client.get_wti_active_contract(tc)
        print(f"Time: {tc.strftime('%Y-%m-%d %H:%M %Z')} -> Active Contract: {res}")
        
    print("\n--- FETCHING LIVE REAL-TIME PRICES ---")
    # Resolve dynamic active contract ID for WTI right now
    now_et = datetime.now(et_tz)
    wti_symbol = pyth_client.get_wti_active_contract(now_et)
    wti_id, resolved_symbol = pyth_client.get_pyth_id("WTI")
    
    print(f"Current Time (ET): {now_et.strftime('%Y-%m-%d %H:%M')}")
    print(f"Dynamic Active WTI Symbol: {wti_symbol}")
    print(f"Resolved Feed Symbol in Cache: {resolved_symbol}")
    print(f"Resolved Feed ID: {wti_id}")
    
    if wti_id:
        price = await pyth_client.get_latest_price(wti_id)
        print(f"Fetched live price: ${price:.2f}")
    else:
        print("Could not resolve Pyth ID for dynamic active contract. Cache is probably not initialized or symbol is not in Pyth database.")

if __name__ == "__main__":
    asyncio.run(test_rollovers())
