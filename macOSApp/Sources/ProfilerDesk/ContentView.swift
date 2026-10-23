import AppKit
import ProfilerCore
import SwiftUI

private let ink = Color(red: 0.11, green: 0.16, blue: 0.24)
private let muted = Color(red: 0.43, green: 0.48, blue: 0.56)
private let accent = Color(red: 0.20, green: 0.35, blue: 0.88)

struct ContentView: View {
    @ObservedObject var model: AppModel
    @State private var showingPreparation = false
    @State private var jumpToLatest = 0

    var body: some View {
        VStack(spacing: 12) {
            header
            devicesPanel
            preparationPanel
            commandPanel
            logPanel
        }
        .padding(.horizontal, 24)
        .padding(.top, 30)
        .padding(.bottom, 20)
        .background(Color(red: 0.955, green: 0.966, blue: 0.984))
        .tint(accent)
        .task { await model.bootstrap() }
        .sheet(isPresented: $showingPreparation) {
            PreparationEditor(text: model.preparation) { model.preparation = $0 }
        }
        .alert("无法完成操作", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("确定") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform.path.ecg")
                .font(.system(size: 23, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(accent.gradient, in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text("性能测试工作台").font(.system(size: 23, weight: .bold)).foregroundStyle(ink)
                Text("Android 会议场景 · CPU / 内存 / 电荷量").font(.system(size: 11)).foregroundStyle(muted)
            }
            Spacer()
            Button { model.chooseProjectDirectory() } label: {
                Label("脚本目录", systemImage: "folder")
            }
            .disabled(model.isRunning)
            .help(model.projectDirectory)
        }
        .padding(.bottom, 4)
    }

    private var devicesPanel: some View {
        Panel {
            VStack(alignment: .leading, spacing: 11) {
                HStack {
                    sectionTitle("连接设备", symbol: "iphone.gen3.radiowaves.left.and.right")
                    Text("\(model.onlineCount) 台在线").font(.system(size: 11)).foregroundStyle(muted)
                    Spacer()
                    if model.isRefreshing { ProgressView().controlSize(.mini).scaleEffect(0.8) }
                    if let date = model.lastRefreshed {
                        Text(date, style: .time).font(.system(size: 10, design: .monospaced)).foregroundStyle(muted)
                    }
                    Button { Task { await model.refreshDevices() } } label: { Label("刷新", systemImage: "arrow.clockwise") }
                        .buttonStyle(.borderless).disabled(model.isRefreshing)
                }
                if model.devices.isEmpty {
                    HStack(spacing: 9) {
                        Image(systemName: "cable.connector").font(.system(size: 22)).foregroundStyle(muted)
                        Text(model.discoveryMessage).font(.system(size: 12)).foregroundStyle(muted)
                        Spacer()
                    }.frame(height: 58)
                } else {
                    GeometryReader { geometry in
                        ScrollView(.horizontal) {
                            HStack(spacing: 12) {
                                ForEach(model.devices) { device in
                                    DeviceCard(device: device, selected: model.selectedDeviceID == device.id) {
                                        model.selectedDeviceID = device.id
                                    }
                                    .frame(width: (geometry.size.width - 12) / 2)
                                    .disabled(model.isRunning || !device.isAvailable)
                                }
                            }
                        }.scrollIndicators(.hidden)
                    }.frame(height: 72)
                    if !model.discoveryMessage.isEmpty {
                        Text(model.discoveryMessage).font(.system(size: 10)).foregroundStyle(.orange)
                    }
                }
            }
        }
    }

    private var preparationPanel: some View {
        Panel {
            HStack(alignment: .center, spacing: 18) {
                VStack(alignment: .leading, spacing: 7) {
                    sectionTitle("场景准备", symbol: "checklist")
                    Text(model.preparation.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                         ? "添加本轮测试的场景说明与准备步骤。" : model.preparation)
                        .font(.system(size: 11)).foregroundStyle(muted).lineLimit(2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Button { showingPreparation = true } label: {
                    Label("详情与编辑", systemImage: "square.and.pencil")
                }
                .accessibilityIdentifier("preparationDetails")
            }
        }
    }

    private var commandPanel: some View {
        Panel {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    sectionTitle("执行命令", symbol: "terminal")
                    Spacer()
                    Text("Terminal · 工作目录 ~/").font(.system(size: 10, design: .monospaced)).foregroundStyle(muted)
                }
                HStack(spacing: 12) {
                    Text("场景名称").foregroundStyle(muted)
                    TextField("例如 Pixel7_120p_SR_off", text: $model.label)
                        .textFieldStyle(.roundedBorder).frame(width: 270)
                        .accessibilityIdentifier("scenarioLabel")
                    Text("时长").foregroundStyle(muted)
                    Picker("时长", selection: $model.duration) {
                        Text("10 秒 · 部署检查").tag(10)
                        Text("60 秒").tag(60)
                        Text("300 秒 · 正式测试").tag(300)
                        Text("600 秒").tag(600)
                    }.labelsHidden().frame(width: 170)
                    Spacer()
                    Button(model.isCustomCommand ? "恢复生成命令" : "重新生成") {
                        model.updateGeneratedCommand(force: true)
                    }.buttonStyle(.borderless)
                }
                .font(.system(size: 11))
                .disabled(model.isRunning)
                Text("命名：设备名_对端画面规格_SR_off/on，例如 Pixel7_120p_SR_off；请按实际画面和超分状态填写。")
                    .font(.system(size: 10)).foregroundStyle(muted)
                HStack(alignment: .center, spacing: 12) {
                    TextEditor(text: $model.command)
                        .font(.system(size: 11, design: .monospaced))
                        .scrollContentBackground(.hidden)
                        .padding(7)
                        .background(Color(red: 0.965, green: 0.974, blue: 0.99), in: RoundedRectangle(cornerRadius: 8))
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.black.opacity(0.07)))
                        .frame(height: 61)
                        .disabled(model.isRunning)
                        .accessibilityLabel("测试 Shell 命令")
                        .accessibilityIdentifier("shellCommand")
                    Button { model.execute() } label: {
                        Label(model.isRunning ? "执行中" : "执行测试", systemImage: "play.fill")
                            .font(.system(size: 12, weight: .semibold)).frame(width: 95, height: 33)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!model.canExecute)
                    .accessibilityIdentifier("executeTest")
                }
                HStack(spacing: 5) {
                    Image(systemName: "folder").foregroundStyle(muted)
                    Text(model.outputDirectory).lineLimit(1).truncationMode(.middle).foregroundStyle(muted)
                        .help(model.outputDirectory)
                    Button("更改") { model.chooseOutputDirectory() }.buttonStyle(.borderless).disabled(model.isRunning)
                    Spacer()
                    Button { copy(model.command) } label: { Label("复制命令", systemImage: "doc.on.doc") }
                        .buttonStyle(.borderless)
                }.font(.system(size: 10))
            }
        }
    }

    private var logPanel: some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                Circle().fill(model.isRunning ? Color.green : Color(red: 0.43, green: 0.49, blue: 0.6)).frame(width: 6, height: 6)
                Text("终端日志").font(.system(size: 12, weight: .semibold)).foregroundStyle(.white)
                Text(model.executionLabel).font(.system(size: 10)).foregroundStyle(Color.white.opacity(0.5))
                Spacer()
                if model.isRunning {
                    Button(model.isStopping ? "正在停止…" : "停止测试") { model.stop() }
                        .disabled(model.isStopping).foregroundStyle(Color(red: 1, green: 0.68, blue: 0.52))
                }
                Button("清空显示") { model.clearDisplay() }.disabled(model.logText.isEmpty)
                Button("到最新") { jumpToLatest += 1 }.disabled(model.logText.isEmpty)
            }
            .buttonStyle(.borderless)
            .font(.system(size: 10))
            .foregroundStyle(Color.white.opacity(0.7))
            .padding(.horizontal, 16).frame(height: 37)
            .background(Color(red: 0.10, green: 0.13, blue: 0.19))
            ZStack(alignment: .topLeading) {
                LogTextView(text: model.logText, jumpToLatest: jumpToLatest)
                if model.logText.isEmpty {
                    VStack(alignment: .leading, spacing: 9) {
                        Text("$ 等待执行测试命令…").foregroundStyle(Color.white.opacity(0.6))
                        Text("执行后，Terminal 与这里显示同一次任务的输出。").foregroundStyle(Color.white.opacity(0.3))
                    }
                    .font(.system(size: 11, design: .monospaced))
                    .padding(18).allowsHitTesting(false)
                }
            }.frame(minHeight: 165, maxHeight: .infinity)
            HStack(spacing: 6) {
                Image(systemName: "hand.draw")
                Text("自动滚动已关闭 · 可自由滚动、选择和复制")
                Spacer()
                Button("日志文件") { model.revealLog() }.disabled(model.jobDirectory == nil)
                Button("打开结果") { model.revealResults() }.disabled(model.resultDirectory == nil)
            }
            .font(.system(size: 10)).foregroundStyle(Color.white.opacity(0.5)).buttonStyle(.borderless)
            .padding(.horizontal, 16).frame(height: 29)
            .background(Color(red: 0.10, green: 0.13, blue: 0.19))
        }
        .background(Color(red: 0.065, green: 0.086, blue: 0.13))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func sectionTitle(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol).font(.system(size: 13, weight: .semibold)).foregroundStyle(ink)
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

private struct Panel<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(.white, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.black.opacity(0.045)))
    }
}

private struct DeviceCard: View {
    let device: AndroidDevice
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: "iphone.gen3").font(.system(size: 27, weight: .light))
                    .foregroundStyle(selected ? accent : muted)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 7) {
                        Text(device.name).font(.system(size: 12, weight: .semibold)).foregroundStyle(ink).lineLimit(1)
                        Label(device.connection.rawValue, systemImage: device.connection == .wireless ? "wifi" : "cable.connector")
                            .font(.system(size: 9, weight: .medium)).foregroundStyle(selected ? accent : muted)
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background((selected ? accent : muted).opacity(0.08), in: Capsule())
                    }
                    Text(device.id).font(.system(size: 9, design: .monospaced)).foregroundStyle(muted)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 5)
                VStack(spacing: 7) {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? accent : muted.opacity(0.35))
                    Text(device.stateLabel).font(.system(size: 9)).foregroundStyle(device.isAvailable ? Color.green : Color.orange)
                }
            }
            .padding(12).frame(maxWidth: .infinity, minHeight: 70)
            .background(selected ? accent.opacity(0.045) : Color(red: 0.975, green: 0.98, blue: 0.988), in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(selected ? accent.opacity(0.55) : Color.black.opacity(0.065), lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 9))
        }
        .buttonStyle(.plain)
        .help("\(device.name) · \(device.connection.rawValue) · \(device.id)")
        .accessibilityLabel("选择设备 \(device.name)，\(device.connection.rawValue)，\(device.stateLabel)")
    }
}

private struct PreparationEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var draft: String
    let save: (String) -> Void

    init(text: String, save: @escaping (String) -> Void) {
        _draft = State(initialValue: text)
        self.save = save
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("场景准备", systemImage: "checklist").font(.system(size: 20, weight: .semibold))
                Spacer()
                Text("保存到本机").font(.system(size: 11)).foregroundStyle(muted)
            }
            Text("记录测试目标、固定条件和操作步骤，作为每轮执行前的准备清单。")
                .font(.system(size: 12)).foregroundStyle(muted)
            TextEditor(text: $draft).font(.system(size: 13)).padding(10)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.black.opacity(0.12)))
                .accessibilityLabel("完整场景准备说明，可编辑")
            HStack {
                Button("恢复默认文本") { draft = AppModel.defaultPreparation }.buttonStyle(.borderless)
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save(draft); dismiss() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
            }
        }
        .padding(24).frame(width: 720, height: 610).tint(accent)
    }
}
