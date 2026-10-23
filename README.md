# Android 会议性能采集

唯一采集入口是本目录的 `./collect.sh`，核心实现为 `collector.py`。默认采集包名 **`com.juphoon.rtc.room` 的主进程**和整机性能，持续 **300 秒**，结束后自动计算四项使用率的样本算术平均值，并记录**起始、结束两次剩余电荷量及其差值**。支持 USB 和无线 ADB；测量放电消耗时使用无线连接并断开所有充电电源。

采用 **Shell 入口 + Python 标准库 + ADB**：命令行可以独立使用，无第三方 Python 包，无需 root，也无需修改手机 App。Python 负责固定时间调度、超时处理、CSV 和结果汇总；Shell 保留现有操作入口。

另提供原生 **macOS 性能测试工作台**：按顺序显示设备及连接方式、可编辑场景准备、Shell 命令和不自动滚动的终端日志。点击执行会打开系统 Terminal，在 `~/` 下运行命令，并同步同一次任务的日志。

```bash
./build_app.sh --open
```

构建后可直接打开 `dist/ProfilerDesk.app`。App 的详细使用与开发说明见 [macOSApp/README.md](macOSApp/README.md)。

## 1. 首次部署

适用于 macOS，需 Python 3.9+、Android SDK Platform-Tools 和支持相关 `/proc`、`dumpsys` 接口的 Android 真机。已在 Pixel 7 / Android 17 / SDK 37 上验证。其他厂商若限制这些接口，工具会停止并给出不完整结果，不会静默换用其他口径。

1. 检查工具是否已经安装：

   ```bash
   python3 --version
   adb version
   ```

2. 缺少工具且已安装 Homebrew 时，可执行：

   ```bash
   brew install python
   brew install --cask android-platform-tools
   ```

   已安装 Android Studio 的用户可直接使用其 SDK 中的 ADB。默认 SDK 路径下，在当前终端执行：

   ```bash
   export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"
   ```

   若要每次打开终端生效，将该行加入 `~/.zshrc`。也可通过 `--adb /实际路径/adb` 指定 ADB。

3. 打开手机开发者选项和 USB 调试，用 USB 数据线连接电脑；解锁手机并允许本机 USB 调试。

4. 进入本目录并检查入口：

   ```bash
   cd /Users/lds/Desktop/workspace/juphoon/profilerTest
   chmod +x collect.sh
   ./collect.sh --help
   adb devices -l
   ```

   设备应显示 `device`；`unauthorized` 表示需要在手机上允许调试，`offline` 表示连接异常。默认选择唯一在线真机（USB 或无线），自动选择时忽略 `emulator-...`。存在多个在线设备或同一手机的多个连接时，必须用 `--serial` 指定 `adb devices -l` 中的完整标识。环境变量 `ANDROID_SERIAL` 也会作为默认标识。

   无线测试先在手机“开发者选项 → 无线调试”中配对、连接，例如 `adb pair IP:配对端口`、`adb connect IP:调试端口`（两个端口可能不同，使用手机显示的实际值）。已连接的 `adb-..._adb-tls-connect._tcp` 标识也可直接传给 `--serial`，脚本不会自动配对或连接设备。

## 2. 每轮测试操作

1. 人工进入会议，选择对应的**对端分辨率**和本轮超分开关状态，确认视频已稳定。开关超分各执行独立一轮，用标签区分。
2. 将手机固定在同一页面，保持对端视频、摄像头、麦克风、亮度、网络、温度和供电条件尽量一致；结束其他性能采集或 profiler 工具。每轮只运行一个采集实例。
3. 在终端启动正式测试，建议用场景名称记录分辨率和超分状态：

   ```bash
   ./collect.sh --label "720p_SR_on"
   ```

   macOS 上可用系统自带 `caffeinate` 防止测试期间电脑空闲休眠：

   ```bash
   caffeinate -i ./collect.sh --label "720p_SR_on"
   ```

   多设备示例：

   ```bash
   ./collect.sh --serial 34061FDH2007C2 --label "1080p_SR_on"
   ```

   无线连接下比较同一设备的超分开关（将标识替换为当前 `adb devices -l` 中的值）：

   ```bash
   TEST_DEVICE='adb-34061FDH2007C2-aAckDr._adb-tls-connect._tcp'
   # 人工关闭超分，稳定后采集第一轮
   ./collect.sh --serial "$TEST_DEVICE" --label "Pixel7_120p_SR_off"
   # 人工开启超分，恢复接近的温度并稳定后采集第二轮
   ./collect.sh --serial "$TEST_DEVICE" --label "Pixel7_120p_SR_on"
   ```

   自定义结果父目录（路径有空格时需引号）：

   ```bash
   ./collect.sh --label "720p_SR_on" --output "./results/720p"
   ```

4. 看到“开始 300s 采集”后保持会议条件不变，等待自动结束。不要切换分辨率、改变超分状态、锁屏、连接充电电源或让电脑休眠。工具只采集指标；分辨率、超分和会议状态由操作者确认，`--label` 只记录场景。
5. 正常结束时会打印平均值和结果目录，退出码为 `0`。每轮新建 `run_年月日_时分秒_微秒/`，默认在脚本所在目录，不覆盖已有数据。从其他工作目录调用入口也适用。

快速检查部署可先短测：

```bash
./collect.sh --duration 20 --label "smoke_only"
```

短测时长必须是 10 的正整数倍，只用于检查工具，不能代替正式 5 分钟测试。正式测试省略 `--duration` 即可。

## 3. 采样时序与计算口径

CPU 和内存各自使用独立工作线程，以同一个单调时钟起点安排时间点，不采用“执行命令后再 sleep 5 秒”的累加方式。共享 ADB 连接/设备仍可能产生少量响应延迟，CSV 会记录实际发起时间和命令耗时。

| 数据 | 默认时间点（秒） | 计入平均值的样本数 |
| --- | --- | ---: |
| CPU 基线 | 0 附近 | 不计入 |
| App / 总 CPU | 5、10、15、…、300 | 60 |
| App / 总内存 | 0、10、20、…、290 | 30 |
| 剩余电荷量 | 窗口开始前、最后一次 CPU 命令完成后 | 仅两条，不计算平均电流 |

预检查和设备信息读取在 300 秒窗口之前。最后一个 CPU 快照在第 300 秒发起，命令完成和写汇总会稍晚，因此终端总耗时略超过 5 分钟。CPU 命令超时为 4 秒，内存命令超时为 8 秒；采样发起时间迟到超过 1 秒会失败，不补采、不跳过后伪装成功。系统时钟大幅变化或休眠造成的时钟不连续也会停止测试。

### CPU

每次 CPU 快照保存 `/proc/PID/stat`、`/proc/stat`、`/proc/uptime` 和完整 `top -b -n 1`。计算使用相邻快照的累计计数器差值，覆盖约 5 秒的真实区间；**不将反复启动 `top` 的首帧 `%CPU` 用于平均值**。

- App CPU 时间：主进程 `utime + stime`，包含其线程，不包含子进程 CPU 时间。
- 总 CPU 时间：`user + nice + system + idle + iowait + irq + softirq + steal`。`guest/guest_nice` 已计入前项，不重复相加。
- App CPU 使用率（整机）：`ΔApp CPU 时间 / Δ总 CPU 时间 × 100%`，范围 0–100%。
- 总 CPU 使用率：`[1 − Δ(idle + iowait) / Δ总 CPU 时间] × 100%`。此处将 I/O 等待视为空闲。
- 附加输出 App CPU 单核口径：`ΔApp ticks / (Δuptime × CLK_TCK) × 100%`，可超过 100%。例如 8 核均在线时，持续占满一个核约对应单核 100%、整机 12.5%。热插拔或离线核心会影响两种口径的对应关系。

主指标采用整机口径以便与总 CPU 直接比较；与 Android `top`、历史记录比较时，应确认对方是否使用单核口径。CPU 指标反映 CPU 执行时间占比，不代表频率、功耗或 GPU/NPU 使用率。

ADB、`top` 和 `dumpsys` 本身有采集开销，整机指标也包含这部分开销。比较不同分辨率或超分开关时，应使用相同工具和采样配置。

### 内存

每 10 秒读取 `/proc/meminfo` 和 `dumpsys meminfo PID`：

| 指标 | 公式 | 单位 |
| --- | --- | --- |
| App 内存使用率 | `TOTAL PSS / MemTotal × 100%` | % |
| 总内存使用率 | `(MemTotal − MemAvailable) / MemTotal × 100%` | % |
| 附加输出 App PSS | `TOTAL PSS / 1024` | MiB |

PSS 按比例分摊共享页，适合衡量 App 实际内存负担。它与 `top` 的 RSS / `%MEM`、Java Heap 或虚拟内存不同。总内存使用率扣除系统估计的可用内存，包含可回收缓存的可用部分；不采用 `MemTotal − MemFree`，因此可能与旧 `top` 头部显示的 used 不同。分母为系统可见物理内存 `MemTotal`，不含 Swap，不是商品标称 RAM。

**进程范围：**名称恰好等于 `com.juphoon.rtc.room` 的主进程。不合并 `:service` 等独立进程；摄像头服务、SurfaceFlinger 等属于总 CPU/总内存。检测到 PID 或进程启动时间变化会停止测试，避免将重启前后的场景混合。

**最终平均：**分别对 60 条 CPU 和 30 条内存有效样本作算术平均。CSV 指标保留 6 位小数，JSON 保存这些 CSV 值的平均数，Markdown 显示 4 位小数。失败样本不填 0、不进入分母；任何错误都会使整轮结果标记为不完整，保留的均值仅用于排查。

### 电荷量消耗

仅读取 `/sys/class/power_supply/battery/charge_counter` 的起始值与结束值，不采集电流、不做电流积分；同时保存这两个端点的放电状态和设备 uptime。

```text
消耗电荷量（uAh）= 起始 charge_counter − 结束 charge_counter
消耗电荷量（mAh）= 消耗电荷量（uAh）/ 1000
超分额外消耗（mAh）= 开启超分一轮的消耗 − 关闭超分一轮的消耗
```

这是整机电量计报告的剩余电荷量变化，不是电池百分比，也不是单独归属于 App 的电量。只比较同一设备、相同条件和时长的开关两轮，建议重复测试。电量计存在刷新间隔、量化和校正，短测差值为 0 不代表不耗电。2026-10-22 探测发现一加 Ace 5 至尊版的电流积分与 `charge_counter` 变化不一致，因此其差值应表述为“设备电量计报告的电荷量消耗”，不能据此保证仪器级准确性。

起始读数在 CPU 基线后、CPU/内存窗口前；结束读数在最后一个 CPU 快照完成后。因此电荷量覆盖窗口通常略长于配置的 300 秒，`battery_charge.interval_seconds` 单独记录设备 uptime 差值。原有 `elapsed_seconds` 仍表示 CPU/内存窗口经过时间，不包含末次电荷量读取耗时。

成功一轮的 `charge_samples.csv` 恰好两条（`start`、`end`），`summary.json` 的 `battery_charge` 保存起止完整记录、`consumed_uah`、`consumed_mah`、实际观测间隔和有效性。`schema_version` 为 `2`。原始单位按这两台已探测设备的 uAh 保存并换算 mAh，不静默套用电流字段的厂商单位。

起止电量读取各限时 4 秒。缺失、权限错误、端点不是 `Discharging`、结束电量高于起始值时，整轮标记 `incomplete`；保留原始数据和已有 CPU/内存结果。负差值原样保留，不取绝对值，也不填 0。只读端点无法发现所有中途充电行为，操作者必须确保全程断开电源。

## 4. 输出文件

| 文件 | 内容 |
| --- | --- |
| `summary.md` | 直接阅读的四项平均使用率、附加指标、起止电荷量/消耗量和完整性状态 |
| `summary.json` | 结构化汇总、设备、场景、时长、样本数、`battery_charge`、错误原因 |
| `cpu_samples.csv` | 每次 CPU 指标、PID、实际区间和主机采样时间 |
| `memory_samples.csv` | 每次 PSS、物理内存计数器、使用率和主机采样时间 |
| `charge_samples.csv` | 起始、结束两条电荷量记录，含 uAh/mAh、放电状态、设备 uptime、时间戳及命令耗时 |
| `charge.log` | 两次电荷量命令的原始输出；异常时保留 stderr 和错误原因 |
| `cpu_baseline.log` | CPU 差值计算的初始计数器 |
| `cpu_top.log` | 每 5 秒的 CPU 计数器和完整进程 `top` 快照 |
| `memory.log` | 每 10 秒的系统内存和目标进程完整内存快照 |
| `device.txt` | 序列号、型号、Android/SDK 版本、系统指纹和时钟频率 |

CSV 使用 UTF-8 BOM，可用 Excel 打开。时间带本机时区偏移。`scheduled_elapsed_s` 是计划时间点，`actual_elapsed_s` 是 ADB 命令实际发起时间，`command_seconds` 是命令耗时；CPU 的 `interval_seconds` 是设备 uptime 差值。

## 5. 验收操作

### 工具自动化测试（无需连接手机）

```bash
python3 -m unittest discover -s tests -v
```

测试通过独立假 ADB 验证完整入口、路径含空格、计算口径、样本时间表、USB/无线设备选择、起止电荷量、负差值/充电识别、错误输出、断连、进程重启、命令超时、Ctrl+C 保存和汇总校验。测试不会操作真机。

### 真机结果验收

完成上述正式测试后，将实际结果目录替换到命令中：

```bash
python3 verify_results.py ./run_实际时间戳
```

校验器会独立读取 CSV 和 JSON，要求：

1. `status` 为 `complete`，时长 300 秒，`formal_five_minute_run` 为 `true`，无错误和中断。
2. CPU 正好 60 条，内存正好 30 条，计划时间点完整、无重复，实际发起延迟和命令耗时在允许范围内。
3. 整轮 PID 一致，原始快照数量与 CSV 一致，必需文件齐全。
4. 内存公式、各项均值、有效样本数与 CSV 重新计算结果一致；整机百分比在 0–100%。
5. 新版结果恰好包含两条自然放电电荷量记录，起止值、单位换算、差值、时间间隔与原始日志和汇总一致。历史无电荷量字段的旧版结果仍按原规则校验。

打印“验收通过”且退出码为 `0` 才表示采集数据通过工具校验。场景是否符合分辨率/超分要求，仍需人工确认。本需求未给出 CPU/内存性能阈值，工具不据此判定产品性能合格与否。

短测结果仅作部署核验时：

```bash
python3 verify_results.py ./run_短测时间戳 --allow-short
```

### 人工交叉检查与异常验收

- 打开 `summary.md` 核对设备和场景，在 CSV 中对四项指标分别求平均，与汇总值对比；注意 % 列中的数值已是百分数，不要再次乘以 100。
- 抽查相邻 CPU 原始计数器，按上面的差值公式计算；抽查内存日志的 `TOTAL PSS`、`MemTotal`、`MemAvailable` 与 CSV。可直接使用 `python3 verify_results.py` 校验所有均值。
- 新开一轮短测后按 Ctrl+C：停止 CPU/内存采样并保存已有数据，尝试读取结束电荷量（最多额外 4 秒），状态为 `incomplete`，退出码 `130`，验收脚本应拒绝。中断结果不能冒充完整 5 分钟消耗量。
- 在单独的异常测试轮中断开 ADB 连接或关闭 App：应报错、保留日志、标记 `incomplete`，退出码 `1`。恢复连接/会议后重新运行；不恢复续采。
- 无设备、未授权、多设备未指定序列号、权限不足、超时、迟到等均不能生成有效验收结果。

## 6. 常见问题

- **找不到 adb / python3：**按部署步骤安装并检查 PATH；`--adb` 可指定含空格的路径。
- **无法读取 `/proc` 或没有 `MemAvailable` / PSS：**查看输出目录日志，确认手机系统支持接口和调试权限；不会自动用 RSS、空值或 0 替代。
- **App 不在运行 / 采集中重启：**先重新进入会议并恢复分辨率、超分状态，再新开一轮。
- **超时 / 采样迟到：**检查 USB 线/无线网络、电脑负载和设备响应，关闭其他 profiler，保持电脑唤醒后重试。
- **内存日志与 CPU `top` 的 `%MEM` 不同：**PSS 与 RSS 口径不同，属于预期现象。

`run_20261020_113227/` 是保留的旧版采集数据；它没有完整时间表和新汇总文件，不能用本验收脚本作正式验收。`validation/` 中的开发验证数据也不代表人工确认过的某个分辨率/超分测试场景。
