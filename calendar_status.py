"""
Everything to do with pulling the FleetLife roster calendar, parsing flight
legs out of event descriptions, and figuring out Aaron's current/upcoming
status from it.
"""

import re
import logging
import requests
from icalendar import Calendar
from datetime import datetime, timedelta, timezone

from config import ICS_URL, CALENDAR_CACHE_SECONDS, UK_TZ, HOME_AIRPORT

log = logging.getLogger("flight_status")


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


def find_current_location(legs, now_utc):
    """Find where Aaron currently is during a layover — the arrival airport
    of the most recently completed leg. Returns None if no leg has
    completed yet (e.g. the trip's report day, before the first leg departs)."""
    completed = [l for l in legs if l["arr_time_utc"] <= now_utc]
    if not completed:
        return None
    latest = max(completed, key=lambda l: l["arr_time_utc"])
    log.debug(f"Most recently completed leg: {latest['full_flight_number']}, now at {latest['arr_airport']}")
    return latest["arr_airport"]


def build_trip_status_board(location):
    """Build the status board data for wherever Aaron currently is on a
    trip. Home base gets the swoosh + 'AT LHR' treatment (it's not really a
    vacation), everywhere else gets the palm tree + 'IN xxx' treatment."""
    if location == HOME_AIRPORT:
        return {"label": f"AT {location}", "icon": "swoosh"}
    return {"label": f"IN {location}", "icon": "palm"}


def ordinal_day(n):
    """3 -> '3rd', 11 -> '11th', 21 -> '21st', etc."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def find_next_trip_leg(cal, now_utc):
    """Look ahead (beyond today) for the next upcoming trip event, and
    return its first leg (dep/arr/departure time), or None if there isn't
    one — e.g. nothing currently scheduled, or the roster doesn't reach
    that far ahead yet."""
    today = now_utc.date()
    upcoming = []
    for component in cal.walk("VEVENT"):
        title = str(component.get("summary", ""))
        if "✈️" not in title:
            continue
        dtstart = component.get("dtstart").dt
        start_date = dtstart if not isinstance(dtstart, datetime) else dtstart.date()
        if start_date > today:
            upcoming.append((start_date, component))

    if not upcoming:
        log.debug("No upcoming trip events found in calendar")
        return None

    upcoming.sort(key=lambda x: x[0])
    _, next_event = upcoming[0]
    description = str(next_event.get("description", ""))
    legs = parse_flight_legs(description)
    if not legs:
        log.debug("Next upcoming trip event had no parseable legs")
        return None

    first_leg = min(legs, key=lambda l: l["dep_time_utc"])
    log.debug(f"Next trip: {first_leg['full_flight_number']} "
               f"{first_leg['dep_airport']}-{first_leg['arr_airport']} on {first_leg['dep_time_utc']}")
    return first_leg


def build_next_flight_message(leg):
    """Build the 'Next Flight: BA249 LHR-GIG 13th Sep 21:45' scrolling
    message from a leg, split into parts so the flight number can be drawn
    in a different color (red) than the rest of the line."""
    local_dep = leg["dep_time_utc"].astimezone(UK_TZ)
    date_str = f"{ordinal_day(local_dep.day)} {local_dep.strftime('%b')}"
    time_str = local_dep.strftime("%H:%M")
    return {
        "prefix": "Next Flight: ",
        "flight_num": leg["full_flight_number"],
        "suffix": f" {leg['dep_airport']}-{leg['arr_airport']} {date_str} {time_str}   ",
    }


def find_upcoming_trips_summary(cal, now_utc, max_count=3):
    """Find the next few upcoming trip events (beyond today). For each,
    return the overall start/end date (first leg's departure to last leg's
    arrival) and the first leg's destination — used for the 'Coming Up'
    screen's compact trip list."""
    today = now_utc.date()
    upcoming = []
    for component in cal.walk("VEVENT"):
        title = str(component.get("summary", ""))
        if "✈️" not in title:
            continue
        dtstart = component.get("dtstart").dt
        start_date = dtstart if not isinstance(dtstart, datetime) else dtstart.date()
        if start_date > today:
            upcoming.append((start_date, component))

    upcoming.sort(key=lambda x: x[0])
    summaries = []
    for _, event in upcoming[:max_count]:
        description = str(event.get("description", ""))
        legs = parse_flight_legs(description)
        if not legs:
            continue
        first_leg = min(legs, key=lambda l: l["dep_time_utc"])
        last_leg = max(legs, key=lambda l: l["arr_time_utc"])
        summaries.append({
            "start": first_leg["dep_time_utc"].astimezone(UK_TZ),
            "end": last_leg["arr_time_utc"].astimezone(UK_TZ),
            "destination": first_leg["arr_airport"],
        })

    log.debug(f"Found {len(summaries)} upcoming trip(s) for the Coming Up screen")
    return summaries


def format_trip_summary_line(trip):
    """'1-3 Sep GIG' (or '30 Aug-2 Sep GIG' if it spans two months) — kept
    compact (no ordinal suffixes, minimal spacing) so up to three of these
    fit on screen simultaneously, unlike the wordier Next Flight message."""
    start, end = trip["start"], trip["end"]
    if start.month == end.month:
        date_part = f"{start.day}-{end.day} {end.strftime('%b')}"
    else:
        date_part = f"{start.day} {start.strftime('%b')}-{end.day} {end.strftime('%b')}"
    return f"{date_part} {trip['destination']}"


def get_status_and_leg(now_utc):
    """
    Returns a tuple: (status_text, current_leg_or_None, legs_or_None)
    current_leg is populated whenever status is "on a trip" AND today has a
    relevant flight leg (scheduled, in-progress, or just-landed). legs is the
    full parsed leg list for the trip (needed to find the current location on
    layover days when no leg touches today) — only populated when status is
    "on a trip", None otherwise.
    """
    cal = get_calendar(now_utc)

    today = now_utc.date()
    events = get_todays_events(cal, today)

    if not events:
        log.info("No events today -> Aaron is at home")
        return "Aaron is at home", None, None

    for event in events:
        title = str(event.get("summary", ""))
        log.debug(f"Evaluating event: '{title}'")

        if "✈️" in title:
            log.info(f"Trip event found: '{title}'")
            description = str(event.get("description", ""))
            legs = parse_flight_legs(description)
            relevant_leg = find_relevant_leg_for_today(legs, now_utc)
            return "Aaron is on a trip", relevant_leg, legs

        if "Simulator" in title or "Duty" in title:
            log.info(f"Training event found: '{title}' -> Aaron is on training")
            return "Aaron is on training", None, None

    log.info("Event(s) present but none matched known patterns -> defaulting to at home")
    return "Aaron is at home", None, None
