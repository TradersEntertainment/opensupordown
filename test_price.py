import asyncio, httpx

async def test():
    async with httpx.AsyncClient() as client:
        resp = await client.get('https://hermes.pyth.network/v2/price_feeds', timeout=10)
        data = resp.json()
        
        # Check which of our tracked symbols have PRE feeds
        our_symbols = ["PLTR", "TSLA", "NVDA", "AAPL", "AMZN", "META", "GOOGL", "MSFT", 
                       "NFLX", "COIN", "HOOD", "ABNB", "RKLB", "SPY", "EWY"]
        
        pre_map = {}
        for f in data:
            sym = f.get("attributes", {}).get("symbol", "")
            fid = f.get("id", "")
            for s in our_symbols:
                if sym == f"Equity.US.{s}/USD.PRE":
                    pre_map[s] = fid
                    
        print(f"Our symbols with PRE feeds: {len(pre_map)}")
        for s, fid in sorted(pre_map.items()):
            print(f"  {s} -> {fid[:20]}...")
        
        missing = [s for s in our_symbols if s not in pre_map]
        print(f"\nMissing PRE feeds: {missing}")
        
        # Test getting current price from PLTR regular feed (should work even pre-market)
        pltr_feeds = [f for f in data if f.get("attributes", {}).get("symbol") == "Equity.US.PLTR/USD"]
        if pltr_feeds:
            pltr_id = pltr_feeds[0]["id"]
            resp2 = await client.get(
                "https://hermes.pyth.network/v2/updates/price/latest",
                params={"ids[]": pltr_id, "parsed": "true"},
                timeout=5
            )
            parsed = resp2.json().get("parsed", [])
            if parsed:
                p = parsed[0]["price"]
                price = float(p["price"]) * (10 ** int(p["expo"]))
                print(f"\nPLTR regular feed current price: ${price:.4f}")

asyncio.run(test())
