"""Cross-platform (macOS, Linux, Windows) multi-client harness: one broadcaster server + N headless Blenders."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("worker.py")


def blender_exe() -> str:
    exe = os.environ.get("MIXER_BLENDER_EXE_PATH") or shutil.which("blender")
    if exe is None and sys.platform == "darwin":
        exe = "/Applications/Blender.app/Contents/MacOS/Blender"
    if exe is None or not Path(exe).exists():
        raise RuntimeError("Set MIXER_BLENDER_EXE_PATH to the Blender executable")
    return exe


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"nothing listening on {port}")


class Server:
    def __init__(self):
        self.port = free_port()
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mixer.broadcaster.apps.server", "--port", str(self.port), "--log-level", "WARNING"],
            env=env,
            cwd=ROOT,
        )
        wait_port(self.port, 10)

    def close(self):
        self.proc.kill()
        self.proc.wait()


class Client:
    """One headless Blender running tests/hypo/worker.py with the Mixer addon enabled."""

    def __init__(self, server: Server, name: str):
        self.name = name
        self.server = server
        self.port = free_port()
        self.scripts = tempfile.mkdtemp(prefix="mixer_scripts_")
        addons = Path(self.scripts) / "addons"
        addons.mkdir()
        try:
            (addons / "mixer").symlink_to(ROOT / "mixer", target_is_directory=True)
        except OSError:  # Windows without symlink privilege
            shutil.copytree(ROOT / "mixer", addons / "mixer")
        env = dict(
            os.environ, BLENDER_USER_SCRIPTS=self.scripts, MIXER_NO_START_SERVER="1", MIXER_USER_PORT=str(server.port)
        )
        if os.environ.get("MIXER_BACKEND") == "bpy":
            # Blender as a Python module (pip "bpy"), no Blender executable
            cmd = [sys.executable, str(WORKER), f"--port={self.port}"]
        else:
            cmd = [
                blender_exe(),
                "-b",
                "--factory-startup",
                "--python-exit-code",
                "255",
                "--python",
                str(WORKER),
                "--",
                f"--port={self.port}",
            ]
        self.proc = subprocess.Popen(cmd, env=env)
        self.sock = self._connect(60)
        self.f = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        self.call("enable")
        self.call("empty_scene")
        self.call("set_server", host="127.0.0.1", port=server.port)
        self.call("connect")

    def _connect(self, timeout: float):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name}: Blender exited with {self.proc.returncode}")
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), 0.5)
                sock.settimeout(180)  # the connect timeout would otherwise stay on the socket
                return sock
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"{self.name}: worker did not start")

    def call(self, op, **kw):
        self.f.write(json.dumps(dict(op=op, **kw)) + "\n")
        self.f.flush()
        res = json.loads(self.f.readline())
        if not res["ok"]:
            raise RuntimeError(f"{self.name}: {res['error']}")
        return res["result"]

    def close(self):
        try:
            self.call("quit")
        except Exception:
            pass
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.scripts, ignore_errors=True)


def wait_until(predicate, clients, timeout: float = 40.0, step: float = 0.5):
    """Pump every client until predicate() holds (condition based wait: CI machines differ in speed)."""
    end = time.monotonic() + timeout
    while True:
        for c in clients:
            c.call("pump", seconds=step)
        if predicate():
            return True
        if time.monotonic() > end:
            return False
