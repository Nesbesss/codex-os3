// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CodexOS3",
    platforms: [.macOS(.v13)],
    targets: [.executableTarget(name: "CodexOS3", path: "Sources/CodexOS3")]
)
