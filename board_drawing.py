"""
Everything that draws pixels: the flight board, status boards, icons, and
the wipe/overlay helpers used by the at-home interludes.
"""

from matrix_backend import graphics
from config import UK_TZ
from calendar_status import ordinal_day, format_trip_summary_line

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
COLOR_PALM_FROND = (40, 170, 90)      # green — breaks from the navy/red palette
                                        # deliberately, since a navy/red palm
                                        # tree wouldn't read as "vacation" at all
COLOR_PALM_TRUNK = (140, 92, 44)      # brown

ROW1_Y = 6    # route codes + roundel baseline
ROW2_Y = 13   # status word baseline
ROW3_Y = 20   # altitude/speed baseline
ROW4_Y = 27   # flight number / ETA baseline

STATUS_LINE1_Y = 23  # status board: flight number baseline (upcoming-flight layout)
STATUS_LINE2_Y = 30  # status board: takeoff time baseline (upcoming-flight layout)

LABEL_ROW_Y_START = 20  # vertical band covering just the single-line label row
LABEL_ROW_Y_END = 28    # (used to scope the at-home wipe/scroll to that row only,
                          # leaving the icon/date/time above it untouched)


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
        "..............",
        "RRRRRRRRRRRR..",
        ".RRRRRRRRRRRR.",
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


def draw_palm_icon(canvas, cx, cy, scale=2):
    """Small pixel-art leaning palm tree (green fronds, brown trunk),
    centered on (cx, cy) — used for the 'on a trip' vacation status board."""
    green = COLOR_PALM_FROND
    brown = COLOR_PALM_TRUNK
    pattern = [
        "..F.F.F..",
        ".FFFFFFF.",
        "...FFF...",
        "....T....",
        "....T....",
        "...TT....",
        "..TT.....",
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
            r, g, b = green if ch == 'F' else brown
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


def draw_black_overlay(canvas, x_start, x_end, y_start=0, y_end=31):
    """Black out columns x_start to x_end (inclusive) within rows y_start to
    y_end (inclusive; defaults to full height). Used to implement wipe
    transitions: draw the normal content first, then paint over the hidden
    portion with black — SetPixel calls are applied in order, so this
    correctly erases whatever was drawn just before it, including
    text/lines drawn via the graphics module."""
    if x_end < x_start:
        return
    for x in range(max(0, x_start), min(63, x_end) + 1):
        for y in range(max(0, y_start), min(31, y_end) + 1):
            canvas.SetPixel(x, y, 0, 0, 0)


def draw_status_board(canvas, small_font, data, now_utc=None, hide_label=False):
    """Draw the simpler board layout: a bigger logo (more room since there's
    no route-code row) with either a single centered label underneath (used
    for at-home / on-training), or two lines (used for an upcoming flight
    that hasn't taken off yet — flight number/route + takeoff time).
    Always shows the date (top-left) and time (top-right) in UK local time,
    flanking the icon. The time's colon blinks on/off each second so it's
    visually clear the clock is live, not a frozen static screen.
    hide_label=True skips drawing the label/sublabel entirely — used by the
    at-home 'next flight' interlude, which wipes/replaces only that text
    while everything else on screen stays untouched."""
    canvas.Clear()

    # Top accent bar, same as the flight board for visual consistency
    graphics.DrawLine(canvas, 0, 0, 63, 0, graphics.Color(*COLOR_ACCENT_BAR))

    # Bigger logo, centered in the upper portion — house for at-home, palm
    # for on-a-trip. Swoosh (training / upcoming flight) stays at its
    # original compact size here, same as it uses on the flight board —
    # at the bigger scale=2 used by house/palm it would collide with the
    # clock text in the corners, since it's a wider shape to begin with.
    icon = data.get("icon")
    if icon == "house":
        draw_house_icon(canvas, 32, 11, scale=2)
    elif icon == "palm":
        draw_palm_icon(canvas, 32, 11, scale=2)
    else:
        draw_wing_swoosh(canvas, 32, 11, scale=1)

    # Date top-left (day number, then month abbreviation stacked below it),
    # time top-right, on every status board. A narrow two-line date column
    # stays clear of the icon horizontally regardless of vertical extent,
    # avoiding the width crunch a single wider line ran into.
    # Redrawn every frame using the live clock, so it just stays current —
    # the status board already redraws every 0.5s in the main loop.
    if now_utc is not None:
        local_now = now_utc.astimezone(UK_TZ)
        day_str = ordinal_day(local_now.day)  # e.g. "30th", "1st", "3rd", "22nd"
        month_str = local_now.strftime("%b")  # e.g. "Sep"
        colon = ":" if local_now.second % 2 == 0 else " "
        time_str = local_now.strftime(f"%H{colon}%M")
        graphics.DrawText(canvas, small_font, 2, ROW1_Y, graphics.Color(*COLOR_ROUTE_CODE), day_str)
        graphics.DrawText(canvas, small_font, 2, ROW2_Y, graphics.Color(*COLOR_ROUTE_CODE), month_str)
        time_x = 64 - 1 - 4 * len(time_str)
        graphics.DrawText(canvas, small_font, time_x, ROW1_Y, graphics.Color(*COLOR_ROUTE_CODE), time_str)

    label = data["label"]
    sublabel = data.get("sublabel")

    if hide_label:
        return

    if sublabel:
        # Two-line layout: flight number/route, then takeoff time below it
        label_x = max(0, (64 - 4 * len(label)) // 2)
        graphics.DrawText(canvas, small_font, label_x, STATUS_LINE1_Y, graphics.Color(*COLOR_ROUTE_CODE), label)
        sub_x = max(0, (64 - 4 * len(sublabel)) // 2)
        graphics.DrawText(canvas, small_font, sub_x, STATUS_LINE2_Y, graphics.Color(*COLOR_ETA), sublabel)
    else:
        # Single-line layout: just the label, centered on the usual row
        label_x = max(0, (64 - 4 * len(label)) // 2)
        graphics.DrawText(canvas, small_font, label_x, ROW4_Y, graphics.Color(*COLOR_ROUTE_CODE), label)


def draw_coming_up_screen(canvas, small_font, trips, progress=0.0):
    """Full-screen takeover replacing the entire at-home display: a
    'COMING UP' heading with a small swoosh logo in the top-right corner,
    then up to three upcoming trips listed below it (one per row, reusing
    the same ROW2_Y/ROW3_Y/ROW4_Y grid the flight board uses).
    The top bar doubles as a progress indicator: it fills in blue from left
    to right as `progress` (0.0 to 1.0) increases, reaching fully blue right
    as the screen is about to revert back to the normal at-home display."""
    canvas.Clear()

    blue_end = int(64 * max(0.0, min(progress, 1.0)))
    for x in range(64):
        color = COLOR_ROUNDEL_OUTER if x < blue_end else COLOR_ACCENT_BAR
        canvas.SetPixel(x, 0, *color)

    graphics.DrawText(canvas, small_font, 1, ROW1_Y, graphics.Color(*COLOR_ROUTE_CODE), "COMING UP")
    draw_wing_swoosh(canvas, 56, 7, scale=1)

    trip_rows = [ROW2_Y, ROW3_Y, ROW4_Y]
    for row_y, trip in zip(trip_rows, trips):
        line = format_trip_summary_line(trip)
        graphics.DrawText(canvas, small_font, 1, row_y, graphics.Color(*COLOR_ETA), line)
