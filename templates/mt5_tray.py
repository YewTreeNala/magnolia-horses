"""
MT5 Trade Tray App — Complete Edition
=======================================
Features:
- Places trades via /go endpoint
- Sleep prevention
- Test mode (0.01 lots)
- Hourly heartbeat via Telegram monitor
- Last signal display at /last_signal
- Open positions at /positions
- Results page at /results
- Tray menu: heartbeat, health, positions, log, quit
"""

import threading
import math
import winsound
import logging
import os
import webbrowser
import ctypes
import requests
from datetime import datetime
from flask import Flask, request, jsonify, Response
import MetaTrader5 as mt5
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

# ── Config ────────────────────────────────────────────────────────────────────
MT5_PATH  = r'C:\Program Files\STARTRADER Financial MetaTrader 5\terminal64.exe'
LOG_FILE  = r'C:\Users\marke\MT5Service\mt5_tray.log'
PORT      = 5000

POINT_VALUES = {
    'GER40.z':  1.1108,
    'XAUUSD':  98.81,
    'NAS100.z': 1.00,
}

RISK_PCT  = 0.20
TEST_MODE = False

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
last_result = {}
last_signal = {'message': 'No signals received yet.', 'parsed': None, 'time': None}
last_channel_message = {'text': 'No messages received yet.', 'time': None}
poll_status = {'poll_num': 0, 'last_id': 0, 'errors': 0, 'time': None}
tray_icon = None

app = Flask(__name__)


def floor_lots(x):
    return math.floor(x * 100) / 100


def get_mt5():
    if not mt5.initialize(path=MT5_PATH):
        raise RuntimeError(f'MT5 not ready: {mt5.last_error()}')
    info = mt5.account_info()
    if info is None:
        mt5.shutdown()
        raise RuntimeError('MT5 not logged in')
    return info


# ── HTML helpers ──────────────────────────────────────────────────────────────
def results_html(data):
    ts = datetime.now().strftime('%d %b %Y %H:%M:%S')
    if 'error' in data:
        body = f'<div style="background:#fff0f0;border-radius:8px;padding:16px"><h2 style="color:#c00;margin:0 0 8px;font-size:15px">Trade failed</h2><p style="font-size:13px">{data["error"]}</p></div>'
    else:
        placed = data.get('trades_placed', 0)
        failed = data.get('trades_failed', 0)
        sc = '#1a9e5c' if placed == 4 else '#e04040'
        tm = ' (TEST MODE)' if data.get('test_mode') else ''
        st = f'{placed}/4 trades placed{tm}'
        rows = ''
        for r in data.get('results', []):
            ic = '&#10003;' if r['status'] == 'ok' else '&#10007;'
            cl = '#1a9e5c' if r['status'] == 'ok' else '#e04040'
            nt = f"Order: {r.get('order','')}" if r['status'] == 'ok' else r.get('comment', '')
            rows += f'<tr><td>Trade {r["trade"]} TP{r["trade"]}</td><td>{data.get("lots","")}</td><td>{r.get("tp","")}</td><td style="color:{cl}">{ic} {r["status"]}</td><td>{nt}</td></tr>'
        body = f'''
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Symbol</div><div style="font-size:17px;font-weight:500">{data.get("symbol","")}</div></div>
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Entry price</div><div style="font-size:17px;font-weight:500">{data.get("entry_price","")}</div></div>
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Lots each</div><div style="font-size:17px;font-weight:500">{data.get("lots","")}</div></div>
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Stop loss</div><div style="font-size:17px;font-weight:500;color:#e04040">{data.get("sl","")}</div></div>
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Max downside</div><div style="font-size:17px;font-weight:500;color:#e04040">-£{data.get("max_downside","")}</div></div>
            <div style="background:#f8f8f8;border-radius:8px;padding:10px"><div style="font-size:11px;color:#888">Direction</div><div style="font-size:17px;font-weight:500">{data.get("direction","")}</div></div>
        </div>
        <div style="border-radius:8px;padding:10px 16px;color:white;font-weight:500;font-size:14px;margin-bottom:12px;background:{sc}">{st}</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr><th style="text-align:left;padding:0 8px 8px;font-size:11px;color:#888">Trade</th><th style="text-align:left;padding:0 8px 8px;font-size:11px;color:#888">Lots</th><th style="text-align:left;padding:0 8px 8px;font-size:11px;color:#888">TP</th><th style="text-align:left;padding:0 8px 8px;font-size:11px;color:#888">Status</th><th style="text-align:left;padding:0 8px 8px;font-size:11px;color:#888">Note</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>'''

    open_rows = ''
    try:
        get_mt5()
        positions = mt5.positions_get()
        mt5.shutdown()
        if positions:
            for p in positions:
                pc = '#1a9e5c' if p.profit >= 0 else '#e04040'
                dr = 'SELL' if p.type == 1 else 'BUY'
                open_rows += f'<tr><td>{p.symbol}</td><td>{dr}</td><td>{p.volume}</td><td>{p.price_open}</td><td>{p.sl}</td><td>{p.tp}</td><td style="color:{pc};font-weight:500">£{p.profit:.2f}</td></tr>'
        else:
            open_rows = '<tr><td colspan="7" style="text-align:center;color:#888">No open positions</td></tr>'
    except Exception as e:
        open_rows = f'<tr><td colspan="7">Could not fetch: {e}</td></tr>'

    return f'''<!DOCTYPE html><html><head><title>MT5 Results</title><meta charset="utf-8">
<style>body{{font-family:-apple-system,Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;color:#222}}
h1{{font-size:20px;font-weight:500;margin:0 0 4px}}.ts{{font-size:12px;color:#888;margin-bottom:20px}}
.card{{background:white;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8e8e8}}
.card h2{{font-size:14px;font-weight:500;color:#555;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-size:11px;color:#888;padding:0 8px 8px;border-bottom:1px solid #eee}}
td{{padding:10px 8px;border-bottom:1px solid #f0f0f0}}
.ref{{font-size:12px;color:#888;margin-top:8px}}</style></head>
<body><h1>MT5 Trade Execution</h1><p class="ts">{ts}</p>
<div class="card"><h2>Execution result</h2>{body}</div>
<div class="card"><h2>Open positions</h2>
<table><thead><tr><th>Symbol</th><th>Dir</th><th>Lots</th><th>Entry</th><th>SL</th><th>TP</th><th>P&L</th></tr></thead>
<tbody>{open_rows}</tbody></table>
<p class="ref"><a href="/positions">Refresh</a></p></div></body></html>'''


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    try:
        info = get_mt5()
        mt5.shutdown()
        return jsonify({'status': 'ok', 'account': info.login, 'balance': round(info.balance, 2), 'equity': round(info.equity, 2), 'free_margin': round(info.margin_free, 2)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/positions', methods=['GET'])
def positions():
    return Response(results_html({}), mimetype='text/html')


@app.route('/results', methods=['GET'])
def results():
    return Response(results_html(last_result), mimetype='text/html')


@app.route('/go', methods=['GET'])
def trade_trigger():
    from flask import request as r
    data = {
        'symbol':    r.args.get('symbol'),
        'direction': r.args.get('direction', 'SELL'),
        'sl':        float(r.args.get('sl', 0)),
        'tps':       [float(r.args.get(f'tp{i}', 0)) for i in range(1, 5)],
        'risk_pct':  float(r.args.get('risk_pct', RISK_PCT))
    }
    resp = requests.post('http://127.0.0.1:5000/trade', json=data)
    return Response(results_html(resp.json()), mimetype='text/html')


@app.route('/trade', methods=['POST'])
def place_trades():
    global last_result
    data = request.get_json()
    if not data:
        last_result = {'error': 'No JSON body'}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': 'No JSON body'}), 400

    symbol    = data.get('symbol')
    direction = data.get('direction', 'SELL').upper()
    sl        = float(data.get('sl', 0))
    tps       = [float(t) for t in data.get('tps', [])]
    risk_pct  = float(data.get('risk_pct', RISK_PCT))

    if not symbol or not sl or len(tps) != 4:
        last_result = {'error': 'symbol, sl and 4 tps required'}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': last_result['error']}), 400

    if symbol not in POINT_VALUES:
        last_result = {'error': f'Unknown symbol: {symbol}'}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': last_result['error']}), 400

    try:
        info = get_mt5()
    except Exception as e:
        last_result = {'error': str(e)}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': str(e)}), 500

    balance = info.equity
    log.info(f'Live equity: £{balance:.2f}')

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        mt5.shutdown()
        last_result = {'error': f'Cannot get price for {symbol}'}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': last_result['error']}), 500

    price    = tick.bid if direction == 'SELL' else tick.ask
    pv       = POINT_VALUES[symbol]
    sl_dist  = abs(sl - price)
    risk_amt = (balance * risk_pct) / 4
    lots     = floor_lots(risk_amt / (sl_dist * pv))

    if TEST_MODE:
        lots = 0.01
        log.info('TEST MODE — lots = 0.01')

    log.info(f'{symbol} {direction} | price={price} sl={sl} dist={sl_dist:.2f} lots={lots}')

    if lots < 0.01:
        mt5.shutdown()
        last_result = {'error': f'Lots too small ({lots})'}
        threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
        return jsonify({'error': last_result['error']}), 400

    order_type   = mt5.ORDER_TYPE_SELL if direction == 'SELL' else mt5.ORDER_TYPE_BUY
    results_list = []

    for i, tp in enumerate(tps):
        req = {
            'action':       mt5.TRADE_ACTION_DEAL,
            'symbol':       symbol,
            'volume':       lots,
            'type':         order_type,
            'price':        price,
            'sl':           sl,
            'tp':           tp,
            'deviation':    20,
            'magic':        20260409,
            'comment':      f'TP{i+1}',
            'type_time':    mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f'Trade {i+1} OK — order {result.order}')
            results_list.append({'trade': i+1, 'status': 'ok', 'order': result.order, 'tp': tp, 'lots': lots})
        else:
            log.error(f'Trade {i+1} FAILED — {result.retcode}: {result.comment}')
            results_list.append({'trade': i+1, 'status': 'failed', 'retcode': result.retcode, 'comment': result.comment})

    # Query actual entry from MT5 positions
    actual_entry = price
    try:
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            magic_pos = [p for p in positions if p.magic == 20260409]
            if magic_pos:
                actual_entry = magic_pos[0].price_open
    except Exception:
        pass

    mt5.shutdown()
    success = sum(1 for r in results_list if r['status'] == 'ok')

    last_result = {
        'test_mode':      TEST_MODE,
        'symbol':         symbol,
        'direction':      direction,
        'entry_price':    actual_entry,
        'sl':             sl,
        'lots':           lots,
        'sl_distance':    round(abs(sl - actual_entry), 2),
        'risk_per_trade': round(risk_amt, 2),
        'max_downside':   round(abs(sl - actual_entry) * pv * lots * 4, 2),
        'trades_placed':  success,
        'trades_failed':  4 - success,
        'results':        results_list
    }

    threading.Timer(0.5, lambda: webbrowser.open('http://localhost:5000/results')).start()
    return jsonify(last_result)


@app.route('/log_signal', methods=['POST'])
def log_signal():
    global last_signal
    data = request.get_json()
    last_signal = {
        'message': data.get('message', ''),
        'parsed':  data.get('parsed', None),
        'time':    data.get('original_time') or datetime.now().strftime('%d %b %Y %H:%M:%S')
    }
    return jsonify({'status': 'ok'})


@app.route('/log_channel_message', methods=['POST'])
def log_channel_message():
    global last_channel_message
    data = request.get_json()
    last_channel_message = {
        'text': data.get('text', ''),
        'time': data.get('time', datetime.now().strftime('%d %b %Y %H:%M:%S'))
    }
    return jsonify({'status': 'ok'})


@app.route('/heartbeat', methods=['GET'])
def heartbeat():
    try:
        info = get_mt5()
        balance = round(info.balance, 2)
        equity  = round(info.equity, 2)
        mt5.shutdown()
        mt5_status = 'connected'
    except Exception as e:
        balance = '?'
        equity  = '?'
        mt5_status = f'error: {str(e)[:60]}'
    return jsonify({
        'status':           mt5_status,
        'balance':          balance,
        'equity':           equity,
        'last_message':     last_channel_message.get('text', 'None'),
        'last_msg_time':    last_channel_message.get('time', 'Never'),
        'last_signal':      last_signal.get('message', 'None'),
        'last_signal_time': last_signal.get('time', 'Never'),
        'time':             datetime.now().strftime('%d %b %Y %H:%M:%S')
    })


@app.route('/last_signal', methods=['GET'])
def last_signal_page():
    ts     = last_signal.get('time', 'Never')
    msg    = last_signal.get('message', 'No signals received yet.')
    parsed = last_signal.get('parsed')
    if parsed:
        parsed_html = f'''<div style="background:#f0faf4;border-radius:8px;padding:14px;margin-top:12px">
            <p style="font-size:12px;color:#1a9e5c;font-weight:500;margin-bottom:8px">Parsed as valid signal</p>
            <table style="width:100%;font-size:13px;border-collapse:collapse">
                <tr><td style="color:#888;padding:4px 0">Symbol</td><td style="font-weight:500">{parsed.get("symbol")}</td></tr>
                <tr><td style="color:#888;padding:4px 0">Direction</td><td style="font-weight:500">{parsed.get("direction")}</td></tr>
                <tr><td style="color:#888;padding:4px 0">Stop loss</td><td style="font-weight:500;color:#e04040">{parsed.get("sl")}</td></tr>
                <tr><td style="color:#888;padding:4px 0">TP1</td><td style="font-weight:500;color:#1a9e5c">{parsed.get("tps",["","","",""])[0]}</td></tr>
                <tr><td style="color:#888;padding:4px 0">TP2</td><td style="font-weight:500;color:#1a9e5c">{parsed.get("tps",["","","",""])[1]}</td></tr>
                <tr><td style="color:#888;padding:4px 0">TP3</td><td style="font-weight:500;color:#1a9e5c">{parsed.get("tps",["","","",""])[2]}</td></tr>
                <tr><td style="color:#888;padding:4px 0">TP4</td><td style="font-weight:500;color:#1a9e5c">{parsed.get("tps",["","","",""])[3]}</td></tr>
            </table></div>'''
    else:
        parsed_html = '<div style="background:#fff8f0;border-radius:8px;padding:12px;margin-top:12px;font-size:13px;color:#c07000">Not a trading signal.</div>'

    return Response(f'''<!DOCTYPE html><html><head><title>Last Signal</title><meta charset="utf-8">
<style>body{{font-family:-apple-system,Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;color:#222}}
h1{{font-size:20px;font-weight:500;margin:0 0 4px}}.ts{{font-size:12px;color:#888;margin-bottom:20px}}
.card{{background:white;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #e8e8e8}}
.card h2{{font-size:14px;font-weight:500;color:#555;margin:0 0 12px}}
.msg{{background:#f8f8f8;border-radius:8px;padding:12px;font-family:monospace;font-size:12px;white-space:pre-wrap;line-height:1.7}}
.ref{{font-size:12px;color:#888;margin-top:12px}}</style></head>
<body><h1>Last Telegram signal</h1><p class="ts">Received: {ts}</p>
<div class="card"><h2>Raw message</h2><div class="msg">{msg}</div>{parsed_html}</div>
<p class="ref"><a href="/last_signal">Refresh</a> | <a href="/positions">Positions</a></p>
</body></html>''', mimetype='text/html')


# ── Tray icon ─────────────────────────────────────────────────────────────────
def make_icon(test=False):
    img  = Image.new('RGB', (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(220, 140, 0) if test else (0, 180, 80))
    draw.text((22, 20), 'MT', fill=(255, 255, 255))
    return img


def check_health(icon, item):
    webbrowser.open('http://localhost:5000/health')


def view_positions(icon, item):
    webbrowser.open('http://localhost:5000/positions')


def open_log(icon, item):
    os.startfile(LOG_FILE)


def send_heartbeat_tray(icon, item):
    webbrowser.open('http://localhost:5000/heartbeat')


def toggle_test_mode(icon, item):
    global TEST_MODE, tray_icon
    TEST_MODE  = not TEST_MODE
    status     = 'TEST MODE — 0.01 lots' if TEST_MODE else 'MT5 Trade — Ready'
    log.info(f'Test mode: {TEST_MODE}')
    tray_icon.icon  = make_icon(TEST_MODE)
    tray_icon.title = status


def quit_app(icon, item):
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    log.info('Tray app stopped.')
    icon.stop()
    os._exit(0)


def prevent_sleep():
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    log.info('Sleep prevention active.')


def play_message_sound():
    """Single bell — new channel message received."""
    try:
        winsound.Beep(880, 150)
    except Exception:
        pass


def play_signal_sound():
    """Three quick high bells — trading signal received."""
    try:
        for _ in range(3):
            winsound.Beep(1320, 100)
            winsound.PlaySound(None, winsound.SND_PURGE)
            import time
            time.sleep(0.08)
    except Exception:
        pass


def run_flask():
    log.info(f'MT5 tray app started on port {PORT}')
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


def main():
    prevent_sleep()
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    global tray_icon
    icon = pystray.Icon(
        'MT5 Trade',
        make_icon(),
        'MT5 Trade — Ready',
        menu=pystray.Menu(
            item('Send heartbeat now', send_heartbeat_tray),
            item('Toggle test mode (0.01 lots)', toggle_test_mode),
            item('Check health', check_health),
            item('View open positions', view_positions),
            item('View log', open_log),
            pystray.Menu.SEPARATOR,
            item('Quit', quit_app)
        )
    )
    tray_icon = icon
    icon.run()


@app.route('/sound_message', methods=['POST'])
def sound_message():
    """Play single bell — called when any channel message arrives."""
    threading.Thread(target=play_message_sound, daemon=True).start()
    return jsonify({'status': 'ok'})


@app.route('/sound_signal', methods=['POST'])
def sound_signal():
    """Play triple bell — called when a trading signal is detected."""
    threading.Thread(target=play_signal_sound, daemon=True).start()
    return jsonify({'status': 'ok'})


@app.route('/update_poll_status', methods=['POST'])
def update_poll_status():
    global poll_status
    data = request.get_json()
    poll_status = {
        'poll_num': data.get('poll_num', 0),
        'last_id':  data.get('last_id', 0),
        'errors':   data.get('errors', 0),
        'time':     data.get('time', datetime.now().strftime('%d %b %Y %H:%M:%S'))
    }
    return jsonify({'status': 'ok'})


@app.route('/poll_status', methods=['GET'])
def poll_status_page():
    ts       = poll_status.get('time', 'Never')
    num      = poll_status.get('poll_num', 0)
    last_id  = poll_status.get('last_id', 0)
    errors   = poll_status.get('errors', 0)
    mins     = round(num * 3 / 60, 1) if num > 0 else 0
    html = f"""<!DOCTYPE html><html><head><title>Poll Status</title>
<style>body{{font-family:-apple-system,Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px}}
h1{{font-size:20px;font-weight:500;margin:0 0 4px}}.ts{{font-size:12px;color:#888;margin-bottom:20px}}
.card{{background:white;border-radius:10px;padding:20px;border:1px solid #e8e8e8}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}}
.metric{{background:#f8f8f8;border-radius:8px;padding:12px}}
.lbl{{font-size:11px;color:#888;margin-bottom:3px}}.val{{font-size:20px;font-weight:500}}
.ok{{color:#1a9e5c}}.err{{color:#e04040}}
.ref{{font-size:12px;color:#888;margin-top:12px}}</style></head>
<body><h1>Telegram monitor poll status</h1><p class="ts">Last reported: {ts}</p>
<div class="card"><div class="grid">
<div class="metric"><div class="lbl">Total polls</div><div class="val">{num}</div></div>
<div class="metric"><div class="lbl">Running for</div><div class="val">{mins} mins</div></div>
<div class="metric"><div class="lbl">Last message ID</div><div class="val">{last_id}</div></div>
<div class="metric"><div class="lbl">Poll errors</div><div class="val {'ok' if errors == 0 else 'err'}">{errors}</div></div>
</div>
<p style="font-size:13px;color:#{'1a9e5c' if num > 0 else 'e04040'}">
{'Polling active — monitor is running.' if num > 0 else 'No poll data yet — monitor may not be running.'}</p>
<p class="ref"><a href="/poll_status">Refresh</a> | <a href="/health">Health</a> | <a href="/last_signal">Last signal</a></p>
</div></body></html>"""
    return Response(html, mimetype='text/html')


if __name__ == '__main__':
    main()
