#!/usr/bin/env python3
"""
Dhan API Token Manager
Handles token validation, renewal, and lifecycle management
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional, Tuple
import pytz

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
        renewal_threshold_hours: float = 2.0
    ):
        """
        Initialize token manager.
        
        Args:
            access_token: Current Dhan API access token
            client_id: Dhan client ID
            telegram_notify_func: Optional function to send Telegram notifications
            renewal_threshold_hours: Hours before expiry to trigger renewal
        """
        self._access_token = access_token
        self._client_id = client_id
        self._telegram_notify = telegram_notify_func
        self._renewal_threshold = timedelta(hours=renewal_threshold_hours)
        self._token_expiry: Optional[datetime] = None
        self._token_validity_str: Optional[str] = None
        self._last_validation: Optional[datetime] = None
    
    @property
    def access_token(self) -> str:
        """Get current access token."""
        return self._access_token
    
    @property
    def client_id(self) -> str:
        """Get client ID."""
        return self._client_id
    
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
            response = requests.post(
                RENEW_TOKEN_URL,
                headers=self.get_headers(),
                json={},  # Empty body
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                new_token = data.get('accessToken') or data.get('access_token')
                
                if new_token:
                    old_token_prefix = self._access_token[:10] if self._access_token else "N/A"
                    self._access_token = new_token
                    
                    # Validate the new token to get expiry
                    is_valid, _ = self.validate_token()
                    
                    if is_valid:
                        print(f"✅ Token renewed successfully!")
                        print(f"   Old token prefix: {old_token_prefix}...")
                        print(f"   New token prefix: {new_token[:10]}...")
                        
                        # Notify via Telegram
                        self._send_renewal_notification(success=True, new_token=new_token)
                        
                        return True, new_token
                    else:
                        return False, "New token validation failed"
                else:
                    error_msg = f"No token in renewal response: {data}"
                    print(f"❌ {error_msg}")
                    return False, error_msg
            else:
                error_msg = f"Token renewal failed: {response.status_code} - {response.text}"
                print(f"❌ {error_msg}")
                
                # Notify via Telegram about failure
                self._send_renewal_notification(success=False, error=error_msg)
                
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Error renewing token: {e}"
            print(f"❌ {error_msg}")
            self._send_renewal_notification(success=False, error=str(e))
            return False, error_msg
    
    def _send_renewal_notification(
        self,
        success: bool,
        new_token: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """Send Telegram notification about token renewal."""
        if self._telegram_notify is None:
            return
        
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        
        if success:
            message = (
                f"🔄 <b>Token Renewed Successfully</b>\n\n"
                f"✅ New token is active\n"
                f"📅 Valid for: 24 hours\n"
                f"🕐 Time: {timestamp}\n\n"
                f"<i>New token (first 20 chars):</i>\n"
                f"<code>{new_token[:20] if new_token else 'N/A'}...</code>\n\n"
                f"⚠️ <b>Update Railway env var if needed</b>"
            )
        else:
            message = (
                f"❌ <b>Token Renewal FAILED</b>\n\n"
                f"🚨 Error: {error}\n"
                f"🕐 Time: {timestamp}\n\n"
                f"⚠️ <b>Action Required:</b>\n"
                f"1. Go to https://web.dhan.co/\n"
                f"2. My Profile → Access DhanHQ APIs\n"
                f"3. Generate new Access Token\n"
                f"4. Update DHAN_API_TOKEN in Railway"
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
        
        success, _ = self.renew_token()
        return success
    
    def get_status(self) -> dict:
        """Get current token status for debugging/logging."""
        time_remaining = self.get_time_until_expiry()
        
        return {
            "token_prefix": self._access_token[:10] + "..." if self._access_token else None,
            "client_id": self._client_id,
            "expiry": self._token_expiry.isoformat() if self._token_expiry else None,
            "time_remaining": str(time_remaining) if time_remaining else None,
            "should_renew": self.should_renew(),
            "last_validation": self._last_validation.isoformat() if self._last_validation else None
        }
