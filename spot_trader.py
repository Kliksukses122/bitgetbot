"""
╔══════════════════════════════════════════════════════════════╗
║               SPOT TRADER - Universal Execution Engine       ║
║                                                              ║
║  Dua Mode Transaksi:                                         ║
║  1. DRY-RUN (Uang Virtual): Latihan 100% aman tanpa risiko   ║
║  2. LIVE (Uang Riil): Eksekusi beli/jual nyata di Bitget     ║
║                                                              ║
║  Cara Ganti Mode Cepat:                                      ║
║  - Di config.json: set "dry_run": false                      ║
║  - Atau di Railway/Env: set DRY_RUN=false                    ║
╚══════════════════════════════════════════════════════════════╝
"""

import ccxt
import time
import schedule
import logging
from modules.config_loader import CONFIG
from modules.database import get_conn, release_conn
from modules.learning_system import record_trade_outcome

# ─────────────────────────────────────────────
# ⚙️ SAKELAR MODE TRANSAKSI (DRY-RUN vs LIVE)
# ─────────────────────────────────────────────
DRY_RUN_MODE       = CONFIG.get('trading', {}).get('dry_run', True)
VIRTUAL_INITIAL_USDT = 1000.0     # Modal awal simulasi $1,000 USDT
RISK_PERCENT       = 0.05         # 5% dari modal per trade
MAX_POSITIONS      = 10           # Maksimal 10 posisi terbuka sekaligus
TP_SPLIT           = [0.30, 0.30, 0.40]  # 30% TP1 | 30% TP2 | 40% TP3
MONITOR_INTERVAL   = 10           # Cek harga setiap 10 detik

# ─────────────────────────────────────────────
# 📋 LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
mode_tag = "DRY-RUN (Simulasi)" if DRY_RUN_MODE else "🚨 LIVE TRADING (UANG RIIL) 🚨"
logger = logging.getLogger(f"SpotTrader-{mode_tag}")

# ─────────────────────────────────────────────
# 🔌 KONEKSI EXCHANGE (SPOT)
# ─────────────────────────────────────────────
exchange = ccxt.bitget({
    'apiKey':   CONFIG['api'].get('bitget_key', ''),
    'secret':   CONFIG['api'].get('bitget_secret', ''),
    'password': CONFIG['api'].get('bitget_passphrase', ''),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
        'adjustForTimeDifference': True
    }
})

# ─────────────────────────────────────────────
# 🛠️ INISIALISASI DATABASE
# ─────────────────────────────────────────────
def init_spot_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS spot_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id       INT UNIQUE,
                symbol          VARCHAR(30),
                pattern         VARCHAR(50),
                entry_price     DECIMAL,
                sl_price        DECIMAL,
                tp1             DECIMAL,
                tp2             DECIMAL,
                tp3             DECIMAL,
                qty_total       DECIMAL,
                qty_remaining   DECIMAL,
                cost_usdt       DECIMAL,
                buy_order_id    VARCHAR(60),
                status          VARCHAR(30) DEFAULT 'PENDING_BUY',
                tp1_hit         BOOLEAN DEFAULT 0,
                tp2_hit         BOOLEAN DEFAULT 0,
                pnl_usdt        DECIMAL DEFAULT 0,
                is_dry_run      BOOLEAN DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS virtual_wallet (
                id              INTEGER PRIMARY KEY,
                balance_usdt    DECIMAL DEFAULT 1000.0,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("SELECT balance_usdt FROM virtual_wallet WHERE id = 1")
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO virtual_wallet (id, balance_usdt) VALUES (1, %s)", (VIRTUAL_INITIAL_USDT,))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS spot_daily_reports (
                report_date     DATE PRIMARY KEY,
                total_pnl       DECIMAL DEFAULT 0,
                win_rate        DECIMAL DEFAULT 0,
                total_wins      INT DEFAULT 0,
                total_losses    INT DEFAULT 0,
                total_trades    INT DEFAULT 0,
                generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info(f"✅ Spot DB Ready | Mode: {mode_tag}")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")
    finally:
        release_conn(conn)

def get_available_balance():
    if DRY_RUN_MODE:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT balance_usdt FROM virtual_wallet WHERE id = 1")
            row = cur.fetchone()
            return float(row[0]) if row else VIRTUAL_INITIAL_USDT
        finally:
            release_conn(conn)
    else:
        try:
            balance = exchange.fetch_balance()
            return float(balance['free'].get('USDT', 0))
        except Exception as e:
            logger.error(f"❌ Gagal ambil saldo riil Bitget: {e}")
            return 0.0

def update_virtual_balance(delta):
    if not DRY_RUN_MODE:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE virtual_wallet SET balance_usdt = balance_usdt + %s WHERE id = 1", (delta,))
        conn.commit()
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 📥 STEP 1: AMBIL SINYAL BARU
# ─────────────────────────────────────────────
def ingest_fresh_signals():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM spot_trades WHERE status IN ('OPEN', 'PARTIAL_TP')")
        current_active = cur.fetchone()[0]
        if current_active >= MAX_POSITIONS:
            return

        usdt_free = get_available_balance()
        if usdt_free < 5:
            logger.warning(f"⚠️ Saldo USDT terlalu rendah: ${usdt_free:.2f}")
            return

        cur.execute("""
            SELECT t.id, t.symbol, t.entry_price, t.sl_price, t.tp1, t.tp2, t.tp3, t.pattern
            FROM trades t
            LEFT JOIN spot_trades s ON t.id = s.signal_id
            WHERE t.status = 'Waiting Entry'
              AND t.side = 'Long'
              AND t.created_at >= datetime('now', '-12 hours')
              AND s.id IS NULL
            ORDER BY t.created_at DESC
        """)
        signals = cur.fetchall()

        for sig in signals:
            if current_active >= MAX_POSITIONS:
                break

            sig_id, sym, entry, sl, tp1, tp2, tp3, pat = sig
            entry, sl = float(entry), float(sl)
            spot_symbol = sym.split(':')[0]

            cost_usdt = usdt_free * RISK_PERCENT
            if cost_usdt < 5:
                continue

            qty = cost_usdt / entry
            is_dry = 1 if DRY_RUN_MODE else 0

            cur.execute("""
                INSERT INTO spot_trades
                    (signal_id, symbol, pattern, entry_price, sl_price, tp1, tp2, tp3, qty_total, qty_remaining, cost_usdt, status, is_dry_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING_BUY', %s)
            """, (sig_id, spot_symbol, pat, entry, sl, tp1, tp2, tp3, qty, qty, cost_usdt, is_dry))

            logger.info(f"📥 [{mode_tag}] Sinyal: {spot_symbol} | Modal: ${cost_usdt:.2f} | Qty: {qty:.4f}")
            current_active += 1

        conn.commit()
    except Exception as e:
        logger.error(f"❌ Ingest Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 🛒 STEP 2: EKSEKUSI BELI (MARKET/LIMIT)
# ─────────────────────────────────────────────
def execute_buy_orders():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, entry_price, qty_total, cost_usdt
            FROM spot_trades
            WHERE status = 'PENDING_BUY'
        """)
        orders = cur.fetchall()
        if not orders:
            return

        for row in orders:
            trade_id, sym, entry, qty, cost = row
            entry = float(entry)
            qty   = float(qty)
            cost  = float(cost)

            try:
                ticker = exchange.fetch_ticker(sym)
                current_price = float(ticker['last'])

                if current_price <= entry:
                    if DRY_RUN_MODE:
                        # Simulasi Beli
                        logger.info(f"⚡ [DRY-RUN BUY] {sym}: Harga {current_price} <= Entry {entry}")
                        update_virtual_balance(-cost)
                        cur.execute("""
                            UPDATE spot_trades
                            SET status = 'OPEN', entry_price = %s,
                                buy_order_id = 'VIRTUAL_ORDER', updated_at = datetime('now')
                            WHERE id = %s
                        """, (current_price, trade_id))
                        conn.commit()
                    else:
                        # Transaksi RIIL di Exchange
                        qty_precise = float(exchange.amount_to_precision(sym, qty))
                        logger.info(f"🚨 [LIVE BUY] Membeli {qty_precise} {sym} di harga ${current_price}...")
                        res = exchange.create_order(sym, 'market', 'buy', qty_precise)
                        actual_entry = float(res.get('average') or res.get('price') or current_price)
                        cur.execute("""
                            UPDATE spot_trades
                            SET status = 'OPEN', entry_price = %s, qty_total = %s, qty_remaining = %s,
                                buy_order_id = %s, updated_at = datetime('now')
                            WHERE id = %s
                        """, (actual_entry, qty_precise, qty_precise, str(res.get('id', '')), trade_id))
                        conn.commit()
                        logger.info(f"✅ [LIVE BOUGHT] {sym} ID: {res.get('id')} @ {actual_entry}")
            except Exception as e:
                logger.error(f"❌ Gagal beli {sym}: {e}")

    except Exception as e:
        logger.error(f"❌ Execute Buy Loop Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 💰 STEP 3: EKSEKUSI JUAL & MONITOR HARGA
# ─────────────────────────────────────────────
def execute_sell(sym, qty, reason):
    """Jual koin (Simulasi jika Dry Run, Transaksi Riil jika Live)"""
    if DRY_RUN_MODE:
        ticker = exchange.fetch_ticker(sym)
        sold_price = float(ticker['last'])
        logger.info(f"💰 [DRY-RUN JUAL {reason}] {sym} | {qty:.4f} koin @ ${sold_price}")
        return sold_price
    else:
        try:
            qty_precise = float(exchange.amount_to_precision(sym, qty))
            res = exchange.create_order(sym, 'market', 'sell', qty_precise)
            sold_price = float(res.get('average') or res.get('price') or 0)
            logger.info(f"🚨 [LIVE JUAL {reason}] {sym} | {qty_precise} koin @ ${sold_price}")
            return sold_price
        except Exception as e:
            logger.error(f"❌ Gagal jual riil {sym}: {e}")
            return 0.0

def monitor_open_positions():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, entry_price, sl_price, tp1, tp2, tp3,
                   qty_total, qty_remaining, cost_usdt, tp1_hit, tp2_hit, pnl_usdt, pattern
            FROM spot_trades
            WHERE status IN ('OPEN', 'PARTIAL_TP')
        """)
        trades = cur.fetchall()
        if not trades:
            return

        for trade in trades:
            (trade_id, sym, entry, sl, tp1, tp2, tp3,
             qty_total, qty_remaining, cost_usdt,
             tp1_hit, tp2_hit, pnl_usdt, pat) = trade

            entry, sl     = float(entry), float(sl)
            tp1, tp2, tp3 = float(tp1), float(tp2), float(tp3)
            qty_total     = float(qty_total)
            qty_rem       = float(qty_remaining)
            pnl_usdt      = float(pnl_usdt or 0)
            cost_usdt     = float(cost_usdt)

            try:
                ticker        = exchange.fetch_ticker(sym)
                current_price = float(ticker['last'])
            except Exception as e:
                continue

            # ── STOP LOSS HIT ──
            if current_price <= sl:
                sold_at = execute_sell(sym, qty_rem, "STOP LOSS")
                sold_at = sold_at if sold_at > 0 else current_price
                loss = (sold_at - entry) * qty_rem
                final_pnl = pnl_usdt + loss
                update_virtual_balance(sold_at * qty_rem)

                cur.execute("""
                    UPDATE spot_trades
                    SET status = 'CLOSED_SL', qty_remaining = 0,
                        pnl_usdt = %s, updated_at = datetime('now')
                    WHERE id = %s
                """, (final_pnl, trade_id))
                conn.commit()

                if pat: record_trade_outcome(sym, pat, is_win=False)
                logger.warning(f"🛑 [SL HIT] {sym} @ ${sold_at} | PnL: ${final_pnl:.2f}")
                continue

            # ── TAKE PROFIT 3 (TUTUP SEMUA) ──
            if current_price >= tp3:
                sold_at = execute_sell(sym, qty_rem, "TP3 FULL CLOSE")
                sold_at = sold_at if sold_at > 0 else current_price
                profit = (sold_at - entry) * qty_rem
                final_pnl = pnl_usdt + profit
                update_virtual_balance(sold_at * qty_rem)

                cur.execute("""
                    UPDATE spot_trades
                    SET status = 'CLOSED_WIN', qty_remaining = 0,
                        pnl_usdt = %s, updated_at = datetime('now')
                    WHERE id = %s
                """, (final_pnl, trade_id))
                conn.commit()

                if pat: record_trade_outcome(sym, pat, is_win=True)
                logger.info(f"🏆 [TP3 FULL WIN] {sym} @ ${sold_at} | PnL: +${final_pnl:.2f}")
                continue

            # ── TAKE PROFIT 2 (Jual 30%) ──
            if current_price >= tp2 and not tp2_hit:
                qty_sell = qty_total * TP_SPLIT[1]
                sold_at = execute_sell(sym, qty_sell, "TP2 (30%)")
                sold_at = sold_at if sold_at > 0 else current_price
                profit = (sold_at - entry) * qty_sell
                update_virtual_balance(sold_at * qty_sell)

                new_rem = max(0, qty_rem - qty_sell)
                new_pnl = pnl_usdt + profit
                cur.execute("""
                    UPDATE spot_trades
                    SET tp2_hit = 1, qty_remaining = %s,
                        pnl_usdt = %s, status = 'PARTIAL_TP', updated_at = datetime('now')
                    WHERE id = %s
                """, (new_rem, new_pnl, trade_id))
                conn.commit()
                logger.info(f"✅ [TP2 HIT] {sym} @ ${sold_at} | PnL: +${new_pnl:.2f}")
                continue

            # ── TAKE PROFIT 1 (Jual 30%) ──
            if current_price >= tp1 and not tp1_hit:
                qty_sell = qty_total * TP_SPLIT[0]
                sold_at = execute_sell(sym, qty_sell, "TP1 (30%)")
                sold_at = sold_at if sold_at > 0 else current_price
                profit = (sold_at - entry) * qty_sell
                update_virtual_balance(sold_at * qty_sell)

                new_rem = max(0, qty_rem - qty_sell)
                new_pnl = pnl_usdt + profit
                cur.execute("""
                    UPDATE spot_trades
                    SET tp1_hit = 1, qty_remaining = %s,
                        pnl_usdt = %s, status = 'PARTIAL_TP', updated_at = datetime('now')
                    WHERE id = %s
                """, (new_rem, new_pnl, trade_id))
                conn.commit()
                logger.info(f"✅ [TP1 HIT] {sym} @ ${sold_at} | PnL: +${new_pnl:.2f}")

    except Exception as e:
        logger.error(f"❌ Monitor Positions Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 📊 LAPORAN HARIAN
# ─────────────────────────────────────────────
def generate_daily_report():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pnl_usdt <= 0 THEN 1 ELSE 0 END),
                   SUM(pnl_usdt)
            FROM spot_trades
            WHERE status IN ('CLOSED_WIN', 'CLOSED_SL')
              AND updated_at >= datetime('now', '-24 hours')
        """)
        row = cur.fetchone()
        if row and row[0] and row[0] > 0:
            total, wins, losses, pnl = row
            pnl      = pnl or 0
            win_rate = (wins / total) * 100 if total > 0 else 0
            bal = get_available_balance()
            logger.info(f"📊 [{mode_tag}] Saldo: ${bal:.2f} | PnL: ${pnl:.2f} | WR: {win_rate:.1f}% ({wins}W/{losses}L)")
    except Exception as e:
        logger.error(f"❌ Report Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 🏁 MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_spot_db()
    logger.info(f"🟢 SpotTrader Memulai | MODE: {mode_tag}")
    logger.info(f"   Saldo Terdeteksi: ${get_available_balance():.2f} USDT")

    schedule.every(1).minutes.do(ingest_fresh_signals)
    schedule.every(10).seconds.do(execute_buy_orders)
    schedule.every(MONITOR_INTERVAL).seconds.do(monitor_open_positions)
    schedule.every().day.at("00:01").do(generate_daily_report)

    logger.info("🚀 Bot Berjalan...")
    ingest_fresh_signals()

    while True:
        schedule.run_pending()
        time.sleep(1)
