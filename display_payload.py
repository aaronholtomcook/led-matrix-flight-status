"""
Top-level orchestration: figures out exactly what should be on screen right
now, by combining the calendar/status logic with live FlightStats data.
"""

import logging

from config import UK_TZ
from calendar_status import (
    get_status_and_leg, find_current_location, build_trip_status_board,
)
from flightstats import fetch_flight_json, parse_flight_json, build_board_data

log = logging.getLogger("flight_status")


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
        status_text, leg, legs = get_status_and_leg(now_utc)
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
        if status_text == "Aaron is on a trip":
            # Layover day — no leg touches today at all. Show the trip
            # screen with wherever the most recently completed leg landed.
            location = find_current_location(legs, now_utc) if legs else None
            if location:
                log.info(f"On a trip, layover day -> displaying status board for location '{location}'")
                return "status_board", build_trip_status_board(location)
            log.info("On a trip, no completed leg yet -> displaying generic trip status board")
            return "status_board", {"label": "ON A TRIP", "icon": "palm"}
        log.info(f"No relevant leg -> displaying base status as scrolling text: '{status_text}'")
        return "text", status_text

    # If the leg hasn't taken off yet, don't bother fetching/showing the
    # flight board — there's nothing live to display. Show the status board
    # (logo + text) instead, with the flight number/route and takeoff time in
    # UK local time (handles GMT/BST automatically).
    if leg["dep_time_utc"] > now_utc:
        local_dep = leg["dep_time_utc"].astimezone(UK_TZ)
        time_str = local_dep.strftime("%H:%M")
        tz_abbrev = local_dep.tzname()
        log.info(f"Leg {leg['full_flight_number']} hasn't departed yet "
                  f"(takeoff at {leg['dep_time_utc']}) — showing takeoff status board instead of flight board")
        return "status_board", {
            "label": f'{leg["full_flight_number"]} {leg["dep_airport"]}-{leg["arr_airport"]}',
            "sublabel": f"T/O {time_str} {tz_abbrev}",
            "icon": "swoosh",
        }

    # Scheduled departure has passed — fetch live FlightStats data to find
    # out the ACTUAL status. Deliberately not short-circuiting on the
    # scheduled arrival time here: a delayed flight can still be genuinely
    # airborne well after its scheduled arrival time, so we trust
    # FlightStats' own status field to decide "landed" rather than assuming
    # it from the roster's schedule alone.
    log.info(f"Relevant leg found: {leg['full_flight_number']} — fetching live/scheduled data")
    try:
        flight_json = fetch_flight_json(leg["airline"], leg["flight_number"])
        flight_data = parse_flight_json(flight_json)
    except Exception as e:
        log.warning(f"Couldn't fetch live flight data for {leg['full_flight_number']} ({e}); "
                     f"showing basic trip status.", exc_info=True)
        return "text", f"Aaron is flying {leg['full_flight_number']} ({leg['dep_airport']}-{leg['arr_airport']})"

    status_raw = (flight_data["status"] or "").upper()
    actually_landed = any(word in status_raw for word in ("LANDED", "ARRIVED"))

    if actually_landed:
        arrival_airport = flight_data["arrival_iata"] or leg["arr_airport"]
        log.info(f"Leg {leg['full_flight_number']} has actually landed at {arrival_airport} "
                  f"(FlightStats status: '{status_raw}') — showing trip status board instead of flight board")
        return "status_board", build_trip_status_board(arrival_airport)

    return "board", build_board_data(leg["full_flight_number"], flight_data, now_utc)
