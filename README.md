# quakerec

Auto-records JQuake (screen + app audio) when an EEW or strong shaking alert arrives, with a pre-roll buffer. Works with any JQuake version because it runs alongside it and uses its own P2PQuake feed.

## How it works
ffmpeg continuously writes 5 s segments to a rolling buffer. A trigger marks the time, and when recording ends the segments from 30 s before the trigger to 10 s after the end are joined (no re-encode) into `clips/<timestamp>.mp4`, plus a `.json` sidecar with event data.

**Starts on:** EEW (codes 554/556), or a quake report with max scale >= `strong_scale` (551).
**Stops on:** EEW cancelled, tsunami forecast (552), no EEW updates for `quiet_timeout_seconds`, or `max_record_seconds`.

## Requirements
- Python 3.9+
- ffmpeg on PATH
- Linux: `xdotool`, X11, PulseAudio or PipeWire with `pactl` (recent enough for `-f json`)
- Windows: VB-Cable (virtual audio device)

## Setup
```
git clone https://github.com/echojxmes/quakerec && cd quakerec
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
```

Put machine-specific settings in `config.local.json` (copy of `config.json`) and run with it as the argument.

### JQuake-only audio
**Linux:** automatic. quakerec creates a `jquake` sink and moves JQuake's audio stream onto it. Set `audio_match` to something unique in JQuake's stream properties (check with `pactl list sink-inputs` while an alert plays; JQuake usually shows as `java`, which also matches other Java apps).

**Windows:**
1. Install VB-Cable.
2. Settings > System > Sound > Volume mixer: set JQuake's output device to `CABLE Input`. (It only appears after it has played a sound; may be listed as `java`/`javaw`.)
3. To keep hearing alerts: Sound control panel > Recording > CABLE Output > Properties > Listen > tick "Listen to this device" and choose your speakers.
4. Find the device name with `ffmpeg -list_devices true -f dshow -i dummy` and set `windows_audio_device`.

Windows window title: `Get-Process | Where MainWindowTitle -like "*JQuake*" | Select MainWindowTitle`

## Run
```
.venv/bin/python quakerec.py [config.local.json]
```

Autostart: run `deploy/install-windows.ps1` (Windows), or copy `deploy/quakerec.service` to `~/.config/systemd/user/` and run `systemctl --user enable --now quakerec`.

## Testing
Set `strong_scale` to 10 temporarily. Any small quake report will trigger a clip, so you can check the whole chain.

## Notes
- Windows window capture breaks if the window is minimized; set `capture_full_screen` to true if needed.
- Disk use is about a minute of video in the buffer, plus clips.
- This is a hobby tool. Do not rely on it for safety.

## License
MIT
