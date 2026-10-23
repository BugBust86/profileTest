#!/usr/bin/env python3
"""Execute one GUI job in Terminal and mirror that same process's output to disk."""
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def run(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    lock = Path(job["lockPath"])
    try:
        lock.mkdir()
    except FileExistsError:
        print("这条测试任务已经启动，请勿重复执行。", flush=True)
        return 1

    status_path = Path(job["statusPath"])

    def status(**values):
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        temporary.replace(status_path)

    process = None
    pending_signal = None

    def interrupt(signum, _frame):
        nonlocal pending_signal
        pending_signal = signum
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, lambda _signum, frame: interrupt(signal.SIGINT, frame))
    with Path(job["logPath"]).open("wb", buffering=0) as log:
        def emit(data):
            log.write(data)
            try:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            except OSError:
                pass

        emit(("$ " + job["command"] + "\n\n").encode("utf-8"))
        try:
            cancelled = status_path.with_name("cancel.requested")
            if cancelled.exists():
                emit("[任务在启动前已取消]\n".encode("utf-8"))
                status(state="finished", exitCode=130)
                return 130
            environment = dict(os.environ, **job["environment"])
            process = subprocess.Popen(
                ["/bin/zsh", "-c", job["command"]],
                cwd=job["workingDirectory"], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            status(state="running", pid=process.pid, startedAt=dt.datetime.now().astimezone().isoformat())
            if cancelled.exists():
                pending_signal = signal.SIGINT
            if pending_signal is not None:
                interrupt(pending_signal, None)
            while True:
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    break
                emit(chunk)
            code = process.wait()
            code = code if code >= 0 else 128 - code
            emit(("\n[执行结束 · 退出码 %d]\n" % code).encode("utf-8"))
            status(state="finished", pid=process.pid, exitCode=code,
                   finishedAt=dt.datetime.now().astimezone().isoformat())
            return code
        except Exception as exc:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            message = str(exc)
            emit(("\n[启动失败] " + message + "\n").encode("utf-8"))
            status(state="finished", exitCode=1, message=message)
            return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
