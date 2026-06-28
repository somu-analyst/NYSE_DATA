@echo off
echo ========================================
echo Market Intelligence Dashboard + Bot
echo ========================================
echo.
echo Starting Telegram Bot (background)...
echo Starting Streamlit Dashboard...
echo.
echo Press Ctrl+C to stop the dashboard
echo ========================================
echo.

cd /d c:\Users\srini\Options_chain_data\NYSE_DATA

REM Start Telegram bot in background (hidden window)
start /B /MIN "" python telegram_bot.py

REM Start all dashboards on different ports
start /MIN python -m streamlit run dashboard.py --server.port 8502
start /MIN python -m streamlit run streamlit_dashboard.py --server.port 8503
start /MIN python -m streamlit run app_options_tracker_enhanced.py --server.port 8504

pause
