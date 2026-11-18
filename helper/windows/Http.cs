// A small HTTP/1.1 server for the Helper's two ports: 8098 (keys sent from a Pad) and 8099 (the deck itself, for
// any browser). A TcpListener, because HttpListener would need admin rights to listen on the network. One request
// per connection, each on a thread-pool thread, so handlers may block. Plain C# 5 (see build.cmd).
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

class Req
{
    public string Method, Path;                    // Path: without the query, percent-decoded
    public Dictionary<string, string> Query, Headers;
    public byte[] Body;
    public bool Local;                             // from this PC itself

    public string Text { get { return Encoding.UTF8.GetString(Body); } }

    public object Json
    {
        get
        {
            try { return Http.Json.DeserializeObject(Body.Length > 0 ? Text : "null"); }
            catch (Exception) { return null; }
        }
    }

    public string Header(string name)
    {
        string v;
        return Headers.TryGetValue(name, out v) ? v : null;
    }
}

class Resp
{
    public int Status = 200;
    public string Type = "application/json";
    public byte[] Body = new byte[0];
}

static class Http
{
    public static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 200 };

    public static Resp JsonResp(object obj, int status = 200)
    {
        return new Resp { Status = status, Body = Encoding.UTF8.GetBytes(Json.Serialize(obj)) };
    }

    public static Dictionary<string, string> DecodeQuery(string q)
    {
        var d = new Dictionary<string, string>();
        foreach (var pair in q.Split('&'))
        {
            if (pair.Length == 0) continue;
            var i = pair.IndexOf('=');
            var k = Unescape(i < 0 ? pair : pair.Substring(0, i));
            d[k] = i < 0 ? "" : Unescape(pair.Substring(i + 1));
        }
        return d;
    }

    static string Unescape(string s)
    {
        try { return Uri.UnescapeDataString(s.Replace('+', ' ')); } catch (Exception) { return s; }
    }

    /// Constant-time comparison (for the pairing code).
    public static bool SameCode(string given, string want)
    {
        var a = Encoding.UTF8.GetBytes(given ?? "");
        var b = Encoding.UTF8.GetBytes(want);
        var diff = a.Length ^ b.Length;
        for (var i = 0; i < b.Length; i++) diff |= b[i] ^ (i < a.Length ? a[i] : 0);
        return diff == 0;
    }

    static readonly Dictionary<int, string> Reasons = new Dictionary<int, string> {
        { 200, "OK" }, { 400, "Bad Request" }, { 401, "Unauthorized" }, { 403, "Forbidden" }, { 404, "Not Found" },
        { 413, "Payload Too Large" }, { 500, "Internal Server Error" }, { 503, "Service Unavailable" } };

    public static void Listen(int port, int maxBody, Func<Req, Resp> handler)
    {
        var t = new Thread(() =>
        {
            while (true)
            {
                try
                {
                    var l = new TcpListener(IPAddress.Any, port);
                    l.Start();
                    while (true)
                    {
                        var c = l.AcceptTcpClient();
                        ThreadPool.QueueUserWorkItem(_ => Serve(c, maxBody, handler));
                    }
                }
                catch (Exception) { Thread.Sleep(3000); }        // e.g. the port is still busy: try again
            }
        });
        t.IsBackground = true;
        t.Start();
    }

    static void Serve(TcpClient c, int maxBody, Func<Req, Resp> handler)
    {
        using (c)
        {
            try
            {
                c.ReceiveTimeout = 10000;
                var s = c.GetStream();
                var buf = new MemoryStream();
                var chunk = new byte[65536];
                int end = -1, length = 0;
                Resp tooBig = null;
                while (true)
                {
                    var n = s.Read(chunk, 0, chunk.Length);
                    if (n <= 0) return;
                    buf.Write(chunk, 0, n);
                    if (end < 0)
                    {
                        var b = buf.GetBuffer();
                        for (var i = 3; i < buf.Length; i++)
                            if (b[i - 3] == 13 && b[i - 2] == 10 && b[i - 1] == 13 && b[i] == 10) { end = i + 1; break; }
                        if (end < 0) { if (buf.Length > 65536) return; continue; }
                        var head = Encoding.ASCII.GetString(b, 0, end);
                        foreach (var line in head.Split(new[] { "\r\n" }, StringSplitOptions.None))
                            if (line.StartsWith("content-length:", StringComparison.OrdinalIgnoreCase)) int.TryParse(line.Substring(15).Trim(), out length);
                        if (length > maxBody) { tooBig = JsonResp(new Dictionary<string, object> { { "error", "That's too big" } }, 413); break; }
                    }
                    if (buf.Length - end >= length) break;
                }
                Resp resp = tooBig;
                if (resp == null)
                {
                    var all = buf.ToArray();
                    var lines = Encoding.ASCII.GetString(all, 0, end).Split(new[] { "\r\n" }, StringSplitOptions.None);
                    var first = lines[0].Split(' ');
                    var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                    for (var i = 1; i < lines.Length; i++)
                    {
                        var k = lines[i].IndexOf(':');
                        if (k > 0) headers[lines[i].Substring(0, k).Trim()] = lines[i].Substring(k + 1).Trim();
                    }
                    var target = first.Length > 1 ? first[1] : "/";
                    var q = target.IndexOf('?');
                    var rawPath = q < 0 ? target : target.Substring(0, q);
                    string path;
                    try { path = Uri.UnescapeDataString(rawPath); } catch (Exception) { path = rawPath; }
                    var body = new byte[length];
                    Array.Copy(all, end, body, 0, length);
                    var remote = c.Client.RemoteEndPoint as IPEndPoint;
                    var req = new Req {
                        Method = first[0], Path = path, Headers = headers, Body = body,
                        Query = q < 0 ? new Dictionary<string, string>() : DecodeQuery(target.Substring(q + 1)),
                        Local = remote != null && IPAddress.IsLoopback(remote.Address),
                    };
                    try { resp = handler(req); }
                    catch (Exception e) { resp = JsonResp(new Dictionary<string, object> { { "error", e.Message } }, 500); }
                }
                string reason;
                if (!Reasons.TryGetValue(resp.Status, out reason)) reason = "OK";
                var top = Encoding.ASCII.GetBytes("HTTP/1.1 " + resp.Status + " " + reason + "\r\nContent-Type: " + resp.Type +
                    "\r\nContent-Length: " + resp.Body.Length + "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n");
                s.Write(top, 0, top.Length);
                s.Write(resp.Body, 0, resp.Body.Length);
            }
            catch (Exception) { }
        }
    }
}
