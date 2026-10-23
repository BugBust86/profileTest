import SwiftUI

@main
struct ProfilerDeskApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        Window("性能测试工作台", id: "main") {
            ContentView(model: model)
                .frame(minWidth: 960, minHeight: 760)
                .preferredColorScheme(.light)
        }
        .defaultSize(width: 1100, height: 860)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("选择采集脚本目录…") { model.chooseProjectDirectory() }
                    .disabled(model.isRunning)
            }
        }
    }
}
