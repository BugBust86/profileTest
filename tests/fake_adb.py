#!/usr/bin/env python3
"""Only for automated tests. Never connects to a phone."""
import os
import sys
import time

mode = os.environ.get("FAKE_ADB_MODE", "normal")
args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
if args == ["devices", "-l"]:
    print("List of devices attached")
    if mode != "no_device":
        if mode == "wireless":
            print("adb-FAKE._adb-tls-connect._tcp\tdevice product:fake model:Fake transport_id:1")
        else:
            print("FAKE\t%s usb:1 model:Fake" % ("unauthorized" if mode == "unauthorized" else "device"))
    if mode == "multiple_devices":
        print("OTHER\tdevice usb:2 model:Fake")
elif args[:1] == ["shell"]:
    script = args[1]
    if "CLK_TCK:" in script:
        print("Model: Fake\nAndroid: Test\nSDK: 37\nCLK_TCK: 100")
    elif "__CHARGE_COUNTER__" in script:
        ending = "echo end" in script
        if mode == "bad_charge" or (mode == "end_charge_failure" and ending):
            print("cat: charge_counter: Permission denied", file=sys.stderr)
            sys.exit(1)
        print("__STAGE__\n" + ("end" if ending else "start"))
        print("__CHARGE_COUNTER__\n" + ("1998000" if ending else "2000000"))
        print("__BATTERY_STATUS__\nDischarging")
        print("__UPTIME__\n%.2f 0" % time.monotonic())
    elif "__APP_MEMINFO__" in script:
        if mode == "disconnect":
            print("error: device offline", file=sys.stderr)
            sys.exit(1)
        if mode == "timeout":
            time.sleep(20)
        print("__PIDS_BEFORE__\n123\n__MEMINFO__")
        print("MemTotal: 1000000 kB\nMemAvailable: 400000 kB")
        print("__APP_MEMINFO__\n** MEMINFO in pid 123 [com.juphoon.rtc.room] **")
        print("TOTAL PSS: 100000" if mode != "bad_memory" else "Permission denied")
        print("__PIDS_AFTER__\n123")
    else:
        ticks = int(time.monotonic() * 100)
        pid = 124 if mode == "restart" and "__TOP__" in script else 123
        fields = ["S"] + ["0"] * 21
        fields[11], fields[12], fields[19] = str(ticks), "0", "12345"
        print("__PIDS__\n%d\n__CMDLINE__\ncom.juphoon.rtc.room\0" % pid)
        print("__PROCESS__\n%d (task (test) name) %s" % (pid, " ".join(fields)))
        print("__CPU__\ncpu %d 0 0 %d 0 0 0 0 999 999" % (ticks * 4, ticks * 4))
        print("__UPTIME__\n%.2f 0" % (ticks / 100))
        if "__TOP__" in script:
            print("__TOP__\nFAKE top snapshot")
else:
    sys.exit(2)
