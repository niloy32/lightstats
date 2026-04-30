# Changelog

All notable changes to LightStats are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Pings now run in parallel within each round, so a slow or dead server no
  longer delays the per-tick UI update.
- `init_db` only runs `VACUUM` when a startup prune actually frees a
  meaningful number of rows (≥10 000), shaving startup time on warm DBs.
- Active-network-interface lookup is cached for 5 s — psutil's per-NIC
  walk is expensive on machines with many virtual adapters.

### Fixed
- Layout teardown in `OverlayWindow._clear_layout` now orphans removed
  widgets so they're actually destroyed, not just hidden — toggling
  widgets repeatedly in Settings no longer accumulates dangling children.

## [1.0.0] - 2026-04-24

Initial public release.

### Added
- Frameless always-on-top overlay with configurable widgets:
  Ping (per-server RTT, jitter, packet loss), Network throughput,
  Active adapter, CPU %, Memory %, GPU (NVIDIA + Windows PDH for AMD/Intel),
  Disk I/O, system uptime.
- SQLite history (`history.db`) with 7-day retention and a built-in
  `pyqtgraph` chart window (5 min through All ranges, per-server toggles).
- System tray icon with status dot, single-instance named-mutex guard,
  and "Start with Windows" registry toggle in the frozen build.
- Settings dialog with per-widget toggles and live font-size preview.
- Single-file Windows build via PyInstaller (`build.bat`) with embedded
  multi-resolution icon and `VERSIONINFO` so Task Manager / Alt-Tab /
  taskbar all show "LightStats".
- Portable storage — `config.json`, `history.db`, `crash.log` live next
  to the executable.

[Unreleased]: https://github.com/niloy32/lightstats/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/niloy32/lightstats/releases/tag/v1.0.0
