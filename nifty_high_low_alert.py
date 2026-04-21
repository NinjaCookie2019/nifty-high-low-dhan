#!/usr/bin/env python3
"""
NIFTY 50 Previous Day High/Low Breakout Alert Bot
Fetches previous day high/low from Dhan API and sends Telegram alerts on breakout
"""

import os
import sys
import requests
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
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
PENDING_RAILWAY_TOKEN = None
STATE_FILE = Path(__file__).resolve().parent / "runtime_state.json"


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

def parse_float_env(name: str, default: float) -> float:
    """Read a float env var, falling back safely when it is unset or invalid."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        print(f"⚠️ Invalid {name}={raw_value!r}; using default {default}")
        return default


# Alert threshold - warn when price is within this many points of high/low
WARNING_THRESHOLD = 20

# Re-arm breakout alerts only after price pulls back through the level by this many points.
BREAKOUT_REARM_BUFFER = parse_float_env("BREAKOUT_REARM_BUFFER", 1.0)

# Tracking state for breakout alerts
class BreakoutState:
    def __init__(self):
        self.trade_date = None
        self.high_broken = False
        self.low_broken = False
        self.high_warning_sent = False
        self.low_warning_sent = False
        self.previous_high = None
        self.previous_low = None
        self.startup_message_sent = False

    def reset_for_date(self, trade_date) -> None:
        self.trade_date = trade_date.isoformat()
        self.high_broken = False
        self.low_broken = False
        self.high_warning_sent = False
        self.low_warning_sent = False
        self.previous_high = None
        self.previous_low = None
        self.startup_message_sent = False

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "high_broken": self.high_broken,
            "low_broken": self.low_broken,
            "high_warning_sent": self.high_warning_sent,
            "low_warning_sent": self.low_warning_sent,
            "previous_high": self.previous_high,
            "previous_low": self.previous_low,
            "startup_message_sent": self.startup_message_sent,
        }

    @classmethod
    def from_dict(cls, payload: dict):
        state = cls()
        if not isinstance(payload, dict):
            return state
        state.trade_date = payload.get("trade_date")
        state.high_broken = bool(payload.get("high_broken", False))
        state.low_broken = bool(payload.get("low_broken", False))
        state.high_warning_sent = bool(payload.get("high_warning_sent", False))
        state.low_warning_sent = bool(payload.get("low_warning_sent", False))
        state.previous_high = payload.get("previous_high")
        state.previous_low = payload.get("previous_low")
        state.startup_message_sent = bool(payload.get("startup_message_sent", False))
        return state


def save_breakout_state(state: BreakoutState) -> None:
    """Persist alert state so same-day restarts do not re-send alerts."""
    tmp_file = STATE_FILE.with_suffix(".tmp")
    try:
        tmp_file.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        tmp_file.replace(STATE_FILE)
    except Exception as exc:
        print(f"⚠️ Failed to persist runtime state: {exc}")


def load_breakout_state(current_date) -> BreakoutState:
    """Load today's persisted state, or start fresh if unavailable."""
    fresh_state = BreakoutState()
    fresh_state.reset_for_date(current_date)
    fresh_state.resumed_from_disk = False

    if not STATE_FILE.exists():
        return fresh_state

    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"⚠️ Failed to read runtime state, starting fresh: {exc}")
        return fresh_state

    state = BreakoutState.from_dict(payload)
    if state.trade_date != current_date.isoformat():
        fresh_state.resumed_from_disk = False
        return fresh_state

    state.resumed_from_disk = True
    print(
        "♻️ Loaded saved state for today: "
        f"high_broken={state.high_broken}, low_broken={state.low_broken}, "
        f"high_warning_sent={state.high_warning_sent}, low_warning_sent={state.low_warning_sent}"
    )
    return state


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


def persist_token_to_railway_with_market_guard(new_token: str) -> tuple:
    """Avoid Railway restarts during market hours by deferring token persistence."""
    global PENDING_RAILWAY_TOKEN

    if is_within_trading_window():
        PENDING_RAILWAY_TOKEN = new_token
        return None, "ℹ️ Railway update deferred until market close to avoid a restart during trading hours."

    return persist_token_to_railway(new_token)


def flush_pending_railway_token() -> None:
    """Persist any deferred token once market hours are over."""
    global PENDING_RAILWAY_TOKEN

    if not PENDING_RAILWAY_TOKEN:
        return

    persist_ok, persist_error = persist_token_to_railway(PENDING_RAILWAY_TOKEN)
    if persist_ok:
        print("✅ Deferred Railway token persistence completed.")
        send_telegram_message(
            "✅ <b>Deferred Railway Token Update Completed</b>\n\n"
            "The renewed DHAN_API_TOKEN was pushed to Railway after market close."
        )
        PENDING_RAILWAY_TOKEN = None
        return

    print(f"⚠️ Deferred Railway token persistence failed: {persist_error}")


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


def check_breakout(current_price: float, prev_high: float, prev_low: float, state: BreakoutState) -> bool:
    """
    Check if current price has broken previous day's high or low
    Sends Telegram alert on first breakout of the day
    Also sends warning when price is within WARNING_THRESHOLD points
    """
    timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    state_changed = False
    
    # Calculate distance from high and low
    distance_from_high = prev_high - current_price
    distance_from_low = current_price - prev_low

    if state.high_broken and current_price <= prev_high - BREAKOUT_REARM_BUFFER:
        state.high_broken = False
        state.high_warning_sent = True
        state_changed = True
        print(
            f"🔁 High breakout re-armed after pullback: "
            f"LTP {current_price:.2f} <= {prev_high - BREAKOUT_REARM_BUFFER:.2f}"
        )

    if state.low_broken and current_price >= prev_low + BREAKOUT_REARM_BUFFER:
        state.low_broken = False
        state.low_warning_sent = True
        state_changed = True
        print(
            f"🔁 Low breakout re-armed after pullback: "
            f"LTP {current_price:.2f} >= {prev_low + BREAKOUT_REARM_BUFFER:.2f}"
        )
    
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
        state_changed = True
    
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
        state_changed = True
    
    # Check if high is broken (re-arms after price pulls back below the level)
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
        state_changed = True
        
    # Check if low is broken (re-arms after price bounces back above the level)
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
        state_changed = True

    return state_changed


def reconcile_state_with_price(current_price: float, prev_high: float, prev_low: float, state: BreakoutState) -> bool:
    """Mark already-triggered conditions after a restart without sending alerts again."""
    state_changed = False

    distance_from_high = prev_high - current_price
    distance_from_low = current_price - prev_low

    if current_price > prev_high and not state.high_broken:
        state.high_broken = True
        state.high_warning_sent = True
        state_changed = True
        print("♻️ Reconciled state: high breakout had already happened before restart.")
    elif 0 < distance_from_high <= WARNING_THRESHOLD and not state.high_warning_sent and not state.high_broken:
        state.high_warning_sent = True
        state_changed = True
        print("♻️ Reconciled state: already near previous high on restart.")

    if current_price < prev_low and not state.low_broken:
        state.low_broken = True
        state.low_warning_sent = True
        state_changed = True
        print("♻️ Reconciled state: low breakout had already happened before restart.")
    elif 0 < distance_from_low <= WARNING_THRESHOLD and not state.low_warning_sent and not state.low_broken:
        state.low_warning_sent = True
        state_changed = True
        print("♻️ Reconciled state: already near previous low on restart.")

    return state_changed


def should_send_startup_message(now: datetime) -> bool:
    """Only send daily startup notification near the market open."""
    notification_cutoff = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return now <= notification_cutoff


def maybe_send_startup_message(state: BreakoutState) -> None:
    """Send the trading-day startup message at most once."""
    if state.startup_message_sent:
        return

    now = datetime.now(IST)
    if not should_send_startup_message(now):
        print("ℹ️ Skipping startup Telegram message because monitoring resumed after market open.")
        state.startup_message_sent = True
        save_breakout_state(state)
        return

    startup_msg = (
        f"🟢 <b>Bot Started - NIFTY 50 Monitoring</b>\n\n"
        f"📊 Previous Day High: <b>{state.previous_high:.2f}</b>\n"
        f"📊 Previous Day Low: <b>{state.previous_low:.2f}</b>\n"
        f"📏 Range: <b>{(state.previous_high - state.previous_low):.2f}</b> points\n"
        f"🕐 Started at: {now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_telegram_message(startup_msg)
    state.startup_message_sent = True
    save_breakout_state(state)


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
        persist_token_func=(
            persist_token_to_railway_with_market_guard if is_railway_persistence_enabled() else None
        ),
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

    state = load_breakout_state(current_time.date())
    last_token_check = datetime.now(IST)
    last_date = current_time.date()
    was_in_trading_window = False
    startup_reconciliation_done = False
    
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
                state.reset_for_date(current_date)
                state.resumed_from_disk = False
                last_date = current_date
                startup_reconciliation_done = False
                print(f"\n📅 New trading day: {current_date}")
                save_breakout_state(state)

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
                flush_pending_railway_token()
                time.sleep(60)
                continue

            # Fetch previous day high/low if not already fetched
            if state.previous_high is None or state.previous_low is None:
                prev_high, prev_low = get_previous_day_high_low()
                
                if prev_high is not None and prev_low is not None:
                    state.previous_high = prev_high
                    state.previous_low = prev_low
                    startup_reconciliation_done = False
                    save_breakout_state(state)
                else:
                    print("⏳ Waiting to fetch previous day data...")
                    time.sleep(60)  # Wait 1 minute before retry
                    continue

            current_price = get_current_ltp()

            if current_price is not None and not startup_reconciliation_done:
                if (
                    getattr(state, "resumed_from_disk", False)
                    and reconcile_state_with_price(current_price, state.previous_high, state.previous_low, state)
                ):
                    save_breakout_state(state)

                maybe_send_startup_message(state)
                state.resumed_from_disk = False
                startup_reconciliation_done = True
            
            if current_price is not None:
                print(
                    f"💹 NIFTY LTP: {current_price:.2f} | High: {state.previous_high:.2f} | "
                    f"Low: {state.previous_low:.2f} | Time: {datetime.now(IST).strftime('%H:%M:%S')}"
                )
                
                # Check for breakouts
                if check_breakout(current_price, state.previous_high, state.previous_low, state):
                    save_breakout_state(state)
            
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
