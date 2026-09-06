"""
Pure-Python sunrise/sunset calculation (no external dependency) and the
day/night brightness decision built on top of it.
"""

import math
import logging
from datetime import datetime, timezone

from config import LOCATION_LAT, LOCATION_LON, DAY_BRIGHTNESS, NIGHT_BRIGHTNESS

log = logging.getLogger("flight_status")


def calculate_sunrise_sunset(date, lat, lon):
    """Approximate sunrise/sunset in UTC for a given date and location, using
    the standard NOAA solar position algorithm. Pure Python (math module
    only) — no external dependency, so no `pip install` needed on devices
    without terminal access. Accurate to within a minute or two, which is
    plenty for deciding day vs. night display brightness."""
    zenith = 90.833  # official sunrise/sunset zenith, includes atmospheric refraction

    def calc(is_sunrise):
        day_of_year = date.timetuple().tm_yday
        lng_hour = lon / 15
        t = day_of_year + ((6 - lng_hour) / 24) if is_sunrise else day_of_year + ((18 - lng_hour) / 24)

        M = (0.9856 * t) - 3.289
        L = M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634
        L = L % 360

        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
        RA = RA % 360
        l_quadrant = (math.floor(L / 90)) * 90
        ra_quadrant = (math.floor(RA / 90)) * 90
        RA = (RA + (l_quadrant - ra_quadrant)) / 15

        sin_dec = 0.39782 * math.sin(math.radians(L))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(lat)))) / \
                (cos_dec * math.cos(math.radians(lat)))

        if cos_h > 1 or cos_h < -1:
            return None  # sun never rises/sets on this date at this latitude (not relevant for the UK)

        H = (360 - math.degrees(math.acos(cos_h))) if is_sunrise else math.degrees(math.acos(cos_h))
        H = H / 15

        T = H + RA - (0.06571 * t) - 6.622
        UT = T - lng_hour
        UT = UT % 24

        hour = int(UT)
        minute = int((UT - hour) * 60)
        second = int((((UT - hour) * 60) - minute) * 60)
        return datetime(date.year, date.month, date.day, hour, minute, second, tzinfo=timezone.utc)

    return calc(True), calc(False)


def get_target_brightness(now_utc):
    """Return DAY_BRIGHTNESS or NIGHT_BRIGHTNESS depending on whether it's
    currently after sunset / before sunrise at the configured location.
    Falls back to day brightness if the calculation fails for any reason —
    a wrong brightness is a minor cosmetic issue, not worth crashing over."""
    try:
        sunrise, sunset = calculate_sunrise_sunset(now_utc.date(), LOCATION_LAT, LOCATION_LON)
        if sunrise is None or sunset is None:
            return DAY_BRIGHTNESS
        if now_utc < sunrise or now_utc > sunset:
            return NIGHT_BRIGHTNESS
        return DAY_BRIGHTNESS
    except Exception as e:
        log.warning(f"Couldn't compute sunrise/sunset ({e}); using day brightness", exc_info=True)
        return DAY_BRIGHTNESS
