#!/usr/bin/env python3
"""
NIFTY 50 Previous Day High/Low Breakout Alert Bot
Fetches previous day high/low from Dhan API and sends Telegram alerts on breakout
"""

import os
import sys
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz

# Import token manager for automatic token renewal
from token_manager import DhanTokenManager
from railway_variable_client import RailwayVariableClient

# Load environment variables
load_dotenv()

# Configuration
DHAN_API_TOKEN = os.getenv("DHAN_API_TOKEN")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID")
FORCE_TOKEN_RENEW_ON_START = os.getenv("FORCE_TOKEN_RENEW_ON_START", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

# Indian timezone
IST = pytz.timezone('Asia/Kolkata')

# Dhan API endpoints
DHAN_BASE_URL = "https://api.dhan.co/v2"
HISTORICAL_DATA_URL = f"{DHAN_BASE_URL}/charts/historical"
LTP_URL = f"{DHAN_BASE_URL}/marketfeed/ltp"

# NIFTY 50 Security ID (IDX_I segment)
# Security ID for NIFTY 50 index is 13
NIFTY_SECURITY_ID = "13"
EXCHANGE_SEGMENT = "IDX_I"

# Token Manager instance (initialized after Telegram setup)
# This handles automatic token renewal
token_manager: DhanTokenManager = None


def get_dhan_headers() -> dict:
    """Get headers for standard Dhan API calls."""
    if token_manager:
        return token_manager.get_headers()
    # Fallback for legacy compatibility
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_API_TOKEN
    }


def get_dhan_market_headers() -> dict:
    """Get headers for Market Feed API calls."""
    if token_manager:
        return token_manager.get_market_headers()
    # Fallback for legacy compatibility
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_API_TOKEN,
        "client-id": DHAN_CLIENT_ID
    }

# Alert threshold - warn when price is within this many points of high/low
WARNING_THRESHOLD = 20

# Tracking state for breakout alerts
class BreakoutState:
    high_broken = False
    low_broken = False
    high_warning_sent = False
    low_warning_sent = False
    previous_high = None
    previous_low = None


def validate_dhan_token() -> bool:
    """Validate if Dhan API token is valid using token manager."""
    global token_manager
    
    if token_manager:
        is_valid, error = token_manager.validate_token()
        if not is_valid:
            print("\n⚠️  Your Dhan API token has expired!")
            print("Please generate a new token from: https://web.dhan.co/")
            print("Go to: My Profile → Access DhanHQ APIs → Generate Access Token")
        return is_valid
    
    # Fallback to direct validation if token_manager not initialized
    url = f"{DHAN_BASE_URL}/profile"
    try:
        response = requests.get(url, headers=get_dhan_headers(), timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Token valid until: {data.get('tokenValidity', 'Unknown')}")
            print(f"📊 Data Plan: {data.get('dataPlan', 'Unknown')}")
            return True
        else:
            print(f"❌ Token validation failed: {response.text}")
            print("\n⚠️  Your Dhan API token has expired!")
            print("Please generate a new token from: https://web.dhan.co/")
            print("Go to: My Profile → Access DhanHQ APIs → Generate Access Token")
            return False
    except Exception as e:
        print(f"❌ Error validating token: {e}")
        return False


def send_telegram_message(message: str) -> bool:
    """Send a message to Telegram channel/group"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ Telegram message sent: {message}")
            return True
        else:
            print(f"❌ Failed to send Telegram message: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")
        return False


def get_missing_railway_persistence_vars() -> list:
    """Get list of Railway env vars required for token persistence."""
    required = {
        "RAILWAY_API_TOKEN": RAILWAY_API_TOKEN,
        "RAILWAY_PROJECT_ID": RAILWAY_PROJECT_ID,
        "RAILWAY_ENVIRONMENT_ID": RAILWAY_ENVIRONMENT_ID,
        "RAILWAY_SERVICE_ID": RAILWAY_SERVICE_ID,
    }
    return [name for name, value in required.items() if not value]


def is_railway_persistence_enabled() -> bool:
    """Check if Railway token persistence is fully configured."""
    return len(get_missing_railway_persistence_vars()) == 0


def persist_token_to_railway(new_token: str) -> tuple:
    """Persist renewed DHAN_API_TOKEN into Railway service variables."""
    missing_vars = get_missing_railway_persistence_vars()
    if missing_vars:
        return False, f"Missing Railway env vars: {', '.join(missing_vars)}"

    client = RailwayVariableClient(
        api_token=RAILWAY_API_TOKEN,
        project_id=RAILWAY_PROJECT_ID,
        environment_id=RAILWAY_ENVIRONMENT_ID,
        service_id=RAILWAY_SERVICE_ID,
    )
    return client.upsert_service_variable("DHAN_API_TOKEN", new_token)


def get_previous_day_high_low() -> tuple:
    """
    Fetch previous trading day's high and low for NIFTY 50
    Returns: (high, low) tuple or (None, None) on failure
    """
    today = datetime.now()
    
    # Calculate previous trading day (skip weekends)
    prev_day = today - timedelta(days=1)
    while prev_day.weekday() >= 5:  # Saturday = 5, Sunday = 6
        prev_day -= timedelta(days=1)
    
    # For historical data, we need a range - get last 5 days to be safe
    from_date = (prev_day - timedelta(days=5)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")
    
    payload = {
        "securityId": NIFTY_SECURITY_ID,
        "exchangeSegment": EXCHANGE_SEGMENT,
        "instrument": "INDEX",
        "expiryCode": 0,
        "oi": False,
        "fromDate": from_date,
        "toDate": to_date
    }
    
    try:
        response = requests.post(
            HISTORICAL_DATA_URL,
            headers=get_dhan_headers(),
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "high" in data and "low" in data and len(data["high"]) > 0:
                # Get the last entry (previous trading day)
                prev_high = data["high"][-1]
                prev_low = data["low"][-1]
                prev_close = data["close"][-1] if "close" in data else None
                
                print(f"📊 Previous Day Data - High: {prev_high}, Low: {prev_low}, Close: {prev_close}")
                return prev_high, prev_low
            else:
                print(f"❌ No historical data found in response: {data}")
                return None, None
        else:
            print(f"❌ Failed to fetch historical data: {response.status_code} - {response.text}")
            return None, None
            
    except Exception as e:
        print(f"❌ Error fetching historical data: {e}")
        return None, None


def get_current_ltp() -> float:
    """
    Fetch current LTP for NIFTY 50
    Returns: LTP value or None on failure
    """
    payload = {
        EXCHANGE_SEGMENT: [int(NIFTY_SECURITY_ID)]
    }
    
    try:
        response = requests.post(
            LTP_URL,
            headers=get_dhan_market_headers(),
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "data" in data and EXCHANGE_SEGMENT in data["data"]:
                ltp = data["data"][EXCHANGE_SEGMENT][NIFTY_SECURITY_ID]["last_price"]
                return ltp
            else:
                print(f"❌ LTP data not found in response: {data}")
                return None
        else:
            print(f"❌ Failed to fetch LTP: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Error fetching LTP: {e}")
        return None


def check_breakout(current_price: float, prev_high: float, prev_low: float, state: BreakoutState) -> None:
    """
    Check if current price has broken previous day's high or low
    Sends Telegram alert on first breakout of the day
    Also sends warning when price is within WARNING_THRESHOLD points
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate distance from high and low
    distance_from_high = prev_high - current_price
    distance_from_low = current_price - prev_low
    
    # Check if approaching high (warning alert)
    if 0 < distance_from_high <= WARNING_THRESHOLD and not state.high_warning_sent and not state.high_broken:
        message = (
            f"⚠️ <b>NIFTY 50 - APPROACHING HIGH!</b>\n\n"
            f"📈 Previous Day High: <b>{prev_high:.2f}</b>\n"
            f"💹 Current Price: <b>{current_price:.2f}</b>\n"
            f"📏 Distance: <b>{distance_from_high:.2f}</b> points away\n"
            f"🕐 Time: {timestamp}"
        )
        send_telegram_message(message)
        state.high_warning_sent = True
    
    # Check if approaching low (warning alert)
    if 0 < distance_from_low <= WARNING_THRESHOLD and not state.low_warning_sent and not state.low_broken:
        message = (
            f"⚠️ <b>NIFTY 50 - APPROACHING LOW!</b>\n\n"
            f"📉 Previous Day Low: <b>{prev_low:.2f}</b>\n"
            f"💹 Current Price: <b>{current_price:.2f}</b>\n"
            f"📏 Distance: <b>{distance_from_low:.2f}</b> points away\n"
            f"🕐 Time: {timestamp}"
        )
        send_telegram_message(message)
        state.low_warning_sent = True
    
    # Check if high is broken (only alert once per day)
    if current_price > prev_high and not state.high_broken:
        message = (
            f"🚀 <b>NIFTY 50 - HIGH BREAKOUT!</b>\n\n"
            f"📈 Previous Day High: <b>{prev_high:.2f}</b>\n"
            f"💹 Current Price: <b>{current_price:.2f}</b>\n"
            f"📊 Breakout by: <b>+{(current_price - prev_high):.2f}</b> points\n"
            f"🕐 Time: {timestamp}"
        )
        send_telegram_message(message)
        state.high_broken = True
        
    # Check if low is broken (only alert once per day)
    if current_price < prev_low and not state.low_broken:
        message = (
            f"🔻 <b>NIFTY 50 - LOW BREAKOUT!</b>\n\n"
            f"📉 Previous Day Low: <b>{prev_low:.2f}</b>\n"
            f"💹 Current Price: <b>{current_price:.2f}</b>\n"
            f"📊 Breakout by: <b>{(current_price - prev_low):.2f}</b> points\n"
            f"🕐 Time: {timestamp}"
        )
        send_telegram_message(message)
        state.low_broken = True


def is_within_trading_window() -> bool:
    """
    Check if we're within the trading window (9:15 AM - 3:30 PM IST)
    """
    now = datetime.now(IST)
    
    # Check if it's a weekday (Monday=0 to Friday=4)
    if now.weekday() >= 5:
        return False
    
    # Trading window (9:15 AM to 3:30 PM)
    window_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    window_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return window_start <= now <= window_end


def run_monitor(check_interval: int = 5) -> None:
    """
    Main monitoring loop
    Args:
        check_interval: Time in seconds between each price check
    """
    print("=" * 50)
    print("🔔 NIFTY 50 High/Low Breakout Alert Bot")
    print("=" * 50)
    
    current_time = datetime.now(IST)
    print(f"🕐 Current IST Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Validate configuration
    if not all([DHAN_API_TOKEN, DHAN_CLIENT_ID, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("❌ Missing required environment variables!")
        print("Please ensure .env file contains:")
        print("  - DHAN_API_TOKEN")
        print("  - DHAN_CLIENT_ID")
        print("  - TELEGRAM_BOT_TOKEN")
        print("  - TELEGRAM_CHAT_ID")
        return

    if is_railway_persistence_enabled():
        print("✅ Railway token persistence is enabled.")
    else:
        missing_vars = get_missing_railway_persistence_vars()
        print("⚠️ Railway token persistence is disabled.")
        print(f"   Missing env vars: {', '.join(missing_vars)}")
        print("   Bot will run, but post-restart token may require manual update.")

    # Initialize Token Manager with Telegram notification capability
    global token_manager
    token_manager = DhanTokenManager(
        access_token=DHAN_API_TOKEN,
        client_id=DHAN_CLIENT_ID,
        telegram_notify_func=send_telegram_message,
        renewal_threshold_hours=2.0,  # Renew 2 hours before expiry
        persist_token_func=persist_token_to_railway if is_railway_persistence_enabled() else None,
    )
    
    # Validate Dhan token
    print("\n🔐 Validating Dhan API token...")
    if not validate_dhan_token():
        return

    # Optional manual test switch: force token renewal immediately on startup.
    # Useful to verify end-to-end renewal + Railway persistence without waiting.
    if FORCE_TOKEN_RENEW_ON_START:
        print("🧪 FORCE_TOKEN_RENEW_ON_START is enabled. Forcing renewal now...")
        send_telegram_message(
            "🧪 <b>Forced Token Renewal Test</b>\n\n"
            "Triggering immediate renewal on startup for verification."
        )
        renew_ok, renew_error = token_manager.renew_token()
        if not renew_ok and token_manager.is_token_expired():
            print("🛑 Forced renewal failed and token is expired. Stopping bot.")
            send_telegram_message(
                "🛑 <b>Bot Stopped - Forced Renewal Failed</b>\n\n"
                "Token is expired and forced renewal failed.\n"
                "Please verify Dhan token and Railway configuration."
            )
            sys.exit(1)
        if not renew_ok:
            print(f"⚠️ Forced renewal failed but token still valid: {renew_error}")

    state = BreakoutState()
    last_token_check = datetime.now(IST)
    last_date = None
    was_in_trading_window = False
    
    while True:
        try:
            now = datetime.now(IST)
            current_date = now.date()
            in_trading_window = is_within_trading_window()

            # Log window transitions for clarity without noisy logs.
            if in_trading_window and not was_in_trading_window:
                print(f"🟢 Entered trading window at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
            elif not in_trading_window and was_in_trading_window:
                print(f"🟡 Exited trading window at {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
            was_in_trading_window = in_trading_window
            
            # Reset state at the start of each new day
            if last_date != current_date:
                state.high_broken = False
                state.low_broken = False
                state.high_warning_sent = False
                state.low_warning_sent = False
                state.previous_high = None
                state.previous_low = None
                last_date = current_date
                print(f"\n📅 New trading day: {current_date}")

            # Periodic token renewal check (every 30 minutes) even outside trading hours
            if (now - last_token_check).total_seconds() >= 1800:  # 30 minutes
                last_token_check = now
                if token_manager and token_manager.should_renew():
                    print("🔄 Token renewal check triggered...")
                    if not token_manager.check_and_renew_if_needed():
                        print("🛑 Token expired and renewal failed. Stopping bot.")
                        send_telegram_message(
                            "🛑 <b>Bot Stopped - Dhan Token Expired</b>\n\n"
                            "Renewal failed after token expiry.\n"
                            "Please verify Dhan token and Railway persistence configuration."
                        )
                        sys.exit(1)
                else:
                    # Log token status periodically
                    time_remaining = token_manager.get_time_until_expiry() if token_manager else None
                    if time_remaining:
                        print(f"🔑 Token status: {time_remaining} until expiry")
            
            # Keep process alive 24x7, but skip market data checks outside the trading window.
            if not in_trading_window:
                time.sleep(60)
                continue

            # Fetch previous day high/low if not already fetched
            if state.previous_high is None or state.previous_low is None:
                prev_high, prev_low = get_previous_day_high_low()
                
                if prev_high is not None and prev_low is not None:
                    state.previous_high = prev_high
                    state.previous_low = prev_low
                    
                    # Send startup message
                    startup_msg = (
                        f"🟢 <b>Bot Started - NIFTY 50 Monitoring</b>\n\n"
                        f"📊 Previous Day High: <b>{prev_high:.2f}</b>\n"
                        f"📊 Previous Day Low: <b>{prev_low:.2f}</b>\n"
                        f"📏 Range: <b>{(prev_high - prev_low):.2f}</b> points\n"
                        f"🕐 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    send_telegram_message(startup_msg)
                else:
                    print("⏳ Waiting to fetch previous day data...")
                    time.sleep(60)  # Wait 1 minute before retry
                    continue
            
            # Get current LTP
            current_price = get_current_ltp()
            
            if current_price is not None:
                print(f"💹 NIFTY LTP: {current_price:.2f} | High: {state.previous_high:.2f} | Low: {state.previous_low:.2f} | Time: {datetime.now().strftime('%H:%M:%S')}")
                
                # Check for breakouts
                check_breakout(current_price, state.previous_high, state.previous_low, state)
            
            # Wait before next check
            time.sleep(check_interval)
            
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
            send_telegram_message("🔴 <b>Bot Stopped</b>\nNIFTY 50 monitoring has been stopped.")
            break
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            time.sleep(30)  # Wait before retry on error


if __name__ == "__main__":
    # Run with 5-second interval between checks
    run_monitor(check_interval=5)
