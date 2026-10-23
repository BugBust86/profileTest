import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import collector as c
from verify_results import verify


class Calculations(unittest.TestCase):
    def test_charge_counter_parsing_and_signed_consumption(self):
        raw = "__STAGE__\nstart\n__CHARGE_COUNTER__\n2362000\n__BATTERY_STATUS__\nDischarging\n__UPTIME__\n100.25 99\n"
        start = c.parse_charge(raw)
        self.assertEqual(start["charge_counter_mah"], 2362)
        end = dict(start, stage="end", charge_counter_uah=2350000,
                   charge_counter_mah=2350, device_uptime_seconds=400.75)
        charge, errors = c.summarize_charge([start, end])
        self.assertEqual(errors, [])
        self.assertEqual(charge["consumed_mah"], 12)
        self.assertEqual(charge["interval_seconds"], 300.5)
        for broken in (raw.replace("2362000", "0"), raw.replace("2362000", "-1"),
                       raw.replace("2362000", "unavailable"), raw.replace("100.25", "nan")):
            with self.subTest(raw=broken), self.assertRaises(c.CollectionError):
                c.parse_charge(broken)
        charge, errors = c.summarize_charge([start])
        self.assertFalse(charge["valid"])
        self.assertIsNone(charge["consumed_mah"])
        charging = dict(end, battery_status="Charging")
        self.assertFalse(c.summarize_charge([start, charging])[0]["valid"])
        increased = dict(end, charge_counter_uah=2363000, charge_counter_mah=2363)
        charge, errors = c.summarize_charge([start, increased])
        self.assertFalse(charge["valid"])
        self.assertEqual(charge["consumed_mah"], -1)  # never abs() or clamp to zero
        self.assertEqual(c.summarize_charge([start, dict(end, charge_counter_uah=2362000)])[0]["consumed_mah"], 0)

    def test_device_selection_supports_wireless_and_requires_explicit_multi_device(self):
        adb = Mock()
        wireless = "adb-FAKE._adb-tls-connect._tcp"
        adb.run.return_value = "List of devices attached\n%s device model:Fake\nemulator-5554 device model:Emulator\n" % wireless
        self.assertEqual(c.select_device(adb, None)[0], wireless)
        adb.run.return_value += "USB device usb:1 model:Fake\n"
        with self.assertRaisesRegex(c.CollectionError, "--serial"):
            c.select_device(adb, None)
        self.assertEqual(c.select_device(adb, wireless)[0], wireless)
        adb.run.return_value = "192.168.1.2:5555 device model:Fake\n"
        self.assertEqual(c.select_device(adb, "192.168.1.2:5555")[0], "192.168.1.2:5555")
        for state in ("offline", "unauthorized"):
            adb.run.return_value = "%s %s\n" % (wireless, state)
            with self.subTest(state=state), self.assertRaises(c.CollectionError):
                c.select_device(adb, wireless)

    def test_cpu_core_normalization_and_iowait(self):
        # 8 cores * 100 ticks * 5s; app used 1 full core, 40% system busy.
        old = dict(pid=123, start_ticks=10, app_ticks=0, total_ticks=0, idle_ticks=0, uptime=10)
        new = dict(pid=123, start_ticks=10, app_ticks=500, total_ticks=4000, idle_ticks=2400, uptime=15)
        value = c.cpu_metrics(old, new, 100)
        self.assertEqual(value["app_cpu_percent"], 12.5)
        self.assertEqual(value["app_cpu_one_core_percent"], 100)
        self.assertEqual(value["total_cpu_percent"], 40)
        new["app_ticks"] = 1000
        self.assertEqual(c.cpu_metrics(old, new, 100)["app_cpu_one_core_percent"], 200)
        for key, value in (("pid", 124), ("start_ticks", 11), ("total_ticks", -1), ("app_ticks", -1)):
            broken = dict(new, **{key: value})
            with self.subTest(key=key), self.assertRaises(c.CollectionError):
                c.cpu_metrics(old, broken, 100)

    def test_parse_process_name_and_guest_ticks(self):
        fields = ["S"] + ["0"] * 21
        fields[11], fields[12], fields[19] = "120", "30", "999"
        raw = "__PIDS__\n123\n__CMDLINE__\n%s\0\n__PROCESS__\n123 (a (b) name) %s\n__CPU__\ncpu 10 2 3 50 4 5 6 7 999 999\n__UPTIME__\n100 200\n" % (c.PACKAGE, " ".join(fields))
        parsed = c.parse_cpu(raw)
        self.assertEqual(parsed["app_ticks"], 150)
        self.assertEqual(parsed["start_ticks"], 999)
        self.assertEqual(parsed["total_ticks"], 87)  # no double-counting guest
        self.assertEqual(parsed["idle_ticks"], 54)  # idle + iowait
        with self.assertRaises(c.CollectionError):
            c.parse_cpu(raw.replace(c.PACKAGE, c.PACKAGE + ":remote"))

    def test_memory_real_android_log_and_old_format(self):
        app = (ROOT / "tests/fixtures/pixel7_meminfo.txt").read_text()
        raw = ("__PIDS_BEFORE__\n5391\n__MEMINFO__\nMemTotal: 7647536 kB\nMemAvailable: 3000000 kB\n"
               "__APP_MEMINFO__\n" + app + "\n__PIDS_AFTER__\n5391\n")
        result = c.parse_memory(raw, 5391)
        self.assertEqual(result["app_pss_kib"], 291380)
        self.assertAlmostEqual(result["app_memory_percent"], 291380 / 7647536 * 100, places=6)
        self.assertEqual(result["mem_used_kib"], 4647536)
        old = raw.replace("TOTAL PSS:", "PSS:")
        self.assertEqual(c.parse_memory(old, 5391)["app_pss_kib"], 291380)
        with self.assertRaises(c.CollectionError):
            c.parse_memory(raw.replace("MemAvailable:", "Unavailable:"), 5391)
        with self.assertRaises(c.CollectionError):
            c.parse_memory(raw.replace("__PIDS_AFTER__\n5391", "__PIDS_AFTER__\n6000"), 5391)

    def test_sampling_window_and_pid_selection(self):
        cpu = list(c.scheduled_offsets(300, 5))
        memory = list(c.scheduled_offsets(300, 10, True))
        self.assertEqual((len(cpu), cpu[0], cpu[-1]), (60, 5, 300))
        self.assertEqual((len(memory), memory[0], memory[-1]), (30, 0, 290))
        for value in ("", "123 124", "error: offline"):
            with self.subTest(value=value), self.assertRaises(c.CollectionError):
                c.parse_pids(value)

    def test_timeout_preserves_partial_raw_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "raw.log"
            adb = c.Adb(sys.executable, threading.Event())
            start = time.monotonic()
            with self.assertRaisesRegex(c.CollectionError, "超时"):
                adb.run(["-u", "-c", "import time; print('partial'); time.sleep(5)"], timeout=0.3, raw_path=path)
            self.assertLess(time.monotonic() - start, 2)
            self.assertIn("partial", path.read_text())
            self.assertIn("超时", path.read_text())


class EntryPoint(unittest.TestCase):
    def run_entry(self, mode, folder, extra=()):
        env = dict(os.environ, FAKE_ADB_MODE=mode)
        env.pop("ANDROID_SERIAL", None)
        return subprocess.run([str(ROOT / "collect.sh"), "--adb", str(ROOT / "tests/fake_adb.py"),
                               "--output", folder, "--duration", "10", *extra],
                              cwd="/tmp", env=env, text=True, capture_output=True, timeout=20)

    def test_complete_entrypoint_and_csv_summary_verifier(self):
        with tempfile.TemporaryDirectory(prefix="collector test ") as folder:
            result = self.run_entry("normal", folder, ("--label", "720p SR on"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            out = next(Path(folder).glob("run_*"))
            summary = verify(out, allow_short=True)
            self.assertEqual(summary["label"], "720p SR on")
            self.assertEqual(summary["schema_version"], 2)
            self.assertEqual(summary["battery_charge"]["consumed_uah"], 2000)
            self.assertEqual(summary["battery_charge"]["consumed_mah"], 2)
            self.assertEqual((out / "charge.log").read_text().count("===== "), 2)
            self.assertIn("2000.000 mAh", (out / "summary.md").read_text())
            for key, expected in (("app_cpu_percent", 12.5), ("app_cpu_one_core_percent", 100),
                                  ("total_cpu_percent", 50), ("app_memory_percent", 10),
                                  ("total_memory_percent", 60)):
                self.assertAlmostEqual(summary["metrics"][key]["mean"], expected, places=5)
            with self.assertRaisesRegex(ValueError, "300 秒"):
                verify(out)
            # Verifier must reject a tampered mean.
            summary["metrics"]["app_cpu_percent"]["mean"] = 99
            (out / "summary.json").write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "平均值"):
                verify(out, allow_short=True)
            summary["metrics"]["app_cpu_percent"]["mean"] = 12.5
            summary["battery_charge"]["consumed_mah"] = 999
            (out / "summary.json").write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "电荷量消耗"):
                verify(out, allow_short=True)
            summary["battery_charge"]["consumed_mah"] = 2
            (out / "summary.json").write_text(json.dumps(summary))
            raw = (out / "charge.log").read_text()
            (out / "charge.log").write_text(raw.replace("1998000", "1997000"))
            with self.assertRaisesRegex(ValueError, "原始日志与 CSV"):
                verify(out, allow_short=True)
            (out / "charge.log").write_text(raw)
            charge_csv = out / "charge_samples.csv"
            lines = charge_csv.read_text(encoding="utf-8-sig").splitlines()
            charge_csv.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "恰好"):
                verify(out, allow_short=True)
            # Legacy CPU/memory-only results still pass without new charge files.
            del summary["schema_version"], summary["battery_charge"]
            (out / "summary.json").write_text(json.dumps(summary))
            verify(out, allow_short=True)

    def test_wireless_entrypoint(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_entry("wireless", folder)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = verify(next(Path(folder).glob("run_*")), allow_short=True)
            self.assertEqual(summary["serial"], "adb-FAKE._adb-tls-connect._tcp")
            self.assertEqual(summary["battery_charge"]["consumed_mah"], 2)

    def test_final_charge_failure_preserves_cpu_and_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_entry("end_charge_failure", folder)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            out = next(Path(folder).glob("run_*"))
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["actual_samples"], {"cpu": 2, "memory": 1})
            self.assertEqual(summary["status"], "incomplete")
            self.assertIsNone(summary["battery_charge"]["end"])
            self.assertIsNone(summary["battery_charge"]["consumed_mah"])
            self.assertIn("Permission denied", (out / "charge.log").read_text())

    def test_failures_never_pass_as_valid_results(self):
        for mode in ("no_device", "unauthorized", "multiple_devices", "disconnect", "bad_memory", "restart", "timeout", "bad_charge"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                result = self.run_entry(mode, folder)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                out = next(Path(folder).glob("run_*"))
                summary = json.loads((out / "summary.json").read_text())
                self.assertEqual(summary["status"], "incomplete")
                self.assertFalse(summary["formal_five_minute_run"])
                with self.assertRaisesRegex(ValueError, "complete"):
                    verify(out, allow_short=True)

    def test_termination_saves_partial_results(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, FAKE_ADB_MODE="normal")
            env.pop("ANDROID_SERIAL", None)
            process = subprocess.Popen([str(ROOT / "collect.sh"), "--adb", str(ROOT / "tests/fake_adb.py"),
                                        "--output", folder], env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    csv_paths = list(Path(folder).glob("run_*/memory_samples.csv"))
                    if csv_paths and csv_paths[0].stat().st_size > 0:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("采集未就绪")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stdout + stderr)
                summary = json.loads(next(Path(folder).glob("run_*/summary.json")).read_text())
                self.assertEqual(summary["status"], "incomplete")
                self.assertTrue(summary["interrupted"])
                self.assertEqual(summary["actual_samples"]["memory"], 1)
                self.assertIsNotNone(summary["battery_charge"]["end"])
                self.assertEqual(summary["battery_charge"]["consumed_mah"], 2)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
