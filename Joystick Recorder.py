import os
import time
import threading

import gremlin
from gremlin import user_script
from gremlin.util import log
from gremlin import types as gtypes
from gremlin.audio_player import AudioPlayer

#
# ---------------------------------------------------------------------------
# User configuration
# ---------------------------------------------------------------------------
#

keyboard_mode_var = user_script.StringVariable(
    "Keyboard Mode",
    "Keyboard mode this hotkey belongs to",
    is_optional=False,
    initial_value="Default"
)

toggle_hotkey_var = user_script.KeyboardVariable(
    "Toggle Recording Hotkey",
    "Press to start/stop joystick recording",
    is_optional=False
)

script_dir = os.path.dirname(os.path.abspath(__file__))
default_record_path = os.path.join(script_dir, "Joystick Recordings")

record_path_var = user_script.StringVariable(
    "Recording Folder",
    "Folder where .txt recordings will be saved",
    is_optional=False,
    initial_value=default_record_path
)

reads_per_second_var = user_script.IntegerVariable(
    "Reads Per Second",
    "How many samples per second to record",
    is_optional=False,
    initial_value=10,
    min_value=1,
    max_value=200
)

max_duration_seconds_var = user_script.IntegerVariable(
    "Max Duration (seconds)",
    "Maximum recording length before auto-stop",
    is_optional=False,
    initial_value=600,
    min_value=1,
    max_value=3600
)

# Warning beep
warning_beep_var = user_script.BoolVariable(
    "Play Warning Beep",
    "Beep every second while recording",
    is_optional=False,
    initial_value=True
)

# Six axes for 6DOF
axis_x_var = user_script.PhysicalInputVariable(
    "X Axis Source", "Joystick axis for X", False,
    [gtypes.InputType.JoystickAxis]
)
axis_y_var = user_script.PhysicalInputVariable(
    "Y Axis Source", "Joystick axis for Y", False,
    [gtypes.InputType.JoystickAxis]
)
axis_z_var = user_script.PhysicalInputVariable(
    "Z Axis Source", "Joystick axis for Z", False,
    [gtypes.InputType.JoystickAxis]
)
axis_rx_var = user_script.PhysicalInputVariable(
    "rX Axis Source", "Joystick axis for rX", False,
    [gtypes.InputType.JoystickAxis]
)
axis_ry_var = user_script.PhysicalInputVariable(
    "rY Axis Source", "Joystick axis for rY", False,
    [gtypes.InputType.JoystickAxis]
)
axis_rz_var = user_script.PhysicalInputVariable(
    "rZ Axis Source", "Joystick axis for rZ", False,
    [gtypes.InputType.JoystickAxis]
)


#
# ---------------------------------------------------------------------------
# Runtime state (shared axis cache)
# ---------------------------------------------------------------------------
#

axis_state = {
    "x": 0.0,
    "y": 0.0,
    "z": 0.0,
    "rx": 0.0,
    "ry": 0.0,
    "rz": 0.0,
}

recording_active = False
stop_requested = False
recording_thread = None


#
# ---------------------------------------------------------------------------
# Axis event handlers (R14 user-script decorator model)
# ---------------------------------------------------------------------------
#

@axis_x_var.decorator(keyboard_mode_var)
def on_x(event):
    axis_state["x"] = event.value

@axis_y_var.decorator(keyboard_mode_var)
def on_y(event):
    axis_state["y"] = event.value

@axis_z_var.decorator(keyboard_mode_var)
def on_z(event):
    axis_state["z"] = event.value

@axis_rx_var.decorator(keyboard_mode_var)
def on_rx(event):
    axis_state["rx"] = event.value

@axis_ry_var.decorator(keyboard_mode_var)
def on_ry(event):
    axis_state["ry"] = event.value

@axis_rz_var.decorator(keyboard_mode_var)
def on_rz(event):
    axis_state["rz"] = event.value


#
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
#

def _ensure_record_folder():
    folder = record_path_var.value
    if not folder:
        folder = default_record_path
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    return folder


def _next_recording_filename():
    folder = _ensure_record_folder()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(folder, f"joystick_recording_{timestamp}.txt")


#
# ---------------------------------------------------------------------------
# Recorder thread (reads cached axis values)
# ---------------------------------------------------------------------------
#

def _recorder_thread():
    global recording_active, stop_requested

    filename = _next_recording_filename()
    reads_per_second = reads_per_second_var.value
    max_duration = max_duration_seconds_var.value
    beep_enabled = warning_beep_var.value

    script_dir = os.path.dirname(os.path.abspath(__file__))
    beep_path = os.path.join(script_dir, "beep.wav")

    interval = 1.0 / float(reads_per_second)
    start_time = time.time()
    last_beep = 0

    log(f"[Recorder] Writing to: {filename}")

    try:
        with open(filename, "w", encoding="utf-8") as f:
            while not stop_requested:
                now = time.time()
                elapsed = now - start_time

                if elapsed >= max_duration:
                    break

                # Optional beep every second
                if beep_enabled and now - last_beep >= 1.0:
                    audio = AudioPlayer()
                    audio.start()
                    audio.enqueue(beep_path, 100)   # 100 = full volume
                    last_beep = now

                # Convert to percentages [-100, 100]
                def pct(v):
                    return max(-100.0, min(100.0, v * 100.0))

                px = pct(axis_state["x"])
                py = pct(axis_state["y"])
                pz = pct(axis_state["z"])
                prx = pct(axis_state["rx"])
                pry = pct(axis_state["ry"])
                prz = pct(axis_state["rz"])

                # Write one line per sample (NO timestamp column)
                f.write(
                    f"{px:7.3f} {py:7.3f} {pz:7.3f} "
                    f"{prx:7.3f} {pry:7.3f} {prz:7.3f}\n"
                )

                time.sleep(interval)

    finally:
        recording_active = False
        stop_requested = False
        log("[Recorder] Recording stopped")


#
# ---------------------------------------------------------------------------
# Toggle hotkey
# ---------------------------------------------------------------------------
#

@toggle_hotkey_var.decorator(keyboard_mode_var)
def toggle_recording(event):
    global recording_active, stop_requested, recording_thread

    if not event.is_pressed:
        return

    if not recording_active:
        log("[Recorder] Starting recording")
        recording_active = True
        stop_requested = False

        recording_thread = threading.Thread(
            target=_recorder_thread,
            daemon=True
        )
        recording_thread.start()
    else:
        log("[Recorder] Stop requested")
        stop_requested = True
