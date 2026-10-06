"""
After a PyInstaller build, check that every module under app/ made it into the
bundle (PyInstaller only follows static imports).

    python -m PyInstaller EveFleetManagementTool.spec --noconfirm
    python tools/check_bundle.py
    python tools/check_bundle.py <workpath>     # a build made with --workpath <workpath>

Any module missing is a failure (add it to the spec's hiddenimports, or import it).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKPATH = ROOT / "build"


def main() -> int:
    workpath = Path(sys.argv[1]) if len(sys.argv) > 1 else WORKPATH
    toc = workpath / "EveFleetManagementTool" / "PYZ-00.toc"
    if not toc.exists():
        print(f"No build found ({toc}): run PyInstaller first.")
        return 1
    bundled = set(re.findall(r"\('(app(?:\.\w+)*)'", toc.read_text(encoding="utf-8", errors="replace")))
    expected = set()
    for path in (ROOT / "app").rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        expected.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    missing = sorted(expected - bundled)
    if missing:
        print("FAIL: not bundled:\n" + "\n".join(f"  {m}" for m in missing))
        return 1
    print(f"OK: all {len(expected)} app modules are bundled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
