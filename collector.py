#!/usr/bin/env python3
"""ADB performance collection. No third-party Python dependencies."""

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time

PACKAGE = "com.juphoon.rtc.room"
CPU_PERIOD = 5
MEMORY_PERIOD = 10
MAX_LATENESS = 1.0
DEFAULT_OUTPUT = Path(__file__).resolve().parent


class CollectionError(Exception):
    pass


def timestamp():
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def section(text, name):
    match = re.search(r"^__" + name + r"__\r?\n(.*?)(?=^__[A-Z_]+__\r?$|\Z)",
                      text, re.M | re.S)
    if not match:
        raise CollectionError("快照缺少字段：" + name)
    return match.group(1).strip()


def parse_pids(text):
    if not re.fullmatch(r"\d+(?:\s+\d+)*", text):
        raise CollectionError("未找到 App 主进程，请先进入会议：" + PACKAGE)
    pids = text.split()
    if len(pids) != 1:
        raise CollectionError("发现多个同名主进程（多用户/分身），无法确定目标：" + text)
    return int(pids[0])


def parse_cpu(text):
    pid = parse_pids(section(text, "PIDS"))
    cmdline = section(text, "CMDLINE").split("\0", 1)[0]
    if cmdline != PACKAGE:
        raise CollectionError("目标进程名称不匹配：" + repr(cmdline))
    # comm may contain spaces/parentheses; the fields after it start at field 3.
    stat = section(text, "PROCESS")
    try:
        fields = stat[stat.rindex(")") + 2:].split()
        if int(stat.split(" ", 1)[0]) != pid:
            raise ValueError("PID mismatch")
        ticks = int(fields[11]) + int(fields[12])  # utime + stime, no child times
        start_ticks = int(fields[19])
        cpu = section(text, "CPU").split()
        if cpu[0] != "cpu" or len(cpu) < 9:
            raise ValueError("unsupported /proc/stat")
        counters = [int(x) for x in cpu[1:9]]
        uptime = float(section(text, "UPTIME").split()[0])
    except (IndexError, ValueError) as exc:
        raise CollectionError("无法解析 CPU 计数器：" + str(exc)) from exc
    return dict(pid=pid, start_ticks=start_ticks, app_ticks=ticks,
                total_ticks=sum(counters), idle_ticks=counters[3] + counters[4],
                uptime=uptime)


def cpu_metrics(previous, current, hz):
    if (previous["pid"], previous["start_ticks"]) != (current["pid"], current["start_ticks"]):
        raise CollectionError("App 主进程退出或重启，停止本轮测试，请重新进入会议后重测")
    total = current["total_ticks"] - previous["total_ticks"]
    idle = current["idle_ticks"] - previous["idle_ticks"]
    app = current["app_ticks"] - previous["app_ticks"]
    seconds = current["uptime"] - previous["uptime"]
    if total <= 0 or seconds <= 0 or app < 0 or not 0 <= idle <= total or app > total:
        raise CollectionError("CPU 计数器回退或无效")
    return dict(pid=current["pid"], interval_seconds=round(seconds, 6),
                app_cpu_percent=round(app / total * 100, 6),
                app_cpu_one_core_percent=round(app / (seconds * hz) * 100, 6),
                total_cpu_percent=round((total - idle) / total * 100, 6))


def parse_memory(text, expected_pid):
    for name in ("PIDS_BEFORE", "PIDS_AFTER"):
        if parse_pids(section(text, name)) != expected_pid:
            raise CollectionError("内存采样期间 App 主进程已变化")
    system = {key: int(value) for key, value in
              re.findall(r"^(\w+):\s+(\d+)\s+kB\s*$", section(text, "MEMINFO"), re.M)}
    if "MemTotal" not in system or "MemAvailable" not in system:
        raise CollectionError("设备未提供 MemTotal/MemAvailable，不能计算总内存使用率")
    total, available = system["MemTotal"], system["MemAvailable"]
    app = section(text, "APP_MEMINFO")
    headers = re.findall(r"\*\* MEMINFO in pid (\d+) \[([^\]]+)\] \*\*", app)
    if headers != [(str(expected_pid), PACKAGE)]:
        raise CollectionError("dumpsys 未返回目标主进程内存，或返回了多个进程")
    pss = re.search(r"\bTOTAL PSS:\s*(\d+)\b", app)
    if not pss:
        pss = re.search(r"^\s*TOTAL\s+(\d+)\s+", app, re.M)
    if not pss:
        raise CollectionError("dumpsys 输出中缺少 TOTAL PSS/TOTAL，不能用 0 代替")
    pss = int(pss.group(1))
    if total <= 0 or not 0 <= available <= total or not 0 < pss <= total:
        raise CollectionError("内存计数器无效")
    return dict(pid=expected_pid, app_pss_kib=pss, app_pss_mib=round(pss / 1024, 6),
                app_memory_percent=round(pss / total * 100, 6),
                mem_total_kib=total, mem_available_kib=available,
                mem_used_kib=total - available,
                total_memory_percent=round((total - available) / total * 100, 6))


class Adb:
    def __init__(self, executable, stop):
        self.executable = executable
        self.serial = None
        self.stop = stop

    def run(self, args, timeout=8, raw_path=None, heading=""):
        command = [self.executable] + (["-s", self.serial] if self.serial else []) + args
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        failure = None
        try:
            while True:
                if self.stop.is_set():
                    failure = "采集已停止"
                    break
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    failure = "ADB 命令超时（%.1fs）：%s" % (timeout, " ".join(args)[:100])
                    break
                try:
                    process.communicate(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate()
            output = stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
            error = stderr.decode("utf-8", errors="replace")
            if raw_path:
                with raw_path.open("a", encoding="utf-8") as stream:
                    stream.write("\n===== %s %s =====\n%s" % (timestamp(), heading, output))
                    if error:
                        stream.write("\n[stderr]\n" + error)
                    if failure:
                        stream.write("\n[error] " + failure + "\n")
        if failure:
            raise CollectionError(failure)
        if process.returncode:
            raise CollectionError("ADB 执行失败：" + (error.strip() or output.strip() or str(process.returncode)))
        return output

    def shell(self, script, **kwargs):
        return self.run(["shell", script], **kwargs)


def select_device(adb, requested):
    listing = adb.run(["devices", "-l"])
    devices = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ("device", "offline", "unauthorized", "recovery", "sideload"):
            devices[fields[0]] = (fields[1], any(x.startswith("usb:") for x in fields[2:]))
    if requested:
        if requested not in devices or devices[requested][0] != "device":
            raise CollectionError("指定设备不可用，请检查 USB 调试授权：\n" + listing)
        if not devices[requested][1]:
            raise CollectionError("指定设备不是 USB 连接，请通过 USB 连接手机")
        return requested, listing
    usb = [serial for serial, (state, wired) in devices.items() if state == "device" and wired]
    if len(usb) != 1:
        raise CollectionError("需要恰好一台已授权的 USB 设备；多设备请使用 --serial：\n" + listing)
    return usb[0], listing


def cpu_script(include_top):
    return """set -eu
printf '__PIDS__\\n'
pids=$(pidof %s) || { echo 'App 主进程未运行，请先进入会议' >&2; exit 1; }
echo "$pids"
set -- $pids
[ "$#" -eq 1 ] || { echo '发现多个同名主进程，请关闭其他用户/分身中的 App' >&2; exit 1; }
printf '__CMDLINE__\\n'
cat /proc/$1/cmdline
printf '\\n__PROCESS__\\n'
cat /proc/$1/stat
printf '__CPU__\\n'
head -n 1 /proc/stat
printf '__UPTIME__\\n'
cat /proc/uptime
%s
""" % (shlex.quote(PACKAGE), "printf '__TOP__\\n'\ntop -b -n 1" if include_top else "")


def memory_script(pid):
    return """set -eu
printf '__PIDS_BEFORE__\\n'
pidof %s
printf '__MEMINFO__\\n'
cat /proc/meminfo
printf '__APP_MEMINFO__\\n'
dumpsys meminfo %d
printf '__PIDS_AFTER__\\n'
pidof %s
""" % (shlex.quote(PACKAGE), pid, shlex.quote(PACKAGE))


def scheduled_offsets(duration, period, include_zero=False):
    return range(0 if include_zero else period, duration if include_zero else duration + 1, period)


def write_summary(out, args, serial, rows, started, elapsed, errors, interrupted):
    expected = {"cpu": args.duration // CPU_PERIOD, "memory": args.duration // MEMORY_PERIOD}
    complete = (not errors and not interrupted and
                all(len(rows[k]) == expected[k] for k in expected))
    definitions = [
        ("App CPU 使用率（整机）", "cpu", "app_cpu_percent", "%"),
        ("App 内存使用率（PSS / MemTotal）", "memory", "app_memory_percent", "%"),
        ("总 CPU 使用率", "cpu", "total_cpu_percent", "%"),
        ("总内存使用率", "memory", "total_memory_percent", "%"),
        ("App CPU 使用率（单核口径）", "cpu", "app_cpu_one_core_percent", "%"),
        ("App PSS", "memory", "app_pss_mib", "MiB"),
    ]
    metrics = {}
    for label, source, key, unit in definitions:
        values = [row[key] for row in rows[source]]
        metrics[key] = {"label": label, "mean": statistics.mean(values) if values else None,
                        "unit": unit, "samples": len(values)}
    result = dict(status="complete" if complete else "incomplete", package=PACKAGE,
                  process_scope="exact main process only; excludes :suffix processes",
                  serial=serial, label=args.label, started_at=started, finished_at=timestamp(),
                  duration_seconds=args.duration, elapsed_seconds=round(elapsed, 3),
                  cpu_interval_seconds=CPU_PERIOD, memory_interval_seconds=MEMORY_PERIOD,
                  expected_samples=expected, actual_samples={k: len(v) for k, v in rows.items()},
                  formal_five_minute_run=complete and args.duration == 300,
                  max_lateness_seconds=MAX_LATENESS, metrics=metrics, errors=errors,
                  interrupted=interrupted)
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 性能采集结果", "", "- 状态：" + ("完整" if complete else "不完整（不可作为验收结果）"),
             "- 正式 5 分钟测试：" + ("是" if result["formal_five_minute_run"] else "否"),
             "- 场景：" + (args.label or "未填写"), "- 包名：" + PACKAGE,
             "- 设备：" + (serial or "未选择"), "- 开始：" + (started or "未开始"),
             "- 采集经过时间：%.3f 秒；配置窗口：%d 秒" % (elapsed, args.duration),
             "- CPU：%d/%d；内存：%d/%d" % (len(rows["cpu"]), expected["cpu"], len(rows["memory"]), expected["memory"]),
             "", "| 指标 | 样本算术平均值 | 有效样本数 |", "| --- | ---: | ---: |"]
    for metric in metrics.values():
        value = "N/A" if metric["mean"] is None else "%.4f %s" % (metric["mean"], metric["unit"])
        lines.append("| %s | %s | %d |" % (metric["label"], value, metric["samples"]))
    lines += ["", "App CPU 整机口径为 0–100%；单核口径可超过 100%。",
              "App 内存使用 PSS，总内存使用 (MemTotal − MemAvailable) / MemTotal。",
              "仅统计名称恰好等于包名的主进程。原始 top 的 %CPU 不参与均值计算。"]
    if errors or interrupted:
        lines += ["", "不完整原因："] + ["- " + x for x in errors]
        if interrupted:
            lines.append("- 用户中断或收到终止信号")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def collect(args):
    stop = threading.Event()
    interrupted = threading.Event()

    def on_signal(signum, frame):
        interrupted.set()
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, on_signal)
    adb_path = shutil.which(args.adb)
    if not adb_path:
        raise CollectionError("找不到 adb，请安装 Android SDK Platform-Tools 并加入 PATH")
    adb = Adb(adb_path, stop)
    args.output.mkdir(parents=True, exist_ok=True)
    out = args.output / ("run_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    out.mkdir()
    print("输出目录：" + str(out), flush=True)
    rows = {"cpu": [], "memory": []}
    errors = []
    serial = None
    started = None
    epoch = None
    workers = []
    try:
        serial, listing = select_device(adb, args.serial)
        adb.serial = serial
        (out / "device.txt").write_text(listing, encoding="utf-8")
        info = adb.shell("""set -eu
echo Model: $(getprop ro.product.model)
echo Android: $(getprop ro.build.version.release)
echo SDK: $(getprop ro.build.version.sdk)
echo Fingerprint: $(getprop ro.build.fingerprint)
echo CLK_TCK: $(getconf CLK_TCK)
""", raw_path=out / "device.txt", heading="device properties")
        hz_match = re.search(r"^CLK_TCK:\s*(\d+)\s*$", info, re.M)
        if not hz_match or int(hz_match.group(1)) <= 0:
            raise CollectionError("无法读取设备 CLK_TCK")
        hz = int(hz_match.group(1))
        baseline = parse_cpu(adb.shell(cpu_script(False), timeout=4,
                                      raw_path=out / "cpu_baseline.log", heading="baseline"))
        started = timestamp()
        epoch = time.monotonic()
        wall_epoch = time.time()
        print("设备 %s，主进程 PID %s。开始 %ds 采集（CPU 5s / 内存 10s），Ctrl+C 可停止。" %
              (serial, baseline["pid"], args.duration), flush=True)

        def worker(kind, period):
            previous = baseline
            csv_file = out / (kind + "_samples.csv")
            try:
                with csv_file.open("w", newline="", encoding="utf-8-sig") as stream:
                    writer = None
                    for offset in scheduled_offsets(args.duration, period, kind == "memory"):
                        if stop.wait(max(0, epoch + offset - time.monotonic())):
                            return
                        actual = time.monotonic() - epoch
                        if abs(time.time() - wall_epoch - actual) > 2:
                            raise CollectionError("系统时钟跳变或电脑休眠，采样窗口不连续，请重测")
                        if actual - offset > MAX_LATENESS:
                            raise CollectionError("%s 采样迟到 %.3fs，可能电脑休眠或设备响应过慢" % (kind, actual - offset))
                        sample_time = timestamp()
                        if kind == "cpu":
                            raw = adb.shell(cpu_script(True), timeout=4, raw_path=out / "cpu_top.log",
                                            heading="scheduled=%ds actual=%.3fs" % (offset, actual))
                            current = parse_cpu(raw)
                            data = cpu_metrics(previous, current, hz)
                            previous = current
                        else:
                            raw = adb.shell(memory_script(baseline["pid"]), timeout=8,
                                            raw_path=out / "memory.log",
                                            heading="scheduled=%ds actual=%.3fs" % (offset, actual))
                            data = parse_memory(raw, baseline["pid"])
                        row = dict(timestamp=sample_time, scheduled_elapsed_s=offset,
                                   actual_elapsed_s=round(actual, 6),
                                   command_seconds=round(time.monotonic() - epoch - actual, 6), **data)
                        if writer is None:
                            writer = csv.DictWriter(stream, fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                        stream.flush()
                        rows[kind].append(row)
                        print("[%3ds] %s %d/%d" % (offset, kind, len(rows[kind]), args.duration // period), flush=True)
            except Exception as exc:
                errors.append("%s: %s" % (kind, exc))
                stop.set()

        workers = [threading.Thread(target=worker, args=("cpu", CPU_PERIOD)),
                   threading.Thread(target=worker, args=("memory", MEMORY_PERIOD))]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join()
    except Exception as exc:
        errors.append(str(exc))
        stop.set()
    finally:
        for thread in workers:
            if thread.ident:
                thread.join()
        elapsed = time.monotonic() - epoch if epoch is not None else 0
        result = write_summary(out, args, serial, rows, started, elapsed, errors, interrupted.is_set())
    print((out / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 130 if interrupted.is_set() else (0 if result["status"] == "complete" else 1)


def main():
    parser = argparse.ArgumentParser(description="采集 com.juphoon.rtc.room 主进程与整机 CPU/内存，默认 5 分钟。")
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"), help="adb devices -l 中的 USB 设备序列号")
    parser.add_argument("--label", default="", help="测试场景，例如 720p_SR_on")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="结果父目录，默认脚本所在目录")
    parser.add_argument("--duration", type=int, default=300, help="采集秒数，必须是 10 的正整数倍；正式测试使用默认 300")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"), help="adb 可执行文件路径，默认使用 PATH 或 ADB 环境变量")
    args = parser.parse_args()
    if args.duration <= 0 or args.duration % MEMORY_PERIOD:
        parser.error("--duration 必须是 10 的正整数倍")
    args.output = args.output.expanduser().resolve()
    try:
        return collect(args)
    except (CollectionError, OSError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
