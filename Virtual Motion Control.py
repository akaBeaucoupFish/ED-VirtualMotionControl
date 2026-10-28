import os
import time
import threading
import gremlin
from gremlin.util import log
from vjoy import vjoy_interface
import gremlin.macro as macro
import gremlin.keyboard as keyboard
from gremlin import user_script

# ============================================================
# USER-CONFIGURABLE VARIABLES (R14)
# ============================================================

device_id_var = user_script.IntegerVariable(
    "vJoy Device ID",
    "Which vJoy device to control",
    is_optional=False,
    initial_value=1,
    min_value=1,
    max_value=16,
)

toggle_hotkey_var = user_script.KeyboardVariable(
    "Toggle Hotkey",
    "Key that starts/stops the camera sequence",
    is_optional=False,
)


keyboard_mode_var = user_script.StringVariable(
    "Keyboard Mode",
    "Keyboard input mode used for the toggle hotkey",
    is_optional=False,
    initial_value="Default",
)

moves_file_var = user_script.StringVariable(
    "Moves File",
    "Path to the camera moves script",
    is_optional=False,
    initial_value="camera_moves.txt",
)

loglevel_var = user_script.SelectionVariable(
    "Log Level",
    "How much detail to log",
    is_optional=False,
    option_list=["0 - Errors", "1 - Commands", "2 - Debug"],
    default_index=1,
)

log_vjoy_reacquire_var = user_script.BoolVariable(
    "Log vJoy Reacquire",
    "Show vJoy reacquire messages (debug only)",
    is_optional=True,
    initial_value=False,
)

update_interval_var = user_script.FloatVariable(
    "Update Interval",
    "Axis update interval during ramps (seconds)",
    is_optional=False,
    initial_value=0.01,
    min_value=0.0001,
    max_value=1.0,
)

pause_poll_var = user_script.FloatVariable(
    "Pause Poll Interval",
    "Polling interval during PAUSE commands (seconds)",
    is_optional=False,
    initial_value=0.01,
    min_value=0.0001,
    max_value=1.0,
)

key_tap_time_var = user_script.FloatVariable(
    "Key Tap Time",
    "Duration of KEYTAP presses (seconds)",
    is_optional=False,
    initial_value=0.05,
    min_value=0.001,
    max_value=1.0,
)

playback_rps_var = user_script.IntegerVariable(
    "Playback Reads Per Second",
    "How many samples per second to play back",
    is_optional=False,
    initial_value=10,
    min_value=1,
    max_value=200,
)

# ============================================================
# CONSTANTS (INTERNAL)
# ============================================================

LOG_PREFIX = "[CAMERA]"

DEVICE_ID = device_id_var.value
TOGGLE_HOTKEY = toggle_hotkey_var.value
DEFAULT_KEYBOARD_MODE = keyboard_mode_var.value

LOGLEVEL = int(loglevel_var.value.split()[0])
LOG_VJOY_REACQUIRE = log_vjoy_reacquire_var.value

UPDATE_INTERVAL = update_interval_var.value
PAUSE_POLL_INTERVAL = pause_poll_var.value
DEFAULT_KEY_TAP_TIME = key_tap_time_var.value

SCRIPT_DIR = os.path.dirname(__file__)
MOVES_FILE = os.path.join(SCRIPT_DIR, moves_file_var.value)

VJOY_MAX = 32767
PERCENT_OFFSET = 100.0
PERCENT_RANGE = 200.0
VJOY_INIT_DELAY = 0.05

COMMENT_PREFIX = "#"

CMD_AXES = "AXES"
CMD_PAUSE = "PAUSE"
CMD_KEYDOWN = "KEYDOWN"
CMD_KEYUP = "KEYUP"
CMD_KEYTAP = "KEYTAP"
CMD_BLOCK = "BLOCK"
CMD_END = "END"
CMD_CALL = "CALL"
CMD_IMPORT = "IMPORT"
CMD_PLAYBACK = "PLAYBACK"

MAX_CALL_DEPTH = 20

AXIS_X = 0x30
AXIS_Y = 0x31
AXIS_Z = 0x32
AXIS_RX = 0x33
AXIS_RY = 0x34
AXIS_RZ = 0x35

AXIS_IDS = {
    "x": AXIS_X,
    "y": AXIS_Y,
    "z": AXIS_Z,
    "rx": AXIS_RX,
    "ry": AXIS_RY,
    "rz": AXIS_RZ,
}

# ============================================================
# STATE
# ============================================================

last_axes = {k: 0.0 for k in AXIS_IDS}
running = False
stop_requested = False
sequence_thread = None
state_lock = threading.Lock()
vj = None
blocks = {}

# ============================================================
# LOGGING
# ============================================================

def log_msg(message, level=1):
    if LOGLEVEL >= level:
        ts = time.strftime("%H:%M:%S") + f".{int((time.time() % 1)*1000):03d}"
        log(f"{LOG_PREFIX} {ts} {message}")

# ============================================================
# VJOY INIT
# ============================================================

def init_vjoy():
    global vj
    if vj is not None:
        return

    vj = vjoy_interface.VJoyInterface()
    time.sleep(VJOY_INIT_DELAY)

    try:
        if not vj.AcquireVJD(DEVICE_ID):
            log_msg(f"Failed to acquire vJoy device {DEVICE_ID}", level=0)
        else:
            log_msg(f"Acquired vJoy device {DEVICE_ID}", level=2)
    except Exception as e:
        log_msg(f"Error initializing vJoy: {e}", level=0)

def ensure_vjoy():
    if vj is None:
        return
    try:
        status = vj.GetVJDStatus(DEVICE_ID)
        if status != 1:
            if LOG_VJOY_REACQUIRE:
                log_msg("Reacquiring vJoy device...", level=2)
            vj.AcquireVJD(DEVICE_ID)
    except Exception as e:
        log_msg(f"vJoy reacquire failed: {e}", level=0)

# ============================================================
# AXIS HELPERS
# ============================================================

def clamp_percent(v):
    return max(-100.0, min(100.0, float(v)))

def percent_to_vjoy(v):
    v = clamp_percent(v)
    return int(((v + PERCENT_OFFSET) / PERCENT_RANGE) * VJOY_MAX)

def set_axis_immediate(axis_name, percent):
    global last_axes
    init_vjoy()
    ensure_vjoy()

    value = clamp_percent(percent)
    vjoy_value = percent_to_vjoy(value)
    axis_id = AXIS_IDS[axis_name]

    try:
        vj.SetAxis(vjoy_value, DEVICE_ID, axis_id)
    except Exception as e:
        log_msg(f"SetAxis failed for {axis_name}: {e}", level=0)

    last_axes[axis_name] = value

def set_axes_immediate(x, y, z, rx, ry, rz):
    set_axis_immediate("x", x)
    set_axis_immediate("y", y)
    set_axis_immediate("z", z)
    set_axis_immediate("rx", rx)
    set_axis_immediate("ry", ry)
    set_axis_immediate("rz", rz)

def ramp_axes(x, y, z, rx, ry, rz, duration_s):
    global last_axes

    log_msg(
        f"Executing AXES: x={x} y={y} z={z} rx={rx} ry={ry} rz={rz} dur={duration_s}",
        level=1
    )

    targets = {
        "x": clamp_percent(x),
        "y": clamp_percent(y),
        "z": clamp_percent(z),
        "rx": clamp_percent(rx),
        "ry": clamp_percent(ry),
        "rz": clamp_percent(rz),
    }

    if duration_s <= 0:
        set_axes_immediate(
            targets["x"], targets["y"], targets["z"],
            targets["rx"], targets["ry"], targets["rz"]
        )
        return

    start = dict(last_axes)
    t_start = time.time()
    t_end = t_start + duration_s

    while True:
        if should_stop():
            return

        now = time.time()
        if now >= t_end:
            break

        t = (now - t_start) / duration_s
        for axis in targets:
            s = start[axis]
            e = targets[axis]
            value = s + (e - s) * t
            set_axis_immediate(axis, value)

        time.sleep(UPDATE_INTERVAL)

    set_axes_immediate(
        targets["x"], targets["y"], targets["z"],
        targets["rx"], targets["ry"], targets["rz"]
    )

# ============================================================
# PAUSE
# ============================================================

def should_stop():
    with state_lock:
        return stop_requested

def wait_seconds(seconds):
    log_msg(f"PAUSE {seconds} seconds", level=1)
    end = time.time() + seconds
    while time.time() < end:
        if should_stop():
            return
        time.sleep(PAUSE_POLL_INTERVAL)

# ============================================================
# KEYSTROKE HELPERS (R14)
# ============================================================

def parse_key_combo(combo_str):
    combo_str = combo_str.strip()
    if not combo_str:
        raise ValueError("Empty key combo")

    parts = combo_str.split("+")
    keys = []
    for name in parts:
        name = name.strip()
        try:
            key_obj = macro.key_from_name(name)
        except Exception as e:
            raise ValueError(f"Unknown key name '{name}' in combo '{combo_str}': {e}")
        keys.append(key_obj)

    if not keys:
        raise ValueError(f"No valid keys in combo '{combo_str}'")
    return keys

def send_key_down(keys):
    log_msg(f"KEYDOWN {'+'.join(k.name for k in keys)}", level=1)
    for k in keys:
        keyboard.send_key_down(k)

def send_key_up(keys):
    log_msg(f"KEYUP {'+'.join(k.name for k in keys)}", level=1)
    for k in keys:
        keyboard.send_key_up(k)

def send_key_tap(keys, tap_time=DEFAULT_KEY_TAP_TIME):
    log_msg(f"KEYTAP {'+'.join(k.name for k in keys)}", level=1)
    send_key_down(keys)
    time.sleep(tap_time)
    send_key_up(keys)

# ============================================================
# SCRIPT PARSING (IMPORT / BLOCK)
# ============================================================

def parse_script_file(path, visited_files=None):
    global blocks

    if visited_files is None:
        visited_files = set()

    full_path = os.path.abspath(path)
    if full_path in visited_files:
        log_msg(f"Skipping already imported file: {full_path}", level=2)
        return []

    visited_files.add(full_path)

    if not os.path.exists(full_path):
        log_msg(f"Script file not found: {full_path}", level=0)
        return []

    log_msg(f"IMPORT {full_path}", level=2)

    try:
        with open(full_path, "r") as f:
            raw_lines = f.readlines()
    except Exception as e:
        log_msg(f"Error reading script file '{full_path}': {e}", level=0)
        return []

    top_level_lines = []
    current_block_name = None
    current_block_lines = []

    base_dir = os.path.dirname(full_path)

    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith(COMMENT_PREFIX):
            continue

        parts = line.split()
        cmd = parts[0].upper()

        if cmd == CMD_IMPORT and current_block_name is None:
            if len(parts) < 2:
                log_msg(f"Invalid IMPORT line: {line}", level=0)
                continue
            import_path = " ".join(parts[1:]).strip()
            import_full = os.path.join(base_dir, import_path)
            imported_lines = parse_script_file(import_full, visited_files)
            top_level_lines.extend(imported_lines)
            continue

        if cmd == CMD_BLOCK and current_block_name is None:
            if len(parts) < 2:
                log_msg(f"BLOCK without name: {line}", level=0)
                continue
            current_block_name = parts[1]
            current_block_lines = []
            log_msg(f"Defining BLOCK '{current_block_name}'", level=2)
            continue

        if cmd == CMD_END and current_block_name is not None:
            blocks[current_block_name] = list(current_block_lines)
            log_msg(
                f"BLOCK '{current_block_name}' defined with {len(current_block_lines)} lines",
                level=2
            )
            current_block_name = None
            current_block_lines = []
            continue

        if current_block_name is not None:
            current_block_lines.append(line)
            continue

        top_level_lines.append(line)

    if current_block_name is not None:
        log_msg(f"BLOCK '{current_block_name}' not closed with END", level=0)

    return top_level_lines

# ============================================================
# SEQUENCE EXECUTION
# ============================================================

def execute_lines(lines):
    global running, stop_requested

    log_msg("Sequence execution started", level=1)

    stack = [(lines, 0)]

    while stack and not should_stop():
        current_lines, idx = stack[-1]

        if idx >= len(current_lines):
            stack.pop()
            continue

        line = current_lines[idx]
        stack[-1] = (current_lines, idx + 1)

        if not line or line.startswith(COMMENT_PREFIX):
            continue

        parts = line.split()
        cmd = parts[0].upper()

        if cmd == CMD_AXES:
            if len(parts) != 8:
                log_msg(f"Invalid AXES line: {line}", level=0)
                continue
            try:
                x, y, z, rx, ry, rz, dur = parts[1:]
                ramp_axes(
                    x=float(x),
                    y=float(y),
                    z=float(z),
                    rx=float(rx),
                    ry=float(ry),
                    rz=float(rz),
                    duration_s=float(dur),
                )
            except Exception as e:
                log_msg(f"Error parsing AXES line '{line}': {e}", level=0)

        elif cmd == CMD_PAUSE:
            if len(parts) != 2:
                log_msg(f"Invalid PAUSE line: {line}", level=0)
                continue
            try:
                wait_seconds(float(parts[1]))
            except Exception as e:
                log_msg(f"Error parsing PAUSE line '{line}': {e}", level=0)

        elif cmd in (CMD_KEYDOWN, CMD_KEYUP, CMD_KEYTAP):
            combo_str = " ".join(parts[1:]).strip()
            try:
                keys = parse_key_combo(combo_str)
            except Exception as e:
                log_msg(f"Error parsing {cmd} '{combo_str}': {e}", level=0)
                continue

            if should_stop():
                break

            try:
                if cmd == CMD_KEYDOWN:
                    send_key_down(keys)
                elif cmd == CMD_KEYUP:
                    send_key_up(keys)
                elif cmd == CMD_KEYTAP:
                    send_key_tap(keys)
            except Exception as e:
                log_msg(f"Error executing {cmd} '{combo_str}': {e}", level=0)

        elif cmd == CMD_CALL:
            if len(parts) < 2:
                log_msg(f"CALL without block name: {line}", level=0)
                continue
            block_name = parts[1]
            if block_name not in blocks:
                log_msg(f"CALL to undefined block '{block_name}'", level=0)
                continue

            if len(stack) >= MAX_CALL_DEPTH:
                log_msg(f"Max CALL depth exceeded when calling '{block_name}'", level=0)
                continue

            block_lines = blocks[block_name]
            log_msg(f"CALL {block_name} ({len(block_lines)} lines)", level=1)
            stack.append((block_lines, 0))

        elif cmd == CMD_PLAYBACK:
            if len(parts) < 2:
                log_msg(f"Invalid PLAYBACK line: {line}", level=0)
                continue

            playback_path = " ".join(parts[1:]).strip()
            full_path = os.path.join(SCRIPT_DIR, playback_path)

            if not os.path.exists(full_path):
                log_msg(f"Playback file not found: {full_path}", level=0)
                continue

            try:
                playback_file(full_path)
            except Exception as e:
                log_msg(f"Error during PLAYBACK '{playback_path}': {e}", level=0)

        else:
            log_msg(f"Unknown command: {cmd}", level=0)

    log_msg("Sequence execution complete", level=1)

    with state_lock:
        running = False
        stop_requested = False

def sequence_worker():
    global running, stop_requested, blocks

    blocks = {}

    if not os.path.exists(MOVES_FILE):
        log_msg("Movement file not found!", level=0)
        with state_lock:
            running = False
        return

    log_msg(f"Loading script: {MOVES_FILE}", level=1)

    top_level_lines = parse_script_file(MOVES_FILE)

    if not top_level_lines and not blocks:
        log_msg("No executable lines or blocks found in script.", level=0)
        with state_lock:
            running = False
        return

    execute_lines(top_level_lines)

# ============================================================
# Read and playback file containing X, Y, Z, rX, rY, rZ %'s
# ============================================================

def playback_file(path):
    log_msg(f"PLAYBACK {path}", level=1)

    rps = playback_rps_var.value
    interval = 1.0 / float(rps)

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        log_msg(f"Failed to read playback file '{path}': {e}", level=0)
        return

    for raw in lines:
        if should_stop():
            set_axes_immediate(0, 0, 0, 0, 0, 0)
            return

        raw = raw.strip()
        if not raw:
            continue

        parts = raw.split()
        if len(parts) != 6:
            log_msg(f"Invalid playback line: {raw}", level=0)
            continue

        try:
            x, y, z, rx, ry, rz = map(float, parts)
        except Exception:
            log_msg(f"Invalid numeric values in playback line: {raw}", level=0)
            continue

        set_axes_immediate(x, y, z, rx, ry, rz)
        time.sleep(interval)

    # Playback finished normally — reset axes
    set_axes_immediate(0, 0, 0, 0, 0, 0)
    log_msg("PLAYBACK complete", level=1)

# ============================================================
# TOGGLE HOTKEY (R14)
# ============================================================

@toggle_hotkey_var.decorator(keyboard_mode_var)
def toggle_sequence(event):
    global running, stop_requested, sequence_thread

    if not event.is_pressed:
        return

    init_vjoy()

    with state_lock:
        if not running:
            log_msg("Starting sequence", level=1)
            running = True
            stop_requested = False
            sequence_thread = threading.Thread(target=sequence_worker, daemon=True)
            sequence_thread.start()
        else:
            log_msg("Stop requested", level=1)
            stop_requested = True
            set_axes_immediate(0, 0, 0, 0, 0, 0)

