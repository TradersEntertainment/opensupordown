import asyncio
import httpx

async def test():
    url = "https://benchmarks.pyth.network/v1/shims/tradingview/history"
    params = {
        "symbol": "Equity.US.SPY/USD",
        "resolution": "D",
        "from": 1781899140,
        "to": 1781899199
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        print("D resolution:", resp.json())
        
        params["resolution"] = "1"
        resp2 = await client.get(url, params=params)
        print("1 resolution:", resp2.json())

if __name__ == "__main__":
    asyncio.run(test())
