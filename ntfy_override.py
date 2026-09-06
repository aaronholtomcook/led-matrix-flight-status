"""
Polls ntfy.sh for pushed custom messages and tracks the currently-active
override (a pushed message temporarily replaces the normal display).
"""

import json
import logging
import requests
from datetime import timedelta

from config import NTFY_TOPIC, NTFY_POLL_SECONDS, NTFY_MESSAGE_DURATION_SECONDS

log = logging.getLogger("flight_status")

# Custom message override state — a message pushed via ntfy.sh temporarily
# replaces the normal display for NTFY_MESSAGE_DURATION_SECONDS, then reverts.
_message_override = {"text": None, "expires_at": None, "last_id": None}


def check_for_message_override(now_utc):
    """Poll ntfy.sh for any new message pushed to our topic. If found, set it
    as the active override. Silently does nothing if NTFY_TOPIC isn't set."""
    if not NTFY_TOPIC:
        return

    try:
        since_ts = int((now_utc - timedelta(seconds=NTFY_POLL_SECONDS * 3)).timestamp())
        url = f"https://ntfy.sh/{NTFY_TOPIC}/json?poll=1&since={since_ts}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        messages = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        actual_messages = [m for m in messages if m.get("event") == "message" and m.get("message")]
        if not actual_messages:
            return

        latest = max(actual_messages, key=lambda m: m["time"])
        if latest["id"] == _message_override["last_id"]:
            return  # already showing this one

        log.info(f"New ntfy message received: '{latest['message']}'")
        _message_override["text"] = latest["message"]
        _message_override["expires_at"] = now_utc + timedelta(seconds=NTFY_MESSAGE_DURATION_SECONDS)
        _message_override["last_id"] = latest["id"]
    except Exception as e:
        log.warning(f"Couldn't check ntfy for messages ({e})", exc_info=True)


def get_active_override(now_utc):
    """Return the currently-active pushed message text, or None if there
    isn't one / it has expired."""
    if _message_override["text"] and _message_override["expires_at"] and now_utc < _message_override["expires_at"]:
        return _message_override["text"]
    return None
