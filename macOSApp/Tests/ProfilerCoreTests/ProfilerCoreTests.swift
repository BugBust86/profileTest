import Foundation
import XCTest
@testable import ProfilerCore

final class ProfilerCoreTests: XCTestCase {
    func testDeviceDiscoveryKeepsTransportAndAuthorization() {
        let output = """
        * daemon started successfully
        List of devices attached
        ABC device usb:123 product:test model:Pixel_7 device:panther transport_id:1
        adb-XYZ-a._adb-tls-connect._tcp device product:test model:PLC110 transport_id:2
        192.168.1.2:5555 unauthorized transport_id:3
        emulator-5554 device model:sdk_gphone
        OFFLINE offline
        """
        let devices = AndroidDevice.parse(output)
        XCTAssertEqual(devices.count, 5)
        XCTAssertEqual(devices[0].name, "Pixel 7")
        XCTAssertEqual(devices[0].connection, .usb)
        XCTAssertEqual(devices[1].connection, .wireless)
        XCTAssertEqual(devices[2].connection, .wireless)
        XCTAssertFalse(devices[2].isAvailable)
        XCTAssertFalse(devices[3].isAvailable)
        XCTAssertEqual(devices[4].stateLabel, "离线")
    }

    func testShellQuotingPreservesSpacesQuotesAndSubstitutions() throws {
        let value = "会议 '测试' $(printf WRONG) `printf WRONG`\n第二行"
        let result = try ProcessRunner.run(executable: "/bin/zsh", arguments: ["-c", "printf '%s' " + ShellCommand.quote(value)])
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertEqual(result.output, value)
        let command = ShellCommand.collection(script: "/a b/collect.sh", adb: "/sdk/adb", serial: "host:5555",
                                              label: value, duration: 300, output: "/a b/results")
        XCTAssertTrue(command.contains("'/a b/collect.sh'"))
        XCTAssertTrue(command.contains("'host:5555'"))
    }

    func testLogDecoderRetainsSplitChineseCharacters() {
        let data = Data("设备：Pixel 7\n".utf8)
        var decoder = LogDecoder()
        var actual = ""
        for byte in data { actual += decoder.append(Data([byte])) }
        actual += decoder.append(Data(), final: true)
        XCTAssertEqual(actual, "设备：Pixel 7\n")
        XCTAssertEqual(decoder.append(Data([0xFF, 0x41]), final: true), "�A")
    }

    func testLogTailReadsOnlyNewBytes() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("第一行\n".utf8).write(to: url)
        var tail = LogTail()
        XCTAssertEqual(try tail.read(url), "第一行\n")
        XCTAssertEqual(try tail.read(url), "")
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data("第二行\n".utf8))
        try handle.close()
        XCTAssertEqual(try tail.read(url, final: true), "第二行\n")
    }

    func testLauncherQuotesPathsAndStartsInHome() {
        let script = TerminalJob.launcher(python: "/python path/python3", runner: "/a'b/runner.py",
                                          job: "/tmp/job file.json", home: "/Users/test user")
        XCTAssertTrue(script.contains("cd -- '/Users/test user'"))
        XCTAssertTrue(script.contains("'/a'\"'\"'b/runner.py'"))
        XCTAssertTrue(script.contains("'/tmp/job file.json'"))
    }
}
