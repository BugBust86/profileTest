"""Integration checks for the actual Terminal runner; no phone or Terminal UI needed."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "Resources/terminal_runner.py"


class TerminalRunnerTests(unittest.TestCase):
    def make_job(self, folder, command):
        folder = Path(folder)
        job = dict(command=command, workingDirectory=str(folder),
                   logPath=str(folder / "terminal.log"), statusPath=str(folder / "status.json"),
                   lockPath=str(folder / "started.lock"), environment={"PYTHONUNBUFFERED": "1"},
                   preparation="测试说明")
        path = folder / "job.json"
        path.write_text(json.dumps(job))
        return path

    def test_mirrors_output_preserves_exit_and_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="terminal runner ") as folder:
            job = self.make_job(folder, "pwd; printf '设备日志\\n'; printf 'error\\n' >&2; exit 7")
            result = subprocess.run([sys.executable, str(RUNNER), str(job)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, (Path(folder) / "terminal.log").read_bytes())
            self.assertIn(str(Path(folder).resolve()).encode(), result.stdout)
            self.assertIn("设备日志".encode(), result.stdout)
            self.assertIn(b"error", result.stdout)
            status = json.loads((Path(folder) / "status.json").read_text())
            self.assertEqual((status["state"], status["exitCode"]), ("finished", 7))
            duplicate = subprocess.run([sys.executable, str(RUNNER), str(job)], capture_output=True, timeout=10)
            self.assertEqual(duplicate.returncode, 1)
            self.assertEqual((Path(folder) / "terminal.log").read_bytes(), result.stdout)

    def test_interrupt_reaches_child_and_keeps_partial_log(self):
        with tempfile.TemporaryDirectory() as folder:
            command = "exec " + sys.executable + " -u -c \"import time; print('started'); time.sleep(30)\""
            job = self.make_job(folder, command)
            process = subprocess.Popen([sys.executable, str(RUNNER), str(job)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    log = Path(folder) / "terminal.log"
                    if log.exists() and b"\nstarted\n" in log.read_bytes():
                        break
                    time.sleep(0.03)
                else:
                    self.fail("Runner did not start")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stderr)
                self.assertIn(b"started", stdout)
                self.assertEqual(json.loads((Path(folder) / "status.json").read_text())["exitCode"], 130)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_cancelled_launch_does_not_execute_command(self):
        with tempfile.TemporaryDirectory() as folder:
            job = self.make_job(folder, "touch should-not-exist")
            (Path(folder) / "cancel.requested").touch()
            result = subprocess.run([sys.executable, str(RUNNER), str(job)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 130)
            self.assertFalse((Path(folder) / "should-not-exist").exists())
            self.assertEqual(json.loads((Path(folder) / "status.json").read_text())["exitCode"], 130)


if __name__ == "__main__":
    unittest.main()
