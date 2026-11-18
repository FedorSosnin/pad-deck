// Pad Deck Helper: a menu bar app that lets Pad Deck keys do things on this Mac: open a website or an app,
// run a Shortcut, press a key combination. The Pad sends it requests over the network; each must carry the
// pairing code shown in the menu. Built into "Pad Deck Helper.app" by build-app.sh.
//
// GET  /ping        is the helper there, and is the pairing code right?
// GET  /apps        installed app names (for the key editor)
// GET  /shortcuts   Shortcuts names
// POST /run         {"type": "url" | "app" | "shortcut" | "hotkey", ...}
import AppKit
import ApplicationServices
import Network
import ServiceManagement

let port: UInt16 = 8098
let defaults = UserDefaults.standard

// ---------- Settings ----------

/// The pairing code, made up once and kept (an earlier script version's code carries over).
let pairingCode: String = {
    if let c = defaults.string(forKey: "code") { return c }
    var code = String(format: "%06d", Int.random(in: 0..<1_000_000))
    let old = NSHomeDirectory() + "/Library/Application Support/Pad Deck Helper/config.json"
    if let d = FileManager.default.contents(atPath: old),
       let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let c = j["code"] as? String { code = c }
    defaults.set(code, forKey: "code")
    return code
}()

var paused: Bool {
    get { defaults.bool(forKey: "paused") }
    set { defaults.set(newValue, forKey: "paused") }
}

struct Failure: Error { let message: String; init(_ m: String) { message = m } }

// ---------- What a key can do ----------

let appDirs = ["/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
               NSHomeDirectory() + "/Applications"]

func appNames() -> [String] {
    var names = Set<String>()
    for d in appDirs {
        for n in (try? FileManager.default.contentsOfDirectory(atPath: d)) ?? [] where n.hasSuffix(".app") && !n.hasPrefix(".") {
            names.insert(String(n.dropLast(4)))
        }
    }
    return names.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
}

func shortcutNames() -> [String] {
    let p = Process(), out = Pipe()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/shortcuts")
    p.arguments = ["list"]
    p.standardOutput = out
    p.standardError = FileHandle.nullDevice
    guard (try? p.run()) != nil else { return [] }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init).filter { !$0.isEmpty }
}

// US-layout key codes.
let keyCodes: [String: CGKeyCode] = {
    var k: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
        "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
        "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
        "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
        "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
        "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "left": 123, "right": 124, "down": 125, "up": 126,
    ]
    for (n, c) in zip(1...12, [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111] as [CGKeyCode]) { k["f\(n)"] = c }
    return k
}()
let modifiers: [String: CGEventFlags] = [
    "cmd": .maskCommand, "command": .maskCommand, "⌘": .maskCommand, "shift": .maskShift, "⇧": .maskShift,
    "alt": .maskAlternate, "option": .maskAlternate, "opt": .maskAlternate, "⌥": .maskAlternate,
    "ctrl": .maskControl, "control": .maskControl, "⌃": .maskControl,
]

/// Press a key combination like "cmd+shift+4" or "ctrl+f5" (needs Accessibility permission).
func pressKeys(_ combo: String) throws {
    let parts = combo.lowercased().replacingOccurrences(of: " ", with: "").split(separator: "+").map(String.init)
    guard let key = parts.last else { throw Failure("No keys given") }
    var flags = CGEventFlags()
    for m in parts.dropLast() {
        guard let f = modifiers[m] else { throw Failure("Unknown modifier: \(m) (use cmd, shift, alt/option, ctrl)") }
        flags.insert(f)
    }
    guard let code = keyCodes[key] else { throw Failure("Unknown key: \(key)") }
    guard AXIsProcessTrusted() else {
        DispatchQueue.main.async { openAccessibility() }
        throw Failure("Allow Pad Deck Helper to press keys: System Settings → Privacy & Security → Accessibility")
    }
    let src = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        let e = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: down)
        e?.flags = flags
        e?.post(tap: .cghidEventTap)
    }
}

func openAccessibility() {
    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
}

func run(_ a: [String: Any]) throws -> String {
    let type = a["type"] as? String ?? ""
    switch type {
    case "url":
        var s = (a["url"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        if !s.contains("://") { s = "https://" + s }
        guard let url = URL(string: s), let host = url.host else { throw Failure("That doesn't look like a web address") }
        NSWorkspace.shared.open(url)
        return "Opened \(host)"
    case "app":
        let name = (a["app"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        guard let path = appDirs.map({ "\($0)/\(name).app" }).first(where: { FileManager.default.fileExists(atPath: $0) }) else {
            throw Failure("Couldn't find the app “\(name)”")
        }
        NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: path), configuration: NSWorkspace.OpenConfiguration())
        return "Opened \(name)"
    case "shortcut":
        let name = a["shortcut"] as? String ?? ""
        guard shortcutNames().contains(name) else { throw Failure("There's no Shortcut called “\(name)”") }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/shortcuts")
        p.arguments = ["run", name]
        try p.run()
        return "Running \(name)"
    case "hotkey":
        let keys = a["keys"] as? String ?? ""
        try pressKeys(keys)
        return "Pressed \(keys)"
    default:
        throw Failure("Unknown action “\(type)”")
    }
}

// ---------- Web server ----------

func respond(_ c: NWConnection, _ status: Int, _ obj: [String: Any]) {
    let body = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data()
    let reason = [200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 503: "Service Unavailable"][status] ?? "OK"
    var out = Data("HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
    out.append(body)
    c.send(content: out, completion: .contentProcessed { _ in c.cancel() })
}

func handle(_ c: NWConnection, method: String, path: String, headers: [String: String], body: Data) {
    // Constant-time comparison of the pairing code.
    let given = Array((headers["x-pad-deck-code"] ?? "").utf8), want = Array(pairingCode.utf8)
    var diff = given.count ^ want.count
    for (i, b) in want.enumerated() { diff |= Int(b ^ (i < given.count ? given[i] : 0)) }
    guard diff == 0 else { return respond(c, 401, ["error": "Wrong pairing code"]) }
    switch (method, path) {
    case ("GET", "/ping"):
        respond(c, 200, ["ok": true, "computer": Host.current().localizedName ?? "Mac", "mac": true, "paused": paused])
    case ("GET", "/apps"):
        respond(c, 200, ["apps": appNames()])
    case ("GET", "/shortcuts"):
        respond(c, 200, ["shortcuts": shortcutNames()])
    case ("POST", "/run"):
        if paused { return respond(c, 503, ["error": "Pad Deck Helper is paused (menu bar on the Mac)"]) }
        do {
            let a = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
            respond(c, 200, ["ok": true, "message": try run(a)])
            DispatchQueue.main.async { Menu.shared.flash() }
        } catch let e as Failure {
            respond(c, 400, ["error": e.message])
        } catch {
            respond(c, 400, ["error": error.localizedDescription])
        }
    default:
        respond(c, 404, ["error": "Not found"])
    }
}

func receive(_ c: NWConnection, _ buffer: Data) {
    c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, done, error in
        var buf = buffer
        if let data { buf.append(data) }
        if let end = buf.range(of: Data("\r\n\r\n".utf8)) {
            let lines = String(decoding: buf[..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
            let first = lines[0].split(separator: " ").map(String.init)
            var headers: [String: String] = [:]
            for l in lines.dropFirst() {
                if let i = l.firstIndex(of: ":") {
                    headers[l[..<i].lowercased()] = l[l.index(after: i)...].trimmingCharacters(in: .whitespaces)
                }
            }
            let length = Int(headers["content-length"] ?? "0") ?? 0
            let body = buf[end.upperBound...]
            if body.count >= length, first.count >= 2 {
                return handle(c, method: first[0], path: first[1], headers: headers, body: Data(body.prefix(length)))
            }
        }
        if done || error != nil || buf.count > 1_000_000 { return c.cancel() }
        receive(c, buf)
    }
}

var listener: NWListener?
func startServer() {
    do {
        let l = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port)!)
        l.newConnectionHandler = { c in
            c.start(queue: .global())
            receive(c, Data())
        }
        l.stateUpdateHandler = { state in
            if case .failed(let e) = state {
                NSLog("Pad Deck Helper: listener failed: \(e)")
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { startServer() }   // e.g. the port was still busy
            }
        }
        l.start(queue: .global())
        listener = l
    } catch {
        NSLog("Pad Deck Helper: can't listen on \(port): \(error)")
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { startServer() }
    }
}

// ---------- Menu bar ----------

final class Menu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    static let shared = Menu()
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

    func applicationDidFinishLaunching(_ note: Notification) {
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        updateIcon()
        startServer()
        if !defaults.bool(forKey: "welcomed") {
            defaults.set(true, forKey: "welcomed")
            welcome()
        }
    }

    func updateIcon(_ symbol: String? = nil) {
        let image = NSImage(systemSymbolName: symbol ?? (paused ? "pause.rectangle" : "square.grid.3x2"),
                            accessibilityDescription: "Pad Deck Helper")
        image?.isTemplate = true
        item.button?.image = image
        item.button?.appearsDisabled = paused
    }

    /// A brief tick in the menu bar when a key ran something.
    func flash() {
        updateIcon("square.grid.3x2.fill")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.updateIcon() }
    }

    func welcome() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Pad Deck Helper is running"
        alert.informativeText = "It lives in the menu bar. To have it open when you log in, turn on Open at Login in its menu.\n\nOn the Pad: Menu → Helper, enter this "
            + "pairing code:\n\n\(pairingCode)\n\nand this Mac's name: \(ProcessInfo.processInfo.hostName)"
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Copy Code")
        if alert.runModal() == .alertSecondButtonReturn { copyCode() }
    }

    // Rebuilt each time it opens, so it always shows the current state.
    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let title = NSMenuItem(title: paused ? "Pad Deck Helper: Paused" : "Pad Deck Helper: On", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        add(menu, "Pairing Code: \(pairingCode)  (Copy)", #selector(copyCode))
        menu.addItem(.separator())
        add(menu, paused ? "Resume" : "Pause", #selector(togglePause), "p")
        let login = add(menu, "Open at Login", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        let keys = add(menu, AXIsProcessTrusted() ? "Key Presses Allowed" : "Allow Key Presses…", #selector(openAccessibilityItem))
        keys.state = AXIsProcessTrusted() ? .on : .off
        menu.addItem(.separator())
        add(menu, "Quit Pad Deck Helper", #selector(quit), "q")
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String = "") -> NSMenuItem {
        let i = menu.addItem(withTitle: title, action: action, keyEquivalent: key)
        i.target = self
        return i
    }

    @objc func copyCode() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pairingCode, forType: .string)
    }

    @objc func togglePause() {
        paused.toggle()
        updateIcon()
    }

    @objc func toggleLogin() {
        if SMAppService.mainApp.status == .enabled { try? SMAppService.mainApp.unregister() }
        else { try? SMAppService.mainApp.register() }
    }

    @objc func openAccessibilityItem() {
        // Asking once puts Pad Deck Helper in the list, ready to switch on.
        _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        openAccessibility()
    }

    @objc func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
app.delegate = Menu.shared
app.setActivationPolicy(.accessory)
app.run()
