"""Single source of truth for the app version.

Bump this on every release and tag the commit with `v<version>` so the
GitHub Actions workflow can attach the built `.exe` to a Release.

`build.py` reads `__version__` to populate the Windows VERSIONINFO
fields embedded in the frozen executable.
"""

__version__ = "1.0.0"
