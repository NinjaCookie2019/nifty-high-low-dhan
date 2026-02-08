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

# Load environment variables
load_dotenv()

# Configuration
DHAN_API_TOKEN = os.getenv("DHAN_API_TOKEN")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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


def is_market_open() -> bool:
    """
    Check if Indian stock market is open
    Market hours: 9:15 AM - 3:30 PM IST, Monday to Friday
    """
    now = datetime.now(IST)
    
    # Check if it's a weekday
    if now.weekday() >= 5:
        return False
    
    # Market hours (9:15 AM to 3:30 PM)
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_open <= now <= market_close


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
    
    # Check if within trading window - EXIT if outside (saves Railway hours)
    if not is_within_trading_window():
        print(f"\n⏰ Outside trading hours (9:15 AM - 3:30 PM IST, Mon-Fri)")
        print(f"📅 Today is: {current_time.strftime('%A')}")
        print("🛑 Exiting to save resources. Will restart at next scheduled time.")
        send_telegram_message(
            f"🔴 <b>Bot Stopped</b>\n\n"
            f"Outside trading hours.\n"
            f"Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')} IST"
        )
        sys.exit(0)
    
    # Validate configuration
    if not all([DHAN_API_TOKEN, DHAN_CLIENT_ID, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("❌ Missing required environment variables!")
        print("Please ensure .env file contains:")
        print("  - DHAN_API_TOKEN")
        print("  - DHAN_CLIENT_ID")
        print("  - TELEGRAM_BOT_TOKEN")
        print("  - TELEGRAM_CHAT_ID")
        return
    
    # Initialize Token Manager with Telegram notification capability
    global token_manager
    token_manager = DhanTokenManager(
        access_token=DHAN_API_TOKEN,
        client_id=DHAN_CLIENT_ID,
        telegram_notify_func=send_telegram_message,
        renewal_threshold_hours=2.0  # Renew 2 hours before expiry
    )
    
    # Validate Dhan token
    print("\n🔐 Validating Dhan API token...")
    if not validate_dhan_token():
        return
    
    state = BreakoutState()
    last_token_check = datetime.now(IST)
    last_date = None
    
    while True:
        try:
            # Exit if market window has closed
            if not is_within_trading_window():
                print("\n🛑 Market closed. Exiting bot to save resources.")
                send_telegram_message(
                    f"🔴 <b>Bot Stopped - Market Closed</b>\n\n"
                    f"Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
                    f"Will restart tomorrow at 9:00 AM IST"
                )
                sys.exit(0)
            
            current_date = datetime.now(IST).date()
            
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
            
            # Check if market is open
            if not is_market_open():
                print(f"⏸️  Market closed. Waiting... (Time: {datetime.now().strftime('%H:%M:%S')})")
                time.sleep(60)  # Check every minute when market is closed
                continue
            
            # Periodic token renewal check (every 30 minutes)
            now = datetime.now(IST)
            if (now - last_token_check).total_seconds() >= 1800:  # 30 minutes
                last_token_check = now
                if token_manager and token_manager.should_renew():
                    print("🔄 Token renewal check triggered...")
                    if not token_manager.check_and_renew_if_needed():
                        print("⚠️ Token renewal failed - will retry later")
                else:
                    # Log token status periodically
                    time_remaining = token_manager.get_time_until_expiry() if token_manager else None
                    if time_remaining:
                        print(f"🔑 Token status: {time_remaining} until expiry")
            
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
