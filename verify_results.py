#!/usr/bin/env python3
"""Independently check exported CSV values, schedule and summary means."""
import argparse
import csv
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_charge(folder, summary):
    charge = summary["battery_charge"]
    require(charge["source"] == "/sys/class/power_supply/battery/charge_counter", "电荷量来源错误")
    require(charge["raw_unit"] == "uAh" and charge["unit"] == "mAh", "电荷量单位错误")
    require(charge["valid"] is True, "电荷量起止读数无效")
    with (folder / "charge_samples.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require([row["stage"] for row in rows] == ["start", "end"], "电荷量必须恰好记录起始和结束两条")
    for row in rows:
        stage = row["stage"]
        saved = charge[stage]
        require(saved is not None and saved["stage"] == stage, "电荷量汇总缺少端点")
        counter = int(row["charge_counter_uah"])
        require(counter > 0 and saved["charge_counter_uah"] == counter, "电荷量原始值不一致或无效")
        mah = float(row["charge_counter_mah"])
        require(math.isfinite(mah) and abs(mah - counter / 1000) <= 0.000001, "电荷量 mAh 换算错误")
        require(abs(saved["charge_counter_mah"] - mah) <= 0.000001, "电荷量汇总 mAh 不一致")
        require(row["battery_status"] == saved["battery_status"] == "Discharging", "电荷量端点不是自然放电")
        require(row["timestamp"] == saved["timestamp"], "电荷量时间戳不一致")
        require(dt.datetime.fromisoformat(row["timestamp"]).tzinfo is not None, "电荷量时间戳缺少时区")
        for key in ("command_seconds", "device_uptime_seconds"):
            value = float(row[key])
            require(math.isfinite(value) and value >= 0 and abs(saved[key] - value) <= 0.000001,
                    "电荷量 " + key + " 无效或与汇总不一致")
        require(float(row["command_seconds"]) <= 4.2, "电荷量读取耗时超差")
    consumed = int(rows[0]["charge_counter_uah"]) - int(rows[1]["charge_counter_uah"])
    require(consumed >= 0, "结束电荷量高于起始值")
    require(charge["consumed_uah"] == consumed, "电荷量消耗差值错误")
    require(abs(charge["consumed_mah"] - consumed / 1000) <= 0.000001, "电荷量消耗 mAh 错误")
    interval = float(rows[1]["device_uptime_seconds"]) - float(rows[0]["device_uptime_seconds"])
    require(summary["duration_seconds"] - 1 <= interval <= summary["duration_seconds"] + 12.5,
            "电荷量起止观测时长异常")
    require(abs(charge["interval_seconds"] - interval) <= 0.000001, "电荷量实际观测间隔错误")
    raw = (folder / "charge.log").read_text(encoding="utf-8")
    require(raw.count("===== ") == 2, "电荷量原始日志必须包含两次读取")
    # Match each raw counter/status/uptime to its exported endpoint.
    snapshots = raw.split("===== ")[1:]
    for snapshot, row in zip(snapshots, rows):
        lines = snapshot.splitlines()
        for marker, expected in (("__STAGE__", row["stage"]),
                                 ("__CHARGE_COUNTER__", row["charge_counter_uah"]),
                                 ("__BATTERY_STATUS__", row["battery_status"])):
            require(marker in lines, "电荷量原始日志缺少 " + marker)
            index = lines.index(marker)
            require(index + 1 < len(lines) and lines[index + 1].strip() == expected,
                    "电荷量原始日志与 CSV 不一致：" + marker)
        require("__UPTIME__" in lines, "电荷量原始日志缺少设备时间")
        index = lines.index("__UPTIME__")
        require(index + 1 < len(lines), "电荷量原始日志缺少设备时间值")
        require(abs(float(lines[index + 1].split()[0]) - float(row["device_uptime_seconds"])) <= 0.000001,
                "电荷量原始时间与 CSV 不一致")


def verify(folder, allow_short=False):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    require(summary["status"] == "complete", "采集状态不是 complete")
    require(not summary["errors"] and not summary["interrupted"], "存在错误或中断")
    require(summary["package"] == "com.juphoon.rtc.room", "包名不匹配")
    duration = summary["duration_seconds"]
    require(isinstance(duration, int) and duration > 0 and duration % 10 == 0, "采集时长无效")
    require(allow_short or duration == 300, "正式验收要求 300 秒；短测可使用 --allow-short")
    require(summary["formal_five_minute_run"] == (duration == 300), "正式测试标记错误")
    require(duration <= summary["elapsed_seconds"] <= duration + 5.2, "实际采集时间异常")
    expected = {"cpu": duration // 5, "memory": duration // 10}
    require(summary["expected_samples"] == expected, "预期样本数错误")
    require(summary["actual_samples"] == expected, "有效样本数不足")
    require(summary["cpu_interval_seconds"] == 5 and summary["memory_interval_seconds"] == 10,
            "采样间隔错误")
    metrics = {
        "cpu": ["app_cpu_percent", "app_cpu_one_core_percent", "total_cpu_percent"],
        "memory": ["app_memory_percent", "total_memory_percent", "app_pss_mib"],
    }
    pids = set()
    for kind, period in (("cpu", 5), ("memory", 10)):
        with (folder / (kind + "_samples.csv")).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(len(rows) == expected[kind], kind + " CSV 样本数错误")
        offsets = list(range(5, duration + 1, 5)) if kind == "cpu" else list(range(0, duration, 10))
        require([int(row["scheduled_elapsed_s"]) for row in rows] == offsets, kind + " 时间点缺失或重复")
        for row, offset in zip(rows, offsets):
            actual = float(row["actual_elapsed_s"])
            require(math.isfinite(actual) and -0.001 <= actual - offset <= 1.001, kind + " 采样发起时间超差")
            command = float(row["command_seconds"])
            require(0 <= command <= (4.2 if kind == "cpu" else 8.2), kind + " 命令耗时超差")
            pids.add(int(row["pid"]))
            if kind == "cpu":
                require(float(row["interval_seconds"]) > 0, "CPU 差值区间无效")
            else:
                total = int(row["mem_total_kib"])
                avail = int(row["mem_available_kib"])
                pss = int(row["app_pss_kib"])
                require(0 < pss <= total and 0 <= avail <= total, "内存原始值无效")
                require(int(row["mem_used_kib"]) == total - avail, "已用内存计算错误")
                for key, value in (("app_memory_percent", pss / total * 100),
                                   ("total_memory_percent", (total - avail) / total * 100),
                                   ("app_pss_mib", pss / 1024)):
                    require(abs(float(row[key]) - value) <= 0.000001, key + " 计算错误")
        for key in metrics[kind]:
            values = [float(row[key]) for row in rows]
            require(all(math.isfinite(x) and x >= 0 for x in values), key + " 包含无效值")
            if key not in ("app_cpu_one_core_percent", "app_pss_mib"):
                require(all(x <= 100 for x in values), key + " 超过 100%")
            metric = summary["metrics"][key]
            require(metric["samples"] == len(values), key + " 汇总样本数不一致")
            require(abs(statistics.mean(values) - metric["mean"]) <= 0.000001,
                    key + " 平均值与 CSV 不一致")
        raw_name = "cpu_top.log" if kind == "cpu" else "memory.log"
        raw = (folder / raw_name).read_text(encoding="utf-8")
        require(raw.count("===== ") == len(rows), raw_name + " 快照数不一致")
    require(len(pids) == 1, "采集中存在多个 PID")
    for name in ("cpu_baseline.log", "device.txt", "summary.md"):
        require((folder / name).stat().st_size > 0, name + " 为空")
    # Existing CPU/memory-only runs remain readable; all version 2 runs require charge data.
    if summary.get("schema_version", 1) >= 2 or "battery_charge" in summary:
        verify_charge(folder, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="核验采样时间、样本数、内存公式、汇总平均值及起止电荷量。")
    parser.add_argument("folder", type=Path, help="run_... 结果目录")
    parser.add_argument("--allow-short", action="store_true", help="允许非 300 秒的开发短测")
    args = parser.parse_args()
    try:
        result = verify(args.folder, args.allow_short)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("验收失败：" + str(exc), file=sys.stderr)
        return 1
    print("验收通过：%ds，CPU %d 条 / 内存 %d 条，时间点与平均值一致。" %
          (result["duration_seconds"], result["actual_samples"]["cpu"], result["actual_samples"]["memory"]))
    if "battery_charge" in result:
        charge = result["battery_charge"]
        print("电荷量：%.3f → %.3f mAh，消耗 %.3f mAh；实际观测 %.3fs。" %
              (charge["start"]["charge_counter_mah"], charge["end"]["charge_counter_mah"],
               charge["consumed_mah"], charge["interval_seconds"]))
    if not result["formal_five_minute_run"]:
        print("这是开发短测，不能替代正式 5 分钟测试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
