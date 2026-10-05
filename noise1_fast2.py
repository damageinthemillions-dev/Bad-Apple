from PIL import Image
from os import get_terminal_size
import imageio
import imageio.v3 as imo
import numpy as np
import random
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable
from pathlib import Path
import math
from pynput import keyboard
from scipy.ndimage import uniform_filter
import subprocess
import shutil
import tempfile
import imageio_ffmpeg

stupid_round = round

def round(n: float) -> int:
    if n >= 0:
        return int(n + 0.5)
    else:
        return int(n - 0.5)

@dataclass
class ThrottleState:
    last_called: float = 0.0
    scheduled: bool = False

throttledFunctions: dict[str, float] = {}

states: dict[str, ThrottleState] = {}

def throttle(amount: float = 0.2):
    def decorator(func: Callable):
        last_called = 0.0
        scheduled = False

        def wrapper(*args, **kwargs):
            nonlocal last_called, scheduled
            now = time.monotonic()
            elapsed = now - last_called

            # Leading edge
            if elapsed >= amount:
                last_called = now
                func(*args, **kwargs)
                return

            # Trailing edge (schedule once)
            if not scheduled:
                scheduled = True
                delay = amount - elapsed

                def delayed():
                    nonlocal scheduled
                    scheduled = False
                    func(*args, **kwargs)

                threading.Timer(delay, delayed).start()
                last_called = now + delay + amount


        return wrapper
    return decorator

def formatText(text: str):
    return "".join((current_select := [], [
        (current_select.append(char), "" if char != ";" else (
            ops := current_select[1:],
            current_select := [],
            "\x1b[0m" if ops[0] == "r" else
            "\x1b[1m" if ops[0] == "s" else
            "\x1b[7m" if ops[0] == "v" else
            "\x1b[22m" if ops[0] == "n" else
            (color := (
                "9" if ops[1] == "r" else
                "8;2;" + ";".join(map(str, {
                    "p": (255, 100, 23),
                    "s": (255, 185, 23),
                    "d": (36, 36, 36),
                    "l": (232, 232, 232),
                    "f": (5, 247, 17),
                    "c": (31, 181, 240),
                    "g": (135, 135, 135)
                }[ops[1]]))
            ), "\x1b[" + ("3" if ops[0] == "f" else "4") + color + "m")[1]
        )[2])[1]
        if char in "$;" or current_select != [] else char
    for char in text])[1])

@throttle(0.1)
def show_progress(progress: int, filter_name: str, in_image: Path, out_image: Path, fps: float = None):
    global last_size
    terminalSize = get_terminal_size()
    calculatedLine: str = (("" if show_progress.started else '\033[2J\033[H') + "$fp;" +
        f"$s;$fl;$bp; {filter_name} $fp;$br;$n;$fs;$v;$r;$fd;$bs; " + (paths := in_image.as_posix().split("/"), "$fs;$br;$v;$r;$fd;$bs;".join(["$s;"+dir+"$n;" if i == len(paths)-1 else dir for i, dir in enumerate(paths)]))[1] +
        " $n;$fs;$br;$fc;$v;$r;$fl;$bc; " + (paths := out_image.as_posix().split("/"), "$fc;$br;$v;$r;$fl;$bc;".join(["$s;"+dir+"$n;" if i == len(paths)-1 else dir for i, dir in enumerate(paths)]))[1] + " $n;$fc;$br;$r; "
    )
    availibleSpace = terminalSize.columns-(15+len(filter_name)+len(in_image.as_posix())+len(out_image.as_posix()))
    show_fps = False
    if fps != None and availibleSpace >= 20: availibleSpace -= 12; show_fps = True
    symbolSpace: int = availibleSpace * 8
    calculatedAmount: float = round(symbolSpace * progress) / 8
    calculatedLine += (("$fl;" if round(calculatedAmount*8) != 0 else "$fg;") if progress != 1 else "$ff;") + "$bg;" + ("$fl;" if progress != 1 else "$ff;")
    calculatedLine += "█" * math.floor(calculatedAmount)
    calculatedLine += ["","▏","▎","▍","▌","▋","▊","▉"][round((calculatedAmount-math.floor(calculatedAmount))*8)]
    calculatedLine += " " * round((symbolSpace/8) - math.ceil(calculatedAmount))
    calculatedLine += "$r;" + (("$fg;" if math.floor(calculatedAmount) != availibleSpace else "$fl;") if progress != 1 else "$ff;")
    calculatedLine += "" if not show_fps else f"$fs;$br;$v;$r;$s;$fd;$bs;{"".join((digit := str(math.floor(fps)), " "*(2-len(digit))+digit)[1])}.{"".join((digit := str(int(fps%1*100)), "0"*(2-len(digit))+digit)[1])}fps $n;$fs;$br;"
    calculatedLine += "$r;" + "\n"
    calculatedLine += "\n".join([log if len(log) <= terminalSize.columns else log[:(terminalSize.columns-3)]+"..." for log in show_progress.logs][(-1*(terminalSize.lines-2)):])
    calculatedLine = formatText(calculatedLine)
    print((f"\x1b[{show_progress.last_size}A" if show_progress.last_size > 0 else '') + calculatedLine)
    show_progress.last_size = calculatedLine.count("\n") + 1
    show_progress.started = True
show_progress.started = False
show_progress.last_size = 0
show_progress.logs = []
show_progress.config = {
    "saveLogs": False,
    "capFrames": None
}

def lade_bild(pfad):
    img = Image.open(pfad).convert("RGB")
    breite, hoehe = img.size
    bild = []
    for y in range(hoehe):
        zeile = []
        for x in range(breite):
            zeile.append(img.getpixel((x, y)))
        bild.append(zeile)
    return bild

def speichere_bild(bild, pfad):
    hoehe = len(bild)
    breite = len(bild[0])
    img = Image.new("RGB", (breite, hoehe))
    for y in range(hoehe):
        for x in range(breite):
            img.putpixel((x, y), bild[y][x])
    img.save(pfad)


# VIDEO I/O HELPERS (.mov / .mp4 + audio)

def _temp_video_path(out_video: Path) -> Path:
    return out_video.with_name(out_video.stem + "._silent" + (out_video.suffix or ".mp4"))

def open_writer(path: Path, fps: float):
    return imageio.get_writer(
        str(path), fps=fps, codec="libx264", pixelformat="yuv420p",
        macro_block_size=1,   # don't silently resize odd-sized frames
    )

def mux_audio(silent_video: Path, source_video: Path, out_video: Path,
              start_frame: int, n_frames: int, fps: float) -> bool:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    start = start_frame / fps
    duration = n_frames / fps
    is_mov = out_video.suffix.lower() in (".mov", ".qt")
    cmd = [
        ffmpeg, "-y",
        "-i", str(silent_video),
        "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(source_video),
        "-map", "0:v:0", "-map", "1:a:0?",      # '?' = don't fail if no audio
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
    ]
    if is_mov:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out_video))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        show_progress.logs.append("Audio mux failed, keeping silent video.")
        show_progress.logs.append(result.stderr.strip().splitlines()[-1] if result.stderr else "")
        shutil.move(str(silent_video), str(out_video))
        return False
    silent_video.unlink(missing_ok=True)
    return True

def safe_frame_count(reader) -> int:
    #count_frames() can't return inf for some .mov files (idk why); fall back to duration*fps.
    try:
        n = reader.count_frames()
        if n != float("inf"):
            return int(n)
    except Exception:
        pass
    meta = reader.get_meta_data()
    return int(round(meta.get("duration", 0) * meta.get("fps", 24)))

def static_effect_one(in_image: Path, out_image: Path, condition: Callable): (static_effect_one.__setattr__("noise", []) if not hasattr(static_effect_one, 'noise') else None, show_progress.__setattr__("logs", []) if not hasattr(show_progress, 'logs') else None, reader := imageio.get_reader(str(in_image)), writer := imageio.get_writer(str(out_image), fps=reader.get_meta_data().get("fps", 24)), frame_count := reader.count_frames(), [(height := frame.shape[:2][0], width := frame.shape[:2][1], static_effect_one.__setattr__("noise", [[2]*width for row in range(height)]) if i == 0 else None, writer.append_data(np.array([[tuple([(random.getrandbits(1), static_effect_one.noise[y].__setitem__(x, np.int8(2)))[0]*255]*3) if condition(frame[y,x]) else tuple([(pixel := random.getrandbits(1), static_effect_one.noise[y].__setitem__(x, np.int8(pixel)))[0]*255]*3) if static_effect_one.noise[y][x] == 2 else tuple([(static_effect_one.noise[y][x].item(), static_effect_one.noise[y].__setitem__(x, np.int8(2)))[0]*255]*3) if condition(frame[y,x]) else tuple([static_effect_one.noise[y][x].item()*255]*3) for x in range(width)] for y in range(height)], dtype=np.uint8)), show_progress.logs.append(f"[{" "*(len(str(frame_count))-len(str(i+1)))+str(i+1)}/{frame_count}] Frame done!"), show_progress((i+1) / frame_count , "Static", in_image, out_image)) for i, frame in enumerate(reader)], reader.close(), writer.close(), show_progress.logs.append(f"Finished and saved to: {str(out_image)}"), show_progress(1 , "Static", in_image, out_image)) 

#I'm so sorry for anyone trying to comprehend this shit

def static_effect(in_image: Path, out_image: Path, condition: Callable = lambda pixel: sum(pixel) / 3 >= 128):
    if not hasattr(static_effect, 'noise'):
        static_effect.noise = []
    reader = imageio.get_reader(str(in_image))
    fps = reader.get_meta_data().get("fps", 24)
    tmp_out = _temp_video_path(out_image)
    writer = open_writer(tmp_out, fps)
    frame_count = safe_frame_count(reader)
    if not hasattr(show_progress, 'logs'):
        show_progress.logs = []
    start_time = time.time()
    time_diff = []

    for i, frame in enumerate(reader):
        if frame.ndim == 3 and frame.shape[2] == 4:   # drop alpha if present
            frame = frame[:, :, :3]
        height, width = frame.shape[:2]
        
        if i == 0:
            static_effect.noise = [[2]*width for row in range(height)]
        image = []
        for y in range(height):
            zeile = []
            for x in range(width):
                r, g, b = frame[y, x]
                zeile.append((int(r), int(g), int(b)))
            image.append(zeile)

        result = [[(
            eq := condition(image[y][x]),
            tuple([(random.getrandbits(1), static_effect.noise[y].__setitem__(x, np.int8(2)))[0]*255]*3)
                if eq else
            tuple([(pixel := random.getrandbits(1), static_effect.noise[y].__setitem__(x, np.int8(pixel)))[0]*255]*3)
            if static_effect.noise[y][x] == 2 else
            tuple([(static_effect.noise[y][x].item(), static_effect.noise[y].__setitem__(x, np.int8(2)))[0]*255]*3)
                if eq else
            tuple([static_effect.noise[y][x].item()*255]*3)
        )[1] for x in range(width)] for y in range(height)]
        out_frame = np.array(result, dtype=np.uint8)
        writer.append_data(out_frame)
        current_time = time.time()
        show_progress.logs.append(f"[{" "*(len(str(frame_count))-len(str(i+1)))+str(i+1)}/{frame_count}] Frame done in {str(current_time - start_time)[:5]}s")
        time_diff.append(current_time - start_time)
        start_time = current_time
        show_progress((i+1) / frame_count , "Static", in_image, out_image, float(f"{round(len(time_diff[-60:]) / sum(time_diff[-60:])*100)/100:.2f}"))

    reader.close()
    writer.close()
    mux_audio(tmp_out, in_image, out_image, 0, len(time_diff), fps)
    show_progress.logs.append(f"Finished and saved to: {str(out_image)}")
    show_progress.logs.append(f"\x1b[1mPress to finish\x1b[22m"+ " "*(get_terminal_size().columns-15))
    show_progress(1 , "Static", in_image, out_image, float(f"{round(len(time_diff) / sum(time_diff)*100)/100:.2f}"))
    input("")

def run_through_filter(in_image: Path, from_frame, to_frame, out_image: Path, name: str, filter: Callable[..., np.ndarray], params: list[Any]):
    reader = imageio.get_reader(str(in_image))
    fps = reader.get_meta_data().get("fps", 24)
    tmp_out = _temp_video_path(out_image)
    writer = open_writer(tmp_out, fps)
    frame_count = safe_frame_count(reader)
    to_frame = min(to_frame, frame_count)   # never run past the end of the video; it will kill itself
    if not hasattr(show_progress, 'logs'):
        show_progress.logs = []
    start_time = time.time()
    time_diff = []
    running = True
    def on_release(key: keyboard.Key):
        nonlocal running
        if key == keyboard.Key.f12:
            running = False
            return False
    listener = keyboard.Listener(on_release=on_release)
    listener.start()
    reader.set_image_index(from_frame)

    for i in range(from_frame, to_frame):
        if not running:
            show_progress.logs.append(f"Manually stopped process with Esc.")
            break
        # Use the already-open reader to avoid imo.imread's legacy plugin bug.
        # get_data() returns imageio's Array subclass, so copy into a plain ndarray.
        raw = reader.get_data(i)
        frame = np.array(raw, dtype=np.uint8, copy=True)
        if frame.ndim == 3 and frame.shape[2] == 4:   # drop alpha if present
            frame = frame[:, :, :3]

        # Filters now accept and return numpy arrays directly
        out_frame = filter(frame, *params)

        writer.append_data(out_frame)
        current_time = time.time()
        show_progress.logs.append(f"[{" "*(len(str(frame_count))-len(str(i+1)))+str(i+1)}/{frame_count}] Frame done in {str(current_time - start_time)[:5]}s")
        time_diff.append(current_time - start_time)
        start_time = current_time
        show_progress((i+1) / frame_count, name, in_image, out_image, float(f"{round(len(time_diff[-60:]) / sum(time_diff[-60:])*100)/100:.2f}"))

    reader.close()
    writer.close()
    mux_audio(tmp_out, in_image, out_image, from_frame, len(time_diff), fps)
    show_progress.logs.append(f"Finished and saved to: {str(out_image)}")
    show_progress.logs.append(f"\x1b[1mPress to finish\x1b[22m"+ " "*(get_terminal_size().columns-15))
    show_progress(1, name, in_image, out_image, float(f"{round(len(time_diff) / sum(time_diff)*100)/100:.2f}"))
    input("")


# Old
def record_color(bild):
    palette = []
    hoehe = len(bild)
    breite = len(bild[0])
    for y in range(hoehe):
        for x in range(breite):
            if bild[y][x] not in palette:
                palette.append(bild[y][x])
        print(y+1,"y/",len(bild),"record")
    return palette

colors_done = {}
def nearest_color(pixel, palette):
    r, g, b = pixel
    if f"{r}-{g}-{b}" in colors_done:
        return colors_done[f"{r}-{g}-{b}"]
    else:
        color = min(
            palette,
            key=lambda c: (
                (r - c[0]) ** 2 +
                (g - c[1]) ** 2 +
                (b - c[2]) ** 2
            )
        )
        colors_done[f"{r}-{g}-{b}"] = color
        return color

# FAST NUMPY IMPLEMENTATIONS

def box_blur2(frame: np.ndarray, box_size: int) -> np.ndarray:
    frame_f = frame.astype(np.float32)
    blurred = uniform_filter(frame_f, size=(box_size, box_size, 1), mode='nearest')
    return np.clip(blurred, 0, 255).astype(np.uint8)


def color_split(frame: np.ndarray, t: int) -> np.ndarray:
    mean = frame.astype(np.float32).mean(axis=2)       # (H, W)
    mask = mean <= t                                    # True → black
    out = np.where(mask[:, :, np.newaxis], 0, 255).astype(np.uint8)
    return out


def convert_to_nearest(frame: np.ndarray, palette: list) -> np.ndarray:
    pal = np.array(palette, dtype=np.int32)            # (P, 3)
    flat = frame.reshape(-1, 3).astype(np.int32)       # (H*W, 3)
    # Squared distances: (H*W, P)
    diff = flat[:, np.newaxis, :] - pal[np.newaxis, :, :]
    dists = (diff ** 2).sum(axis=2)
    indices = dists.argmin(axis=1)                     # (H*W,)
    return pal[indices].reshape(frame.shape).astype(np.uint8)


def fuzz(frame: np.ndarray, yellow: int, ran: int) -> np.ndarray:
    h, w, _ = frame.shape
    noise = np.random.randint(-ran, ran + 1, size=(h, w, 3), dtype=np.int32)
    bias = np.array([yellow, yellow, -yellow], dtype=np.int32)
    out = frame.astype(np.int32) + bias + noise
    return np.clip(out, 0, 255).astype(np.uint8)


def clamp(wert):
    if wert <= 0:
        wert = 0
    elif wert >= 255:
        wert = 255
    return wert


def Fuzzy(frame: np.ndarray, box_size: int, t: int, yellow: int, ran: int) -> np.ndarray:
    return fuzz(color_split(box_blur2(frame, box_size), t), yellow, ran)


def Fuzzy_v2(frame: np.ndarray, box_size: int, step: int, yellow: int, ran: int) -> np.ndarray:
    return fuzz(convert_to_nearest(box_blur2(frame, box_size), gray_values(step)), yellow, ran)


# ─────────────────────────────────────────────────────────────────────────────

# You can freely eddit all of the stuff below

# ─────────────────────────────────────────────────────────────────────────────

palette = [
    (255, 0, 0),    # Red
    (0, 255, 0),    # Green
    (0, 0, 255),    # Blue
    (255, 255, 255),# White
    (0, 0, 0)       # Black
]

def rgb_values(step):
    rgb_values = [
        (r, g, b)
        for r in range(0, 257, step)
        for g in range(0, 257, step)
        for b in range(0, 257, step)
        ]
    return rgb_values

def (step):
    rgb_values = [
        (v, v, v)
        for v in range(0, 257, step)
    ]
    return rgb_values

#run_through_filter(Path("in", "badapple.mov"), 0, 6572, Path("why_do_you_hurt_me_in_this_way.mp4"), "Fuzzy", Fuzzy, [16,127,100,0])
run_through_filter(Path("badapple.mov"), 0, 6572, Path("why_do_you_hurt_me_in_this_way3.mov"), "Fuzzy", Fuzzy_v2, [16,64,0,0])
# run_through_filter(Path("in", "clip.mov"), 0, 10**9, Path("out", "clip_fuzzy.mov"), "Fuzzy", Fuzzy_v2, [16,64,0,50])
# showVideoProgress(0.5, "Static", Path("in","bad_apple.mp4"), Path("out","static_apple.mp4"))
# static_effect(Path("in", "bad_apple.mp4"), Path("test_video_3.mp4"))

# Explenation

#filter(input_path, from_frame, to_frame, output_path, filter_type, filter_version, parameters[x,y,z,a])

# input_path = path from .py file ("subfolder",) "file_name (.mp4 / .mov)"
# output_path = path from .py file ("subfolder",) "file_name (.mp4 / .mov)"

# from_frame = start frame (from 0)
# to_frame = end frame

# filter_type = don't change this (unused)
# filter_version = Fuzzy / Fuzzy_v2; Fuzzy = black and white, Fuzzy_v2 glayscale palette

# x = box blur scan size
# y = step size in the palette (if you set this too low; it will take ages to finish and the effect will desappear (I have no clue why))
# z = adds a yellow tint to the video (I have no clue why I even added this)
# a = adds a random number between -a and +a to very color channel for every pixel (rerolls the number every color channel and pixel), this adds like a fizze effect to the video



#  _____  __                                                    ______  _____  _____            _     _ _         _   _             _   _     _               
# |_   _|/ _|                                                  |  ____|/ ____|/ ____|          | |   (_) |       | | | |           | | | |   (_)              
#   | | | |_    _   _  ___  _   _    _ __  _ __ ___  ___ ___   | |__  | (___ | |      __      _| |__  _| | ___   | |_| |__   ___   | |_| |__  _ _ __   __ _             ___  
#   | | |  _|  | | | |/ _ \| | | |  | '_ \| '__/ _ \/ __/ __|  |  __|  \___ \| |      \ \ /\ / / '_ \| | |/ _ \  | __| '_ \ / _ \  | __| '_ \| | '_ \ / _` |         .'/   \  
#  _| |_| |    | |_| | (_) | |_| |  | |_) | | |  __/\__ \__ \  | |____ ____) | |____   \ V  V /| | | | | |  __/  | |_| | | |  __/  | |_| | | | | | | | (_| |        / /     \ 
# |_____|_|     \__, |\___/ \__,_|  | .__/|_|  \___||___/___/  |______|_____/ \_____|   \_/\_/ |_| |_|_|_|\___|   \__|_| |_|\___|   \__|_| |_|_|_| |_|\__, |        | |     | 
#                __/ |              | |                                                                                                                __/ |        | |     |                                                                                       
#               |___/               |_|                                                                                                               |___/         |/`.   .'                                                                                     
#                                                                                                                                                                    `.|   |  
#   _                               _                _ _               _ _ _     _____ _______ ____  _____             _   _ _____      _____    __      ________     ||___| 
#  (_)                             (_)              (_) |             (_) | |   / ____|__   __/ __ \|  __ \      /\   | \ | |  __ \    / ____|  /\ \    / /  ____|    |/___/ 
#   _ ___    _ __ _   _ _ __  _ __  _ _ __   __ _    _| |_   __      ___| | |  | (___    | | | |  | | |__) |    /  \  |  \| | |  | |  | (___   /  \ \  / /| |__       .'.--. 
#  | / __|  | '__| | | | '_ \| '_ \| | '_ \ / _` |  | | __|  \ \ /\ / / | | |   \___ \   | | | |  | |  ___/    / /\ \ | . ` | |  | |   \___ \ / /\ \ \/ / |  __|     | |    | 
#  | \__ \  | |  | |_| | | | | | | | | | | | (_| |  | | |_    \ V  V /| | | |   ____) |  | | | |__| | |       / ____ \| |\  | |__| |   ____) / ____ \  /  | |____    \_\    / 
#  |_|___/  |_|   \__,_|_| |_|_| |_|_|_| |_|\__, |  |_|\__|    \_/\_/ |_|_|_|  |_____/   |_|  \____/|_|      /_/    \_\_| \_|_____/   |_____/_/    \_\/   |______|    `''--' 
#                                            __/ |  
#                                           |___/   

# Fair warning some of the things here are ether disfunctional or unused

# Also the UI was made by a friend of mine NOT me
