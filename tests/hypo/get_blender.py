"""Download and unpack Blender for the current OS; print the executable path. Stdlib only (CI + local).

usage: get_blender.py <X.Y | X.Y.Z> <dest>   (X.Y picks the newest patch release of that series)
Old releases name their files differently (macOS.dmg, linux-glibc217-x86_64, windows64): the directory listing is
matched, nothing is guessed.
"""
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

version = sys.argv[1]
dest = Path(sys.argv[2]).resolve()
dest.mkdir(parents=True, exist_ok=True)
series = ".".join(version.split(".")[:2])
# download.blender.org answers 403 to urllib's default User-Agent and to busy runners: send a browser one,
# and fall back to mirrors that carry the same directory layout
BASES = [
    "https://download.blender.org/release/",
    "https://mirror.clarkson.edu/blender/release/",
    "https://ftp.nluug.nl/pub/graphics/blender/release/",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def fetch(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def retry(fn, tries=5):
    """download.blender.org times out or refuses (403) now and then on CI runners"""
    for attempt in range(tries):
        try:
            return fn()
        except OSError:  # URLError, HTTPError, timeouts
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def first_base(fn):
    """try each base in order (with retries); return (base, result) of the first that works"""
    last = None
    for base in BASES:
        try:
            return base, retry(lambda: fn(base), tries=3)
        except OSError as e:
            last = e
            print(f"{base}: {e}; trying next mirror", file=sys.stderr)
    raise last


base, listing = first_base(lambda b: fetch(f"{b}Blender{series}/").read().decode())
index = f"{base}Blender{series}/"
system = platform.system()
machine = platform.machine().lower()

PATTERNS = {
    "Linux": r"linux(?:-glibc\d+)?[-_]?(?:x64|x86_64|64)\.tar\.(?:xz|bz2)",
    "Windows": r"windows(?:-x64|64)\.zip",
    "Darwin": r"macos(?:-arm64|-x64)?\.dmg",
}
if system not in PATTERNS:
    raise SystemExit(f"unsupported OS {system}")
files = re.findall(r'href="(blender-(\d+\.\d+(?:\.\d+)?)-([^"]+))"', listing)
wanted = [(name, ver, rest) for name, ver, rest in files
          if re.fullmatch(PATTERNS[system], rest, re.IGNORECASE) and (version.count(".") == 1 or ver == version)]
if not wanted:
    raise SystemExit(f"no {system} build of Blender {version} in {index}")


def vkey(ver):
    return tuple(int(x) for x in ver.split("."))


newest = max(vkey(v) for _, v, _ in wanted)
wanted = [w for w in wanted if vkey(w[1]) == newest]
if system == "Darwin":
    # prefer a native arm64 build; Intel builds run under Rosetta
    native = [w for w in wanted if ("arm64" in w[2]) == (machine == "arm64")]
    wanted = native or wanted
name = wanted[0][0]
archive = dest / name
if not archive.exists():
    def download(b):
        with fetch(f"{b}Blender{series}/{name}", timeout=120) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f)

    # the listing's base first, then the others
    BASES.sort(key=lambda b: b != base)
    try:
        first_base(download)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise

if system == "Linux":
    with tarfile.open(archive) as t:
        t.extractall(dest)
    exe = next(dest.glob("blender-*/blender"))
elif system == "Windows":
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    exe = next(dest.glob("blender-*/blender.exe"))
else:
    mnt = dest / "mnt"
    app = dest / "Blender.app"
    if not app.exists():
        subprocess.run(["hdiutil", "attach", str(archive), "-mountpoint", str(mnt), "-nobrowse", "-quiet"], check=True)
        subprocess.run(["cp", "-R", str(next(mnt.glob("Blender*.app"))), str(app)], check=True)
        subprocess.run(["hdiutil", "detach", str(mnt), "-quiet"], check=True)
    exe = app / "Contents" / "MacOS" / "Blender"
print(exe)
