#!/usr/bin/env python3
"""
Scalping Bot V4: Close on ANY profit, open NEW trade, repeat forever
Handles multiple lots and continuous scalping
"""
import time, os

POS = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_positions.csv"
CMD = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_commands.csv"
CID = 7000

def get_pos():
    if not os.path.exists(POS): return []
    with open(POS) as f: lines = f.readlines()[1:]
    r = []
    for l in lines:
        p = l.strip().split(',')
        if len(p) >= 8:
            try: r.append({'t':p[1],'s':p[2],'f':float(p[7])})
            except: pass
    return r

def close_pos(t,s,p):
    global CID
    CID += 1
    with open(CMD,'a') as f: f.write(f"{CID},CLOSE,{s},CLOSE,0.01,0,0,20,AC-{t}\n")
    print(f"[CLOSE] {s} t={t} profit=${p:.2f}")

def open_trade(sym):
    global CID
    CID += 1
    with open(CMD,'a') as f: f.write(f"{CID},MARKET,{sym},BUY,0.01,4510.00,4555.00,20,AO-{CID}\n")
    print(f"[OPEN] {sym}")

while True:
    try:
        pos = get_pos()
        if pos:
            for p in pos:
                if p['f'] > 0:  # ANY profit!
                    close_pos(p['t'], p['s'], p['f'])
                    time.sleep(3)
                    open_trade("XAUUSD")
                    time.sleep(3)
        else:
            open_trade("XAUUSD")
            time.sleep(5)
        time.sleep(2)
    except Exception as e:
        print(f"[ERR] {e}")
        time.sleep(2)