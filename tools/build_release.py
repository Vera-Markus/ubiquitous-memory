"""
Build a release (step 8.3): one command from source to the files people download.

    python tools/build_release.py
    python tools/build_release.py <output folder>     # default: dist/release-<version>
    python tools/build_release.py ... --require-installer   # fail without Inno Setup (CI)

Steps:
  1. Clean PyInstaller build of EveFleetManagementTool.spec. The work and dist
     folders live in %TEMP%, because OneDrive often refuses to delete build/ and dist/.
  2. tools/check_bundle.py on that build.
  3. Refuse to continue if the build has a data/ folder (logins and the database
     must never ship; the app downloads the database on first run).
  4. Portable zip: EveFleetManagementTool-<version>-portable.zip.
  5. Installer: EveFleetManagementTool-<version>-setup.exe, if Inno Setup's ISCC.exe
     is found (on PATH, in the usual install folders, or in the ISCC environment
     variable). Skipped with a warning otherwise.
  6. SHA256SUMS.txt for every file produced.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "EveFleetManagementTool.spec"
ISS = ROOT / "installer" / "EveFleetManagementTool.iss"
APP_NAME = "EveFleetManagementTool"


def read_version() -> str:
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        sys.exit("FAIL: no __version__ in app/version.py")
    return match.group(1)


def find_iscc() -> Path | None:
    candidates = [os.environ.get("ISCC"), shutil.which("ISCC")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
            candidates.append(str(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def run(cmd: list[str]) -> None:
    print(f"\n> {' '.join(cmd)}", flush=True)
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if code != 0:
        sys.exit(f"FAIL (exit {code}): {' '.join(cmd)}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    version = read_version()
    args = [a for a in sys.argv[1:] if a != "--require-installer"]
    require_installer = len(args) < len(sys.argv) - 1
    out_dir = Path(args[0]).resolve() if args else ROOT / "dist" / f"release-{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.iterdir():
        if old.is_file():
            old.unlink()
    print(f"Release {version} -> {out_dir}")

    scratch = Path(tempfile.gettempdir()) / f"{APP_NAME}-release"
    shutil.rmtree(scratch, ignore_errors=True)
    workpath, distpath = scratch / "build", scratch / "dist"

    # 1-2. Build and check the bundle.
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--workpath", str(workpath), "--distpath", str(distpath), str(SPEC)])
    run([sys.executable, str(ROOT / "tools" / "check_bundle.py"), str(workpath)])
    app_dir = distpath / APP_NAME
    if not (app_dir / f"{APP_NAME}.exe").is_file():
        sys.exit(f"FAIL: no {APP_NAME}.exe in {app_dir}")

    # 3. Nothing personal in the build.
    if (app_dir / "data").exists():
        sys.exit(f"FAIL: {app_dir / 'data'} exists; a release must not include data/")

    # 4. Portable zip, with the app folder at the top.
    zip_path = out_dir / f"{APP_NAME}-{version}-portable.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(app_dir))
    print(f"\nPortable zip: {zip_path.name}")

    # 5. Installer.
    iscc = find_iscc()
    if iscc:
        run([str(iscc), "/Q", f"/DAppVersion={version}", f"/DSourceDir={app_dir}", f"/O{out_dir}", str(ISS)])
        print(f"Installer: {APP_NAME}-{version}-setup.exe")
    elif require_installer:
        sys.exit("FAIL: Inno Setup (ISCC.exe) not found and --require-installer was given")
    else:
        print("WARNING: Inno Setup (ISCC.exe) not found; no installer built. "
              "Install Inno Setup 6 or set the ISCC environment variable.")

    # 6. Checksums (sha256sum format; Get-FileHash shows the same hex in upper case).
    files = sorted(p for p in out_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    lines = [f"{sha256(p)}  {p.name}" for p in files]
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nSHA256SUMS.txt:\n" + "\n".join(f"  {line}" for line in lines))
    print(f"\nOK: release {version} built in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
