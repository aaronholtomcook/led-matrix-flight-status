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

Run with: sudo -E env PATH=$PATH python3 test.py

Test flags:
  --date YYYY-MM-DD [--time HH:MM]   Pretend it's this date/time (UTC)
  --test-flight BA409                Skip the calendar entirely and just
                                      show live/scheduled data for this
                                      flight number, refreshed periodically.
  --debug                            Verbose DEBUG-level logging.
  --dev-mode                         Use the rgbmatrix_sim emulator.

Requires: pip install requests icalendar --break-system-packages

This is the entry point only — see matrix_backend.py, config.py,
calendar_status.py, flightstats.py, ntfy_override.py, brightness.py,
board_drawing.py, and display_payload.py for the actual logic.
"""

import argparse
import re
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import date, datetime, timezone

from matrix_backend import RGBMatrix, RGBMatrixOptions, graphics, LOG_FILE, USE_DEV_MODE
from config import (
    NTFY_TOPIC, NTFY_POLL_SECONDS, GROUND_REFRESH_SECONDS, FLIGHT_REFRESH_SECONDS,
    HOME_CYCLE_INTERVAL_SECONDS, HOME_WIPE_DURATION_SECONDS, HOME_ANIMATION_TICK,
    TRIP_LIST_CYCLE_INTERVAL_SECONDS, TRIP_LIST_DISPLAY_SECONDS,
    BRIGHTNESS_CHECK_SECONDS, NIGHT_BRIGHTNESS,
)
from calendar_status import get_calendar, find_next_trip_leg, build_next_flight_message, find_upcoming_trips_summary
from ntfy_override import check_for_message_override, get_active_override
from brightness import get_target_brightness
from board_drawing import (
    draw_board, draw_status_board, draw_coming_up_screen, draw_black_overlay,
    ROW4_Y, LABEL_ROW_Y_START, LABEL_ROW_Y_END, COLOR_ROUTE_CODE, COLOR_ACCENT_BAR,
)
from display_payload import get_display_payload

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


# ---------------- ARGPARSE ----------------
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
parser.add_argument(
    "--dev-mode",
    action="store_true",
    help="Run using the rgbmatrix_sim emulator instead of real hardware. "
         "Already detected earlier (before this parser runs) to pick the "
         "correct backend — registered here too just so it doesn't get "
         "rejected as an unrecognized argument.",
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

log.info(f"=== test.py starting (log level: {logging.getLevelName(log_level)}) ===")
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
small_font = graphics.Font()
if USE_DEV_MODE:
    big_font.LoadFont("./rgbmatrix_sim/fonts/7x13.bdf")
    small_font.LoadFont("./rgbmatrix_sim/fonts/4x6.bdf")
else:
    big_font.LoadFont("/home/aaron/Documents/rpi-rgb-led-matrix/fonts/7x13.bdf")
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

current_brightness = get_target_brightness(current_time())
try:
    matrix.brightness = current_brightness
    log.info(f"Initial brightness set to {current_brightness} "
              f"({'night' if current_brightness == NIGHT_BRIGHTNESS else 'day'} mode)")
except Exception as e:
    log.warning(f"Couldn't set initial matrix brightness ({e}) — using the static options.brightness value")

text_color = graphics.Color(255, 255, 0)  # yellow, for the plain-text fallback mode

pos = options.cols
canvas = matrix.CreateFrameCanvas()
last_refresh = time.monotonic()
last_message_check = time.monotonic()
last_brightness_check = time.monotonic()

if kind == "text":
    scroll_message = data + "   "
else:
    scroll_message = None

# Tracks what was actually rendered last frame (which may be an override,
# distinct from the underlying `kind`/`data`), so we know when to reset the
# scroll position as we enter/leave override mode.
last_rendered_key = None

# At-home "next flight" interlude: a sub-state machine independent of
# kind/data, since it animates on top of the same underlying AT HOME status
# without that status itself changing. Reset to "normal" whenever the
# underlying status actually changes (see render_key check below), so an
# in-progress animation never gets stuck if e.g. the pilot's status changes
# mid-wipe.
home_anim_phase = "normal"       # normal | wipe_out | scroll | wipe_in
home_anim_phase_start = None
home_next_cycle_time = time.monotonic() + HOME_CYCLE_INTERVAL_SECONDS
home_scroll_pos = options.cols
home_next_flight_msg = None

# "Coming Up" trip-list screen: a separate, independent interlude from the
# above — a full-screen takeover rather than a wipe/scroll on the label row.
# Mutually exclusive with the interlude above (see the render loop): each
# only starts when the other is currently idle, so they never overlap.
home_list_active = False
home_list_end_time = None
home_list_next_cycle_time = time.monotonic() + TRIP_LIST_CYCLE_INTERVAL_SECONDS
home_list_trips = None

log.info(f"Display starting in '{kind}' mode. Press CTRL+C to stop.")
if NTFY_TOPIC:
    log.info(f"ntfy.sh custom message override enabled (polling every {NTFY_POLL_SECONDS}s)")
else:
    log.info("NTFY_TOPIC not set — custom message override disabled")

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
                else:
                    log.debug("Status unchanged.")
            else:
                log.debug("Refresh returned nothing usable; keeping previous display.")
            last_refresh = time.monotonic()

        if time.monotonic() - last_message_check > NTFY_POLL_SECONDS:
            check_for_message_override(current_time())
            last_message_check = time.monotonic()

        if time.monotonic() - last_brightness_check > BRIGHTNESS_CHECK_SECONDS:
            target_brightness = get_target_brightness(current_time())
            if target_brightness != current_brightness:
                log.info(f"Brightness changing: {current_brightness} -> {target_brightness} "
                          f"({'night' if target_brightness == NIGHT_BRIGHTNESS else 'day'} mode)")
                try:
                    matrix.brightness = target_brightness
                    current_brightness = target_brightness
                except Exception as e:
                    log.warning(f"Couldn't update matrix brightness ({e})")
            last_brightness_check = time.monotonic()

        override_text = get_active_override(current_time())

        if override_text is not None:
            render_key = ("override", override_text)
            render_kind = "text"
            render_message = override_text + "   "
        else:
            render_key = (kind, str(data))
            render_kind = kind
            render_message = scroll_message

        if render_key != last_rendered_key:
            pos = options.cols  # reset scroll position whenever what's shown actually changes
            last_rendered_key = render_key
            if home_anim_phase != "normal":
                log.debug("Underlying status changed mid at-home animation — resetting to normal")
            home_anim_phase = "normal"
            home_next_cycle_time = time.monotonic() + HOME_CYCLE_INTERVAL_SECONDS
            if home_list_active:
                log.debug("Underlying status changed mid Coming Up screen — resetting")
            home_list_active = False
            home_list_next_cycle_time = time.monotonic() + TRIP_LIST_CYCLE_INTERVAL_SECONDS

        is_home_screen = (render_kind == "status_board" and data.get("icon") == "house"
                            and override_text is None)

        if home_list_active:
            # Full-screen "Coming Up" takeover — highest priority, blocks
            # everything else (including the next-flight interlude) until
            # its display time is up.
            if time.monotonic() >= home_list_end_time:
                home_list_active = False
                home_list_next_cycle_time = time.monotonic() + TRIP_LIST_CYCLE_INTERVAL_SECONDS
                # falls through to normal rendering below, this same iteration
            else:
                remaining = home_list_end_time - time.monotonic()
                progress = 1.0 - max(0.0, remaining) / TRIP_LIST_DISPLAY_SECONDS
                draw_coming_up_screen(canvas, small_font, home_list_trips, progress)
                canvas = matrix.SwapOnVSync(canvas)
                time.sleep(0.5)
                continue

        if is_home_screen and home_anim_phase != "normal":
            # Mid-interlude: wipe out just the "AT HOME" text (icon/date/time
            # stay visible throughout), scroll "Next Flight: ..." in at the
            # same size/position as that text, then wipe it back in.
            now_mono = time.monotonic()

            if home_anim_phase == "wipe_out":
                elapsed = now_mono - home_anim_phase_start
                progress = min(elapsed / HOME_WIPE_DURATION_SECONDS, 1.0)
                draw_status_board(canvas, small_font, data, current_time())
                draw_black_overlay(canvas, 0, int(64 * progress) - 1,
                                     LABEL_ROW_Y_START, LABEL_ROW_Y_END)
                canvas = matrix.SwapOnVSync(canvas)
                if progress >= 1.0:
                    home_anim_phase = "scroll"
                    home_scroll_pos = options.cols
                time.sleep(HOME_ANIMATION_TICK)

            elif home_anim_phase == "scroll":
                draw_status_board(canvas, small_font, data, current_time(), hide_label=True)
                x = home_scroll_pos
                w1 = graphics.DrawText(canvas, small_font, x, ROW4_Y,
                                         graphics.Color(*COLOR_ROUTE_CODE), home_next_flight_msg["prefix"])
                x += w1
                w2 = graphics.DrawText(canvas, small_font, x, ROW4_Y,
                                         graphics.Color(*COLOR_ACCENT_BAR), home_next_flight_msg["flight_num"])
                x += w2
                w3 = graphics.DrawText(canvas, small_font, x, ROW4_Y,
                                         graphics.Color(*COLOR_ROUTE_CODE), home_next_flight_msg["suffix"])
                len_drawn = w1 + w2 + w3
                home_scroll_pos -= 1
                canvas = matrix.SwapOnVSync(canvas)
                if home_scroll_pos + len_drawn < 0:
                    home_anim_phase = "wipe_in"
                    home_anim_phase_start = time.monotonic()
                time.sleep(HOME_ANIMATION_TICK)

            elif home_anim_phase == "wipe_in":
                elapsed = now_mono - home_anim_phase_start
                progress = min(elapsed / HOME_WIPE_DURATION_SECONDS, 1.0)
                draw_status_board(canvas, small_font, data, current_time())
                draw_black_overlay(canvas, int(64 * progress), 63,
                                     LABEL_ROW_Y_START, LABEL_ROW_Y_END)
                canvas = matrix.SwapOnVSync(canvas)
                if progress >= 1.0:
                    home_anim_phase = "normal"
                    home_next_cycle_time = time.monotonic() + HOME_CYCLE_INTERVAL_SECONDS
                time.sleep(HOME_ANIMATION_TICK)

            continue  # skip the normal rendering branch below while mid-interlude

        if (is_home_screen and home_anim_phase == "normal" and not home_list_active
                and time.monotonic() >= home_list_next_cycle_time):
            # Time for the "Coming Up" full-screen trip list.
            cal_for_lookup = get_calendar(current_time())
            trips = find_upcoming_trips_summary(cal_for_lookup, current_time(), max_count=3)
            if trips:
                home_list_trips = trips
                home_list_active = True
                home_list_end_time = time.monotonic() + TRIP_LIST_DISPLAY_SECONDS
                log.info(f"Showing Coming Up screen with {len(trips)} upcoming trip(s)")
                continue  # start showing it next iteration
            else:
                log.debug("No upcoming trips found for Coming Up screen — skipping this cycle")
                home_list_next_cycle_time = time.monotonic() + TRIP_LIST_CYCLE_INTERVAL_SECONDS

        if is_home_screen and time.monotonic() >= home_next_cycle_time:
            # Time for the next interlude — look ahead for the next trip.
            cal_for_lookup = get_calendar(current_time())
            next_leg = find_next_trip_leg(cal_for_lookup, current_time())
            if next_leg:
                home_next_flight_msg = build_next_flight_message(next_leg)
                full_text = home_next_flight_msg["prefix"] + home_next_flight_msg["flight_num"] + home_next_flight_msg["suffix"]
                log.info(f"At-home interlude starting: '{full_text.strip()}'")
                home_anim_phase = "wipe_out"
                home_anim_phase_start = time.monotonic()
                continue  # start animating next iteration
            else:
                log.debug("No next flight found for interlude — skipping this cycle")
                home_next_cycle_time = time.monotonic() + HOME_CYCLE_INTERVAL_SECONDS

        if render_kind == "board":
            draw_board(canvas, small_font, data)
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.5)  # static layout — no need to redraw every 30ms
        elif render_kind == "status_board":
            draw_status_board(canvas, small_font, data, current_time())
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.5)
        else:
            canvas.Clear()
            len_drawn = graphics.DrawText(canvas, big_font, pos, 20, text_color, render_message)
            pos -= 1
            if pos + len_drawn < 0:
                pos = options.cols
            canvas = matrix.SwapOnVSync(canvas)
            time.sleep(0.03)
except KeyboardInterrupt:
    log.info("KeyboardInterrupt received — stopping, clearing display...")
    matrix.Clear()
    log.info("=== test.py stopped cleanly ===")
