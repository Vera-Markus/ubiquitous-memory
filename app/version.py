"""
The app's version, shown in the window title, Help ▸ About and at the top of each session log.

From RC6 on the release candidate is the minor number: 1.6.0 is RC6, tagged v1.6.0-rc6
(the hyphen marks a pre-release on GitHub; the release workflow checks the part before it).
"""
__version__ = "1.7.4"

try:
    # Written by tools/build_release.py for the build only (git-ignored): when this program was built.
    from app.build_info import BUILD_DATE
except ImportError:
    BUILD_DATE = None       # running from source
