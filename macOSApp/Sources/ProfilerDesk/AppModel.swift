import AppKit
import Combine
import Foundation
import ProfilerCore

@MainActor
final class AppModel: ObservableObject {
    @Published var devices: [AndroidDevice] = []
    @Published var isRefreshing = false
    @Published var discoveryMessage = "正在查找已连接的设备…"
    @Published var lastRefreshed: Date?
    @Published var selectedDeviceID: String? {
        didSet {
            defaults.set(selectedDeviceID, forKey: "selectedDevice")
            updateScenarioDeviceName()
            updateGeneratedCommand()
        }
    }
    @Published var projectDirectory: String {
        didSet { defaults.set(projectDirectory, forKey: "projectDirectory"); updateGeneratedCommand(force: true) }
    }
    @Published var outputDirectory: String {
        didSet { defaults.set(outputDirectory, forKey: "outputDirectory"); updateGeneratedCommand() }
    }
    @Published var preparation: String {
        didSet { defaults.set(preparation, forKey: "preparation") }
    }
    @Published var label = "设备名_120p_SR_off" { didSet { updateGeneratedCommand() } }
    @Published var duration = 300 { didSet { updateGeneratedCommand() } }
    @Published var command = ""
    @Published var logText = ""
    @Published var isRunning = false
    @Published var isStopping = false
    @Published var executionLabel = "等待执行"
    @Published var exitCode: Int?
    @Published var jobDirectory: URL?
    @Published var resultDirectory: URL?
    @Published var errorMessage: String?

    private let defaults = UserDefaults.standard
    private let home = FileManager.default.homeDirectoryForCurrentUser
    private var adb: String?
    private var python: String?
    private var generatedCommand = ""
    private var labelDeviceName = "设备名"
    private var names: [String: String] = [:]
    private var monitor: Task<Void, Never>?
    private var logTail = LogTail()
    private var launchDate = Date()
    private var childPID: Int32?
    private var started = false

    private static let sceneNamingInstruction = "2. 人工设置超分开关，场景名称使用「设备名_对端画面规格_SR_off」或「设备名_对端画面规格_SR_on」，例如 Pixel7_120p_SR_off / Pixel7_120p_SR_on。设备名由所选设备自动填入（去掉空格），对端画面规格与 SR 状态需按实际场景填写；命令中的 --label 会同步更新，但不会替你设置设备上的超分开关。"

    static let defaultPreparation = """
    测试目标
    在同一台设备、同一会议场景和相同时长下，对比关闭超分与开启超分的 CPU、内存和电荷量消耗。

    场景设置
    1. 入会并选择本轮对端分辨率（例如 120P 或 240P），使用固定视频内容与帧率。
    \(sceneNamingInstruction)
    3. 固定摄像头、麦克风、扬声器音量、网络及屏幕页面。
    4. 关闭自动亮度，固定亮度；两轮初始温度、电池电量尽量接近。
    5. 测量电荷量时使用无线 ADB，全程断开 USB 和无线充电电源。

    测试步骤
    1. 设置场景，稳定约 2 分钟后开始测试。
    2. 默认采集 300 秒；期间不改变超分、分辨率、亮度或会议状态，不锁屏。
    3. 关闭超分与开启超分分别执行一轮，每种分辨率建议重复至少 3 对，交替顺序。

    结果核对
    CPU 每 5 秒、内存每 10 秒采样；电荷量仅记录起止两次，消耗量 = 起始 − 结束。
    查看 summary.md，并用 verify_results.py 校验结果。
    超分额外电荷量消耗 = 开启超分消耗 − 关闭超分消耗。
    电荷量为设备电量计估算；差值为 0 不代表没有耗电，起止观测间隔以报告为准。
    """

    init() {
        let developmentRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let configured = Bundle.main.object(forInfoDictionaryKey: "ProfilerProjectPath") as? String
        let candidates: [String?] = [defaults.string(forKey: "projectDirectory"), configured,
                          developmentRoot.path, Bundle.main.resourceURL?.appendingPathComponent("Collector").path]
        let root = candidates.compactMap { $0 }.first {
            FileManager.default.fileExists(atPath: URL(fileURLWithPath: $0).appendingPathComponent("collect.sh").path)
        } ?? developmentRoot.path
        projectDirectory = root
        let bundled = root == Bundle.main.resourceURL?.appendingPathComponent("Collector").path
        outputDirectory = defaults.string(forKey: "outputDirectory") ??
            (bundled ? home.appendingPathComponent("Documents/ProfilerDesk").path : URL(fileURLWithPath: root).appendingPathComponent("results").path)
        preparation = (defaults.string(forKey: "preparation") ?? Self.defaultPreparation)
            .replacingOccurrences(of: "2. 人工设置超分开关，以场景名称记录，例如 120p_SR_off / 120p_SR_on。",
                                  with: Self.sceneNamingInstruction)
        selectedDeviceID = defaults.string(forKey: "selectedDevice")
        adb = ExecutableLocator.find("adb")
        python = ExecutableLocator.find("python3")
        updateGeneratedCommand(force: true)
    }

    var selectedDevice: AndroidDevice? { devices.first { $0.id == selectedDeviceID } }
    var onlineCount: Int { devices.filter(\.isAvailable).count }
    var isCustomCommand: Bool { command != generatedCommand }
    var canExecute: Bool { !isRunning && !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    func bootstrap() async {
        if !started { restorePreviousJob() }
        await refreshDevices()
        while !Task.isCancelled {
            do { try await Task.sleep(nanoseconds: 5_000_000_000) } catch { return }
            await refreshDevices()
        }
    }

    private func restorePreviousJob() {
        started = true
        if let previous = defaults.string(forKey: "lastJob"),
           FileManager.default.fileExists(atPath: previous) {
            jobDirectory = URL(fileURLWithPath: previous)
            logTail = LogTail()
            drainLog(final: false)
            if let status = readStatus(), status.state == "running", let pid = status.pid, kill(pid, 0) == 0 {
                if let data = try? Data(contentsOf: URL(fileURLWithPath: previous).appendingPathComponent("job.json")),
                   let job = try? JSONDecoder().decode(TerminalJob.self, from: data) { command = job.command }
                isRunning = true
                childPID = pid
                executionLabel = "测试执行中"
                beginMonitoring()
            } else {
                drainLog(final: true)
                exitCode = readStatus()?.exitCode
                executionLabel = exitCode.map { $0 == 0 ? "上次执行完成" : "上次执行结束 · \($0)" } ?? "上次执行记录"
            }
        }
    }

    func refreshDevices() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        adb = ExecutableLocator.find("adb")
        python = ExecutableLocator.find("python3")
        guard let adb else {
            discoveryMessage = "未找到 ADB，请安装 Android Platform-Tools，或放入默认 Android SDK 目录。"
            devices = []
            return
        }
        do {
            let result = try await Task.detached {
                try ProcessRunner.run(executable: adb, arguments: ["devices", "-l"])
            }.value
            guard result.exitCode == 0 else {
                devices = []
                discoveryMessage = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
                return
            }
            var discovered = AndroidDevice.parse(result.output)
            for index in discovered.indices {
                let device = discovered[index]
                if let name = names[device.id] { discovered[index].name = name }
                else if device.isAvailable && !isRunning {
                    let details = try? await Task.detached {
                        try ProcessRunner.run(executable: adb, arguments: ["-s", device.id, "shell",
                            "getprop ro.vendor.oplus.market.name; getprop ro.product.model"], timeout: 3)
                    }.value
                    if let details, details.exitCode == 0,
                       let name = details.output.split(whereSeparator: \.isNewline).first.map(String.init), !name.isEmpty {
                        names[device.id] = name
                        discovered[index].name = name
                    }
                }
            }
            devices = discovered
            if !isRunning && !discovered.contains(where: { $0.id == selectedDeviceID && $0.isAvailable }) {
                selectedDeviceID = discovered.first(where: \.isAvailable)?.id
            }
            lastRefreshed = Date()
            discoveryMessage = discovered.isEmpty ? "尚未发现设备。连接 USB 或完成无线 ADB 配对后刷新。" : ""
            updateScenarioDeviceName()
            updateGeneratedCommand()
        } catch {
            devices = []
            discoveryMessage = "读取设备失败：\(error.localizedDescription)"
        }
    }

    private func updateScenarioDeviceName() {
        guard !isRunning, let device = selectedDevice else { return }
        let name = device.name.components(separatedBy: .whitespacesAndNewlines).joined()
        guard !name.isEmpty else { return }
        let prefix = labelDeviceName + "_"
        if label.hasPrefix(prefix) {
            // Keep the chosen picture specification and SR state when switching devices.
            label = name + "_" + label.dropFirst(prefix.count)
        }
        labelDeviceName = name
    }

    func updateGeneratedCommand(force: Bool = false) {
        guard !isRunning else { return }
        let wasGenerated = command.isEmpty || command == generatedCommand
        generatedCommand = ShellCommand.collection(
            script: URL(fileURLWithPath: projectDirectory).appendingPathComponent("collect.sh").path,
            adb: adb ?? "adb", serial: selectedDeviceID ?? "请先选择设备",
            label: label, duration: duration, output: outputDirectory)
        if force || wasGenerated { command = generatedCommand }
    }

    func chooseProjectDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "选择包含 collect.sh 和 collector.py 的目录"
        panel.directoryURL = URL(fileURLWithPath: projectDirectory)
        if panel.runModal() == .OK, let url = panel.url {
            guard FileManager.default.fileExists(atPath: url.appendingPathComponent("collect.sh").path) else {
                errorMessage = "该目录中没有 collect.sh，请选择采集工具所在目录。"
                return
            }
            projectDirectory = url.path
        }
    }

    func chooseOutputDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.message = "选择测试结果的保存目录"
        if panel.runModal() == .OK, let url = panel.url { outputDirectory = url.path }
    }

    func execute() {
        guard canExecute else { return }
        if !isCustomCommand && selectedDevice?.isAvailable != true {
            errorMessage = "请先选择一台已连接、已授权的设备。"
            return
        }
        guard let python = ExecutableLocator.find("python3") else {
            errorMessage = "未找到 Python 3，请先安装 Python 3.9 或更高版本。"
            return
        }
        let bundledRunner = Bundle.main.resourceURL?.appendingPathComponent("terminal_runner.py")
        let runner = [bundledRunner, URL(fileURLWithPath: projectDirectory).appendingPathComponent("macOSApp/Resources/terminal_runner.py")]
            .compactMap { $0 }.first { FileManager.default.fileExists(atPath: $0.path) }
        guard let runner else { errorMessage = "缺少终端运行组件，请重新构建 App。"; return }
        do {
            let base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                   appropriateFor: nil, create: true)
            let directory = base.appendingPathComponent("ProfilerDesk/Runs/\(UUID().uuidString)")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let job = TerminalJob(command: command, workingDirectory: home.path,
                                  logPath: directory.appendingPathComponent("terminal.log").path,
                                  statusPath: directory.appendingPathComponent("status.json").path,
                                  lockPath: directory.appendingPathComponent("started.lock").path,
                                  environment: ExecutableLocator.environment(adb: adb, python: python),
                                  preparation: preparation)
            let jobFile = directory.appendingPathComponent("job.json")
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(job).write(to: jobFile, options: .atomic)
            try preparation.write(to: directory.appendingPathComponent("scenario.txt"), atomically: true, encoding: .utf8)
            let launcher = directory.appendingPathComponent("执行测试.command")
            try TerminalJob.launcher(python: python, runner: runner.path, job: jobFile.path, home: home.path)
                .write(to: launcher, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: launcher.path)
            jobDirectory = directory
            defaults.set(directory.path, forKey: "lastJob")
            logTail = LogTail()
            logText = ""
            exitCode = nil
            childPID = nil
            resultDirectory = nil
            launchDate = Date()
            executionLabel = "正在打开 Terminal…"
            isRunning = true
            isStopping = false
            beginMonitoring()
            let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
            NSWorkspace.shared.open([launcher], withApplicationAt: terminal,
                                    configuration: NSWorkspace.OpenConfiguration()) { [weak self] _, error in
                if let error {
                    Task { @MainActor in
                        self?.errorMessage = "无法打开 Terminal：\(error.localizedDescription)"
                        self?.cancelUnstartedJob()
                        self?.isRunning = false
                        self?.executionLabel = "终端启动失败"
                    }
                }
            }
        } catch {
            errorMessage = "创建测试任务失败：\(error.localizedDescription)"
        }
    }

    func stop() {
        guard isRunning else { return }
        guard let pid = childPID else {
            cancelUnstartedJob()
            isStopping = true
            executionLabel = "正在取消启动…"
            return
        }
        if kill(-pid, SIGINT) == 0 {
            isStopping = true
            executionLabel = "正在停止并保存数据…"
        } else if errno != ESRCH {
            errorMessage = "无法停止任务：\(String(cString: strerror(errno)))"
        }
    }

    func clearDisplay() { logText = "" }

    func revealResults() {
        if let resultDirectory { NSWorkspace.shared.open(resultDirectory) }
        else { NSWorkspace.shared.open(URL(fileURLWithPath: outputDirectory)) }
    }

    func revealLog() {
        guard let jobDirectory else { return }
        NSWorkspace.shared.activateFileViewerSelecting([jobDirectory.appendingPathComponent("terminal.log")])
    }

    private func readStatus() -> JobStatus? {
        guard let jobDirectory,
              let data = try? Data(contentsOf: jobDirectory.appendingPathComponent("status.json")) else { return nil }
        return try? JSONDecoder().decode(JobStatus.self, from: data)
    }

    private func cancelUnstartedJob() {
        guard let jobDirectory else { return }
        do {
            try Data().write(to: jobDirectory.appendingPathComponent("cancel.requested"), options: .atomic)
            let state = try JSONSerialization.data(withJSONObject: ["state": "finished", "exitCode": 130])
            try state.write(to: jobDirectory.appendingPathComponent("status.json"), options: .atomic)
        } catch { errorMessage = "无法取消启动：\(error.localizedDescription)" }
    }

    private func drainLog(final: Bool) {
        guard let jobDirectory else { return }
        do {
            repeat {
                let added = try logTail.read(jobDirectory.appendingPathComponent("terminal.log"), final: final)
                if added.isEmpty { break }
                logText += added
                if !final { break }
            } while true
            for line in logText.components(separatedBy: .newlines).reversed() where line.hasPrefix("输出目录：") {
                let path = String(line.dropFirst("输出目录：".count)).trimmingCharacters(in: .whitespaces)
                if FileManager.default.fileExists(atPath: path) { resultDirectory = URL(fileURLWithPath: path) }
                break
            }
        } catch { executionLabel = "日志读取失败：\(error.localizedDescription)" }
    }

    private func beginMonitoring() {
        monitor?.cancel()
        monitor = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled && self.isRunning {
                let status = self.readStatus()
                self.drainLog(final: status?.state == "finished")
                if let status {
                    if status.state == "finished" {
                        self.exitCode = status.exitCode
                        self.isRunning = false
                        self.isStopping = false
                        self.childPID = nil
                        self.executionLabel = status.exitCode == 0 ? "执行完成" :
                            (status.exitCode == 130 ? "已停止 · 数据已保留" : "执行结束 · 退出码 \(status.exitCode ?? 1)")
                        break
                    }
                    self.childPID = status.pid
                    if !self.isStopping { self.executionLabel = "测试执行中" }
                    if let pid = status.pid, kill(pid, 0) != 0 && errno == ESRCH {
                        // Give the runner time to flush the final log and atomic status file.
                        if Date().timeIntervalSince(self.launchDate) > 2 {
                            try? await Task.sleep(nanoseconds: 500_000_000)
                            if self.readStatus()?.state != "finished" {
                                self.drainLog(final: true)
                                self.isRunning = false
                                self.executionLabel = "进程已退出，请查看终端日志"
                            }
                        }
                    }
                } else if Date().timeIntervalSince(self.launchDate) > 30 {
                    self.cancelUnstartedJob()
                    self.isRunning = false
                    self.executionLabel = "终端未启动任务"
                    self.errorMessage = "Terminal 尚未启动任务。请检查终端窗口中的提示，或重新点击执行。"
                }
                do { try await Task.sleep(nanoseconds: 300_000_000) } catch { return }
            }
        }
    }
}
