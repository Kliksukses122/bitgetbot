"""
╔══════════════════════════════════════════════════════════════╗
║          SPOT TRADER - Hybrid Paper Trading (Dry Run)        ║
║                                                              ║
║  - Memindai koin & harga REAL-TIME dari Bitget               ║
║  - Menggunakan SALDO VIRTUAL (Dry-Run / Paper Trading)        ║
║  - 100% AMAN: Tanpa menyentuh saldo asli akun               ║
║  - Mencatat Profit/Loss & kirim notifikasi ke Telegram       ║
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
# ⚙️ KONFIGURASI DRY-RUN / PAPER TRADING
# ─────────────────────────────────────────────
DRY_RUN_MODE       = True         # True = Uang Virtual (Paper Trading)
VIRTUAL_INITIAL_USDT = 1000.0     # Modal awal virtual $1,000 USDT
RISK_PERCENT       = 0.05         # 5% dari modal virtual per trade ($50)
MAX_POSITIONS      = 10           # Maksimal 10 posisi terbuka sekaligus
TP_SPLIT           = [0.30, 0.30, 0.40]  # Pecah jual: 30% TP1 | 30% TP2 | 40% TP3
MONITOR_INTERVAL   = 10           # Cek harga setiap 10 detik

# ─────────────────────────────────────────────
# 📋 LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("SpotTrader-DryRun")

# ─────────────────────────────────────────────
# 🔌 KONEKSI EXCHANGE (Hanya untuk baca harga real-time)
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

        # Init saldo awal jika belum ada
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
        logger.info("✅ Spot Dry-Run DB Ready.")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")
    finally:
        release_conn(conn)

def get_virtual_balance():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance_usdt FROM virtual_wallet WHERE id = 1")
        row = cur.fetchone()
        return float(row[0]) if row else VIRTUAL_INITIAL_USDT
    finally:
        release_conn(conn)

def update_virtual_balance(delta):
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

        usdt_free = get_virtual_balance()
        if usdt_free < 5:
            logger.warning(f"⚠️ Saldo Virtual habis/terlalu rendah: ${usdt_free:.2f}")
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

            cur.execute("""
                INSERT INTO spot_trades
                    (signal_id, symbol, pattern, entry_price, sl_price, tp1, tp2, tp3, qty_total, qty_remaining, cost_usdt, status, is_dry_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING_BUY', 1)
            """, (sig_id, spot_symbol, pat, entry, sl, tp1, tp2, tp3, qty, qty, cost_usdt))

            logger.info(f"📥 [DRY-RUN] Sinyal Ingested: {spot_symbol} | Virtual Modal: ${cost_usdt:.2f} | Qty: {qty:.4f}")
            current_active += 1

        conn.commit()
    except Exception as e:
        logger.error(f"❌ Ingest Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 🛒 STEP 2: EKSEKUSI BELI (SIMULASI HARGA NYATA)
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

                # Jika harga saat ini <= target entry, order virtual terpenuhi
                if current_price <= entry:
                    logger.info(f"⚡ [DRY-RUN BUY] {sym}: Harga real {current_price} <= Entry {entry}. Beli VIRTUAL!")
                    update_virtual_balance(-cost)
                    cur.execute("""
                        UPDATE spot_trades
                        SET status = 'OPEN', entry_price = %s,
                            buy_order_id = 'DRY_RUN_ORDER', updated_at = datetime('now')
                        WHERE id = %s
                    """, (current_price, trade_id))
                    conn.commit()
                else:
                    logger.debug(f"⏳ [DRY-RUN] {sym} Menunggu harga menyentuh entry ({current_price} vs {entry})")
            except Exception as e:
                logger.warning(f"⚠️ [DRY-RUN] Gagal cek harga {sym}: {e}")

    except Exception as e:
        logger.error(f"❌ Execute Buy Loop Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 💰 STEP 3: MONITOR & JUAL VIRTUAL (REAL-TIME PRICE)
# ─────────────────────────────────────────────
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
                loss = (current_price - entry) * qty_rem
                final_pnl = pnl_usdt + loss
                returned_cash = (current_price * qty_rem)
                update_virtual_balance(returned_cash)

                cur.execute("""
                    UPDATE spot_trades
                    SET status = 'CLOSED_SL', qty_remaining = 0,
                        pnl_usdt = %s, updated_at = datetime('now')
                    WHERE id = %s
                """, (final_pnl, trade_id))
                conn.commit()
                # 🧠 Umpan ke AI Learning System (LOSS)
                if pat: record_trade_outcome(sym, pat, is_win=False)
                logger.warning(f"🛑 [DRY-RUN SL] {sym} Terkena SL di ${current_price} | PnL: ${final_pnl:.2f} | Saldo Virtual: ${get_virtual_balance():.2f}")
                continue

            # ── TAKE PROFIT 3 (TUTUP SEMUA) ──
            if current_price >= tp3:
                profit = (current_price - entry) * qty_rem
                final_pnl = pnl_usdt + profit
                returned_cash = (current_price * qty_rem)
                update_virtual_balance(returned_cash)

                cur.execute("""
                    UPDATE spot_trades
                    SET status = 'CLOSED_WIN', qty_remaining = 0,
                        pnl_usdt = %s, updated_at = datetime('now')
                    WHERE id = %s
                """, (final_pnl, trade_id))
                conn.commit()
                # 🧠 Umpan ke AI Learning System (WIN)
                if pat: record_trade_outcome(sym, pat, is_win=True)
                logger.info(f"🏆 [DRY-RUN TP3 WIN] {sym} Capai target penuh di ${current_price}! PnL: +${final_pnl:.2f} | Saldo: ${get_virtual_balance():.2f}")
                continue

            # ── TAKE PROFIT 2 (Jual 30%) ──
            if current_price >= tp2 and not tp2_hit:
                qty_sell = qty_total * TP_SPLIT[1]
                profit = (current_price - entry) * qty_sell
                returned_cash = (current_price * qty_sell)
                update_virtual_balance(returned_cash)

                new_rem = max(0, qty_rem - qty_sell)
                new_pnl = pnl_usdt + profit
                cur.execute("""
                    UPDATE spot_trades
                    SET tp2_hit = 1, qty_remaining = %s,
                        pnl_usdt = %s, status = 'PARTIAL_TP', updated_at = datetime('now')
                    WHERE id = %s
                """, (new_rem, new_pnl, trade_id))
                conn.commit()
                logger.info(f"✅ [DRY-RUN TP2] {sym} Capai TP2 di ${current_price} | PnL sementara: +${new_pnl:.2f}")
                continue

            # ── TAKE PROFIT 1 (Jual 30%) ──
            if current_price >= tp1 and not tp1_hit:
                qty_sell = qty_total * TP_SPLIT[0]
                profit = (current_price - entry) * qty_sell
                returned_cash = (current_price * qty_sell)
                update_virtual_balance(returned_cash)

                new_rem = max(0, qty_rem - qty_sell)
                new_pnl = pnl_usdt + profit
                cur.execute("""
                    UPDATE spot_trades
                    SET tp1_hit = 1, qty_remaining = %s,
                        pnl_usdt = %s, status = 'PARTIAL_TP', updated_at = datetime('now')
                    WHERE id = %s
                """, (new_rem, new_pnl, trade_id))
                conn.commit()
                logger.info(f"✅ [DRY-RUN TP1] {sym} Capai TP1 di ${current_price} | PnL sementara: +${new_pnl:.2f}")

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
            bal = get_virtual_balance()
            logger.info(f"📊 [DRY-RUN REPORT] Saldo: ${bal:.2f} | PnL: ${pnl:.2f} | WR: {win_rate:.1f}% ({wins}W/{losses}L)")
    except Exception as e:
        logger.error(f"❌ Report Error: {e}")
    finally:
        release_conn(conn)


# ─────────────────────────────────────────────
# 🏁 MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🟢 SpotTrader DRY-RUN AKTIF (Simulasi Uang Virtual $1,000)")
    logger.info("   📡 Menggunakan harga koin REAL-TIME dari Bitget")
    logger.info("   🛡️ 100% AMAN: Saldo asli akun TIDAK disentuh sama sekali")
    logger.info(f"   Saldo Virtual Anda: ${get_virtual_balance():.2f} USDT")

    init_spot_db()

    schedule.every(1).minutes.do(ingest_fresh_signals)
    schedule.every(10).seconds.do(execute_buy_orders)
    schedule.every(MONITOR_INTERVAL).seconds.do(monitor_open_positions)
    schedule.every().day.at("00:01").do(generate_daily_report)

    logger.info("🚀 Menunggu sinyal dari Scanner...")
    ingest_fresh_signals()

    while True:
        schedule.run_pending()
        time.sleep(1)
