// The deck itself, for any browser on the network: http://this-mac:8099. Serves the Pad Deck web app (bundled in
// the app's Resources/web) and the same /api the Pad's server.py answers, so the web app works unchanged.
// The contract is docs/server-api.md; tools/check-server.py checks it. Saved data lives in
// ~/Library/Application Support/Pad Deck Helper/.
import Foundation

let deckPort: UInt16 = 8099
let webRoot = Bundle.main.resourceURL!.appendingPathComponent("web")
let dataDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Pad Deck Helper")
let layoutURL = dataDir.appendingPathComponent("layout.json")
let settingsURL = dataDir.appendingPathComponent("settings.json")
let layoutsDir = dataDir.appendingPathComponent("layouts")
let userIconsDir = dataDir.appendingPathComponent("icons")
let iconTypes = ["png", "jpg", "jpeg", "svg", "webp", "gif"]
let backupName = "Before last load"

let defaultSettings: [String: Any] = [
    "sound": true, "vibrate": true, "volume": 0.6, "leds": true, "obsHost": "", "obsPort": 4455, "obsPassword": "",
    "brightness": 180, "helperHost": "", "helperPort": 8098, "helperCode": "", "autoUpdate": true,
]

let contentTypes = ["html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                    "json": "application/json", "webmanifest": "application/manifest+json", "svg": "image/svg+xml",
                    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
                    "woff2": "font/woff2", "mp4": "video/mp4", "ico": "image/x-icon"]

func readJSON(_ url: URL) -> Any? {
    guard let d = try? Data(contentsOf: url) else { return nil }
    return try? JSONSerialization.jsonObject(with: d)
}

func writeJSON(_ obj: Any, _ url: URL) throws {
    let d = try JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .withoutEscapingSlashes])
    try d.write(to: url, options: .atomic)
}

/// First run: an empty layout, plus the example layouts that ship with Pad Deck.
func prepareData() {
    let fm = FileManager.default
    try? fm.createDirectory(at: userIconsDir, withIntermediateDirectories: true)
    if !fm.fileExists(atPath: layoutsDir.path) {
        try? fm.createDirectory(at: layoutsDir, withIntermediateDirectories: true)
        for f in (try? fm.contentsOfDirectory(at: webRoot.appendingPathComponent("layouts"), includingPropertiesForKeys: nil)) ?? [] {
            try? fm.copyItem(at: f, to: layoutsDir.appendingPathComponent(f.lastPathComponent))
        }
    }
    if !fm.fileExists(atPath: layoutURL.path) {
        try? writeJSON(["columns": 4, "rows": 3, "pages": [["buttons": [Any]()]]], layoutURL)
    }
}

// ---------- Settings ----------

func isBool(_ v: Any) -> Bool { (v as? NSNumber).map { CFGetTypeID($0) == CFBooleanGetTypeID() } ?? false }

/// A value in the type of the default (like server.py's type(default)(value)).
func coerce(_ v: Any, like d: Any) -> Any? {
    if isBool(d) { return (v as? NSNumber)?.boolValue ?? (v as? String).map { !$0.isEmpty } }
    if d is String { return (v as? String) ?? (v as? NSNumber)?.stringValue }
    if let dn = d as? NSNumber {
        let n = (v as? NSNumber) ?? (v as? String).flatMap { Double($0) }.map { NSNumber(value: $0) }
        guard let n else { return nil }
        return CFNumberIsFloatType(dn) ? n.doubleValue : n.intValue
    }
    return nil
}

func loadSettings() -> [String: Any] {
    var s = defaultSettings
    for (k, v) in readJSON(settingsURL) as? [String: Any] ?? [:] { s[k] = v }
    return s
}

// ---------- Layouts ----------

func layoutFile(_ name: String) throws -> URL {
    var slug = ""
    for ch in name.lowercased().unicodeScalars {
        if ("a"..."z").contains(ch) || ("0"..."9").contains(ch) { slug.unicodeScalars.append(ch) }
        else if !slug.hasSuffix("-") { slug += "-" }
    }
    slug = String(slug.trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(60))
    guard !slug.isEmpty else { throw Failure("Give the layout a name") }
    return layoutsDir.appendingPathComponent(slug + ".json")
}

let savedFormat: DateFormatter = {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return f
}()

func saveLayoutAs(_ name: String, _ layout: Any? = nil) throws {
    let url = try layoutFile(name)
    guard let layout = layout ?? readJSON(layoutURL) else { throw Failure("There's no layout to save") }
    try writeJSON(["name": name, "saved": savedFormat.string(from: Date()), "layout": layout], url)
}

func listLayouts() -> [[String: Any]] {
    var out: [[String: Any]] = []
    for f in (try? FileManager.default.contentsOfDirectory(at: layoutsDir, includingPropertiesForKeys: nil)) ?? [] {
        guard let e = readJSON(f) as? [String: Any], let layout = e["layout"] as? [String: Any] else { continue }
        let pages = layout["pages"] as? [[String: Any]] ?? [["buttons": layout["buttons"] ?? []]]
        let buttons = pages.reduce(0) { $0 + (($1["buttons"] as? [Any]) ?? []).filter { !($0 is NSNull) }.count }
        out.append(["name": e["name"] ?? "", "saved": e["saved"] ?? "", "pages": pages.count,
                    "columns": layout["columns"] ?? 4, "rows": layout["rows"] ?? 3, "buttons": buttons])
    }
    return out.sorted { ($0["saved"] as? String ?? "") > ($1["saved"] as? String ?? "") }
}

/// Make a layout current (nil: a fresh, empty 4×3 one); the one it replaces is kept as "Before last load".
func makeCurrent(_ layout: Any?) throws {
    if let current = readJSON(layoutURL) { try saveLayoutAs(backupName, current) }
    try writeJSON(layout ?? ["columns": 4, "rows": 3, "pages": [["buttons": [Any]()]]], layoutURL)
}

func validLayout(_ v: Any?) -> Bool {
    guard let l = v as? [String: Any], let pages = l["pages"] as? [Any], !pages.isEmpty else { return false }
    return pages.allSatisfy { (($0 as? [String: Any])?["buttons"]) is [Any] }
}

// ---------- Icons ----------

func iconNames() -> [String] {
    var names = Set<String>()
    for dir in [webRoot.appendingPathComponent("icons"), userIconsDir] {
        for n in (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        where !n.hasPrefix(".") && iconTypes.contains((n as NSString).pathExtension.lowercased()) { names.insert(n) }
    }
    return names.sorted { Array($0.unicodeScalars.map(\.value)).lexicographicallyPrecedes($1.unicodeScalars.map(\.value)) }
}

func safeIconName(_ raw: String) -> String {
    let base = (raw as NSString).lastPathComponent
    return String(base.unicodeScalars.map {
        CharacterSet.alphanumerics.contains($0) || "_.-".unicodeScalars.contains($0) ? Character($0) : "_"
    })
}

/// Copy the layout (and the icons it uses that this Mac doesn't have) from a Pad on the network.
func importLayout(from rawHost: String) throws -> Int {
    let host = rawHost.trimmingCharacters(in: .whitespaces)
        .replacingOccurrences(of: "http://", with: "").split(separator: "/").first.map(String.init) ?? ""
    let hostPort = host.contains(":") ? host : host + ":8099"
    guard validHost(host), let base = URL(string: "http://" + hostPort) else { throw Failure("Enter the Pad's address, e.g. pad7.local") }
    let code = ["X-Pad-Deck-Code": pairingCode]
    let (status, data): (Int, Data)
    do { (status, data) = try fetch(base.appendingPathComponent("api/layout"), headers: code, timeout: 6) }
    catch { throw Failure("Can't reach a Pad at \(host)") }
    if status == 401 { throw Failure("That Pad has a different pairing code. Set this Helper's code on it (Menu → Helper).") }
    guard status == 200, let layout = try? JSONSerialization.jsonObject(with: data), validLayout(layout) else {
        throw Failure("\(host) didn't send a Pad Deck layout")
    }
    let have = Set(iconNames())
    var wanted = Set<String>()
    for p in (layout as! [String: Any])["pages"] as! [[String: Any]] {
        for b in p["buttons"] as? [Any] ?? [] { if let i = (b as? [String: Any])?["icon"] as? String { wanted.insert(i) } }
    }
    var copied = 0
    for name in wanted.subtracting(have) {
        let safe = safeIconName(name)
        guard safe == name, iconTypes.contains((name as NSString).pathExtension.lowercased()),
              let (st, img) = try? fetch(base.appendingPathComponent("icons").appendingPathComponent(name), timeout: 6), st == 200 else { continue }
        if (try? img.write(to: userIconsDir.appendingPathComponent(name), options: .atomic)) != nil { copied += 1 }
    }
    try makeCurrent(layout)
    return copied
}

// ---------- Addresses (for the phone set-up page) ----------

func ipv4Addresses() -> [String] {
    var out: [String] = []
    var ifs: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&ifs) == 0 else { return [] }
    defer { freeifaddrs(ifs) }
    var p = ifs
    while let i = p {
        let flags = Int32(i.pointee.ifa_flags)
        if let a = i.pointee.ifa_addr, a.pointee.sa_family == UInt8(AF_INET), flags & IFF_UP != 0, flags & IFF_LOOPBACK == 0 {
            let name = String(cString: i.pointee.ifa_name)
            if name.hasPrefix("en") || name.hasPrefix("bridge") {
                let addr = a.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr }
                out.append(String(cString: inet_ntoa(addr)))
            }
        }
        p = i.pointee.ifa_next
    }
    return out
}

// ---------- Routes ----------

func deckHandle(_ r: Request) -> Response {
    let paired = r.local || sameCode(r.headers["x-pad-deck-code"], pairingCode)
    if !r.path.hasPrefix("/api/") { return serveFile(r.path) }
    if r.path == "/api/device" { return json(["device": "helper", "os": "mac", "local": r.local, "paired": paired]) }
    guard paired else { return json(["error": "Enter the pairing code", "pair": true], 401) }
    do {
        return try route(r)
    } catch let e as Failure {
        return json(["error": e.message], 400)
    } catch {
        return json(["error": error.localizedDescription], 500)
    }
}

func route(_ r: Request) throws -> Response {
    let body = r.json as? [String: Any]
    switch (r.method, r.path) {
    case ("GET", "/api/layout"):
        return json(readJSON(layoutURL) ?? ["columns": 4, "rows": 3, "pages": [["buttons": [Any]()]]])
    case ("PUT", "/api/layout"):
        guard validLayout(body) else { return json(["error": "invalid layout"], 400) }
        try writeJSON(body!, layoutURL)
        return json(["ok": true])
    case ("GET", "/api/settings"):
        return json(loadSettings())
    case ("PUT", "/api/settings"):
        guard let body else { return json(["error": "invalid settings"], 400) }
        var s = loadSettings()
        for (k, d) in defaultSettings { if let v = body[k], let c = coerce(v, like: d) { s[k] = c } }
        try writeJSON(s, settingsURL)
        return json(["ok": true])
    case ("GET", "/api/layouts"):
        return json(listLayouts())
    case ("POST", "/api/layouts"):
        try saveLayoutAs((body?["name"] as? String ?? "").trimmingCharacters(in: .whitespaces))
        return json(["ok": true])
    case ("POST", "/api/layouts/load"):
        let url = try layoutFile((body?["name"] as? String ?? "").trimmingCharacters(in: .whitespaces))
        guard let e = readJSON(url) as? [String: Any], let layout = e["layout"] else {
            return json(["error": "That layout no longer exists"], 404)
        }
        try makeCurrent(layout)
        return json(["ok": true])
    case ("POST", "/api/layouts/new"):
        try makeCurrent(nil)
        return json(["ok": true])
    case ("POST", "/api/layouts/import"):
        return json(["ok": true, "icons": try importLayout(from: body?["host"] as? String ?? "")])
    case ("DELETE", "/api/layouts"):
        if let url = try? layoutFile(r.query["name"] ?? "") { try? FileManager.default.removeItem(at: url) }
        return json(["ok": true])
    case ("GET", "/api/icons"):
        return json(iconNames())
    case ("POST", "/api/icons"):
        let name = safeIconName(r.headers["x-filename"] ?? "")
        guard iconTypes.contains((name as NSString).pathExtension.lowercased()), !r.body.isEmpty else {
            return json(["error": "unsupported file type"], 400)
        }
        try r.body.write(to: userIconsDir.appendingPathComponent(name), options: .atomic)
        return json(["name": name])
    case ("POST", "/api/run"):
        guard let a = body else { throw Failure("Unknown action 'None'") }
        if paused { throw Failure("Pad Deck Helper is paused (menu bar on the Mac)") }
        let message: String
        switch a["type"] as? String ?? "" {
        case "http":
            let url = (a["url"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            guard url.hasPrefix("http://") || url.hasPrefix("https://") else {
                throw Failure("The web address should start with http:// or https://")
            }
            message = try httpSend(url, method: a["method"] as? String, body: a["body"] as? String)
        case "plug":
            message = try runPlug(a)
        default:
            message = try run(a)
        }
        DispatchQueue.main.async { Menu.shared.flash() }
        return json(["ok": true, "message": message])
    case ("GET", "/api/helper"):
        switch r.query["what"] ?? "ping" {
        case "ping": return json(["ok": true, "computer": Host.current().localizedName ?? "Mac", "mac": true, "paused": paused])
        case "apps": return json(["apps": appNames()])
        case "shortcuts": return json(["shortcuts": shortcutNames()])
        default: return json(["error": "Not found"], 404)
        }
    case ("GET", "/api/plugs"):
        return json(["plugs": kasaFind()])
    case ("POST", "/api/plugs/state"):
        return json(["states": plugStates(r.json as? [[String: Any]] ?? [])])
    case ("POST", "/api/buzz"):
        return json(["ok": true])
    case ("POST", "/api/busy"):
        Updater.shared.onAir = Date()
        return json(["ok": true])
    case ("GET", "/api/update"):
        if r.query["check"] == "1" || Updater.shared.checked == nil { Updater.shared.check() }
        return json(Updater.shared.state())
    case ("POST", "/api/update"):
        if Updater.shared.latest == nil { Updater.shared.check() }
        guard Updater.shared.available, let latest = Updater.shared.latest else {
            return json(["error": Updater.shared.error ?? "Pad Deck Helper is up to date"], 400)
        }
        Updater.shared.start()
        return json(["ok": true, "message": "Updating to \(latest)…"])
    case ("GET", "/api/connect"):
        guard r.local else { return json(["error": "Only on this Mac"], 403) }
        return json(["addresses": ipv4Addresses(), "port": Int(deckPort), "code": pairingCode,
                     "computer": Host.current().localizedName ?? "Mac", "hostname": ProcessInfo.processInfo.hostName])
    default:
        return json(["error": "Not found"], 404)
    }
}

func serveFile(_ path: String) -> Response {
    let rel = path == "/" ? "index.html" : String(path.dropFirst())
    guard !rel.split(separator: "/").contains(where: { $0 == ".." || $0.hasPrefix(".") }) else { return json(["error": "Not found"], 404) }
    var candidates = [webRoot.appendingPathComponent(rel)]
    if rel.hasPrefix("icons/") { candidates.insert(userIconsDir.appendingPathComponent(String(rel.dropFirst(6))), at: 0) }
    for url in candidates {
        var dir: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &dir), !dir.boolValue, let d = try? Data(contentsOf: url) {
            return Response(type: contentTypes[url.pathExtension.lowercased()] ?? "application/octet-stream", body: d)
        }
    }
    return Response(status: 404, type: "text/plain", body: Data("Not found".utf8))
}

let deckServer = HTTPServer(port: deckPort, maxBody: 8_000_000, handler: deckHandle)
