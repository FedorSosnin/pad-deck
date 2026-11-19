// Updates for Pad Deck Helper: checks GitHub for a newer release a few times a day, and installs it (the new
// app replaces this one where it is, then opens). With "Update automatically" on (the deck's System section),
// it installs by itself between 3 and 5 at night. The menu has Check for Updates…; the deck's System section
// uses GET/POST /api/update (DeckServer.swift).
import AppKit

let repo = "FedorSosnin/pad-deck"
let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"

func versionParts(_ v: String) -> [Int] {
    v.split(whereSeparator: { !$0.isNumber }).prefix(3).compactMap { Int($0) }
}

func isNewer(_ a: String, than b: String) -> Bool {
    let x = versionParts(a), y = versionParts(b)
    for i in 0..<max(x.count, y.count) {
        let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
        if p != q { return p > q }
    }
    return false
}

final class Updater {
    static let shared = Updater()
    private let lock = NSLock()
    private(set) var latest: String?
    private(set) var notes = ""
    private(set) var error: String?
    private(set) var checked: Date?
    private(set) var busy = false
    var onAir = Date.distantPast     // a deck said OBS is streaming or recording (POST /api/busy)
    private var asset: URL?

    var available: Bool { latest.map { isNewer($0, than: appVersion) } ?? false }

    /// Ask GitHub for the newest release (blocking).
    func check() {
        do {
            let (status, data) = try fetch(URL(string: "https://api.github.com/repos/\(repo)/releases/latest")!,
                                           headers: ["User-Agent": "Pad-Deck-Updater", "Accept": "application/vnd.github+json"], timeout: 15)
            guard status == 200, let rel = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = rel["tag_name"] as? String else { throw Failure("GitHub answered \(status)") }
            let zip = (rel["assets"] as? [[String: Any]] ?? []).first { $0["name"] as? String == "Pad-Deck-Helper-Mac.zip" }
            lock.lock()
            latest = String(tag.drop { $0 == "v" })
            notes = String((rel["body"] as? String ?? "").prefix(4000))
            asset = (zip?["browser_download_url"] as? String).flatMap(URL.init(string:))
            error = nil
            lock.unlock()
        } catch {
            self.error = "Couldn't check for updates (\((error as? Failure)?.message ?? error.localizedDescription))"
        }
        checked = Date()
    }

    func state() -> [String: Any] {
        ["version": appVersion, "latest": latest ?? NSNull(), "notes": notes, "error": error ?? NSNull(), "available": available,
         "busy": busy, "checked": checked?.timeIntervalSince1970 ?? 0, "auto": isOn(loadSettings()["autoUpdate"] ?? true)]
    }

    /// Download the new app, put it where this one is, and open it. Returns false if it's already installing.
    @discardableResult
    func start() -> Bool {
        lock.lock()
        if busy { lock.unlock(); return false }
        busy = true
        lock.unlock()
        DispatchQueue.global().async {
            do { try self.install() } catch {
                self.error = "The update didn't install: \((error as? Failure)?.message ?? error.localizedDescription)"
                self.busy = false
            }
        }
        return true
    }

    private func install() throws {
        guard let asset else { throw Failure("this release has no Mac app yet") }
        let here = Bundle.main.bundleURL
        if here.path.contains("/AppTranslocation/") { throw Failure("move Pad Deck Helper to Applications first") }
        let (status, zip) = try fetch(asset, timeout: 300)
        guard status == 200 else { throw Failure("the download failed (\(status))") }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("pad-deck-update-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let zipURL = tmp.appendingPathComponent("helper.zip")
        try zip.write(to: zipURL)
        try runTool("/usr/bin/ditto", ["-x", "-k", zipURL.path, tmp.path])
        let fresh = tmp.appendingPathComponent("Pad Deck Helper.app")
        guard FileManager.default.fileExists(atPath: fresh.appendingPathComponent("Contents/MacOS/PadDeckHelper").path) else {
            throw Failure("the download doesn't look like Pad Deck Helper")
        }
        try? runTool("/usr/bin/xattr", ["-dr", "com.apple.quarantine", fresh.path])
        do { _ = try FileManager.default.replaceItemAt(here, withItemAt: fresh) }
        catch { throw Failure("couldn't replace the app in \(here.deletingLastPathComponent().path)") }
        // Open the new one once this one has quit.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "sleep 1; /usr/bin/open \"$0\"", here.path]
        try p.run()
        DispatchQueue.main.async { NSApp.terminate(nil) }
    }

    private func runTool(_ path: String, _ args: [String]) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        try p.run()
        p.waitUntilExit()
        if p.terminationStatus != 0 { throw Failure("\((path as NSString).lastPathComponent) failed") }
    }

    /// Check a few times a day; with "Update automatically" on, install between 3 and 5 at night.
    func watch() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 60) {
            while true {
                if (self.checked?.timeIntervalSinceNow ?? -.infinity) < -6 * 3600 { self.check() }
                let hour = Calendar.current.component(.hour, from: Date())
                if self.available, isOn(loadSettings()["autoUpdate"] ?? true), (3..<5).contains(hour),
                   self.onAir.timeIntervalSinceNow < -5 * 60 { self.start() }
                Thread.sleep(forTimeInterval: 15 * 60)
            }
        }
    }
}
