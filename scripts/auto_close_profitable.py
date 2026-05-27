#!/usr/bin/env python3
"""
Auto-Close + Auto-Open Script for Scalping
- Checks every 3 seconds for profitable positions to close
- Opens new positions when cooldown finished
"""
import time
import os
import sys

POSITIONS_FILE = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_positions.csv"
COMMANDS_FILE = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_commands.csv"
STATUS_FILE = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_status.json"
MIN_PROFIT = 0.10  # Close if profit > $0.10
RETRY_INTERVAL = 3

command_id = 300

def log(msg):
    print(f"[AUTO] {msg}")
    sys.stdout.flush()

def read_positions():
    positions = []
    if not os.path.exists(POSITIONS_FILE):
        return positions
    with open(POSITIONS_FILE, 'r') as f:
        lines = f.readlines()[1:]
        for line in lines:
            parts = line.strip().split(',')
            if len(parts) >= 8:
                try:
                    positions.append({
                        'ticket': parts[1],
                        'symbol': parts[2],
                        'profit': float(parts[7])
                    })
                except:
                    pass
    return positions

def read_status():
    if not os.path.exists(STATUS_FILE):
        return {}
    with open(STATUS_FILE, 'r') as f:
        return eval(f.read())

def close_position(ticket, symbol, profit):
    global command_id
    command_id += 1
    with open(COMMANDS_FILE, 'a') as f:
        f.write(f"{command_id},CLOSE,{symbol},CLOSE,0.01,0,0,20,AutoClose-{ticket}\n")
    log(f"CLOSED: {symbol} ticket={ticket} profit=${profit:.2f}")

def open_position(symbol):
    global command_id
    command_id += 1
    with open(COMMANDS_FILE, 'a') as f:
        f.write(f"{command_id},MARKET,{symbol},BUY,0.01,4515.00,4545.00,20,AutoOpen\n")
    log(f"OPENED: {symbol}")

def main():
    log("🚀 Auto-Close+Open Script STARTED (threshold: $0.10)")
    
    while True:
        try:
            # Check positions for closing
            positions = read_positions()
            for pos in positions:
                if pos['profit'] > MIN_PROFIT:
                    close_position(pos['ticket'], pos['symbol'], pos['profit'])
                    time.sleep(1)
            
            # Check if new positions should be opened
            status = read_status()
            
            if status.get('positions', 0) == 0:
                xau_signal = "cooldown"
                for s in status.get('symbols', []):
                    if s.get('symbol') == 'XAUUSD':
                        xau_signal = s.get('last_signal', 'no_signal')
                        break
                
                if "cooldown" not in xau_signal:
                    log(f"Opening new XAUUSD (signal: {xau_signal})")
                    open_position("XAUUSD")
                    time.sleep(5)
            
            time.sleep(RETRY_INTERVAL)
            
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(RETRY_INTERVAL)

if __name__ == "__main__":
    main()