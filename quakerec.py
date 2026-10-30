import asyncio, atexit, glob, json, os, platform, subprocess, sys, time
from datetime import datetime
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "config.json")))

IS_WIN = platform.system() == "Windows"
BUF_DIR = os.path.join(HERE, CFG["buffer_dir"])
OUT_DIR = os.path.join(HERE, CFG["output_dir"])
SEG, PRE, POST = CFG["segment_seconds"], CFG["pre_roll_seconds"], CFG["post_roll_seconds"]
QUIET, MAXREC, STRONG = CFG["quiet_timeout_seconds"], CFG["max_record_seconds"], CFG["strong_scale"]
os.makedirs(BUF_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

state = {"rec": False, "start": 0, "last": 0, "info": {}, "busy": 0}
loaded_modules = []

# ---------- Linux: isolate JQuake audio on its own sink ----------
def pactl(*args):
    return subprocess.check_output(["pactl", *args], text=True).strip()

def setup_jquake_sink():
    if any(s["name"] == "jquake" for s in json.loads(pactl("-f", "json", "list", "sinks"))):
        return
    loaded_modules.append(pactl("load-module", "module-null-sink",
                                "sink_name=jquake", "sink_properties=device.description=JQuake"))
    # lets you still hear alerts on your speakers
    loaded_modules.append(pactl("load-module", "module-loopback",
                                "source=jquake.monitor", "latency_msec=30"))
    atexit.register(lambda: [subprocess.run(["pactl", "unload-module", m]) for m in loaded_modules])

async def route_jquake_audio():
    match = CFG["audio_match"].lower()
    while True:
        try:
            sinks = json.loads(pactl("-f", "json", "list", "sinks"))
            idx = next(s["index"] for s in sinks if s["name"] == "jquake")
            for i in json.loads(pactl("-f", "json", "list", "sink-inputs")):
                blob = " ".join(str(v) for v in i.get("properties", {}).values()).lower()
                if match in blob and i.get("sink") != idx:
                    subprocess.run(["pactl", "move-sink-input", str(i["index"]), "jquake"])
        except Exception:
            pass
        await asyncio.sleep(0.5)

# ---------- ffmpeg rolling buffer ----------
def ffmpeg_cmd():
    out = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(CFG["crf"]), "-pix_fmt", "yuv420p",
           "-force_key_frames", f"expr:gte(t,n_forced*{SEG})",
           "-c:a", "aac", "-b:a", "128k",
           "-f", "segment", "-segment_time", str(SEG), "-segment_format", "mpegts",
           "-reset_timestamps", "1", "-strftime", "1", f"{BUF_DIR}/%Y%m%d_%H%M%S.ts"]
    fps = str(CFG["framerate"])
    if IS_WIN:
        target = "desktop" if CFG["capture_full_screen"] else f"title={CFG['window_title']}"
        src = ["-f", "gdigrab", "-framerate", fps, "-i", target,
               "-f", "dshow", "-i", f"audio={CFG['windows_audio_device']}"]
    else:
        if CFG["capture_full_screen"]:
            src = ["-f", "x11grab", "-framerate", fps, "-i", ":0.0"]
        else:
            geo = subprocess.check_output(
                f'xdotool search --name "{CFG["window_title"]}" | head -1 | xargs xdotool getwindowgeometry --shell',
                shell=True, text=True)
            g = dict(l.split("=") for l in geo.split())
            src = ["-f", "x11grab", "-framerate", fps, "-video_size", f"{g['WIDTH']}x{g['HEIGHT']}",
                   "-i", f":0.0+{g['X']},{g['Y']}"]
        src += ["-f", "pulse", "-i", CFG["linux_audio_source"]]
    return ["ffmpeg", "-loglevel", "warning", *src, *out]

async def run_ffmpeg():
    while True:
        try:
            p = await asyncio.create_subprocess_exec(*ffmpeg_cmd())
            await p.wait()
        except Exception as e:
            print("ffmpeg launch failed:", e, flush=True)
        await asyncio.sleep(3)

def seg_time(path):
    return datetime.strptime(os.path.basename(path)[:15], "%Y%m%d_%H%M%S").timestamp()

async def cleaner():
    while True:
        await asyncio.sleep(SEG)
        if state["rec"] or state["busy"]:
            continue
        for f in glob.glob(f"{BUF_DIR}/*.ts"):
            if time.time() - seg_time(f) > PRE + 3 * SEG:
                try:
                    os.remove(f)
                except OSError:
                    pass

# ---------- recording state ----------
def start_recording(reason, info):
    now = time.time()
    state.update(rec=True, start=now, last=now, info={"trigger": reason, **info})
    print(datetime.now().isoformat(timespec="seconds"), "REC start:", reason, flush=True)

def stop_recording(reason):
    if not state["rec"]:
        return
    state["rec"] = False
    state["busy"] += 1
    asyncio.create_task(finalize(state["start"], time.time(), reason, dict(state["info"])))

async def finalize(start, stop, reason, info):
    try:
        await asyncio.sleep(POST + SEG + 1)
        segs = sorted(f for f in glob.glob(f"{BUF_DIR}/*.ts")
                      if start - PRE - SEG <= seg_time(f) <= stop + POST)
        if not segs:
            print("No segments found, nothing saved", flush=True)
            return
        name = datetime.fromtimestamp(start).strftime("%Y%m%d_%H%M%S")
        lst = f"{OUT_DIR}/{name}.txt"
        with open(lst, "w") as fh:
            fh.writelines(f"file '{os.path.abspath(s)}'\n" for s in segs)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", f"{OUT_DIR}/{name}.mp4"])
        os.remove(lst)
        with open(f"{OUT_DIR}/{name}.json", "w") as fh:
            json.dump({**info, "stop_reason": reason, "start": start, "stop": stop}, fh, indent=2)
        print("Saved", name, flush=True)
    finally:
        state["busy"] -= 1

# ---------- P2PQuake feed ----------
async def on_event(msg):
    code, now = msg.get("code"), time.time()
    if code in (554, 556):                                   # EEW detection / warning
        if msg.get("cancelled"):
            stop_recording("eew_cancelled")
        else:
            if not state["rec"]:
                start_recording("eew", {"eew": msg.get("earthquake", {})})
            state["last"] = now
    elif code == 551 and (msg.get("earthquake", {}).get("maxScale") or 0) >= STRONG:
        if not state["rec"]:
            start_recording("strong_shaking", {"quake": msg.get("earthquake", {})})
        state["last"] = now
    elif code == 552 and not msg.get("cancelled") and state["rec"]:   # tsunami forecast
        stop_recording("tsunami_warning")

async def watchdog():
    while True:
        await asyncio.sleep(1)
        if state["rec"]:
            now = time.time()
            if now - state["last"] > QUIET:
                stop_recording("quiet_timeout")
            elif now - state["start"] > MAXREC:
                stop_recording("max_duration")

async def feed():
    async for ws in websockets.connect(CFG["feed_url"]):
        try:
            async for raw in ws:
                await on_event(json.loads(raw))
        except websockets.ConnectionClosed:
            continue

async def main():
    tasks = [run_ffmpeg(), cleaner(), watchdog(), feed()]
    if not IS_WIN and CFG.get("isolate_app_audio", True):
        setup_jquake_sink()
        tasks.append(route_jquake_audio())
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())