import sys
sys.path.append('backend')
import asyncio
import signal_scanner

async def main():
    # Force UTF-8 encoding for stdout if needed, or write to file
    await signal_scanner.load_historical_data()
    
    analysis = signal_scanner.analyze_reversal_risk('PLTR', 1.11, 360)
    
    with open('pltr_results.txt', 'w', encoding='utf-8') as f:
        f.write("ANALYSIS RESULTS:\n")
        f.write(str(analysis) + "\n\n")
        
        f.write("MATCHED DAYS DETAILS:\n")
        data = signal_scanner._historical_cache['PLTR']
        count = 0
        for day in data:
            for snap in day['snapshots']:
                if snap['direction'] == 'UP' and abs(snap['minutes_to_close'] - 360) <= 30:
                    snap_abs = abs(snap['diff_pct'])
                    if snap_abs >= 1.11 * 0.5:
                        count += 1
                        reversed_flag = day['final_direction'] != 'UP'
                        f.write(f"{count}. Date: {day['date']}, Snap Price: ${snap['price']:.2f}, Snap Diff: {snap['diff_pct']:.2f}%, "
                                f"Final Close: ${day['final_close']:.2f}, Final Diff: {day['final_diff_pct']:.2f}%, Reversed: {reversed_flag}\n")

if __name__ == '__main__':
    asyncio.run(main())
