#!/usr/bin/env python3
"""
Scroll Aaron's current status on a 32x64 RGB LED matrix panel, pulled from
the FleetLife roster calendar:

  - "Aaron is at home"
  - "Aaron is on training"
  - Flight details (route, status/altitude/speed, ETA) for whichever flight
    leg is most relevant to today — shown whether it's scheduled, currently
    in progress, or has just landed. No longer requires being literally
    airborne to show flight info.

Run with: sudo -E env PATH=$PATH python3 flight_status_matrix.py

Test flags:
  --date YYYY-MM-DD [--time HH:MM]   Pretend it's this date/time (UTC)
  --test-flight BA409                Skip the calendar entirely and just
                                      show live/scheduled data for this
                                      flight number, refreshed periodically.
                                      Great for testing display formatting
                                      without waiting for a real trip.

Requires: pip install requests icalendar --break-system-packages
"""

import argparse
import os
import re
import time
import requests
import json
import logging
from logging.handlers import RotatingFileHandler
from icalendar import Calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

ICS_URL = os.environ.get("FLEETLIFE_ICS_URL")
if not ICS_URL:
    raise SystemExit(
        "FLEETLIFE_ICS_URL environment variable is not set. "
        "This keeps your private calendar token out of the git repo — "
        "see the setup instructions for how to configure it."
    )
GROUND_REFRESH_SECONDS = 300   # how often to re-check status when there's no active flight leg (5 min)
FLIGHT_REFRESH_SECONDS = 60    # how often to re-check a relevant flight's live/scheduled data (1 min)
CALENDAR_CACHE_SECONDS = 24 * 60 * 60  # how long to reuse a fetched calendar before pulling fresh
                                          # (the ICS feed already contains events across many days, so
                                          # a cached calendar still correctly detects the day rolling
                                          # over — only mid-day roster changes need a fresh pull)
UK_TZ = ZoneInfo("Europe/London")  # automatically handles the GMT/BST switch

FLIGHTSTATS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Log file lives in /tmp rather than under the home directory. RGBMatrix()
# drops privileges from root to the 'daemon' user once initialized, and
# 'daemon' often can't traverse into /home/<user>/... — /tmp is always
# writable by everyone, so logging keeps working after that privilege drop.
LOG_FILE = "/tmp/flight_status_matrix.log"

log = logging.getLogger("flight_status")

# ---------------- MATRIX CONFIGURATION ----------------
options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 1
options.parallel = 1
options.hardware_mapping = 'adafruit-hat'
options.brightness = 60
options.gpio_slowdown = 0  # Pi Zero W has a slower processor — the library's own docs
                             # recommend 0 for Model A/A+/B+/Zero boards. (Higher values like
                             # 2-4 are for the faster Pi 3/4/5, which push data too fast for
                             # the panel — the opposite problem to what a Zero W has.)
                             # If flicker persists, try 1 next, but 0 is the documented starting point.
options.led_rgb_sequence = 'RBG'
# options.panel_type = 'FM6126A'  # tried this, made flickering worse — panel likely
                                    # isn't this chip type. Leaving commented out for now.
# options.disable_hardware_pulsing = True  # uncomment if you get an snd_bcm2835 sound conflict error
# --------------------------------------------------------


# ---------------- CALENDAR / STATUS LOGIC ----------------

def get_todays_events(cal, check_date):
    """Return VEVENT components covering the given date."""
    log.debug(f"Scanning calendar for events covering {check_date}")
    todays_events = []
    for component in cal.walk("VEVENT"):
        dtstart = component.get("dtstart").dt
        dtend_field = component.get("dtend")
        dtend = dtend_field.dt if dtend_field else dtstart

        is_all_day = not isinstance(dtstart, datetime)
        start_date = dtstart if is_all_day else dtstart.date()
        end_date = dtend if is_all_day else dtend.date()

        match = (start_date <= check_date < end_date) if is_all_day else (start_date <= check_date <= end_date)
        if match:
            summary = str(component.get("summary", ""))
            log.debug(f"  Match: '{summary}' ({start_date} to {end_date}, all_day={is_all_day})")
            todays_events.append(component)

    log.info(f"Found {len(todays_events)} event(s) covering {check_date}")
    return todays_events


def parse_flight_legs(description):
    """Parse lines like '13Sep26  BA249 LHR 2045 → GIG 0825' into structured legs."""
    pattern = re.compile(
        r'(\d{1,2}[A-Za-z]{3}\d{2})\s+([A-Z]{2})(\d{1,4})\s+([A-Z]{3})\s+(\d{4})\s*(?:→|->)\s*([A-Z]{3})\s+(\d{4})'
    )
    legs = []
    for m in pattern.finditer(description):
        date_str, airline, flight_num, dep, dep_time, arr, arr_time = m.groups()
        dep_date = datetime.strptime(date_str, "%d%b%y").date()
        dep_dt = datetime.combine(dep_date, datetime.strptime(dep_time, "%H%M").time()).replace(tzinfo=timezone.utc)

        arr_time_obj = datetime.strptime(arr_time, "%H%M").time()
        arr_date = dep_date
        if arr_time_obj <= dep_dt.time():
            arr_date = dep_date + timedelta(days=1)
        arr_dt = datetime.combine(arr_date, arr_time_obj).replace(tzinfo=timezone.utc)

        legs.append({
            "airline": airline,
            "flight_number": flight_num,
            "full_flight_number": f"{airline}{flight_num}",
            "dep_airport": dep,
            "arr_airport": arr,
            "dep_time_utc": dep_dt,
            "arr_time_utc": arr_dt,
        })

    log.info(f"Parsed {len(legs)} flight leg(s) from event description")
    for leg in legs:
        log.debug(f"  Leg: {leg['full_flight_number']} {leg['dep_airport']}@{leg['dep_time_utc']} "
                   f"-> {leg['arr_airport']}@{leg['arr_time_utc']}")
    return legs


def find_relevant_leg_for_today(legs, now_utc):
    """Pick whichever leg is most relevant 'today': in-progress, else the next
    upcoming one departing/arriving today, else the most recently completed
    one today. Returns None if no leg touches today at all."""
    today = now_utc.date()
    candidates = [leg for leg in legs if leg["dep_time_utc"].date() == today or leg["arr_time_utc"].date() == today]
    log.debug(f"{len(candidates)} leg(s) touch today ({today}) out of {len(legs)} total legs")

    if not candidates:
        log.info("No flight leg touches today — nothing relevant to show")
        return None

    for leg in candidates:
        if leg["dep_time_utc"] <= now_utc <= leg["arr_time_utc"]:
            log.info(f"Selected leg (currently in progress): {leg['full_flight_number']} "
                      f"{leg['dep_airport']}->{leg['arr_airport']}")
            return leg

    upcoming = [l for l in candidates if l["dep_time_utc"] > now_utc]
    if upcoming:
        leg = min(upcoming, key=lambda l: l["dep_time_utc"])
        log.info(f"Selected leg (upcoming today): {leg['full_flight_number']} "
                  f"{leg['dep_airport']}->{leg['arr_airport']}, departs {leg['dep_time_utc']}")
        return leg

    leg = max(candidates, key=lambda l: l["arr_time_utc"])
    log.info(f"Selected leg (most recently completed today): {leg['full_flight_number']} "
              f"{leg['dep_airport']}->{leg['arr_airport']}, arrived {leg['arr_time_utc']}")
    return leg


_calendar_cache = {"cal": None, "fetched_at": None}


def get_calendar(now_utc, force=False):
    """Return a cached parsed Calendar, refetching only if the cache is
    missing, stale (older than CALENDAR_CACHE_SECONDS), or force=True.
    The roster rarely changes intra-day, so there's no need to hit the
    network every single status check."""
    global _calendar_cache
    cached_cal = _calendar_cache["cal"]
    fetched_at = _calendar_cache["fetched_at"]
    age = (now_utc - fetched_at).total_seconds() if fetched_at else None

    if force or cached_cal is None or age is None or age > CALENDAR_CACHE_SECONDS:
        log.info(f"Calendar cache miss/stale (age={age}) — fetching fresh calendar")
        resp = requests.get(ICS_URL, timeout=15)
        resp.raise_for_status()
        log.debug(f"Calendar fetch OK, HTTP {resp.status_code}, {len(resp.text)} bytes")
        cal = Calendar.from_ical(resp.text)
        _calendar_cache["cal"] = cal
        _calendar_cache["fetched_at"] = now_utc
        return cal

    log.debug(f"Using cached calendar (age={age:.0f}s, refreshes after {CALENDAR_CACHE_SECONDS}s)")
    return cached_cal


def get_status_and_leg(now_utc):
    """
    Returns a tuple: (status_text, current_leg_or_None)
    current_leg is populated whenever status is "on a trip" AND today has a
    relevant flight leg (scheduled, in-progress, or just-landed).
    """
    cal = get_calendar(now_utc)

    today = now_utc.date()
    events = get_todays_events(cal, today)

    if not events:
        log.info("No events today -> Aaron is at home")
        return "Aaron is at home", None

    for event in events:
        title = str(event.get("summary", ""))
        log.debug(f"Evaluating event: '{title}'")

        if "✈️" in title:
            log.info(f"Trip event found: '{title}'")
            description = str(event.get("description", ""))
            legs = parse_flight_legs(description)
            relevant_leg = find_relevant_leg_for_today(legs, now_utc)
            return "Aaron is on a trip", relevant_leg

        if "Simulator" in title or "Duty" in title:
            log.info(f"Training event found: '{title}' -> Aaron is on training")
            return "Aaron is on training", None

    log.info("Event(s) present but none matched known patterns -> defaulting to at home")
    return "Aaron is at home", None


# ---------------- FLIGHTSTATS FETCH ----------------

def fetch_flight_json(airline, flight_number):
    """Fetch and return the raw flight JSON object from FlightStats."""
    url = f"https://www.flightstats.com/v2/flight-tracker/{airline}/{flight_number}"
    log.info(f"Fetching FlightStats page: {url}")
    resp = requests.get(url, headers=FLIGHTSTATS_HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text
    log.debug(f"FlightStats fetch OK, HTTP {resp.status_code}, {len(html)} bytes")

    marker = "__NEXT_DATA__ = "
    start = html.find(marker)
    if start == -1:
        log.error("__NEXT_DATA__ marker not found in FlightStats page — page layout may have changed")
        raise ValueError("Could not find __NEXT_DATA__ in FlightStats page")

    decoder = json.JSONDecoder()
    data, end_index = decoder.raw_decode(html[start + len(marker):])
    log.debug(f"Parsed __NEXT_DATA__ JSON OK ({end_index} characters)")
    flight = data["props"]["initialState"]["flightTracker"]["flight"]
    log.debug(f"Flight JSON top-level keys: {list(flight.keys())}")
    return flight


def parse_flight_json(flight_json):
    """Extract a consistent set of fields regardless of flight phase
    (scheduled / in-flight / landed)."""
    positional = flight_json.get("positional", {}).get("flexTrack", {})
    positions = positional.get("positions", [])
    latest = positions[0] if positions else None  # newest position is first

    schedule = flight_json.get("schedule", {})
    eta_str = schedule.get("estimatedActualArrivalUTC") or schedule.get("scheduledArrivalUTC")
    eta_dt = datetime.fromisoformat(eta_str.replace("Z", "+00:00")) if eta_str else None

    parsed = {
        "status": flight_json.get("status", {}).get("status"),  # Scheduled / Departed / Landed etc.
        "departure_iata": flight_json.get("departureAirport", {}).get("iata"),
        "arrival_iata": flight_json.get("arrivalAirport", {}).get("iata"),
        "altitude_ft": latest.get("altitudeFt") if latest else None,
        "speed_mph": latest.get("speedMph") if latest else None,
        "phase": flight_json.get("flightNote", {}).get("phase"),
        "eta_utc": eta_dt,
    }
    log.info(f"Parsed flight data: status={parsed['status']}, "
              f"altitude={parsed['altitude_ft']}, speed={parsed['speed_mph']}, "
              f"phase={parsed['phase']}, eta={parsed['eta_utc']}")
    return parsed


def build_board_data(full_flight_number, flight_data, now_utc):
    """Build a structured dict describing everything the board layout needs
    to draw, whether the flight is scheduled, in-flight, or just landed."""
    dep = flight_data["departure_iata"] or "???"
    arr = flight_data["arrival_iata"] or "???"

    has_live_position = flight_data["altitude_ft"] is not None
    speed_kts = round(flight_data["speed_mph"] * 0.868976) if has_live_position else None

    # Trust FlightStats' own status field for "landed" — don't infer it purely
    # from ETA-vs-now time math. A heavily delayed or held flight can have a
    # scheduled/estimated arrival time that's already in the past even though
    # it genuinely hasn't departed yet, which previously caused a false
    # "Landed" reading on flights that were still "Scheduled".
    status_raw = (flight_data["status"] or "").upper()
    landed = any(word in status_raw for word in ("LANDED", "ARRIVED"))
    log.debug(f"Status field from FlightStats: '{status_raw}' -> landed={landed}")

    eta_str = ""
    if flight_data["eta_utc"]:
        remaining = flight_data["eta_utc"] - now_utc
        total_min = int(remaining.total_seconds() // 60)
        if landed:
            eta_str = "Landed"
        elif total_min >= 0:
            h, m = divmod(total_min, 60)
            eta_str = f"{h}h{m:02d}m"
        else:
            # ETA has technically passed but status says we haven't landed
            # (e.g. long ground hold, delayed estimate). A negative countdown
            # doesn't make sense, but we still want *something* shown for a
            # scheduled flight — fall back to the absolute arrival clock time,
            # converted to UK local time (handles BST/GMT automatically).
            local_eta = flight_data["eta_utc"].astimezone(UK_TZ)
            eta_str = local_eta.strftime("%H:%M")
            log.debug(f"ETA {flight_data['eta_utc']} is in the past but status "
                       f"isn't landed/arrived — showing UK local time '{eta_str}' instead of a countdown.")

    if landed:
        status_word = "LANDED"
    elif has_live_position:
        status_word = "EN ROUTE"
    else:
        status_word = status_raw or "SCHEDULED"

    board = {
        "dep": dep,
        "arr": arr,
        "status_word": status_word,
        "landed": landed,
        "has_live_position": has_live_position,
        "altitude_ft": flight_data["altitude_ft"],
        "speed_kts": speed_kts,
        "flight_number": full_flight_number,
        "eta_str": eta_str,
    }
    log.debug(f"Built board data: {board}")
    return board


def get_display_payload(now_utc, forced_airline=None, forced_flight_number=None):
    """Top-level: figure out exactly what to show right now.
    Returns a tuple (kind, data):
      ("board", board_dict) -> render the compact flight board layout
      ("status_board", {"label": ...}) -> render the logo + short label layout
                                           (used for at-home / on-training)
      ("text", message_str) -> render as plain scrolling text (fallback/errors)
    """
    log.debug(f"get_display_payload called, now_utc={now_utc}, "
               f"forced={forced_airline}{forced_flight_number or ''}")

    # Manual override for testing: skip the calendar entirely
    if forced_airline and forced_flight_number:
        full_number = f"{forced_airline}{forced_flight_number}"
        log.info(f"TEST MODE active — forcing flight {full_number}, skipping calendar")
        try:
            flight_json = fetch_flight_json(forced_airline, forced_flight_number)
            flight_data = parse_flight_json(flight_json)
            return "board", build_board_data(full_number, flight_data, now_utc)
        except Exception as e:
            log.warning(f"Couldn't fetch test flight {full_number} ({e})", exc_info=True)
            return "text", f"{full_number}: fetch error"

    try:
        status_text, leg = get_status_and_leg(now_utc)
    except Exception as e:
        log.warning(f"Couldn't fetch calendar ({e}); keeping last known status.", exc_info=True)
        return None, None

    if not leg:
        if status_text == "Aaron is at home":
            log.info("At home -> displaying status board with 'AT HOME' label and house icon")
            return "status_board", {"label": "AT HOME", "icon": "house"}
        if status_text == "Aaron is on training":
            log.info("On training -> displaying status board with 'ON A COURSE' label and swoosh icon")
            return "status_board", {"label": "ON A COURSE", "icon": "swoosh"}
        log.info(f"No relevant leg -> displaying base status as scrolling text: '{status_text}'")
        return "text", status_text

    log.info(f"Relevant leg found: {leg['full_flight_number']} — fetching live/scheduled data")
    try:
        flight_json = fetch_flight_json(leg["airline"], leg["flight_number"])
        flight_data = parse_flight_json(flight_json)
    except Exception as e:
        log.warning(f"Couldn't fetch live flight data for {leg['full_flight_number']} ({e}); "
                     f"showing basic trip status.", exc_info=True)
        return "text", f"Aaron is flying {leg['full_flight_number']} ({leg['dep_airport']}-{leg['arr_airport']})"

    return "board", build_board_data(leg["full_flight_number"], flight_data, now_utc)


# ---------------- BOARD DRAWING (64x32 layout) ----------------

# Colours, matching the navy/red BA-style palette from the mockup
COLOR_ACCENT_BAR = (200, 16, 46)      # red
COLOR_ROUNDEL_OUTER = (30, 60, 90)    # navy
COLOR_ROUNDEL_INNER = (200, 16, 46)   # red
COLOR_ROUTE_CODE = (255, 255, 255)    # white
COLOR_STATUS_SCHEDULED = (58, 214, 160)   # teal
COLOR_STATUS_ENROUTE = (240, 168, 48)     # amber
COLOR_STATUS_LANDED = (100, 220, 100)     # green
COLOR_DATA_ROW = (240, 168, 48)       # amber, altitude/speed
COLOR_FLIGHT_NUM = (143, 163, 191)    # muted blue-gray
COLOR_ETA = (255, 255, 255)           # white
COLOR_PLACEHOLDER = (90, 90, 90)      # dim gray for "no data yet"

ROW1_Y = 6    # route codes + roundel baseline
ROW2_Y = 13   # status word baseline
ROW3_Y = 20   # altitude/speed baseline
ROW4_Y = 27   # flight number / ETA baseline


def draw_filled_circle(canvas, cx, cy, radius, rgb):
    """Draw a filled circle by setting individual pixels — the graphics
    module's DrawCircle only draws an outline, and we need solid fills for
    the tiny roundel at this resolution."""
    r, g, b = rgb
    r_sq = radius * radius
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx * dx + dy * dy <= r_sq:
                canvas.SetPixel(cx + dx, cy + dy, r, g, b)


def draw_wing_swoosh(canvas, cx, cy, scale=1):
    """Small abstract diagonal swoosh in navy/red, centered on (cx, cy).
    This is an original, heavily-simplified pixel pattern in the same
    two-tone palette — not a reproduction of any airline's actual logo.
    scale=2 draws it at double size (used for the home/training status board,
    where there's no route-code row competing for space)."""
    red = COLOR_ACCENT_BAR
    navy = COLOR_ROUNDEL_OUTER
    pattern = [
        "R.............",
        "RRRRRRRRRRRR..",
        "RRRRRRRRRRRRR.",
        "........RRBBB.",
        ".......BBBBB..",
        "......BBBB...."
    ]
    height = len(pattern)
    width = len(pattern[0])
    total_w = width * scale
    total_h = height * scale
    start_x = cx - total_w // 2
    start_y = cy - total_h // 2
    for row_idx, row in enumerate(pattern):
        for col_idx, ch in enumerate(row):
            if ch == '.':
                continue
            r, g, b = red if ch == 'R' else navy
            for sy in range(scale):
                for sx in range(scale):
                    canvas.SetPixel(start_x + col_idx * scale + sx, start_y + row_idx * scale + sy, r, g, b)


def draw_house_icon(canvas, cx, cy, scale=2):
    """Small pixel-art house icon (red roof, white walls), centered on
    (cx, cy) — used for the 'at home' status board."""
    red = COLOR_ACCENT_BAR
    white = COLOR_ROUTE_CODE
    pattern = [
        "...RR...",
        "..RRRR..",
        ".RRRRRR.",
        "RRRRRRRR",
        ".WWWWWW.",
        ".WWWWWW.",
    ]
    height = len(pattern)
    width = len(pattern[0])
    total_w = width * scale
    total_h = height * scale
    start_x = cx - total_w // 2
    start_y = cy - total_h // 2
    for row_idx, row in enumerate(pattern):
        for col_idx, ch in enumerate(row):
            if ch == '.':
                continue
            r, g, b = red if ch == 'R' else white
            for sy in range(scale):
                for sx in range(scale):
                    canvas.SetPixel(start_x + col_idx * scale + sx, start_y + row_idx * scale + sy, r, g, b)


def status_color(board):
    if board["landed"]:
        return COLOR_STATUS_LANDED
    if board["has_live_position"]:
        return COLOR_STATUS_ENROUTE
    return COLOR_STATUS_SCHEDULED


def draw_board(canvas, small_font, board):
    """Draw the compact BA-style board layout for a flight."""
    canvas.Clear()

    # Top accent bar
    graphics.DrawLine(canvas, 0, 0, 63, 0, graphics.Color(*COLOR_ACCENT_BAR))

    # Row 1: dep code -- roundel -- arr code
    dep_color = graphics.Color(*COLOR_ROUTE_CODE)
    arr_color = graphics.Color(*COLOR_ROUTE_CODE)
    graphics.DrawText(canvas, small_font, 1, ROW1_Y, dep_color, board["dep"])
    graphics.DrawText(canvas, small_font, 64 - 1 - 4 * len(board["arr"]), ROW1_Y, arr_color, board["arr"])
    draw_wing_swoosh(canvas, 32, ROW1_Y - 3)

    # Row 2: status word, centered, color-coded
    status_word = board["status_word"]
    status_x = max(0, (64 - 4 * len(status_word)) // 2)
    graphics.DrawText(canvas, small_font, status_x, ROW2_Y, graphics.Color(*status_color(board)), status_word)

    # Row 3: altitude + speed, or a dim placeholder if not yet tracking
    if board["has_live_position"]:
        alt_text = f"{board['altitude_ft']}ft"
        spd_text = f"{board['speed_kts']}kts"
        graphics.DrawText(canvas, small_font, 1, ROW3_Y, graphics.Color(*COLOR_DATA_ROW), alt_text)
        graphics.DrawText(canvas, small_font, 64 - 1 - 4 * len(spd_text), ROW3_Y, graphics.Color(*COLOR_DATA_ROW), spd_text)
    else:
        graphics.DrawText(canvas, small_font, 1, ROW3_Y, graphics.Color(*COLOR_PLACEHOLDER), "-- --")

    # Row 4: flight number + ETA
    graphics.DrawText(canvas, small_font, 1, ROW4_Y, graphics.Color(*COLOR_FLIGHT_NUM), board["flight_number"])
    eta_text = board["eta_str"]
    if eta_text:
        graphics.DrawText(canvas, small_font, 64 - 1 - 4 * len(eta_text), ROW4_Y, graphics.Color(*COLOR_ETA), eta_text)


def draw_status_board(canvas, small_font, data):
    """Draw the simpler board layout for at-home / on-training: a bigger
    swoosh logo (more room since there's no route-code row) with a short
    label underneath."""
    canvas.Clear()

    # Top accent bar, same as the flight board for visual consistency
    graphics.DrawLine(canvas, 0, 0, 63, 0, graphics.Color(*COLOR_ACCENT_BAR))

    # Bigger logo, centered in the upper portion — house for at-home, swoosh for training
    if data.get("icon") == "house":
        draw_house_icon(canvas, 32, 11, scale=2)
    else:
        draw_wing_swoosh(canvas, 32, 11, scale=2)

    # Label centered underneath
    label = data["label"]
    label_x = max(0, (64 - 4 * len(label)) // 2)
    graphics.DrawText(canvas, small_font, label_x, ROW4_Y, graphics.Color(*COLOR_ROUTE_CODE), label)


# ---------------- MATRIX SETUP ----------------
parser = argparse.ArgumentParser(description="Scroll Aaron's flight/duty status on the LED matrix.")
parser.add_argument(
    "--date",
    help="Test against a specific date instead of today, format YYYY-MM-DD (e.g. --date 2026-09-15).",
    default=None,
)
parser.add_argument(
    "--time",
    help="Test against a specific UTC time of day, format HH:MM (e.g. --time 22:30). "
         "Requires --date to also be set.",
    default=None,
)
parser.add_argument(
    "--test-flight",
    help="Skip the calendar entirely and just show live/scheduled data for this "
         "flight number, e.g. --test-flight BA409. Useful for testing display "
         "formatting without waiting for a real trip.",
    default=None,
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable verbose DEBUG-level logging (shows every parsed field, raw "
         "candidate lists, etc.) in addition to the normal INFO-level logs.",
)
args = parser.parse_args()

# ---------------- LOGGING SETUP ----------------
log_level = logging.DEBUG if args.debug else logging.INFO
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Rotating file handler: caps the log at ~1MB x 3 backups so it can't grow
# forever on a Pi that runs this script indefinitely.
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
file_handler.setFormatter(log_formatter)

log.setLevel(log_level)
log.addHandler(console_handler)
log.addHandler(file_handler)

log.info(f"=== flight_status_matrix.py starting (log level: {logging.getLevelName(log_level)}) ===")
log.info(f"Logging to console and to {LOG_FILE}")

test_now = None
if args.date:
    test_date = date.fromisoformat(args.date)
    test_time = datetime.strptime(args.time, "%H:%M").time() if args.time else datetime.min.time()
    test_now = datetime.combine(test_date, test_time).replace(tzinfo=timezone.utc)
    log.info(f"TEST MODE: treating 'now' as {test_now}")

forced_airline, forced_flight_number = None, None
if args.test_flight:
    m = re.match(r'^([A-Za-z]{2})(\d{1,4})$', args.test_flight.strip())
    if not m:
        raise SystemExit(f"--test-flight value '{args.test_flight}' doesn't look like AA123 (e.g. BA409)")
    forced_airline, forced_flight_number = m.group(1).upper(), m.group(2)
    log.info(f"TEST MODE: forcing flight {forced_airline}{forced_flight_number}, ignoring calendar")


def current_time():
    return test_now if test_now else datetime.now(timezone.utc)


log.info("Loading fonts...")
big_font = graphics.Font()
big_font.LoadFont("/home/aaron/Documents/rpi-rgb-led-matrix/fonts/7x13.bdf")
small_font = graphics.Font()
small_font.LoadFont("/home/aaron/Documents/rpi-rgb-led-matrix/fonts/4x6.bdf")
log.info("Fonts loaded OK.")

# Fetch the initial payload BEFORE creating the RGBMatrix instance.
# RGBMatrix() drops privileges from root to the 'daemon' user once initialized,
# which can block network/file access that depends on paths under
# /home/<user>/... if that directory isn't world-traversable. Fetching first avoids that.
log.info("Fetching initial status...")
kind, data = get_display_payload(current_time(), forced_airline, forced_flight_number)
if kind is None:
    kind, data = "text", "Aaron's status unknown"
log.info(f"Initial payload: kind={kind}, data={data}")

log.info("Initializing matrix...")
matrix = RGBMatrix(options=options)
log.info("Matrix initialized OK.")

text_color = graphics.Color(255, 255, 0)  # yellow, for the plain-text fallback mode

pos = options.cols
canvas = matrix.CreateFrameCanvas()
last_refresh = time.monotonic()

if kind == "text":
    scroll_message = data + "   "
else:
    scroll_message = None

log.info(f"Display starting in '{kind}' mode. Press CTRL+C to stop.")

try:
    while True:
        refresh_interval = FLIGHT_REFRESH_SECONDS if kind == "board" else GROUND_REFRESH_SECONDS
        if time.monotonic() - last_refresh > refresh_interval:
            log.debug("Refresh interval elapsed, re-checking status...")
            new_kind, new_data = get_display_payload(current_time(), forced_airline, forced_flight_number)
            if new_kind is not None:
                if new_kind != kind or new_data != data:
                    log.info(f"Display changed: ({kind}, {data}) -> ({new_kind}, {new_data})")
                    kind, data = new_kind, new_data
                    scroll_message = (data + "   ") if kind == "text" else None
                    pos = options.cols  # reset scroll position on any change
                else:
                    log.debug("Status unchanged.")
            else:
                log.debug("Refresh returned nothing usable; keeping previous display.")
            last_refresh = time.monotonic()

        if kind == "board":
            draw_board(canvas, small_font, data)
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.5)  # static layout — no need to redraw every 30ms
        elif kind == "status_board":
            draw_status_board(canvas, small_font, data)
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.5)
        else:
            canvas.Clear()
            len_drawn = graphics.DrawText(canvas, big_font, pos, 20, text_color, scroll_message)
            pos -= 1
            if pos + len_drawn < 0:
                pos = options.cols
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.03)
except KeyboardInterrupt:
    log.info("KeyboardInterrupt received — stopping, clearing display...")
    matrix.Clear()
    log.info("=== flight_status_matrix.py stopped cleanly ===")