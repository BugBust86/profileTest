// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ProfilerDesk",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "ProfilerDesk", targets: ["ProfilerDesk"])],
    targets: [
        .target(name: "ProfilerCore"),
        .executableTarget(name: "ProfilerDesk", dependencies: ["ProfilerCore"]),
        .testTarget(name: "ProfilerCoreTests", dependencies: ["ProfilerCore"])
    ]
)
