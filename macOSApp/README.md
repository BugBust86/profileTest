# 性能测试工作台（macOS）

原生 SwiftUI / AppKit 应用，macOS 13+。沿用仓库中的 `collect.sh`、`collector.py`，支持 USB 和无线 ADB。

## 构建和打开

在仓库根目录执行：

```bash
./build_app.sh --open
```

产物位于 `dist/ProfilerDesk.app`，可以直接双击，或拖到“应用程序”。构建需要 Xcode 或 Swift 开发工具；运行需要 Python 3.9+ 和 Android Platform-Tools。App 会从 PATH、Homebrew 和 `~/Library/Android/sdk/platform-tools/adb` 查找工具。

App 默认使用当前仓库的脚本。移动到其他电脑时，若原目录不存在，会使用 App 内附的采集脚本。可以在首页“脚本目录”更改来源；修改采集脚本后重新构建，可更新 App 中的副本。此构建采用本机临时签名，未做 Developer ID 公证。

## 首页操作

1. **连接设备**：每 5 秒刷新列表，显示设备名称、USB / 无线连接、授权状态。点击卡片选择测试设备；离线、未授权和模拟器不可选择。首次无线连接仍需自行完成 ADB 配对。
2. **场景准备**：点击“详情与编辑”查看全部文本，支持修改、保存、恢复默认。文本保存在当前 macOS 用户的本机设置中。每次执行都会另存一份当时的 `scenario.txt`。
3. **执行命令**：场景名称默认采用 `设备名_对端画面规格_SR_off/on`，例如 `Pixel7_120p_SR_off`、`Pixel7_120p_SR_on`。选择设备后自动填入设备名（去掉空格），切换设备时保留画面规格和 SR 状态；也可以自定义完整名称。按实际场景修改 `120p` 和 `off/on`，命令中的 `--label` 同步更新。标签仅作记录，画面规格与超分开关仍需在设备上人工设置。选择时长后自动生成含完整脚本路径与设备标识的命令，也可直接编辑。手动修改命令后，参数刷新不会覆盖自定义命令；点击“恢复生成命令”恢复。默认结果目录为仓库下的 `results/`，可以更改。
4. **执行测试**：打开系统 Terminal，在用户主目录 `~/` 下执行界面中的命令。命令中使用绝对路径，因此不依赖 Terminal 当前目录。每次只执行一次；不会在 App 中再启动第二份采集。
5. **终端日志**：同一任务的 stdout / stderr 同时出现在 Terminal 和 App。新增内容不会自动滚动或改变选区，可手动上下、左右滚动并复制。只有主动点击“到最新”才跳到末尾。“清空显示”只清空界面，不删除完整日志。
6. **停止测试**：向任务进程组发送 SIGINT，现有采集器可保存 CPU、内存和结束电荷量。测试是否完整以采集器汇总为准。

场景、超分开关和会议页面仍由操作者准备。正式电荷量比较请全程断开充电电源，使用无线 ADB。

## 文件与记录

- 正式采集结果：界面“结果目录”下的 `run_.../`，内容与命令行相同；点击“打开结果”查看。
- App 执行记录：`~/Library/Application Support/ProfilerDesk/Runs/<任务ID>/`。
- 每条执行记录包含 `job.json`（命令及运行配置）、`scenario.txt`、`terminal.log`、`status.json`、启动用的 `.command` 文件。
- 场景文本、项目目录、结果目录和上次任务路径使用 `UserDefaults` 保存；App 重启后会恢复上次日志，运行中的任务继续显示。
- App 退出不会主动终止系统 Terminal 中的任务；重新打开后可继续查看日志。

## 源码

```text
Package.swift
Sources/ProfilerCore/ProfilerCore.swift   # 设备解析、命令转义、日志增量读取、工具定位
Sources/ProfilerDesk/AppModel.swift      # 设备刷新、设置保存、任务执行与状态同步
Sources/ProfilerDesk/ContentView.swift   # 首页和场景编辑窗口
Sources/ProfilerDesk/LogTextView.swift   # 不自动滚动的原生日志视图
Sources/ProfilerDesk/ProfilerDeskApp.swift
Resources/terminal_runner.py            # Terminal 中运行的任务，日志同步和信号转发
Resources/GenerateIcon.swift            # App 图标生成
Tests/                                 # 核心逻辑与终端运行组件测试
```

## 验证

```bash
swift test --package-path macOSApp -c release
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s macOSApp/Tests -p 'test_*.py' -v
```
