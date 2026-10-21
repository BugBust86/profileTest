#!/usr/bin/env python3
"""Independently check exported CSV values, schedule and summary means."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


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
    return summary


def main():
    parser = argparse.ArgumentParser(description="核验采样时间、样本数、内存公式及所有汇总平均值。")
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
    if not result["formal_five_minute_run"]:
        print("这是开发短测，不能替代正式 5 分钟测试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
