// Smart plugs and web requests for Pad Deck keys, run from this Mac (the same as server.py does on the Pad).
// TP-Link Kasa: JSON over TCP port 9999 with a simple XOR "encryption", found by a UDP broadcast. Shelly and
// Tasmota: plain local web requests. Newer Kasa hardware that only takes TP-Link's login isn't supported.
import Foundation

// ---------- Web requests ----------

/// A blocking web request (the deck server answers each connection on its own thread).
func fetch(_ url: URL, method: String = "GET", body: Data? = nil, headers: [String: String] = [:],
           timeout: TimeInterval = 8) throws -> (Int, Data) {
    var req = URLRequest(url: url, timeoutInterval: timeout)
    req.httpMethod = method
    req.httpBody = body
    for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
    let done = DispatchSemaphore(value: 0)
    var result: (Int, Data)?
    var failure: Error?
    let task = URLSession.shared.dataTask(with: req) { data, response, error in
        if let error { failure = error }
        else { result = ((response as? HTTPURLResponse)?.statusCode ?? 0, data ?? Data()) }
        done.signal()
    }
    task.resume()
    if done.wait(timeout: .now() + timeout + 1) == .timedOut { task.cancel(); throw Failure("Couldn't reach it: timed out") }
    if let failure { throw Failure("Couldn't reach it: \(failure.localizedDescription)") }
    return result!
}

func httpSend(_ urlString: String, method: String? = nil, body: String? = nil) throws -> String {
    guard let url = URL(string: urlString) else { throw Failure("That doesn't look like a web address") }
    let hasBody = !(body ?? "").isEmpty
    let trimmed = (body ?? "").trimmingCharacters(in: .whitespaces)
    let json = trimmed.hasPrefix("{") || trimmed.hasPrefix("[")
    let (status, _) = try fetch(url, method: (method ?? (hasBody ? "POST" : "GET")).uppercased(),
                                body: hasBody ? Data(body!.utf8) : nil,
                                headers: json ? ["Content-Type": "application/json"] : [:])
    guard (200..<400).contains(status) else {
        throw Failure("The device answered \(status) \(HTTPURLResponse.localizedString(forStatusCode: status).capitalized)")
    }
    return "Sent (\(status))"
}

// ---------- Sockets ----------

func ipv4Address(_ host: String) throws -> in_addr {
    var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM, ai_protocol: 0, ai_addrlen: 0,
                         ai_canonname: nil, ai_addr: nil, ai_next: nil)
    var res: UnsafeMutablePointer<addrinfo>?
    guard getaddrinfo(host, nil, &hints, &res) == 0, let first = res else { throw Failure("Can't find \(host)") }
    defer { freeaddrinfo(res) }
    return first.pointee.ai_addr.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr }
}

func sockaddrFor(_ addr: in_addr, _ port: UInt16) -> sockaddr_in {
    var sa = sockaddr_in()
    sa.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    sa.sin_family = sa_family_t(AF_INET)
    sa.sin_port = port.bigEndian
    sa.sin_addr = addr
    return sa
}

/// Waits until the socket is readable (or writable); false on timeout.
func waitFor(_ fd: Int32, write: Bool = false, until deadline: Date) -> Bool {
    let ms = Int32(max(0, deadline.timeIntervalSinceNow * 1000))
    var p = pollfd(fd: fd, events: Int16(write ? POLLOUT : POLLIN), revents: 0)
    return poll(&p, 1, ms) > 0
}

// ---------- Kasa ----------

func kasaXor(_ data: [UInt8], decrypt: Bool = false) -> [UInt8] {
    var key: UInt8 = 171
    return data.map { b in
        let out = key ^ b
        key = decrypt ? b : out
        return out
    }
}

func kasa(_ ip: String, _ cmd: [String: Any]) throws -> [String: Any] {
    let payload = kasaXor(Array(try JSONSerialization.data(withJSONObject: cmd)))
    let addr = try ipv4Address(ip)
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { throw Failure("Couldn't reach the Kasa plug at \(ip)") }
    defer { close(fd) }
    _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
    var sa = sockaddrFor(addr, 9999)
    let deadline = Date().addingTimeInterval(4)
    let rc = withUnsafePointer(to: &sa) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
    if rc != 0 {
        guard errno == EINPROGRESS, waitFor(fd, write: true, until: deadline) else {
            throw Failure("Couldn't reach the Kasa plug at \(ip): timed out")
        }
        var err: Int32 = 0
        var len = socklen_t(MemoryLayout<Int32>.size)
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len)
        if err == ECONNREFUSED { throw Failure("This Kasa plug doesn't take local commands (newer Kasa models need TP-Link's login)") }
        if err != 0 { throw Failure("Couldn't reach the Kasa plug at \(ip): \(String(cString: strerror(err)))") }
    }
    let n = UInt32(payload.count).bigEndian
    let message = withUnsafeBytes(of: n) { Array($0) } + payload
    var sent = 0
    while sent < message.count {
        guard waitFor(fd, write: true, until: deadline) else { throw Failure("Couldn't reach the Kasa plug at \(ip): timed out") }
        let w = message[sent...].withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
        if w <= 0 { throw Failure("Couldn't reach the Kasa plug at \(ip)") }
        sent += w
    }
    var buf = [UInt8]()
    var chunk = [UInt8](repeating: 0, count: 4096)
    while buf.count < 4 || buf.count < 4 + Int(UInt32(buf[0]) << 24 | UInt32(buf[1]) << 16 | UInt32(buf[2]) << 8 | UInt32(buf[3])) {
        guard waitFor(fd, until: deadline) else { break }
        let r = recv(fd, &chunk, chunk.count, 0)
        if r <= 0 { break }
        buf += chunk[0..<r]
    }
    guard buf.count > 4, let j = try? JSONSerialization.jsonObject(with: Data(kasaXor(Array(buf[4...]), decrypt: true))) as? [String: Any] else {
        throw Failure("The Kasa plug sent an answer Pad Deck doesn't understand")
    }
    return j
}

func kasaInfo(_ ip: String) throws -> [String: Any] {
    guard let info = (try kasa(ip, ["system": ["get_sysinfo": [String: Any]()]])["system"] as? [String: Any])?["get_sysinfo"] as? [String: Any] else {
        throw Failure("The Kasa plug sent an answer Pad Deck doesn't understand")
    }
    return info
}

func kasaChild(_ info: [String: Any], _ outlet: String) -> [String: Any]? {
    (info["children"] as? [[String: Any]] ?? []).first { outlet.hasSuffix($0["id"] as? String ?? "\u{0}") }
}

func isOn(_ v: Any?) -> Bool { (v as? NSNumber)?.boolValue ?? false }

/// Turn a Kasa plug (or one outlet of a strip, by its id) on, off, or the other way round.
func kasaSwitch(_ ip: String, _ action: String, outlet: String?) throws -> String {
    let on: Bool
    if action == "toggle" {
        let info = try kasaInfo(ip)
        if let outlet {
            guard let child = kasaChild(info, outlet) else { throw Failure("That outlet isn't on the strip any more") }
            on = !isOn(child["state"])
        } else {
            on = !isOn(info["relay_state"])
        }
    } else {
        on = action == "on"
    }
    var cmd: [String: Any] = ["system": ["set_relay_state": ["state": on ? 1 : 0]]]
    if let outlet { cmd["context"] = ["child_ids": [outlet]] }
    _ = try kasa(ip, cmd)
    return "\(outlet == nil ? "Plug" : "Outlet") \(on ? "on" : "off")"
}

/// Kasa plugs on the network (they answer a broadcast): one entry per strip outlet.
func kasaFind(wait: TimeInterval = 1.5) -> [[String: String]] {
    let fd = socket(AF_INET, SOCK_DGRAM, 0)
    guard fd >= 0 else { return [] }
    defer { close(fd) }
    var yes: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &yes, socklen_t(MemoryLayout<Int32>.size))
    let probe = kasaXor(Array(#"{"system": {"get_sysinfo": {}}}"#.utf8))
    var dest = sockaddrFor(in_addr(s_addr: INADDR_BROADCAST), 9999)
    for _ in 0..<2 {
        _ = withUnsafePointer(to: &dest) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            sendto(fd, probe, probe.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
    }
    var found: [String: [String: Any]] = [:]
    let deadline = Date().addingTimeInterval(wait)
    var buf = [UInt8](repeating: 0, count: 16384)
    while Date() < deadline {
        guard waitFor(fd, until: deadline) else { break }
        var from = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let n = withUnsafeMutablePointer(to: &from) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            recvfrom(fd, &buf, buf.count, 0, $0, &len) } }
        guard n > 0 else { continue }
        let ip = String(cString: inet_ntoa(from.sin_addr))
        if let j = try? JSONSerialization.jsonObject(with: Data(kasaXor(Array(buf[0..<n]), decrypt: true))) as? [String: Any],
           let info = (j["system"] as? [String: Any])?["get_sysinfo"] as? [String: Any] {
            found[ip] = info
        }
    }
    var plugs: [[String: String]] = []
    for ip in found.keys.sorted() {
        let info = found[ip]!
        let alias = info["alias"] as? String ?? ip
        if let children = info["children"] as? [[String: Any]], !children.isEmpty {
            for c in children {
                let id = c["id"] as? String ?? ""
                let cid = id.count > 2 ? id : (info["deviceId"] as? String ?? "") + id
                plugs.append(["name": "\(alias): \(c["alias"] as? String ?? id)", "ip": ip, "outlet": cid])
            }
        } else {
            plugs.append(["name": alias, "ip": ip, "outlet": ""])
        }
    }
    return plugs
}

// ---------- Plug keys ----------

let plugURLs: [String: [String: String]] = [
    "shelly": ["toggle": "/relay/0?turn=toggle", "on": "/relay/0?turn=on", "off": "/relay/0?turn=off"],
    "shelly2": ["toggle": "/rpc/Switch.Toggle?id=0", "on": "/rpc/Switch.Set?id=0&on=true", "off": "/rpc/Switch.Set?id=0&on=false"],
    "tasmota": ["toggle": "/cm?cmnd=Power%20Toggle", "on": "/cm?cmnd=Power%20On", "off": "/cm?cmnd=Power%20Off"],
]

func validHost(_ s: String) -> Bool { !s.isEmpty && s.range(of: #"^[A-Za-z0-9.\-:]+$"#, options: .regularExpression) != nil }

func runPlug(_ a: [String: Any]) throws -> String {
    let ip = (a["ip"] as? String ?? "").trimmingCharacters(in: .whitespaces)
    let action = a["do"] as? String ?? "toggle"
    guard validHost(ip) else { throw Failure("Enter the plug's IP address (from its app or your router)") }
    guard ["toggle", "on", "off"].contains(action) else { throw Failure("Unknown plug action '\(action)'") }
    let brand = a["brand"] as? String ?? ""
    if brand == "kasa" {
        let outlet = a["outlet"] as? String ?? ""
        return try kasaSwitch(ip, action, outlet: outlet.isEmpty ? nil : outlet)
    }
    guard let paths = plugURLs[brand] else { throw Failure("Unknown plug type '\(brand)'") }
    _ = try httpSend("http://" + ip + paths[action]!)
    return "Plug: \(action)"
}

/// Whether each plug [{brand, ip, outlet}] is on: true / false, or NSNull if it didn't answer. Asked all at once.
func plugStates(_ plugs: [[String: Any]]) -> [Any] {
    let plugs = Array(plugs.prefix(40))
    let lock = NSLock()
    var kasaInfos: [String: [String: Any]] = [:]
    var states = [Any](repeating: NSNull(), count: plugs.count)
    let kasaIPs = Set(plugs.filter { $0["brand"] as? String == "kasa" }.compactMap { $0["ip"] as? String }.filter(validHost))
    let others = plugs.indices.filter { plugs[$0]["brand"] as? String != "kasa" }
    let jobs: [() -> Void] = kasaIPs.map { ip in {
        if let info = try? kasaInfo(ip) { lock.lock(); kasaInfos[ip] = info; lock.unlock() }
    } } + others.map { i in {
        let p = plugs[i], ip = p["ip"] as? String ?? "", brand = p["brand"] as? String ?? ""
        let path = ["shelly": "/relay/0", "shelly2": "/rpc/Switch.GetStatus?id=0", "tasmota": "/cm?cmnd=Power"][brand]
        guard let path, validHost(ip), let url = URL(string: "http://" + ip + path),
              let (status, data) = try? fetch(url, timeout: 3), status == 200,
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let on: Bool = brand == "tasmota" ? (j["POWER"] as? String == "ON") : isOn(j["ison"] ?? j["output"])
        lock.lock(); states[i] = on; lock.unlock()
    } }
    let group = DispatchGroup()
    for job in jobs { DispatchQueue.global().async(group: group, execute: job) }
    _ = group.wait(timeout: .now() + 6)
    lock.lock(); defer { lock.unlock() }
    for (i, p) in plugs.enumerated() where p["brand"] as? String == "kasa" {
        guard let info = kasaInfos[p["ip"] as? String ?? ""] else { continue }
        let outlet = p["outlet"] as? String ?? ""
        if outlet.isEmpty { states[i] = isOn(info["relay_state"]) }
        else if let child = kasaChild(info, outlet) { states[i] = isOn(child["state"]) }
    }
    return states
}
