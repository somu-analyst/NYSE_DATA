import subprocess
from datetime import datetime, timedelta
import os
from pathlib import Path

import pandas_market_calendars as mcal  # pip install pandas-market-calendars[web:195]


BASE_DIR = r"C:\Users\srini\Options_chain_data\NYSE_DATA"

JOB1 = os.path.join(BASE_DIR, "NYSE_YFin.py")
JOB2 = os.path.join(BASE_DIR, "NYSE_Telegram.py")
JOB3 = os.path.join(BASE_DIR, "NYSE_options_live.py")

STATE_DIR = BASE_DIR
STATE_FILE = os.path.join(STATE_DIR, "run_all_offhours_last_ok.txt")

NYSE = mcal.get_calendar("NYSE")  # reuse one instance[web:195]


def in_off_hours():
    now = datetime.now()
    h = now.hour
    # off hours: 17:00–23:59 or 00:00–08:59
    return (h >= 17) or (h < 9)


def is_trading_day(date_obj):
    d_str = date_obj.strftime("%Y-%m-%d")
    sched = NYSE.schedule(start_date=d_str, end_date=d_str)
    return not sched.empty  # schedule non-empty only for trading sessions[web:197]


def last_trading_day_before(date_obj):
    """Return last NYSE trading day on or before given date."""
    # go back some buffer days to handle weekends/holidays
    start = date_obj - timedelta(days=10)
    sched = NYSE.schedule(start_date=start.strftime("%Y-%m-%d"),
                          end_date=date_obj.strftime("%Y-%m-%d"))
    if sched.empty:
        return None
    # last index in schedule is last trading day
    return sched.index[-1].date()


def already_ran_for(date_obj):
    """True if we have a success flag recorded for that calendar date."""
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE, "r") as f:
            last_date_str = f.read().strip()
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        return last_date == date_obj
    except Exception:
        return False


def mark_success_for(date_obj):
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    d_str = date_obj.strftime("%Y-%m-%d")
    with open(STATE_FILE, "w") as f:
        f.write(d_str)


def run_job(path):
    python_exe = "python"  # or full path to python.exe
    subprocess.run([python_exe, path], check=True, cwd=os.path.dirname(path))


if __name__ == "__main__":
    now = datetime.now()
    today = now.date()

    # only run in off-hours
    if not in_off_hours():
        raise SystemExit(0)

    # find last trading day on or before today
    last_trd = last_trading_day_before(today)
    if last_trd is None:
        raise SystemExit(0)

    # rule:
    # - if today is a trading day: run only for today (no weekend makeup)
    # - if today is Sat/Sun: run only if last trading day was Fri (or any
    #   prior trading day) and that last_trd has NOT been successfully run
    is_today_trading = is_trading_day(today)

    if is_today_trading:
        # trading day: only run for today, and only if not already done
        target_day = today
        if already_ran_for(target_day):
            raise SystemExit(0)
    else:
        # non-trading day: allow weekend make-up only
        # only run if last_trd has not been processed yet
        target_day = last_trd
        if already_ran_for(target_day):
            raise SystemExit(0)

    # run sequence; if any fails, do NOT mark success
    run_job(JOB1)
    run_job(JOB2)
    run_job(JOB3)

    # mark success for target trading day (Friday if weekend make-up)
    mark_success_for(target_day)
