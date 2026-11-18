// A small HTTP/1.1 server for the Helper's two ports: 8098 (keys sent from a Pad) and 8099 (the deck itself,
// for any browser). One request per connection; each runs on its own thread, so handlers may block.
import Foundation
import Network

struct Request {
    let method: String
    let path: String                 // without the query, percent-decoded
    let query: [String: String]
    let headers: [String: String]    // lower-case names
    let body: Data
    let local: Bool                  // from this Mac itself

    var json: Any? { try? JSONSerialization.jsonObject(with: body) }
}

struct Response {
    var status = 200
    var type = "application/json"
    var body = Data()
    var headers: [String: String] = [:]
}

func json(_ obj: Any, _ status: Int = 200) -> Response {
    Response(status: status, body: (try? JSONSerialization.data(withJSONObject: obj, options: [.withoutEscapingSlashes])) ?? Data())
}

let reasons = [200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
               413: "Payload Too Large", 500: "Internal Server Error", 503: "Service Unavailable"]

func decodeQuery(_ q: Substring) -> [String: String] {
    var out: [String: String] = [:]
    for pair in q.split(separator: "&") {
        let kv = pair.split(separator: "=", maxSplits: 1).map {
            String($0).replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? String($0)
        }
        if let k = kv.first { out[k] = kv.count > 1 ? kv[1] : "" }
    }
    return out
}

final class HTTPServer {
    let port: UInt16
    let handler: (Request) -> Response
    let maxBody: Int
    private var listener: NWListener?

    init(port: UInt16, maxBody: Int = 1_000_000, handler: @escaping (Request) -> Response) {
        self.port = port
        self.maxBody = maxBody
        self.handler = handler
    }

    func start() {
        do {
            let l = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port)!)
            l.newConnectionHandler = { [weak self] c in
                c.start(queue: .global())
                self?.receive(c, Data())
            }
            l.stateUpdateHandler = { [weak self, weak l] state in
                // A busy port shows up as .waiting (or .failed): try again in a few seconds.
                switch state {
                case .failed(let e), .waiting(let e):
                    NSLog("Pad Deck Helper: port \(self?.port ?? 0): \(e)")
                    l?.cancel()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self?.start() }
                default: break
                }
            }
            l.start(queue: .global())
            listener = l
        } catch {
            NSLog("Pad Deck Helper: can't listen on \(port): \(error)")
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.start() }
        }
    }

    static func isLocal(_ c: NWConnection) -> Bool {
        guard case .hostPort(let host, _) = c.endpoint else { return false }
        switch host {
        case .ipv4(let a): return a.isLoopback
        case .ipv6(let a): return a.isLoopback || (a.asIPv4?.isLoopback ?? false)
        default: return false
        }
    }

    private func send(_ c: NWConnection, _ r: Response) {
        var head = "HTTP/1.1 \(r.status) \(reasons[r.status] ?? "OK")\r\nContent-Type: \(r.type)\r\n"
            + "Content-Length: \(r.body.count)\r\nCache-Control: no-store\r\nConnection: close\r\n"
        for (k, v) in r.headers { head += "\(k): \(v)\r\n" }
        var out = Data((head + "\r\n").utf8)
        out.append(r.body)
        c.send(content: out, completion: .contentProcessed { _ in c.cancel() })
    }

    private func receive(_ c: NWConnection, _ buffer: Data) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 262_144) { [self] data, _, done, error in
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
                if length > maxBody { return send(c, json(["error": "That's too big"], 413)) }
                let body = buf[end.upperBound...]
                if body.count >= length, first.count >= 2 {
                    let target = first[1]
                    let qi = target.firstIndex(of: "?")
                    let rawPath = qi.map { String(target[..<$0]) } ?? target
                    let req = Request(method: first[0], path: rawPath.removingPercentEncoding ?? rawPath,
                                      query: qi.map { decodeQuery(target[target.index(after: $0)...]) } ?? [:],
                                      headers: headers, body: Data(body.prefix(length)), local: HTTPServer.isLocal(c))
                    DispatchQueue.global().async { self.send(c, self.handler(req)) }
                    return
                }
            }
            if done || error != nil || buf.count > maxBody + 65536 { return c.cancel() }
            receive(c, buf)
        }
    }
}

/// Constant-time comparison (for the pairing code).
func sameCode(_ given: String?, _ want: String) -> Bool {
    let g = Array((given ?? "").utf8), w = Array(want.utf8)
    var diff = g.count ^ w.count
    for (i, b) in w.enumerated() { diff |= Int(b ^ (i < g.count ? g[i] : 0)) }
    return diff == 0
}
