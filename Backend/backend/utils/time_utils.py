# backend/utils/time_utils.py
"""
Time and Timezone Utilities
============================
Provides local time information based on IP geolocation.
"""

import requests
from datetime import datetime
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class TimeZoneDetector:
    """
    Detects user's timezone from IP address and provides local time.
    """

    def __init__(self):
        self._cached_timezone: Optional[str] = None
        self._cached_ip: Optional[str] = None
        self._cache_timestamp: Optional[datetime] = None

    def get_timezone_from_ip(self, ip_address: str) -> Optional[str]:
        """
        Get timezone from IP address using ip-api.com (free, no key required).

        Args:
            ip_address: IP address to lookup

        Returns:
            Timezone string (e.g., "America/New_York") or None if failed
        """
        # Skip localhost/private IPs
        if ip_address in ["127.0.0.1", "localhost", "::1"] or ip_address.startswith("192.168."):
            logger.debug(f"Skipping timezone lookup for local IP: {ip_address}")
            return None

        # Check cache (valid for 1 hour)
        if (self._cached_ip == ip_address and
            self._cached_timezone and
            self._cache_timestamp and
            (datetime.utcnow() - self._cache_timestamp).total_seconds() < 3600):
            logger.debug(f"Using cached timezone for {ip_address}: {self._cached_timezone}")
            return self._cached_timezone

        try:
            # Use ip-api.com free service
            response = requests.get(
                f"http://ip-api.com/json/{ip_address}",
                timeout=3
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    timezone = data.get("timezone")
                    if timezone:
                        # Cache the result
                        self._cached_ip = ip_address
                        self._cached_timezone = timezone
                        self._cache_timestamp = datetime.utcnow()
                        logger.info(f"Detected timezone {timezone} for IP {ip_address}")
                        return timezone
                else:
                    logger.warning(f"IP API returned failure: {data.get('message', 'unknown error')}")
            else:
                logger.warning(f"IP API returned status {response.status_code}")

        except requests.RequestException as e:
            logger.error(f"Failed to get timezone for IP {ip_address}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error getting timezone: {e}")

        return None

    def get_local_time_info(self, ip_address: str) -> Dict[str, any]:
        """
        Get comprehensive local time information for an IP address.

        Returns:
            Dict with timezone, current_time, date, day_of_week, etc.
        """
        timezone_str = self.get_timezone_from_ip(ip_address)

        result = {
            "success": False,
            "ip_address": ip_address,
            "timezone": None,
            "current_time": None,
            "current_date": None,
            "day_of_week": None,
            "utc_time": datetime.utcnow().isoformat(),
        }

        if timezone_str:
            try:
                # Try to use pytz if available, otherwise use basic UTC offset
                try:
                    import pytz
                    tz = pytz.timezone(timezone_str)
                    local_time = datetime.now(tz)

                    result.update({
                        "success": True,
                        "timezone": timezone_str,
                        "current_time": local_time.strftime("%I:%M %p"),  # 12-hour format
                        "current_time_24h": local_time.strftime("%H:%M"),  # 24-hour format
                        "current_date": local_time.strftime("%Y-%m-%d"),
                        "day_of_week": local_time.strftime("%A"),
                        "full_datetime": local_time.strftime("%A, %B %d, %Y at %I:%M %p"),
                        "iso_datetime": local_time.isoformat(),
                    })
                except ImportError:
                    # Fallback: just use UTC time with timezone name
                    utc_time = datetime.utcnow()
                    result.update({
                        "success": True,
                        "timezone": timezone_str,
                        "current_time": utc_time.strftime("%I:%M %p") + " (UTC)",
                        "current_date": utc_time.strftime("%Y-%m-%d"),
                        "day_of_week": utc_time.strftime("%A"),
                        "warning": "pytz not installed, showing UTC time"
                    })

            except Exception as e:
                logger.error(f"Error formatting time for timezone {timezone_str}: {e}")
        else:
            # Fallback to UTC if no timezone detected
            utc_time = datetime.utcnow()
            result.update({
                "success": False,
                "timezone": "UTC",
                "current_time": utc_time.strftime("%I:%M %p") + " UTC",
                "current_date": utc_time.strftime("%Y-%m-%d"),
                "day_of_week": utc_time.strftime("%A"),
                "warning": "Could not detect timezone, showing UTC time"
            })

        return result


# Singleton instance
_time_detector: Optional[TimeZoneDetector] = None


def get_time_detector() -> TimeZoneDetector:
    """Get or create global TimeZoneDetector instance."""
    global _time_detector
    if _time_detector is None:
        _time_detector = TimeZoneDetector()
    return _time_detector
