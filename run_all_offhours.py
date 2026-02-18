import os
import sys
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas_market_calendars as mcal
import ctypes

# ================== PATHS / CONFIG ==================
BASE_DIR = r"C:\Users\srini\Options_chain_data\NYSE_DATA"
JOB1 = os.path.join(BASE_DIR, "NYSE_YFin.py")
JOB2 = os.path.join(BASE_DIR, "NYSE_Telegram.py")
STATE_DIR = BASE_DIR
STATE_FILE = os.path.join(STATE_DIR, "run_all_offhours_last_ok.txt")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Anti-sleep flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# Globals
sleep_cookie = None
stop_sleep_thread = False
main_log = None  # Global for log_msg

def prevent_sleep():
    global sleep_cookie
    sleep_cookie = ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    )

def refresh_sleep():
    global stop_sleep_thread
    while not stop_sleep_thread:
        time.sleep(30)
        if sleep_cookie:
            ctypes.windll.kernel32.SetThreadExecutionState(sleep_cookie)

def allow_sleep():
    global stop_sleep_thread
    stop_sleep_thread = True
    if sleep_cookie:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

NYSE = mcal.get_calendar("NYSE")

def log_msg(msg):
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {msg}")
    if main_log:
        main_log.write(f"[{timestamp}] {msg}\n")
        main_log.flush()

# ... (keep all other helpers unchanged: in_off_hours, is_trading_day, etc.)

def run_job_headless(path):
    job_dir = os.path.dirname(path)
    job_name = os.path.splitext(os.path.basename(path))[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    log_file = os.path.join(LOG_DIR, f"{job_name}_{ts}.log")
    
    log_msg(f"Starting {job_name} -> {log_file}")
    
    with open(log_file, "a", encoding="utf-8") as log:
        log.write("=" * 80 + "\n")
        log.write(f"[{datetime.now().isoformat()}] Started: {path}\n\n")
        
        proc = subprocess.Popen(
            [sys.executable, "-u", path],
            cwd=job_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        
        for line in proc.stdout:
            log.write(line)
            log.flush()
        
        proc.wait()
        log.write(f"\n[{datetime.now().isoformat()}] Ended rc={proc.returncode}\n")
        log.write("=" * 80 + "\n")
    
    log_msg(f"{job_name} finished rc={proc.returncode}")
    return proc.returncode

if __name__ == "__main__":
    global stop_sleep_thread, main_log
    stop_sleep_thread = False
    start_time = datetime.now()
    ts = start_time.strftime("%Y%m%d_%H%M")
    main_log_path = os.path.join(LOG_DIR, f"scheduler_{ts}.log")
    
    main_log = open(main_log_path, "a", encoding="utf-8")
    
    try:
        prevent_sleep()
        sleep_thread = threading.Thread(target=refresh_sleep, daemon=True)
        sleep_thread.start()
        
        log_msg("=== SCHEDULER STARTED ===")
        log_msg(f"Today: {datetime.now().date()}")
        
        # Your existing logic (off-hours, trading day, etc.)
        if not in_off_hours():
            log_msg("Not off-hours. Exiting.")
            sys.exit(0)
        
        today = datetime.now().date()
        last_trd = last_trading_day_before(today)
        if last_trd is None:
            log_msg("No trading day found. Exiting.")
            sys.exit(0)
        
        is_today_trading = is_trading_day(today)
        log_msg(f"Today trading: {is_today_trading}, Last: {last_trd}")
        
        target_day = today if is_today_trading else last_trd
        if already_ran_for(target_day):
            log_msg(f"Already ran for {target_day}. Exiting.")
            sys.exit(0)
        
        log_msg(f"Running for target: {target_day}")
        
        rc1 = run_job_headless(JOB1)
        rc2 = run_job_headless(JOB2)
        
        success = (rc1 == 0 and rc2 == 0)
        if success:
            mark_success_for(target_day)
            log_msg("ALL JOBS SUCCESS!")
        else:
            log_msg(f"FAILURE: rc1={rc1}, rc2={rc2}")
        
        elapsed = (datetime.now() - start_time).total_seconds()
        log_msg(f"Total runtime: {elapsed:.1f}s")
        log_msg("=== SCHEDULER ENDED ===")
    
    finally:
        allow_sleep()
        if main_log:
            main_log.close()
    
    sys.exit(0 if success else 1)
