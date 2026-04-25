# Contributing to LightStats

Thanks for your interest! This is a small, opinionated tool. PRs and
issues are welcome — a few notes to keep things smooth.

## Issues

- Reproduction steps + Windows version + the relevant chunk of
  `crash.log` (if any) make bugs much easier to fix.
- For feature requests, mention which widget(s) it relates to.

## Development setup

```cmd
git clone https://github.com/niloy32/lightstats.git
cd lightstats
run-debug.bat
```

`run-debug.bat` creates a `.venv`, installs `requirements.txt`, and
launches `main.py` with a visible console so tracebacks are readable.

## Producing a release build

```cmd
build.bat
```

Outputs `dist\LightStats.exe`. The build embeds the multi-resolution
icon from `icons/` and pulls the version string from `version.py`.

## Code style

- Python 3.11+ (PyQt6 doesn't support older).
- Prefer small, well-named modules over large grab-bags. Each existing
  module has a one-job feel; please keep it that way.
- Type hints are appreciated but not strictly required.

## Cutting a release

1. Bump `__version__` in `version.py`.
2. Update `CHANGELOG.md` (move items from `[Unreleased]` into a dated
   `[x.y.z]` section).
3. Commit: `chore: release vX.Y.Z`.
4. Tag and push: `git tag vX.Y.Z && git push --tags`.

The `release` GitHub Actions workflow builds `LightStats.exe` and
attaches it to a GitHub Release automatically.
