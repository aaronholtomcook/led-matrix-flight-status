# led-matrix-flight-status
Poll my calendar to find where I am in the world, and then display it on my LED


rgbmatrix_sim

Add the fonts from the https://github.com/hzeller/rpi-rgb-led-matrix/ project into the fonts folder

## Command-line arguments

| Flag | Description |
|---|---|
| `--date YYYY-MM-DD` | Pretend it's this date (UTC) instead of today. Useful for testing how the display looks on a real trip day before it happens. |
| `--time HH:MM` | Pretend it's this specific time (UTC) on the given date. Requires `--date` to also be set. |
| `--test-flight AA123` | Skip the calendar entirely and show live/scheduled FlightStats data for this exact flight number (e.g. `BA409`), refreshed periodically. Handy for testing the board layout against any real flight, any time. |
| `--debug` | Enable verbose DEBUG-level logging — full parsed field dumps, every candidate leg considered, raw calendar matches, etc. — on top of the normal INFO-level logs. |
| `--dev-mode` | Run using the `rgbmatrix_sim` emulator instead of real hardware. Auto-enabled on Windows; pass explicitly to force it on any platform. |

### Examples

```bash
# Normal operation (real hardware, real current time)
sudo -E env PATH=$PATH python3 test.py

# Test what the board looks like for a specific flight, right now
sudo -E env PATH=$PATH python3 test.py --test-flight BA409

# Simulate being mid-flight on a specific upcoming trip
sudo -E env PATH=$PATH python3 test.py --date 2026-09-13 --time 22:00

# Run with full debug logging
sudo -E env PATH=$PATH python3 test.py --debug

# Develop/test locally without a Pi (Windows or forced elsewhere)
python3 test.py --dev-mode
```

Flags can be combined, e.g. `--test-flight BA409 --debug` to debug-trace a specific flight's data fetch and formatting.