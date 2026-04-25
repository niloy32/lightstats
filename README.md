# LightStats

[![Release](https://img.shields.io/github/v/release/niloy32/lightstats?display_name=tag&sort=semver)](https://github.com/niloy32/lightstats/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/niloy32/lightstats/total)](https://github.com/niloy32/lightstats/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Windows 10+](https://img.shields.io/badge/Windows-10%2B-0078D6?logo=windows)](#install)

Lightweight PyQt6 desktop overlay that monitors your connection **and**
system. Frameless, draggable, resizable, always-on-top. Has a proper
taskbar entry and registers in Task Manager. Everything it measures is
logged to SQLite and viewable as a chart.

> **Free, open source, no telemetry.** One self-contained `.exe`,
> portable settings, ~50 MB.

<a id="install"></a>
## Install

Grab the latest **[`LightStats.exe`](https://github.com/niloy32/lightstats/releases/latest)**
from the Releases page and double-click. No installer, no Python
required — Windows 10 or later.

User data (`config.json`, `history.db`, `crash.log`) is written next to
the `.exe`, so you can drop it on a USB stick or in a portable-apps
folder and it stays self-contained.

## What it shows (toggleable)

| Widget | Detail |
|---|---|
| **Ping** | Per-server RTT, jitter (rolling 30 samples), packet loss % |
| **Network** | ↓ download + ↑ upload throughput, auto-formatted units |
| **Adapter** | Active interface name + nominal link speed |
| **CPU** | Overall % |
| **Memory** | Used / total GB + % |
| **GPU** | Utilization % — NVIDIA adds VRAM + temp; AMD / Intel via Windows PDH |
| **Disk I/O** | Read / write throughput |
| **Uptime** | Time since last boot |

Pick which ones appear from the **⚙ Settings** dialog (gear icon on the
overlay, or right-click tray → *Settings…*). Preferences + font size are
saved to `config.json`.

## App icon

Drop your icon into `icon/` as one of:

```
icon/app.ico   ← preferred on Windows (multi-resolution)
icon/app.png
icon/icon.ico
icon/icon.png
```

Until you do, LightStats uses a procedurally generated dot icon so the
app still launches and shows up in the taskbar. The icon is applied to
the overlay, chart window, settings dialog, Task Manager entry, and
Alt-Tab.

## Run (Windows)

**`run.bat`** is the one launcher you need. It prefers the frozen build
when it exists, falling back to a dev-mode source run otherwise:

1. If `dist\LightStats.exe` exists, it launches that directly — proper
   "LightStats" branding in Task Manager / taskbar / Alt-Tab, no Python
   required on the machine.
2. Otherwise it sets up `.venv`, installs deps, and runs `main.py` via
   `pythonw`. That's the first-boot path; build the .exe once (below)
   and subsequent launches take path 1.

If nothing visible happens, double-click **`run-debug.bat`** — source
mode with a visible console so Python tracebacks are immediately
readable. Also check `crash.log` next to `main.py` (or next to
`LightStats.exe` for the frozen build).

Dev-mode caveat: when falling back to source, Task Manager shows the
process as "Python" rather than "LightStats" because Store-installed
Python re-execs itself via the AppExecutionAlias, bypassing any branding
we apply to the venv's shim. Cosmetic only; the frozen build fixes it.

### Building the frozen `.exe`

Double-click **`build.bat`** to produce `dist\LightStats.exe` (~50 MB)
— a self-contained Windows binary that runs on any Windows 10+ machine
with no Python install required. Task Manager, taskbar, Alt-Tab, and
the System Tray all show "LightStats" with the icon. Copy the .exe
anywhere; user data (`config.json`, `history.db`, `crash.log`) lands
next to it, portable-app style.

Flags: `build.bat --clean` wipes caches first; `--no-upx` disables UPX.

Only one instance runs at a time — double-clicking while it's up just
brings the existing window to the front.

## Controls

- **Left-click tray icon** — toggle overlay
- **Right-click tray icon** — menu (show/hide overlay, show chart, settings, quit)
- **Drag overlay** — left-click + drag anywhere
- **Resize overlay** — drag the bottom-right grip
- **Taskbar button** — click to minimize / restore like any other app
- **⚙** — open settings
- **📈** — open history chart
- **✕** — close LightStats (quits the app; the ✕ button background turns red on hover)

## Settings dialog

- Checkbox per widget (Ping / Network / Adapter / CPU / Memory / GPU / Disk I/O / Uptime)
- **Font size** slider + spinbox (8–22 pt, live preview while dragging).
  Cancel restores the previous size.
- **Start with Windows** — only visible in the frozen build; adds a
  per-user `HKCU\...\Run` entry so LightStats launches at sign-in.

## Chart window

- **Metric dropdown**: Ping · Network ↓/↑ · CPU % · Memory % · GPU % · VRAM % · Disk read/write
- **Range buttons**: 5 min · 15 min · 1 hr · 6 hr · 24 hr · All
- In ping mode: per-server toggles; packet loss renders as gaps in the line
- Y-axis formatter adapts per metric (ms / % / bytes-per-second)
- Auto-refresh every 3 s, with a manual Refresh button

## How GPU support works

- **NVIDIA**: `nvidia-ml-py` gives util %, VRAM used/total, temp
- **AMD / Intel** (Windows): a small `ctypes` wrapper around the Windows
  Performance Data Helper (PDH) reads `\GPU Engine(*)\Utilization Percentage`
  — the same counter Task Manager uses. Works on any GPU on Win10+. No
  VRAM info (PDH doesn't expose it consistently).
- Falls back silently if neither path succeeds.

## Data storage

Pings and metrics land in `history.db` (SQLite, WAL mode), next to
whichever launcher you used — `main.py` in dev, `LightStats.exe` in the
frozen build:

- `pings(ts, server, host, rtt_ms)` — one row per server per second
- `metrics(ts, name, value)` — one row per metric per second

Retention: **7 days**. Older rows pruned + `VACUUM`ed on startup. To nuke
history, close the app and delete `history.db`.

`config.json` and `crash.log` sit alongside `history.db` — everything is
portable, no `%APPDATA%` or registry storage (aside from the autostart
entry, which is opt-in).

## File layout

```
main.py              entry point, wires workers + UI
errors.py            crash logging + global exception hooks
paths.py             dev/frozen-aware path helpers
single_instance.py   named-mutex second-instance guard
autostart.py         HKCU\...\Run toggle for the frozen build
config.py            JSON preferences (widgets + font size)
config.json          generated on first save
db.py                SQLite schema + helpers
ping_worker.py       ping subprocess + rolling stats + DB writes
system_worker.py     psutil poller + DB writes
gpu.py               NVML + Windows PDH GPU utilization
overlay.py           frameless resizable taskbar window
tray.py              system tray icon with status dot
settings_dialog.py   pick which widgets show + font size
chart_window.py      pyqtgraph history viewer with metric selector
ico_builder.py       PNG set → single .ico (used by build.py)
build.py             PyInstaller driver: produces dist\LightStats.exe
build.bat            one-click frozen build
icon_loader.py       loads icon/app.{ico,png} or generates a fallback
icon/                drop your app icon here
history.db           generated on first run
crash.log            generated if anything goes wrong
requirements.txt     PyQt6, pyqtgraph, numpy, psutil, nvidia-ml-py
run.bat              silent launcher (uses pythonw)
run-debug.bat        visible-console launcher (uses python)
```

## Windows integration details

- App registers under `com.lightstats.desktop` as its `AppUserModelID`, so
  Task Manager shows it as "LightStats" (with your icon) rather than
  grouping it under Python.
- Window is frameless but **has a taskbar entry** (not `Qt.Tool`) — you can
  minimize via taskbar, Alt-Tab to it, etc.
- Always-on-top is kept on; minimize doesn't break the stay-on-top flag.

## Tuning

All in `main.py`:
- `DEFAULT_SERVERS` — add/remove ping targets
- `interval_ms` on `PingWorker` / `SystemWorker` — default 1000
- `db.init_db(retention_days=7)` — change history retention
- `APP_USER_MODEL_ID` — if you fork/rebrand, change to a new ID so Windows
  doesn't confuse your build with an older install

## Contributing

Bug reports and PRs are welcome. See **[CONTRIBUTING.md](CONTRIBUTING.md)**
for the dev-setup walkthrough and the release procedure.

## License

[MIT](LICENSE) © 2026 Tanzimul Haque
