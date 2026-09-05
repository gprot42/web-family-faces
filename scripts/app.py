#!/usr/bin/env python3
"""Start, stop, restart, and check Family Faces on any platform.

    python scripts/app.py start
    python scripts/app.py stop
    python scripts/app.py restart
    python scripts/app.py status
    python scripts/app.py logs [api|ui]

macOS users can keep using scripts/app.sh; this launcher is the same thing
in Python so Windows (scripts/app.cmd) and Linux get one too. It runs the
API with the project's virtual environment and the UI with the frontend's
own Vite, writes PID files under data/run, and logs under data/logs.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
HOST = os.environ.get("PHOTOSORT_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PHOTOSORT_PORT", "8741"))
UI_PORT = int(os.environ.get("PHOTOSORT_UI_PORT", "5174"))
DATA_DIR = Path(os.environ.get("PHOTOSORT_DATA", ROOT / "data"))
RUN_DIR = DATA_DIR / "run"
LOG_DIR = DATA_DIR / "logs"
PIDS = {"api": RUN_DIR / "api.pid", "ui": RUN_DIR / "ui.pid"}
LOGS = {"api": LOG_DIR / "api.log", "ui": LOG_DIR / "ui.log"}


def venv_python() -> Path:
    candidates = [ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"]
    for path in candidates:
        if path.exists():
            return path
    sys.exit("missing .venv: create it with `python -m venv .venv` and install backend/requirements.txt")


def vite_binary() -> list[str]:
    if WINDOWS:
        cmd = ROOT / "frontend" / "node_modules" / ".bin" / "vite.cmd"
        if cmd.exists():
            return [str(cmd)]
    else:
        binary = ROOT / "frontend" / "node_modules" / ".bin" / "vite"
        if binary.exists():
            return [str(binary)]
    sys.exit("frontend dependencies missing: run `npm install` inside frontend/")


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((HOST, port)) == 0


def api_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{API_PORT}/api/health", timeout=1.5) as res:
            return res.status == 200
    except Exception:
        return False


def read_pid(name: str) -> int | None:
    try:
        return int(PIDS[name].read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_tree(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(25):
        if not pid_alive(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def env_for_api() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT / "backend"))
    env["PHOTOSORT_DATA"] = str(DATA_DIR)
    env["PHOTOSORT_HOST"] = HOST
    env["PHOTOSORT_PORT"] = str(API_PORT)
    env["PHOTOSORT_UI_PORT"] = str(UI_PORT)
    for key, value in {
        "OMP_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "ORT_INTRA_OP_NUM_THREADS": "2",
        "ORT_INTER_OP_NUM_THREADS": "1",
    }.items():
        env.setdefault(key, value)
    return env


def spawn(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOGS[name], "ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    PIDS[name].write_text(str(proc.pid))
    return proc.pid


def cmd_start(debug: bool = False) -> None:
    if api_healthy() and port_open(UI_PORT):
        print("Family Faces is already running")
        cmd_status()
        return
    cmd_stop(quiet=True)
    for port, what in ((API_PORT, "API"), (UI_PORT, "UI")):
        if port_open(port):
            sys.exit(f"port {port} is in use by another program; free it or set PHOTOSORT_PORT / PHOTOSORT_UI_PORT")
    api_cmd = [str(venv_python()), "-m", "uvicorn", "photosort.main:app", "--host", HOST, "--port", str(API_PORT)]
    if debug:
        api_cmd += ["--reload", "--reload-dir", str(ROOT / "backend" / "photosort"), "--log-level", "debug"]
    print(f"-> starting API on {HOST}:{API_PORT}")
    spawn("api", api_cmd, ROOT, env_for_api())
    print(f"-> starting UI  on {HOST}:{UI_PORT}")
    ui_env = dict(os.environ, PHOTOSORT_HOST=HOST, PHOTOSORT_PORT=str(API_PORT), PHOTOSORT_UI_PORT=str(UI_PORT))
    spawn("ui", vite_binary() + ["--host", HOST, "--port", str(UI_PORT), "--strictPort"], ROOT / "frontend", ui_env)
    for _ in range(60):
        if api_healthy():
            break
        if not pid_alive(read_pid("api")):
            sys.exit(f"API failed to start; see {LOGS['api']}")
        time.sleep(0.25)
    else:
        sys.exit(f"API did not answer; see {LOGS['api']}")
    for _ in range(40):
        if port_open(UI_PORT):
            break
        time.sleep(0.15)
    print("Family Faces is running")
    print(f"  UI   http://{HOST}:{UI_PORT}")
    print(f"  API  http://{HOST}:{API_PORT}")
    print(f"  logs {LOGS['api']}")
    print(f"       {LOGS['ui']}")
    print(f"  stop python scripts/app.py stop")


def cmd_stop(quiet: bool = False) -> None:
    for name in ("api", "ui"):
        pid = read_pid(name)
        if pid_alive(pid):
            if not quiet:
                print(f"-> stopping {name} pid {pid}")
            kill_tree(pid)
        try:
            PIDS[name].unlink()
        except OSError:
            pass
    if not quiet:
        print("-> stopped")


def cmd_status() -> int:
    api_pid, ui_pid = read_pid("api"), read_pid("ui")
    health = "ok" if api_healthy() else "down"
    print("Family Faces")
    print(f"  host     {HOST}")
    print(f"  API      :{API_PORT}  pid {api_pid if pid_alive(api_pid) else '-'}  health {health}")
    print(f"  UI       :{UI_PORT}  pid {ui_pid if pid_alive(ui_pid) else '-'}  {'listening' if port_open(UI_PORT) else '(not running)'}")
    print(f"  UI URL   http://{HOST}:{UI_PORT}")
    return 0 if health == "ok" and port_open(UI_PORT) else 1


def cmd_logs(which: str) -> None:
    for name in (["api", "ui"] if which == "both" else [which]):
        path = LOGS[name]
        print(f"===== {path} =====")
        try:
            lines = path.read_text(errors="replace").splitlines()[-50:]
            print("\n".join(lines))
        except OSError:
            print("no log yet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["start", "stop", "restart", "status", "debug", "logs"])
    parser.add_argument("which", nargs="?", default="both", choices=["api", "ui", "both"])
    args = parser.parse_args()
    if args.command == "start":
        cmd_start()
    elif args.command == "debug":
        cmd_start(debug=True)
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "restart":
        cmd_stop()
        cmd_start()
    elif args.command == "status":
        sys.exit(cmd_status())
    elif args.command == "logs":
        cmd_logs(args.which)


if __name__ == "__main__":
    main()
