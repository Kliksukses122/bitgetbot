import json
import os
from dotenv import load_dotenv

load_dotenv()

def load_config():
    config = {}
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            config = json.load(f)

    # Allow overriding/setting via environment variables (ideal for Railway)
    if 'api' not in config:
        config['api'] = {}
    if 'system' not in config:
        config['system'] = {}
    if 'setup' not in config:
        config['setup'] = {}
    if 'strategy' not in config:
        config['strategy'] = {}
    if 'indicators' not in config:
        config['indicators'] = {}
    if 'patterns' not in config:
        config['patterns'] = {}
    if 'pattern_signals' not in config:
        config['pattern_signals'] = {}

    # Read from environment variables if present
    if os.getenv('BITGET_KEY'):
        config['api']['bitget_key'] = os.getenv('BITGET_KEY')
    if os.getenv('BITGET_SECRET'):
        config['api']['bitget_secret'] = os.getenv('BITGET_SECRET')
    if os.getenv('BITGET_PASSPHRASE'):
        config['api']['bitget_passphrase'] = os.getenv('BITGET_PASSPHRASE')
    if os.getenv('TELEGRAM_BOT_TOKEN'):
        config['api']['telegram_bot_token'] = os.getenv('TELEGRAM_BOT_TOKEN')
    if os.getenv('TELEGRAM_CHAT_ID'):
        config['api']['telegram_chat_id'] = os.getenv('TELEGRAM_CHAT_ID')

    # Mode Transaksi: DRY_RUN (Default: True untuk aman)
    # Ubah DRY_RUN=false di .env atau di config.json untuk transaksi uang riil
    if 'trading' not in config:
        config['trading'] = {}
    if os.getenv('DRY_RUN') is not None:
        config['trading']['dry_run'] = os.getenv('DRY_RUN').lower() in ('true', '1', 'yes')
    else:
        config['trading'].setdefault('dry_run', True)

    # Defaults for system
    config['system'].setdefault('timezone', 'Asia/Jakarta')
    config['system'].setdefault('max_threads', 20)
    if os.getenv('SCAN_INTERVAL_MINUTES'):
        config['system']['check_interval_minutes'] = int(os.getenv('SCAN_INTERVAL_MINUTES'))
    else:
        config['system'].setdefault('check_interval_minutes', 5)
    config['system'].setdefault('timeframes', ["1h", "4h", "1d", "1w"])

    config['setup'].setdefault('fib_entry_start', 0.5)
    config['setup'].setdefault('fib_entry_end', 0.618)
    config['setup'].setdefault('fib_sl', 0.27)
    config['setup'].setdefault('fib_tp_1', 1.0)
    config['setup'].setdefault('fib_tp_2', 1.618)
    config['setup'].setdefault('fib_tp_3', 2.618)

    config['strategy'].setdefault('min_tech_score', 5)
    config['strategy'].setdefault('min_quant_score', 3)
    config['strategy'].setdefault('risk_reward_min', 3.0)

    config['indicators'].setdefault('min_rvol', 2.0)

    config['patterns'].setdefault('tolerance', 0.015)
    config['patterns'].setdefault('double_top', True)
    config['patterns'].setdefault('double_bottom', True)
    config['patterns'].setdefault('bull_flag', True)
    config['patterns'].setdefault('bear_flag', True)
    config['patterns'].setdefault('ascending_triangle', True)
    config['patterns'].setdefault('descending_triangle', True)
    config['patterns'].setdefault('bullish_rectangle', True)

    config['pattern_signals'].setdefault('double_bottom', 'Long')
    config['pattern_signals'].setdefault('double_top', 'Short')
    config['pattern_signals'].setdefault('bull_flag', 'Long')
    config['pattern_signals'].setdefault('bear_flag', 'Short')
    config['pattern_signals'].setdefault('ascending_triangle', 'Long')
    config['pattern_signals'].setdefault('descending_triangle', 'Short')
    config['pattern_signals'].setdefault('bullish_rectangle', 'Long')

    return config

CONFIG = load_config()