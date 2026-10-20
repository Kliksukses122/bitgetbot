"""
╔══════════════════════════════════════════════════════════════╗
║             AI LEARNING & ADAPTIVE STRATEGY ENGINE           ║
║                                                              ║
║  Sistem Pembelajaran Otomatis (Machine Learning Feedback):   ║
║  1. Menganalisis histori Win/Loss setiap pola & koin.        ║
║  2. Pola dengan Win Rate rendah (< 40%) otomatis di-blacklist║
║     atau dikurangi bobot skornya.                            ║
║  3. Pola berkinerja tinggi (> 65%) mendapat bonus skor.      ║
║  4. Menghitung akurasi model & menyesuaikan batas filter     ║
║     secara dinamis tanpa intervensi manual.                  ║
╚══════════════════════════════════════════════════════════════╝
"""

import logging
from modules.database import get_conn, release_conn

logger = logging.getLogger("LearningEngine")

def init_learning_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pattern_learning (
                pattern_name    VARCHAR(50) PRIMARY KEY,
                total_trades    INTEGER DEFAULT 0,
                wins            INTEGER DEFAULT 0,
                losses          INTEGER DEFAULT 0,
                win_rate        REAL DEFAULT 50.0,
                score_weight    REAL DEFAULT 1.0,
                is_disabled     BOOLEAN DEFAULT 0,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS coin_learning (
                symbol          VARCHAR(30) PRIMARY KEY,
                total_trades    INTEGER DEFAULT 0,
                wins            INTEGER DEFAULT 0,
                losses          INTEGER DEFAULT 0,
                win_rate        REAL DEFAULT 50.0,
                is_blacklisted  BOOLEAN DEFAULT 0,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
    except Exception as e:
        logger.error(f"Error init learning tables: {e}")
    finally:
        release_conn(conn)

def record_trade_outcome(symbol, pattern, is_win):
    """
    Mencatat hasil perdagangan (Menang / Kalah) ke sistem pembelajaran
    dan memperbarui statistik akurasi secara real-time.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()

        # 1. Update Pattern Learning
        cur.execute("SELECT total_trades, wins, losses FROM pattern_learning WHERE pattern_name = %s", (pattern,))
        row = cur.fetchone()
        if row:
            total, wins, losses = row
            total += 1
            if is_win:
                wins += 1
            else:
                losses += 1
            wr = (wins / total) * 100

            # Bobot adaptif:
            # - Jika Win Rate > 65% dan sampel >= 5: Bonus bobot skor (+0.5)
            # - Jika Win Rate < 40% dan sampel >= 5: Nonaktifkan pola sementara
            disabled = 1 if (total >= 5 and wr < 35.0) else 0
            weight = 1.3 if wr >= 65.0 else (0.7 if wr < 45.0 else 1.0)

            cur.execute("""
                UPDATE pattern_learning
                SET total_trades = %s, wins = %s, losses = %s,
                    win_rate = %s, score_weight = %s, is_disabled = %s,
                    updated_at = datetime('now')
                WHERE pattern_name = %s
            """, (total, wins, losses, wr, weight, disabled, pattern))
        else:
            w = 1 if is_win else 0
            l = 0 if is_win else 1
            wr = 100.0 if is_win else 0.0
            cur.execute("""
                INSERT INTO pattern_learning (pattern_name, total_trades, wins, losses, win_rate, score_weight, is_disabled)
                VALUES (%s, 1, %s, %s, %s, 1.0, 0)
            """, (pattern, w, l, wr))

        # 2. Update Coin Learning
        cur.execute("SELECT total_trades, wins, losses FROM coin_learning WHERE symbol = %s", (symbol,))
        crow = cur.fetchone()
        if crow:
            total, wins, losses = crow
            total += 1
            if is_win:
                wins += 1
            else:
                losses += 1
            c_wr = (wins / total) * 100
            blacklisted = 1 if (total >= 4 and c_wr < 25.0) else 0
            cur.execute("""
                UPDATE coin_learning
                SET total_trades = %s, wins = %s, losses = %s,
                    win_rate = %s, is_blacklisted = %s, updated_at = datetime('now')
                WHERE symbol = %s
            """, (total, wins, losses, c_wr, blacklisted, symbol))
        else:
            w = 1 if is_win else 0
            l = 0 if is_win else 1
            c_wr = 100.0 if is_win else 0.0
            cur.execute("""
                INSERT INTO coin_learning (symbol, total_trades, wins, losses, win_rate, is_blacklisted)
                VALUES (%s, 1, %s, %s, %s, 0)
            """, (symbol, w, l, c_wr))

        conn.commit()
        logger.info(f"🧠 [LEARNING UPDATE] Pola: {pattern} | Koin: {symbol} | Hasil: {'WIN' if is_win else 'LOSS'}")
    except Exception as e:
        logger.error(f"Error recording learning outcome: {e}")
    finally:
        release_conn(conn)

def evaluate_signal_suitability(symbol, pattern):
    """
    Digunakan oleh Scanner sebelum memilih sinyal:
    - Return False jika koin atau pola memiliki riwayat performa buruk (Loss berulang).
    - Return modifier skor berdasarkan kehandalan pola yang dipelajari.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()

        # Cek apakah koin masuk blacklist karena sering false breakout
        cur.execute("SELECT is_blacklisted, win_rate FROM coin_learning WHERE symbol = %s", (symbol,))
        crow = cur.fetchone()
        if crow and crow[0] == 1:
            logger.info(f"🚫 [AI SKIP] Koin {symbol} diabaikan (Histori Win Rate terlalu rendah: {crow[1]:.1f}%)")
            return False, 0.0, f"Coin blacklisted (WR {crow[1]:.1f}%)"

        # Cek status pembelajaran pola
        cur.execute("SELECT is_disabled, score_weight, win_rate, total_trades FROM pattern_learning WHERE pattern_name = %s", (pattern,))
        prow = cur.fetchone()
        if prow:
            is_dis, weight, p_wr, p_total = prow
            if is_dis == 1:
                logger.info(f"🚫 [AI SKIP] Pola {pattern} dimatikan sementara (Histori Win Rate buruk: {p_wr:.1f}%)")
                return False, 0.0, f"Pattern {pattern} muted (WR {p_wr:.1f}%)"
            
            bonus_score = (weight - 1.0) * 2.0  # Bobot dinamis
            reason = f"AI Pattern WR: {p_wr:.1f}% ({p_total} trades)"
            return True, bonus_score, reason

        return True, 0.0, "New pattern (Neutral)"
    except Exception as e:
        logger.error(f"Evaluate learning error: {e}")
        return True, 0.0, "Default"
    finally:
        release_conn(conn)
