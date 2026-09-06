"""
Fetching and parsing live flight data from FlightStats, and building the
structured data the flight board layout needs from it.
"""

import json
import logging
import requests
from datetime import datetime

from config import FLIGHTSTATS_HEADERS, UK_TZ

log = logging.getLogger("flight_status")


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
