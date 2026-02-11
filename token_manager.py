#!/usr/bin/env python3
"""
Dhan API Token Manager
Handles token validation, renewal, and lifecycle management
"""

from datetime import datetime, timedelta
from typing import Callable, Optional, Tuple

import pytz
import requests

# Indian timezone
IST = pytz.timezone('Asia/Kolkata')

# Dhan API endpoints
DHAN_BASE_URL = "https://api.dhan.co/v2"
PROFILE_URL = f"{DHAN_BASE_URL}/profile"
RENEW_TOKEN_URL = f"{DHAN_BASE_URL}/RenewToken"


class DhanTokenManager:
    """
    Manages Dhan API token lifecycle including validation and renewal.
    
    Dhan tokens expire after 24 hours. This manager:
    1. Validates token on startup
    2. Tracks token expiry time
    3. Automatically renews token before expiry
    4. Provides headers with valid token for API calls
    """
    
    def __init__(
        self,
        access_token: str,
        client_id: str,
        telegram_notify_func=None,
        renewal_threshold_hours: float = 2.0,
        persist_token_func: Optional[Callable[[str], Tuple[bool, Optional[str]]]] = None,
    ):
        """
        Initialize token manager.
        
        Args:
            access_token: Current Dhan API access token
            client_id: Dhan client ID
            telegram_notify_func: Optional function to send Telegram notifications
            renewal_threshold_hours: Hours before expiry to trigger renewal
            persist_token_func: Optional callback to persist renewed token
        """
        self._access_token = access_token
        self._client_id = client_id
        self._telegram_notify = telegram_notify_func
        self._renewal_threshold = timedelta(hours=renewal_threshold_hours)
        self._persist_token_func = persist_token_func
        self._token_expiry: Optional[datetime] = None
        self._token_validity_str: Optional[str] = None
        self._last_validation: Optional[datetime] = None
        self._last_renewal_failed = False
        self._token_expired_alert_sent = False
        self._last_persist_ok: Optional[bool] = None
    
    @property
    def access_token(self) -> str:
        """Get current access token."""
        return self._access_token
    
    @property
    def client_id(self) -> str:
        """Get client ID."""
        return self._client_id

    @property
    def last_persist_ok(self) -> Optional[bool]:
        """Get status of latest token persistence attempt."""
        return self._last_persist_ok
    
    def get_headers(self) -> dict:
        """Get headers for standard Dhan API calls."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self._access_token
        }
    
    def get_market_headers(self) -> dict:
        """Get headers for Dhan Market Feed API calls (requires client-id)."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self._access_token,
            "client-id": self._client_id
        }
    
    def validate_token(self) -> Tuple[bool, Optional[str]]:
        """
        Validate if current token is valid by calling profile endpoint.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            response = requests.get(
                PROFILE_URL,
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self._token_validity_str = data.get('tokenValidity', 'Unknown')
                self._last_validation = datetime.now(IST)
                
                # Parse token validity to datetime
                self._parse_token_expiry(self._token_validity_str)
                
                print(f"✅ Token valid until: {self._token_validity_str}")
                print(f"📊 Data Plan: {data.get('dataPlan', 'Unknown')}")
                return True, None
            else:
                error_msg = f"Token validation failed: {response.text}"
                print(f"❌ {error_msg}")
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Error validating token: {e}"
            print(f"❌ {error_msg}")
            return False, error_msg
    
    def _parse_token_expiry(self, validity_str: str) -> None:
        """
        Parse token validity string to datetime.
        Dhan returns format like: "2024-12-28 10:30:00"
        """
        try:
            # Try common formats
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S"]:
                try:
                    dt = datetime.strptime(validity_str, fmt)
                    self._token_expiry = IST.localize(dt)
                    return
                except ValueError:
                    continue
            
            # If parsing fails, assume 24 hours from now
            print(f"⚠️ Could not parse token validity '{validity_str}', assuming 24hr validity")
            self._token_expiry = datetime.now(IST) + timedelta(hours=24)
            
        except Exception as e:
            print(f"⚠️ Error parsing token expiry: {e}")
            self._token_expiry = datetime.now(IST) + timedelta(hours=24)
    
    def get_token_expiry(self) -> Optional[datetime]:
        """Get token expiry datetime."""
        return self._token_expiry
    
    def get_time_until_expiry(self) -> Optional[timedelta]:
        """Get time remaining until token expires."""
        if self._token_expiry is None:
            return None
        return self._token_expiry - datetime.now(IST)
    
    def should_renew(self) -> bool:
        """
        Check if token should be renewed.
        Returns True if token expires within renewal threshold.
        """
        if self._token_expiry is None:
            return False
        
        time_remaining = self.get_time_until_expiry()
        if time_remaining is None:
            return False
        
        return time_remaining <= self._renewal_threshold

    def is_token_expired(self) -> bool:
        """Check whether token has already expired."""
        time_remaining = self.get_time_until_expiry()
        if time_remaining is None:
            return False
        return time_remaining.total_seconds() <= 0
    
    def renew_token(self) -> Tuple[bool, Optional[str]]:
        """
        Renew the access token using Dhan's RenewToken API.
        
        The renewed token will be valid for another 24 hours.
        Note: A token can only be renewed once before it expires.
        
        Returns:
            Tuple of (success, new_token_or_error)
        """
        print("🔄 Attempting to renew Dhan API token...")

        try:
            renew_headers = {
                "Accept": "application/json",
                "access-token": self._access_token,
                "dhanClientId": self._client_id,
            }
            response = requests.get(
                RENEW_TOKEN_URL,
                headers=renew_headers,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                new_token = (
                    data.get('accessToken')
                    or data.get('access_token')
                    or data.get('token')
                )

                if new_token:
                    old_token_prefix = self._access_token[:10] if self._access_token else "N/A"
                    self._access_token = new_token

                    # Validate the new token to get expiry
                    is_valid, validation_error = self.validate_token()

                    if is_valid:
                        recovered_from_failure = self._last_renewal_failed
                        self._last_renewal_failed = False
                        self._token_expired_alert_sent = False

                        persist_ok, persist_error = self._persist_renewed_token(new_token)

                        print(f"✅ Token renewed successfully!")
                        print(f"   Old token prefix: {old_token_prefix}...")
                        print(f"   New token prefix: {new_token[:10]}...")

                        # Notify via Telegram
                        self._send_renewal_success_notification(
                            new_token=new_token,
                            persist_ok=persist_ok,
                            persist_error=persist_error,
                            recovered_from_failure=recovered_from_failure,
                        )

                        return True, new_token
                    return self._handle_renewal_failure(
                        validation_error or "New token validation failed"
                    )
                else:
                    safe_payload = self._sanitize_payload(data)
                    error_msg = f"No token in renewal response: {safe_payload}"
                    return self._handle_renewal_failure(error_msg)
            else:
                error_msg = f"Token renewal failed: {response.status_code} - {response.text}"
                return self._handle_renewal_failure(error_msg)

        except Exception as e:
            error_msg = f"Error renewing token: {e}"
            return self._handle_renewal_failure(error_msg)

    def _sanitize_payload(self, payload: dict) -> dict:
        """Remove sensitive token-like values before logging or notifications."""
        if not isinstance(payload, dict):
            return {"value": str(payload)}

        safe_payload = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if "token" in lowered:
                token_value = str(value) if value is not None else ""
                safe_payload[key] = f"{token_value[:10]}..." if token_value else "REDACTED"
            else:
                safe_payload[key] = value
        return safe_payload

    def _persist_renewed_token(self, new_token: str) -> Tuple[Optional[bool], Optional[str]]:
        """Persist renewed token using configured callback, if available."""
        if self._persist_token_func is None:
            self._last_persist_ok = None
            return None, None

        try:
            persist_ok, persist_error = self._persist_token_func(new_token)
        except Exception as exc:
            persist_ok = False
            persist_error = f"Exception from token persistence callback: {exc}"

        self._last_persist_ok = persist_ok

        if persist_ok:
            print("✅ Renewed token persisted successfully.")
            return True, None

        error_msg = persist_error or "Unknown token persistence error"
        print(f"⚠️ Token renewed, but persistence failed: {error_msg}")
        return False, error_msg

    def _handle_renewal_failure(self, error_msg: str) -> Tuple[bool, Optional[str]]:
        """Handle failed renewal with transition-aware notifications."""
        print(f"❌ {error_msg}")

        first_failure = not self._last_renewal_failed
        self._last_renewal_failed = True

        if first_failure:
            self._send_first_renewal_failure_notification(error_msg)

        if self.is_token_expired():
            if not self._token_expired_alert_sent:
                self._send_token_expired_notification(error_msg)
                self._token_expired_alert_sent = True
            return False, error_msg

        return False, error_msg

    def _send_renewal_success_notification(
        self,
        new_token: str,
        persist_ok: Optional[bool],
        persist_error: Optional[str],
        recovered_from_failure: bool,
    ) -> None:
        """Send Telegram notification about token renewal success."""
        if self._telegram_notify is None:
            return

        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

        if persist_ok is True:
            persistence_status = "✅ Railway variable updated (DHAN_API_TOKEN)"
        elif persist_ok is False:
            persistence_status = (
                "⚠️ Token renewed in memory, but Railway update failed.\n"
                f"🚨 Error: {persist_error}"
            )
        else:
            persistence_status = "ℹ️ Railway persistence not configured"

        recovery_line = (
            "✅ Recovered from previous renewal failure\n"
            if recovered_from_failure
            else ""
        )

        message = (
            f"🔄 <b>Token Renewed Successfully</b>\n\n"
            f"✅ New token is active\n"
            f"{recovery_line}"
            f"{persistence_status}\n"
            f"📅 Valid for: 24 hours\n"
            f"🕐 Time: {timestamp}\n\n"
            f"<i>Token prefix:</i>\n"
            f"<code>{new_token[:10]}...</code>"
        )

        try:
            self._telegram_notify(message)
        except Exception as e:
            print(f"⚠️ Failed to send Telegram notification: {e}")

    def _send_first_renewal_failure_notification(self, error: str) -> None:
        """Send first-failure transition notification."""
        if self._telegram_notify is None:
            return

        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"⚠️ <b>Dhan Token Renewal Failed</b>\n\n"
            f"🚨 Error: {error}\n"
            f"🔁 Bot will retry automatically.\n"
            f"🕐 Time: {timestamp}"
        )
        try:
            self._telegram_notify(message)
        except Exception as e:
            print(f"⚠️ Failed to send Telegram notification: {e}")

    def _send_token_expired_notification(self, error: str) -> None:
        """Send critical notification when token has expired."""
        if self._telegram_notify is None:
            return

        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🛑 <b>Dhan Token Expired</b>\n\n"
            f"❌ Renewal failed after expiry.\n"
            f"🚨 Error: {error}\n"
            f"⚠️ Bot will stop until token is fixed.\n"
            f"🕐 Time: {timestamp}"
        )
        try:
            self._telegram_notify(message)
        except Exception as e:
            print(f"⚠️ Failed to send Telegram notification: {e}")

    def check_and_renew_if_needed(self) -> bool:
        """
        Check if token needs renewal and renew if necessary.
        
        Returns:
            True if token is valid (either not expired or successfully renewed)
        """
        if not self.should_renew():
            return True
        
        time_remaining = self.get_time_until_expiry()
        print(f"⏰ Token expires in {time_remaining}. Attempting renewal...")
        
        success, error = self.renew_token()
        if success:
            return True

        if self.is_token_expired():
            print("🛑 Token renewal failed and token has expired.")
            return False

        print(f"⚠️ Token renewal failed but token still valid. Will retry. Error: {error}")
        return True

    def get_status(self) -> dict:
        """Get current token status for debugging/logging."""
        time_remaining = self.get_time_until_expiry()

        return {
            "token_prefix": self._access_token[:10] + "..." if self._access_token else None,
            "client_id": self._client_id,
            "expiry": self._token_expiry.isoformat() if self._token_expiry else None,
            "time_remaining": str(time_remaining) if time_remaining else None,
            "should_renew": self.should_renew(),
            "is_expired": self.is_token_expired(),
            "last_validation": self._last_validation.isoformat() if self._last_validation else None,
            "last_renewal_failed": self._last_renewal_failed,
            "last_persist_ok": self._last_persist_ok,
        }
