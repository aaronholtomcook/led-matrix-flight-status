"""
All configuration in one place: environment-variable-driven secrets, and
every timing/location/threshold constant used across the other modules.
"""

import os
from zoneinfo import ZoneInfo

ICS_URL = os.environ.get("FLEETLIFE_ICS_URL")
if not ICS_URL:
    raise SystemExit(
        "FLEETLIFE_ICS_URL environment variable is not set. "
        "This keeps your private calendar token out of the git repo — "
        "see the setup instructions for how to configure it."
    )

# ntfy.sh custom message override — optional. If not configured, this feature
# is silently skipped rather than failing the whole script, since it's an
# enhancement, not core functionality.
NTFY_TOPIC = "aaronholtomcook-flightstatus-swx1ck1s"  # public ntfy.sh topic — not treated as a
                                                          # secret, just reasonably unique to avoid
                                                          # colliding with unrelated ntfy.sh users.
                                                          # Committed directly so it ships to every
                                                          # device automatically via the git
                                                          # auto-updater — no per-device SSH/config
                                                          # needed. Worst case if discovered: someone
                                                          # pushes a stray message that shows for
                                                          # NTFY_MESSAGE_DURATION_SECONDS, then it's gone.
NTFY_POLL_SECONDS = 30           # how often to check for a new pushed message
NTFY_MESSAGE_DURATION_SECONDS = 15 * 60  # how long a pushed message stays on screen before reverting

GROUND_REFRESH_SECONDS = 300   # how often to re-check status when there's no active flight leg (5 min)
FLIGHT_REFRESH_SECONDS = 60    # how often to re-check a relevant flight's live/scheduled data (1 min)
CALENDAR_CACHE_SECONDS = 24 * 60 * 60  # how long to reuse a fetched calendar before pulling fresh
                                          # (the ICS feed already contains events across many days, so
                                          # a cached calendar still correctly detects the day rolling
                                          # over — only mid-day roster changes need a fresh pull)

HOME_CYCLE_INTERVAL_SECONDS = 60   # how often the at-home screen shows the "next flight" interlude
HOME_WIPE_DURATION_SECONDS = 1.0   # how long each wipe transition takes
HOME_ANIMATION_TICK = 0.03         # frame interval during wipes/scrolling — smooth, matches the
                                      # existing plain-text scroll rate elsewhere in the script
TRIP_LIST_CYCLE_INTERVAL_SECONDS = 300  # how often the "Coming Up" full-screen trip list appears (5 min)
TRIP_LIST_DISPLAY_SECONDS = 60          # how long it stays up

UK_TZ = ZoneInfo("Europe/London")  # automatically handles the GMT/BST switch
HOME_AIRPORT = "LHR"  # base airport — treated as "home", not a vacation destination

# Location used to compute sunrise/sunset for auto-dimming. Hardcoded for
# Newcastle upon Tyne, since both devices are fixed there and there's no
# terminal access to configure per-device env vars for this.
LOCATION_LAT = 54.9783
LOCATION_LON = -1.6178

DAY_BRIGHTNESS = 100
NIGHT_BRIGHTNESS = 50
BRIGHTNESS_CHECK_SECONDS = 300  # how often to re-evaluate day/night (5 min — sunset doesn't move fast)

FLIGHTSTATS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
