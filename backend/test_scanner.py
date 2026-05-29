import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import signal_scanner

async def test_search_and_parse():
    print("--- TESTING PUBLIC-SEARCH & PARSING ---")
    
    # Test question parser directly
    test_questions = [
        "Will WTI Crude Oil hit $80 in May 2026?",
        "Will Palantir close above $22 on June 18?",
        "Will Gold hit $3,500 in week 22?",
        "Will Tesla close below $150.50?",
        "Will HSI exceed 20000?",
        "Will Airbnb touch $120.00?"
    ]
    
    print("\n1. Testing parse_market_question:")
    for q in test_questions:
        sym, threshold, m_type = signal_scanner.parse_market_question(q)
        print(f"Question: '{q}' -> parsed: Symbol={sym}, Threshold={threshold}, Type={m_type}")
        
    print("\n2. Testing fetch_active_binary_markets from Gamma API:")
    markets = await signal_scanner.fetch_active_binary_markets()
    print(f"Successfully fetched {len(markets)} active binary markets!")
    
    # Print first 5 resolved markets
    print("\n3. First 5 resolved markets in search results:")
    count = 0
    for m in markets:
        question = m.get("question", "")
        sym, threshold, m_type = signal_scanner.parse_market_question(question)
        if sym and threshold is not None:
            print(f"- Question: '{question}'\n  Parsed -> Symbol={sym}, Threshold={threshold}, Type={m_type}")
            print(f"  Prices: {m.get('outcomes')} -> {m.get('outcomePrices')}")
            print(f"  End Date: {m.get('endDateIso') or m.get('endDate')}")
            count += 1
            if count >= 5:
                break
                
    if count == 0:
        print("No active markets could be parsed from current search results. Maybe watchlist symbols do not match current active binary markets.")

if __name__ == "__main__":
    asyncio.run(test_search_and_parse())
