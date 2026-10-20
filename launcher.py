"""
Launcher: Menjalankan Scanner (main.py) dan SpotTrader (spot_trader.py) bersamaan.

- main.py       : Scanner menggunakan data Futures untuk analisa sinyal
- spot_trader.py: Eksekusi beli/jual di pasar SPOT (tanpa leverage)
"""
import subprocess
import sys
import time

def main():
    print("=" * 55)
    print("  🚀 Bybit Hybrid Bot - Scanner Futures + Spot Trader")
    print("=" * 55)
    print("  📡 Scanner  : membaca data Futures (volume tinggi)")
    print("  🛒 Executor : mengeksekusi order di pasar SPOT")
    print("  ⚡ No Leverage | No Liquidation Risk")
    print("=" * 55)

    # Jalankan Scanner sinyal (baca data Futures)
    p1 = subprocess.Popen([sys.executable, "main.py"])
    print("✅ Scanner (main.py) dimulai...")

    time.sleep(3)  # Beri waktu scanner inisialisasi DB dulu

    # Jalankan Spot Trader (eksekusi di Spot)
    p2 = subprocess.Popen([sys.executable, "spot_trader.py"])
    print("✅ Spot Trader (spot_trader.py) dimulai...")
    print("\n🟢 Sistem berjalan. Tekan Ctrl+C untuk berhenti.\n")

    try:
        p1.wait()
        p2.wait()
    except KeyboardInterrupt:
        print("\n⛔ Sinyal berhenti diterima. Mematikan sistem...")
        p1.terminate()
        p2.terminate()
        p1.wait()
        p2.wait()
        print("✅ Sistem berhasil dimatikan secara aman.")

if __name__ == "__main__":
    main()
