"""RFC 8628 Device Authorization Grant for Jiro CLI.

Implements the device code flow for secure CLI authentication.
No local server needed — works everywhere (desktop, SSH, Docker).
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class DeviceAuthError(Exception):
    """Device auth flow error."""
    pass


class DeviceAuthManager:
    """OAuth 2.0 Device Authorization Grant (RFC 8628) client."""

    def __init__(self, web_base: str = "https://searchjiro.vercel.app"):
        self.web_base = web_base.rstrip("/")
        self.device_code: Optional[str] = None
        self.user_code: Optional[str] = None
        self.verification_uri: Optional[str] = None
        self.verification_uri_complete: Optional[str] = None
        self.interval: int = 5
        self.expires_at: float = 0

    def request_code(self) -> dict:
        """Step 1: Request device code from server.

        Returns:
            {
                device_code, user_code, verification_uri,
                verification_uri_complete, expires_in, interval
            }
        """
        url = f"{self.web_base}/api/auth/device/code"
        req = Request(url, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req, data=b"{}", timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise DeviceAuthError(f"Failed to request device code: {e.code} {body}")
        except URLError as e:
            raise DeviceAuthError(f"Network error: {e.reason}")
        except Exception as e:
            raise DeviceAuthError(f"Unexpected error: {e}")

        self.device_code = data["device_code"]
        self.user_code = data["user_code"]
        self.verification_uri = data["verification_uri"]
        self.verification_uri_complete = data.get(
            "verification_uri_complete", self.verification_uri
        )
        self.interval = data.get("interval", 5)
        self.expires_at = time.time() + data.get("expires_in", 900)

        return data

    def poll_token(self) -> dict:
        """Step 2: Poll server for access token.

        Returns:
            On success: { api_key, user, plan, credits }
            On pending: None (caller should retry after interval)
            On error: raises DeviceAuthError

        Raises:
            DeviceAuthError: on expired_token, access_denied, or network errors
        """
        if not self.device_code:
            raise DeviceAuthError("No device code. Call request_code() first.")

        url = f"{self.web_base}/api/auth/device/token"
        payload = json.dumps({"device_code": self.device_code}).encode()
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(body)
            except Exception:
                error_data = {"error": f"HTTP {e.code}"}

            error_type = error_data.get("error", "")

            if e.code == 428 or error_type == "authorization_pending":
                return None  # Keep polling

            if error_type == "slow_down":
                # Increase interval
                self.interval = error_data.get("interval", self.interval + 5)
                return None

            if error_type == "expired_token" or e.code == 410:
                raise DeviceAuthError("Device code expired. Run 'jiro auth login' again.")

            if error_type == "access_denied" or e.code == 403:
                raise DeviceAuthError("Access denied by user.")

            raise DeviceAuthError(f"Token error: {error_type} ({e.code})")
        except URLError as e:
            raise DeviceAuthError(f"Network error: {e.reason}")
        except DeviceAuthError:
            raise
        except Exception as e:
            raise DeviceAuthError(f"Unexpected error: {e}")

    def wait_for_authorization(
        self,
        on_poll: Optional[Callable[[int], None]] = None,
    ) -> dict:
        """Blocking loop: poll until authorized or expired.

        Args:
            on_poll: Optional callback called with remaining seconds on each poll.

        Returns:
            { api_key, user, plan, credits }

        Raises:
            DeviceAuthError: on timeout, denial, or network errors
        """
        poll_count = 0
        while time.time() < self.expires_at:
            remaining = int(self.expires_at - time.time())
            if on_poll:
                on_poll(remaining)

            result = self.poll_token()
            if result is not None:
                return result

            poll_count += 1
            time.sleep(self.interval)

        raise DeviceAuthError(
            "Timed out waiting for authorization. Run 'jiro auth login' again."
        )
