// Menu bar companion for the os3-router service.
// The router itself runs as a LaunchAgent (installed by install.sh); this app shows its
// state and offers the everyday actions. It only talks to the router's local API.
import AppKit
import ServiceManagement
import SwiftUI

@main
struct CodexOS3App: App {
    @StateObject private var model = RouterModel()

    init() {
        // `CodexOS3 --snapshot out.png`: render the menu to a PNG (docs/tests), then quit
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            let out = args[i + 1]
            Task { @MainActor in
                let m = RouterModel()
                try? await Task.sleep(nanoseconds: 2_500_000_000)
                let r = ImageRenderer(content: MenuContent(model: m).background(Color(nsColor: .windowBackgroundColor)))
                r.scale = 2
                if let img = r.nsImage, let tiff = img.tiffRepresentation,
                   let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                    try? png.write(to: URL(fileURLWithPath: out))
                }
                exit(0)
            }
        }
    }

    var body: some Scene {
        MenuBarExtra {
            MenuContent(model: model)
        } label: {
            Image(systemName: model.symbol)
        }
        .menuBarExtraStyle(.window)
    }
}

// MARK: - State

struct Limits: Decodable { let p_pct: Double?; let p_reset: Double?; let s_pct: Double?; let s_reset: Double? }
struct Agent: Decodable { let status: String?; let running: Bool?; let version: String? }
struct Finding: Decodable { let kind: String; let level: String; let msg: String }
struct Watchdog: Decodable { let findings: [Finding]? }
struct UsageLimit: Decodable { let ts: Double; let resets: String? }
struct Status: Decodable {
    let version: String; let limits: Limits?; let agent: Agent?; let watchdog: Watchdog?
    let running: Int; let model: String; let endpoint: String; let usage_limit: UsageLimit?
    let whats_new: Bool?
}
struct WhatsNew: Decodable {
    struct Section: Decodable { let title: String; let body: String }
    let version: String; let show: Bool; let sections: [Section]
}
struct Config: Decodable { let api_key: String; let port: Int; let model: String }

@MainActor
final class RouterModel: ObservableObject {
    @Published var status: Status?
    @Published var error: String?
    @Published var busy: String?
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    private var timer: Timer?
    private var showingWhatsNew = false

    static let home = (ProcessInfo.processInfo.environment["CODEX_OS3_HOME"]
                       ?? NSString(string: "~/.codex-os3").expandingTildeInPath)
    static let serviceLabel = "ai.codexos3.router"

    var port: Int {
        let url = URL(fileURLWithPath: Self.home + "/config.json")
        if let d = try? Data(contentsOf: url),
           let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let p = j["port"] as? Int { return p }
        return 11435
    }
    var base: String { "http://127.0.0.1:\(port)" }

    init() {
        // first launch: start with the user's session, like the router service does
        if !UserDefaults.standard.bool(forKey: "didRegisterLoginItem") {
            try? SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: "didRegisterLoginItem")
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    var symbol: String {
        guard let s = status else { return error == nil ? "circle.dotted" : "xmark.circle" }
        if let ul = s.usage_limit, Date().timeIntervalSince1970 - ul.ts < 3600 { return "exclamationmark.octagon" }
        if (s.watchdog?.findings ?? []).contains(where: { $0.level == "error" }) { return "exclamationmark.triangle" }
        if s.agent?.status != "connected" { return "exclamationmark.triangle" }
        return s.running > 0 ? "bolt.circle.fill" : "checkmark.circle"
    }

    func refresh() {
        Task {
            do {
                let (d, _) = try await URLSession.shared.data(from: URL(string: base + "/api/status")!)
                status = try JSONDecoder().decode(Status.self, from: d)
                error = nil
                if status?.whats_new == true && !showingWhatsNew { showWhatsNew() }
            } catch {
                status = nil
                self.error = "Router not running"
            }
        }
    }

    func post(_ path: String) async throws -> [String: Any] {
        var r = URLRequest(url: URL(string: base + "/api/" + path)!)
        r.httpMethod = "POST"
        r.setValue("1", forHTTPHeaderField: "X-Codex-OS3")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data("{}".utf8)
        r.timeoutInterval = 120
        let (d, _) = try await URLSession.shared.data(for: r)
        return (try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:]
    }

    func config() async -> Config? {
        guard let (d, _) = try? await URLSession.shared.data(from: URL(string: base + "/api/config")!) else { return nil }
        return try? JSONDecoder().decode(Config.self, from: d)
    }

    func copySetup() {
        Task {
            guard let c = await config() else { return }
            let text = """
            endpoint: http://localhost:\(c.port)/v1
            model id: \(c.model)
            api key: \(c.api_key)
            context window: 200000
            """
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            flash("Copied OS3 settings")
        }
    }

    func copyKey() {
        Task {
            guard let c = await config() else { return }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(c.api_key, forType: .string)
            flash("Copied API key")
        }
    }

    func restartAgent() {
        busy = "Restarting rabbit-agent…"
        Task {
            let r = try? await post("agent/restart")
            busy = nil
            flash((r?["message"] as? String) ?? "Restart requested")
            refresh()
        }
    }

    func reload() {
        Task { _ = try? await post("reload"); flash("Router reload requested"); refresh() }
    }

    /// After an update: the changelog since the version last seen, once (the web UI or the
    /// Windows tray may show it instead; Continue marks it seen for all of them).
    func showWhatsNew() {
        showingWhatsNew = true
        Task {
            defer { showingWhatsNew = false }
            guard let (d, _) = try? await URLSession.shared.data(from: URL(string: base + "/api/whatsnew")!),
                  let w = try? JSONDecoder().decode(WhatsNew.self, from: d), w.show else { return }
            let text = w.sections.map { $0.title + "\n" + Self.plain($0.body) }.joined(separator: "\n\n")
            let view = NSTextView(frame: NSRect(x: 0, y: 0, width: 520, height: 320))
            view.string = text
            view.isEditable = false
            view.font = .systemFont(ofSize: 13)
            view.textContainerInset = NSSize(width: 6, height: 6)
            let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 520, height: 320))
            scroll.documentView = view
            scroll.hasVerticalScroller = true
            let alert = NSAlert()
            alert.messageText = "What's new in os3-router \(w.version)"
            alert.informativeText = "os3-router was updated."
            alert.accessoryView = scroll
            alert.addButton(withTitle: "Continue")
            alert.addButton(withTitle: "Open dashboard")
            NSApp.activate(ignoringOtherApps: true)
            let r = alert.runModal()
            _ = try? await post("whatsnew/seen")
            if r == .alertSecondButtonReturn { openDashboard() }
        }
    }

    /// Changelog markdown -> plain bullets
    static func plain(_ body: String) -> String {
        var items: [String] = []
        for line in body.components(separatedBy: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("- ") { items.append("• " + t.dropFirst(2)) }
            else if !t.isEmpty, !items.isEmpty { items[items.count - 1] += " " + t }
        }
        return items.joined(separator: "\n").replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "`", with: "")
    }

    func openDashboard(_ tab: String = "") {
        NSWorkspace.shared.open(URL(string: "http://localhost:\(port)/" + (tab.isEmpty ? "" : "#" + tab))!)
    }

    /// Start/stop the LaunchAgent through launchd, so the service keeps the proper
    /// context (processes started any other way can lose macOS permissions).
    func service(start: Bool) {
        let uid = getuid()
        let plist = NSString(string: "~/Library/LaunchAgents/\(Self.serviceLabel).plist").expandingTildeInPath
        let args = start ? ["bootstrap", "gui/\(uid)", plist] : ["bootout", "gui/\(uid)/\(Self.serviceLabel)"]
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        try? p.run()
        p.waitUntilExit()
        if start {  // already loaded: make sure it runs
            let k = Process()
            k.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            k.arguments = ["kickstart", "gui/\(uid)/\(Self.serviceLabel)"]
            try? k.run()
            k.waitUntilExit()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.refresh() }
    }

    func toggleLaunchAtLogin() {
        do {
            if launchAtLogin { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() }
        } catch { flash("Could not change login item: \(error.localizedDescription)") }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    @Published var note: String?
    func flash(_ s: String) {
        note = s
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { if self.note == s { self.note = nil } }
    }
}

// MARK: - UI

struct Meter: View {
    let title: String; let pct: Double?; let reset: Double?
    var color: Color { guard let p = pct else { return .secondary }; return p >= 90 ? .red : p >= 70 ? .orange : .accentColor }
    var resetText: String {
        guard let r = reset else { return "" }
        let s = r - Date().timeIntervalSince1970
        if s <= 0 { return "resetting" }
        let h = Int(s) / 3600, m = (Int(s) % 3600) / 60
        return "resets in " + (h > 0 ? "\(h) h " : "") + "\(m) min"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(pct.map { "\(Int($0.rounded()))%" } ?? "–").font(.caption.monospacedDigit().weight(.semibold))
            }
            GeometryReader { g in  // drawn by hand: same look as the web dashboard's meters
                ZStack(alignment: .leading) {
                    Capsule().fill(color.opacity(0.18))
                    Capsule().fill(color).frame(width: g.size.width * min(1, max(0, (pct ?? 0) / 100)))
                }
            }.frame(height: 7)
            Text(resetText).font(.caption2).foregroundStyle(.tertiary)
        }
    }
}

struct MenuContent: View {
    @ObservedObject var model: RouterModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("OS3 Router").font(.headline)
                Spacer()
                if let v = model.status?.version { Text("v\(v)").font(.caption).foregroundStyle(.secondary) }
            }
            if let s = model.status {
                statusLine(s)
                Meter(title: "5-hour window", pct: s.limits?.p_pct, reset: s.limits?.p_reset)
                Meter(title: "Weekly limit", pct: s.limits?.s_pct, reset: s.limits?.s_reset)
                ForEach(Array((s.watchdog?.findings ?? []).prefix(3).enumerated()), id: \.offset) { _, f in
                    Label(f.msg, systemImage: f.level == "error" ? "xmark.octagon" : "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(f.level == "error" ? .red : .orange).lineLimit(3)
                }
            } else {
                Label(model.error ?? "Connecting…", systemImage: "xmark.circle").foregroundStyle(.red)
                Button("Start router service") { model.service(start: true) }
            }
            if let b = model.busy { ProgressView(b).controlSize(.small) }
            if let n = model.note { Text(n).font(.caption).foregroundStyle(.secondary) }
            Divider()
            Group {
                Button("Open dashboard") { model.openDashboard() }
                Button("Copy OS3 settings") { model.copySetup() }
                Button("Copy API key") { model.copyKey() }
                Button("Restart rabbit-agent") { model.restartAgent() }.disabled(model.status == nil || model.busy != nil)
                Button("Reload router (no downtime)") { model.reload() }.disabled(model.status == nil)
            }.buttonStyle(.plain)
            Divider()
            Toggle("Open at login", isOn: Binding(get: { model.launchAtLogin }, set: { _ in model.toggleLaunchAtLogin() }))
                .toggleStyle(.checkbox)
            HStack {
                Button("Stop router") { model.service(start: false) }.disabled(model.status == nil)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 290)
        .onAppear { model.refresh() }
    }

    @ViewBuilder func statusLine(_ s: Status) -> some View {
        let agentOK = s.agent?.status == "connected" && (s.agent?.running ?? false)
        VStack(alignment: .leading, spacing: 2) {
            Label(agentOK ? "rabbit-agent connected" : "rabbit-agent: \(s.agent?.status ?? "not found")",
                  systemImage: agentOK ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(agentOK ? .green : .red)
            Text(s.running > 0 ? "Working on \(s.running) request(s) · \(s.model)" : "Idle · \(s.model)")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
