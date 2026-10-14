# Scans Nifty 200 on the 1H chart during NSE market hours and sends a Telegram alert when ALL match:
#   EMA20 crosses above EMA50  +  price above EMA200  +  ADX > 20  +  volume > 1.5x average
# Exit plan (from the backtest): target +10%, stoploss -5%, or after 10 trading days.
import os, sys, json, time, urllib.request, urllib.parse
import numpy as np, pandas as pd
import strategy_lab as s
import nifty50_supertrend_backtest as b

UNIVERSE = "nifty200"
TARGET, STOP, MAX_DAYS = 0.10, 0.05, 10
FRESH_MIN = 55                      # signal candle must have closed within the last 55 minutes
STATE = "alerted.json"              # remembers sent alerts so the same signal is never sent twice
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TZ = "Asia/Kolkata"

def send(text):
    if not TOKEN or not CHAT:
        print("Telegram secrets missing, message not sent:\n" + text); return
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=30)
    except Exception as e:
        print("Telegram error:", e)

def candle_end(t):
    close = t.normalize() + pd.Timedelta(hours=15, minutes=30)
    return min(t + pd.Timedelta(hours=1), close)

def in_market_hours(now):
    """Mon-Fri, 9:30 to 15:40 IST."""
    mins = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 30 <= mins <= 15 * 60 + 40

def market_open_today(now, index_df):
    """NSE holidays need no list: if the Nifty index has no candle dated today, the market is closed."""
    if index_df is None or len(index_df) == 0: return False
    return index_df.index[-1].date() == now.date()

def analyze(df, now):
    """Return a dict if the latest COMPLETE candle matches every condition, else None."""
    live_price = float(df.Close.iloc[-1])                         # latest price, even from the forming candle
    df = df[[candle_end(t) <= now for t in df.index]]             # drop the candle still forming
    if len(df) < 250: return None
    end = candle_end(df.index[-1])
    if now - end > pd.Timedelta(minutes=FRESH_MIN): return None   # stale data
    c = df.Close
    e20, e50, e200 = (c.ewm(span=x, adjust=False).mean() for x in (20, 50, 200))
    adx = s.adx(df)
    vx = df.Volume / df.Volume.rolling(20).mean()
    cross = e20.iloc[-1] > e50.iloc[-1] and e20.iloc[-2] <= e50.iloc[-2]
    if cross and c.iloc[-1] > e200.iloc[-1] and adx.iloc[-1] > 20 and vx.iloc[-1] > 1.5:
        return dict(entry=live_price, adx=float(adx.iloc[-1]), vx=float(vx.iloc[-1]), end=end)
    return None

def load_state(now):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    cutoff = (now - pd.Timedelta(days=3)).isoformat()
    return {k: v for k, v in st.items() if v >= cutoff}

def fetch(tickers):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 40):
        chunk = [t + ".NS" for t in tickers[i:i + 40]]
        try:
            raw = yf.download(chunk, period="90d", interval="1h", auto_adjust=True,
                              group_by="ticker", progress=False, threads=True)
            for t in chunk:
                try:
                    d = raw[t].dropna()
                    if len(d): out[t[:-3]] = d
                except Exception: pass
        except Exception as e:
            print("download error", e)
        time.sleep(1)
    return out

def fmt(sym, r, now):
    p = r["entry"]
    exit_by = (now.normalize() + pd.offsets.BDay(MAX_DAYS)).strftime("%d %b")
    return (f"BUY SIGNAL (1H): {sym}\n"
            f"Entry: {p:.2f} (buy near this price)\n"
            f"Target (+10%): {p*(1+TARGET):.2f}\n"
            f"Stoploss (-5%): {p*(1-STOP):.2f}\n"
            f"Exit by about {exit_by} if neither is hit\n"
            f"Why: EMA20 crossed above EMA50 (candle closed {r['end'].strftime('%H:%M')}), "
            f"ADX {r['adx']:.0f}, volume {r['vx']:.1f}x, above EMA200")

if __name__ == "__main__":
    import yfinance as yf
    if os.environ.get("TEST_ONLY", "").lower() == "true":
        send("Test OK: your alert bot is connected to Telegram."); sys.exit()
    now = pd.Timestamp.now(tz=TZ)
    if not in_market_hours(now):
        print("Outside market hours, nothing to do:", now); sys.exit()
    idx = yf.download("^NSEI", period="5d", interval="1h", progress=False)
    if idx is not None and len(idx) and idx.index.tz is None: idx.index = idx.index.tz_localize(TZ)
    if not market_open_today(now, idx):
        print("Market closed today (holiday), nothing to do:", now); sys.exit()

    data = fetch(b.get_universe(UNIVERSE))
    print("Stocks loaded:", len(data), "| time:", now)
    state, lines = load_state(now), []
    for sym, df in data.items():
        if df.index.tz is None: df.index = df.index.tz_localize(TZ)
        r = analyze(df, now)
        if r:
            key = f"{sym}|{r['end'].strftime('%Y-%m-%d %H:%M')}"
            if key in state: continue                              # already alerted
            state[key] = now.isoformat()
            lines.append(fmt(sym, r, now))
    json.dump(state, open(STATE, "w"))
    print("New signals:", len(lines))
    for i in range(0, len(lines), 4):
        send("\n\n".join(lines[i:i + 4]) + "\n\nBacktest: ~48% wins, ~+0.7% per trade. Not guaranteed. Not financial advice.")
