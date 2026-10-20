import requests, json, os, pytz, pandas as pd, numpy as np
import mplfinance as mpf
from scipy.signal import argrelextrema
from datetime import datetime
from psycopg2.extras import RealDictCursor
from modules.config_loader import CONFIG
from modules.database import get_conn, release_conn

def get_now(): return datetime.now(pytz.timezone(CONFIG['system']['timezone']))
def format_price(value): return "{:.8f}".format(float(value)).rstrip('0').rstrip('.') if float(value) < 1 else "{:.2f}".format(float(value))

def generate_chart(df, symbol, pattern, timeframe):
    filename = f"chart_{symbol.replace('/','')}_{timeframe}.png"
    try:
        plot_df = df.iloc[-100:].copy()
        if 'timestamp' in plot_df.columns: plot_df.set_index('timestamp', inplace=True)
        plot_df.index = pd.to_datetime(plot_df.index)

        n=3
        min_idx = argrelextrema(plot_df['low'].values, np.less_equal, order=n)[0]
        max_idx = argrelextrema(plot_df['high'].values, np.greater_equal, order=n)[0]
        
        peak_dates, peak_vals = plot_df.index[max_idx], plot_df['high'].iloc[max_idx].values
        valley_dates, valley_vals = plot_df.index[min_idx], plot_df['low'].iloc[min_idx].values
        
        lines, colors = [], []
        def add_line(dates, vals, color):
            if len(dates) >= 2: lines.append([(str(dates[-2]), float(vals[-2])), (str(dates[-1]), float(vals[-1]))]); colors.append(color)

        if pattern in ['ascending_triangle', 'bullish_rectangle', 'double_top', 'bear_flag', 'descending_triangle']: add_line(peak_dates, peak_vals, 'red')
        if pattern in ['descending_triangle', 'bullish_rectangle', 'double_bottom', 'bull_flag', 'ascending_triangle']: add_line(valley_dates, valley_vals, 'green')

        mc = mpf.make_marketcolors(up='#2ebd85', down='#f6465d', edge='inherit', wick='inherit', volume='in')
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc)
        apds = []
        if 'EMA_Fast' in plot_df.columns: apds.append(mpf.make_addplot(plot_df['EMA_Fast'], color='cyan', width=1))
        
        ratios, vol_panel = (3, 1), 1
        if 'MACD_h' in plot_df.columns:
            cols = ['#2ebd85' if v >= 0 else '#f6465d' for v in plot_df['MACD_h']]
            apds.append(mpf.make_addplot(plot_df['MACD_h'], type='bar', panel=1, color=cols, ylabel='MACD'))
            ratios, vol_panel = (3, 1, 1), 2

        kwargs = dict(type='candle', style=s, addplot=apds, title=f"\n{symbol} ({timeframe}) - {pattern}", figsize=(12, 8), panel_ratios=ratios, volume=True, volume_panel=vol_panel, savefig=dict(fname=filename, dpi=100, bbox_inches='tight'))
        if lines: kwargs['alines'] = dict(alines=lines, colors=colors, linewidths=1.5, alpha=0.7)
        mpf.plot(plot_df, **kwargs)
        return filename
    except Exception as e: print(f"Chart Error: {e}"); return None

def send_alert(data):
    token = CONFIG['api'].get('telegram_bot_token')
    chat_id = CONFIG['api'].get('telegram_chat_id')
    if not token or not chat_id: return False
    
    symbol = data['Symbol']
    
    # 1. Generate Chart
    image_path = None
    try: image_path = generate_chart(data['df'], symbol, data['Pattern'], data['Timeframe'])
    except: pass

    # 2. Prepare Data
    is_long = data['Side'] == 'Long'
    emoji = "🚀" if is_long else "🔻"
    
    fund_rate = data['df'].get('funding', pd.Series([0])).iloc[-1]
    if isinstance(fund_rate, pd.Series): fund_rate = fund_rate.iloc[-1]
    fund_pct = fund_rate * 100
    
    smc_reasons_str = str(data.get('SMC_Reasons', ''))
    
    # Text Formatting (HTML for Telegram)
    text = f"<b>{emoji} SIGNAL: {symbol} ({data['Pattern']})</b>\n"
    text += f"<b>{data['Side']}</b> | <b>{data['Timeframe']}</b>\n\n"
    
    text += f"🎯 <b>Entry:</b> <code>{format_price(data['Entry'])}</code>\n"
    text += f"🛑 <b>Stop:</b> <code>{format_price(data['SL'])}</code>\n"
    text += f"💰 <b>RR:</b> <code>1:{data.get('RR', 0.0)}</code>\n\n"
    
    text += f"🏁 <b>Targets:</b>\n"
    text += f"TP1: <code>{format_price(data['TP1'])}</code>\n"
    text += f"TP2: <code>{format_price(data['TP2'])}</code>\n"
    text += f"TP3: <code>{format_price(data['TP3'])}</code>\n\n"
    
    text += f"📊 <b>Scores:</b> Tech {data['Tech_Score']} | SMC {data['SMC_Score']} | Quant {data['Quant_Score']} | Deriv {data['Deriv_Score']}\n"
    text += f"🧠 <b>Bias:</b> {data['BTC_Bias']}\n"
    
    try:
        if image_path:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(image_path, 'rb') as f:
                r = requests.post(url, data={'chat_id': chat_id, 'caption': text, 'parse_mode': 'HTML'}, files={'photo': f})
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            r = requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'})
            
        if r.status_code == 200:
            msg_id = r.json().get('result', {}).get('message_id')
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trades (symbol, side, timeframe, pattern, entry_price, sl_price, tp1, tp2, tp3, reason, 
                tech_score, quant_score, deriv_score, smc_score, basis, btc_bias, z_score, zeta_score, obi, 
                tech_reasons, quant_reasons, deriv_reasons, smc_reasons, message_id, channel_id, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Waiting Entry')
            """, (symbol, data['Side'], data['Timeframe'], data['Pattern'], data['Entry'], data['SL'], data['TP1'], 
                  data['TP2'], data['TP3'], data['Reason'], data['Tech_Score'], data['Quant_Score'], data['Deriv_Score'], 
                  data['SMC_Score'], data['Basis'], data['BTC_Bias'], data['Z_Score'], data['Zeta_Score'], data['OBI'], 
                  data.get('Tech_Reasons',''), data.get('Quant_Reasons',''), data.get('Deriv_Reasons',''), smc_reasons_str,
                  str(msg_id), str(chat_id)))
            conn.commit()
            release_conn(conn)
            return True
            
    except Exception as e:
        print(f"Telegram Alert Error: {e}")
        return False
    finally:
        if image_path and os.path.exists(image_path): os.remove(image_path)

def update_status_dashboard():
    token = CONFIG['api'].get('telegram_bot_token')
    chat_id = CONFIG['api'].get('telegram_chat_id')
    if not token or not chat_id: return
    
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT symbol, side, status, entry_hit_at, created_at FROM trades WHERE status NOT LIKE '%%Closed%%' ORDER BY created_at DESC")
        trades = cur.fetchall()
        lines = [f"<code>{(t['entry_hit_at'] or t['created_at']).strftime('%H:%M')}</code> {'🟢' if 'Active' in t['status'] else '⏳'} <b>{t['symbol']}</b> ({t['side']}): {t['status']}" for t in trades]
        content = "<b>📊 LIVE DASHBOARD</b>\n" + ("\n".join(lines) if lines else "No active trades.")
        
        cur.execute("SELECT value_text FROM bot_state WHERE key_name = 'telegram_dashboard_msg_id'")
        row = cur.fetchone()
        msg_id = row[0] if row else None
        
        if msg_id:
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            requests.post(url, json={'chat_id': chat_id, 'message_id': int(msg_id), 'text': content, 'parse_mode': 'HTML'})
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            r = requests.post(url, json={'chat_id': chat_id, 'text': content, 'parse_mode': 'HTML'})
            if r.status_code == 200:
                new_id = r.json().get('result', {}).get('message_id')
                cur.execute("INSERT INTO bot_state (key_name, value_text) VALUES ('telegram_dashboard_msg_id', %s) ON CONFLICT (key_name) DO UPDATE SET value_text = EXCLUDED.value_text", (str(new_id),))
                conn.commit()
    except Exception as e: pass
    finally: release_conn(conn)

def send_scan_completion(count, duration, bias):
    token = CONFIG['api'].get('telegram_bot_token')
    chat_id = CONFIG['api'].get('telegram_chat_id')
    if not token or not chat_id: return
    text = f"🔭 <b>Scan Cycle Complete</b>\n⏱️ Duration: <code>{duration:.2f}s</code>\n📶 Signals: <code>{count}</code>\n📊 Bias: <b>{bias}</b>"
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'})
    except: pass

def run_fast_update():
    update_status_dashboard()
