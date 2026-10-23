import Foundation
import Darwin

public enum DeviceConnection: String, Sendable {
    case usb = "USB"
    case wireless = "无线"
    case emulator = "模拟器"
    case unknown = "未知连接"
}

public struct AndroidDevice: Identifiable, Equatable, Sendable {
    public let id: String
    public var name: String
    public let state: String
    public let connection: DeviceConnection
    public var isAvailable: Bool { state == "device" && connection != .emulator }
    public var stateLabel: String {
        switch state {
        case "device": return "已连接"
        case "unauthorized": return "待授权"
        case "offline": return "离线"
        default: return state
        }
    }

    public init(id: String, name: String, state: String, connection: DeviceConnection) {
        self.id = id
        self.name = name
        self.state = state
        self.connection = connection
    }

    public static func parse(_ output: String) -> [AndroidDevice] {
        output.split(whereSeparator: \.isNewline).compactMap { line in
            let fields = line.split(whereSeparator: \.isWhitespace).map(String.init)
            guard fields.count >= 2,
                  ["device", "offline", "unauthorized", "recovery", "sideload"].contains(fields[1]) else { return nil }
            let serial = fields[0]
            let model = fields.first(where: { $0.hasPrefix("model:") })
                .map { String($0.dropFirst(6)).replacingOccurrences(of: "_", with: " ") } ?? serial
            let connection: DeviceConnection
            if serial.hasPrefix("emulator-") { connection = .emulator }
            else if fields.contains(where: { $0.hasPrefix("usb:") }) { connection = .usb }
            else if serial.contains("_adb-tls-connect._tcp") || serial.contains(":") { connection = .wireless }
            else { connection = .unknown }
            return AndroidDevice(id: serial, name: model, state: fields[1], connection: connection)
        }
    }
}

public enum ShellCommand {
    public static func quote(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
    }

    public static func collection(script: String, adb: String, serial: String,
                                  label: String, duration: Int, output: String) -> String {
        [quote(script), "--adb", quote(adb), "--serial", quote(serial),
         "--label", quote(label), "--duration", String(duration), "--output", quote(output)].joined(separator: " ")
    }
}

public struct TerminalJob: Codable, Sendable {
    public let command: String
    public let workingDirectory: String
    public let logPath: String
    public let statusPath: String
    public let lockPath: String
    public let environment: [String: String]
    public let preparation: String

    public init(command: String, workingDirectory: String, logPath: String, statusPath: String,
                lockPath: String, environment: [String: String], preparation: String) {
        self.command = command
        self.workingDirectory = workingDirectory
        self.logPath = logPath
        self.statusPath = statusPath
        self.lockPath = lockPath
        self.environment = environment
        self.preparation = preparation
    }

    public static func launcher(python: String, runner: String, job: String, home: String) -> String {
        """
        #!/bin/bash
        cd -- \(ShellCommand.quote(home)) || exit 1
        exec \(ShellCommand.quote(python)) -u \(ShellCommand.quote(runner)) \(ShellCommand.quote(job))

        """
    }
}

public struct JobStatus: Codable, Sendable {
    public let state: String
    public let pid: Int32?
    public let exitCode: Int?
    public let message: String?
}

/// Keep a partial UTF-8 character until the next disk read; malformed bytes are replaced.
public struct LogDecoder {
    private var pending = Data()
    public init() {}

    public mutating func append(_ data: Data, final: Bool = false) -> String {
        pending.append(data)
        let bytes = Array(pending)
        var end = bytes.count
        if !final, !bytes.isEmpty {
            var start = bytes.count - 1
            while start > 0 && bytes[start] & 0xC0 == 0x80 { start -= 1 }
            let lead = bytes[start]
            let expected = (0xC2...0xDF).contains(lead) ? 2 :
                (0xE0...0xEF).contains(lead) ? 3 : (0xF0...0xF4).contains(lead) ? 4 : 1
            if bytes.count - start < expected { end = start }
        }
        let result = String(decoding: bytes.prefix(end), as: UTF8.self)
        pending = Data(bytes.dropFirst(end))
        return result
    }
}

public struct LogTail {
    private var offset: UInt64 = 0
    private var decoder = LogDecoder()
    public init() {}

    public mutating func read(_ url: URL, final: Bool = false) throws -> String {
        guard FileManager.default.fileExists(atPath: url.path) else { return "" }
        let file = try FileHandle(forReadingFrom: url)
        defer { try? file.close() }
        try file.seek(toOffset: offset)
        let data = try file.read(upToCount: 262_144) ?? Data()
        offset += UInt64(data.count)
        // Drain large logs before flushing a trailing partial character.
        let reachedEnd = data.count < 262_144
        return decoder.append(data, final: final && reachedEnd)
    }
}

public enum ExecutableLocator {
    public static func find(_ name: String, home: URL = FileManager.default.homeDirectoryForCurrentUser) -> String? {
        let environment = ProcessInfo.processInfo.environment
        let folders = (environment["PATH"] ?? "").split(separator: ":").map(String.init) + [
            home.appendingPathComponent("Library/Android/sdk/platform-tools").path,
            "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"
        ]
        return folders.map { URL(fileURLWithPath: $0).appendingPathComponent(name).path }
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    public static func environment(adb: String?, python: String?) -> [String: String] {
        var result: [String: String] = [:]
        let extra = [adb, python].compactMap { $0 }.map { URL(fileURLWithPath: $0).deletingLastPathComponent().path }
        result["PATH"] = (extra + [ProcessInfo.processInfo.environment["PATH"] ?? "", "/opt/homebrew/bin", "/usr/local/bin",
                                    "/usr/bin", "/bin", "/usr/sbin", "/sbin"]).joined(separator: ":")
        result["PYTHONUNBUFFERED"] = "1"
        result["PYTHONDONTWRITEBYTECODE"] = "1"
        return result
    }
}

public struct ProcessResult: Sendable {
    public let output: String
    public let exitCode: Int32
}

public enum ProcessRunner {
    /// Call from a background task. Only used for short ADB discovery commands.
    public static func run(executable: String, arguments: [String], timeout: Double = 5) throws -> ProcessResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        let watchdog = DispatchWorkItem {
            if process.isRunning { process.terminate() }
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) {
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: watchdog)
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        watchdog.cancel()
        return ProcessResult(output: String(decoding: data, as: UTF8.self), exitCode: process.terminationStatus)
    }
}
