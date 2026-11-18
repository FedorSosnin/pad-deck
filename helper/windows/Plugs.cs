// Smart plugs and web requests for Pad Deck keys, run from this PC (the same as server.py does on the Pad and
// Plugs.swift on a Mac). TP-Link Kasa: JSON over TCP port 9999 with a simple XOR "encryption", found by a UDP
// broadcast. Shelly and Tasmota: plain local web requests. Plain C# 5 (see build.cmd).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

static class Plugs
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

    // ---------- Web requests ----------

    /// A blocking web request: (status, body). Throws Failure if it can't reach the address.
    public static KeyValuePair<int, byte[]> Fetch(string url, string method, byte[] body, Dictionary<string, string> headers, int timeoutMs)
    {
        var req = (HttpWebRequest)WebRequest.Create(url);
        req.Method = method ?? "GET";
        req.Timeout = timeoutMs;
        req.ReadWriteTimeout = timeoutMs;
        req.AllowAutoRedirect = true;
        if (headers != null)
            foreach (var h in headers)
            {
                if (h.Key.Equals("Content-Type", StringComparison.OrdinalIgnoreCase)) req.ContentType = h.Value;
                else req.Headers[h.Key] = h.Value;
            }
        try
        {
            if (body != null)
            {
                req.ContentLength = body.Length;
                using (var s = req.GetRequestStream()) s.Write(body, 0, body.Length);
            }
            using (var res = (HttpWebResponse)req.GetResponse())
                return new KeyValuePair<int, byte[]>((int)res.StatusCode, ReadAll(res.GetResponseStream()));
        }
        catch (WebException e)
        {
            var res = e.Response as HttpWebResponse;
            if (res != null)
                using (res) return new KeyValuePair<int, byte[]>((int)res.StatusCode, ReadAll(res.GetResponseStream()));
            throw new Failure("Couldn't reach it: " + e.Message);
        }
    }

    static byte[] ReadAll(Stream s)
    {
        using (var m = new MemoryStream()) { s.CopyTo(m); return m.ToArray(); }
    }

    static string Reason(int status)
    {
        var name = ((HttpStatusCode)status).ToString();          // "NotFound" → "Not Found"
        return Regex.IsMatch(name, "^[0-9]+$") ? "" : Regex.Replace(name, "(?<=[a-z])(?=[A-Z])", " ");
    }

    public static string HttpSend(string url, string method, string body)
    {
        Uri uri;
        if (!Uri.TryCreate(url, UriKind.Absolute, out uri)) throw new Failure("That doesn't look like a web address");
        var hasBody = !string.IsNullOrEmpty(body);
        var t = (body ?? "").Trim();
        var headers = new Dictionary<string, string>();
        if (t.StartsWith("{") || t.StartsWith("[")) headers["Content-Type"] = "application/json";
        var r = Fetch(url, (method ?? (hasBody ? "POST" : "GET")).ToUpperInvariant(),
                      hasBody ? Encoding.UTF8.GetBytes(body) : null, headers, 8000);
        if (r.Key < 200 || r.Key >= 400) throw new Failure("The device answered " + r.Key + " " + Reason(r.Key));
        return "Sent (" + r.Key + ")";
    }

    // ---------- Kasa ----------

    static byte[] Xor(byte[] data, bool decrypt)
    {
        byte key = 171;
        var out_ = new byte[data.Length];
        for (var i = 0; i < data.Length; i++)
        {
            out_[i] = (byte)(key ^ data[i]);
            key = decrypt ? data[i] : out_[i];
        }
        return out_;
    }

    static IPAddress Resolve(string host)
    {
        IPAddress ip;
        if (IPAddress.TryParse(host, out ip)) return ip;
        try
        {
            var a = Dns.GetHostAddresses(host).FirstOrDefault(x => x.AddressFamily == AddressFamily.InterNetwork);
            if (a != null) return a;
        }
        catch (Exception) { }
        throw new Failure("Can't find " + host);
    }

    public static Dictionary<string, object> Kasa(string ip, object cmd)
    {
        var payload = Xor(Encoding.UTF8.GetBytes(Json.Serialize(cmd)), false);
        var addr = Resolve(ip);
        using (var c = new TcpClient(AddressFamily.InterNetwork))
        {
            try
            {
                var ar = c.BeginConnect(addr, 9999, null, null);
                if (!ar.AsyncWaitHandle.WaitOne(4000)) throw new Failure("Couldn't reach the Kasa plug at " + ip + ": timed out");
                c.EndConnect(ar);
            }
            catch (SocketException e)
            {
                if (e.SocketErrorCode == SocketError.ConnectionRefused)
                    throw new Failure("This Kasa plug doesn't take local commands (newer Kasa models need TP-Link's login)");
                throw new Failure("Couldn't reach the Kasa plug at " + ip + ": " + e.Message);
            }
            c.ReceiveTimeout = 4000;
            c.SendTimeout = 4000;
            var s = c.GetStream();
            var len = BitConverter.GetBytes(payload.Length);
            if (BitConverter.IsLittleEndian) Array.Reverse(len);
            s.Write(len, 0, 4);
            s.Write(payload, 0, payload.Length);
            var buf = new MemoryStream();
            var chunk = new byte[4096];
            try
            {
                while (true)
                {
                    var b = buf.ToArray();
                    if (b.Length >= 4 && b.Length >= 4 + ((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3])) break;
                    var n = s.Read(chunk, 0, chunk.Length);
                    if (n <= 0) break;
                    buf.Write(chunk, 0, n);
                }
            }
            catch (IOException) { }
            var all = buf.ToArray();
            try
            {
                if (all.Length <= 4) throw new Exception();
                var j = Json.DeserializeObject(Encoding.UTF8.GetString(Xor(all.Skip(4).ToArray(), true))) as Dictionary<string, object>;
                if (j == null) throw new Exception();
                return j;
            }
            catch (Exception) { throw new Failure("The Kasa plug sent an answer Pad Deck doesn't understand"); }
        }
    }

    static object SysinfoCmd()
    {
        return new Dictionary<string, object> { { "system", new Dictionary<string, object> { { "get_sysinfo", new Dictionary<string, object>() } } } };
    }

    static Dictionary<string, object> Sub(Dictionary<string, object> d, string key)
    {
        object v;
        return d != null && d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null;
    }

    public static Dictionary<string, object> KasaInfo(string ip)
    {
        var info = Sub(Sub(Kasa(ip, SysinfoCmd()), "system"), "get_sysinfo");
        if (info == null) throw new Failure("The Kasa plug sent an answer Pad Deck doesn't understand");
        return info;
    }

    static IEnumerable<Dictionary<string, object>> Children(Dictionary<string, object> info)
    {
        object v;
        if (!info.TryGetValue("children", out v) || !(v is object[])) return new Dictionary<string, object>[0];
        return ((object[])v).OfType<Dictionary<string, object>>();
    }

    static Dictionary<string, object> Child(Dictionary<string, object> info, string outlet)
    {
        return Children(info).FirstOrDefault(c => c.ContainsKey("id") && outlet.EndsWith(Convert.ToString(c["id"])));
    }

    static bool On(Dictionary<string, object> d, string key)
    {
        object v;
        if (d == null || !d.TryGetValue(key, out v) || v == null) return false;
        if (v is bool) return (bool)v;
        try { return Convert.ToDouble(v) != 0; } catch (Exception) { return false; }
    }

    public static string KasaSwitch(string ip, string action, string outlet)
    {
        bool on;
        if (action == "toggle")
        {
            var info = KasaInfo(ip);
            if (outlet != null)
            {
                var child = Child(info, outlet);
                if (child == null) throw new Failure("That outlet isn't on the strip any more");
                on = !On(child, "state");
            }
            else on = !On(info, "relay_state");
        }
        else on = action == "on";
        var cmd = new Dictionary<string, object> {
            { "system", new Dictionary<string, object> { { "set_relay_state", new Dictionary<string, object> { { "state", on ? 1 : 0 } } } } } };
        if (outlet != null) cmd["context"] = new Dictionary<string, object> { { "child_ids", new[] { outlet } } };
        Kasa(ip, cmd);
        return (outlet == null ? "Plug" : "Outlet") + " " + (on ? "on" : "off");
    }

    /// Kasa plugs on the network (they answer a broadcast): one entry per strip outlet.
    public static List<Dictionary<string, string>> KasaFind()
    {
        var found = new SortedDictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
        using (var u = new UdpClient(AddressFamily.InterNetwork))
        {
            u.EnableBroadcast = true;
            var probe = Xor(Encoding.UTF8.GetBytes("{\"system\": {\"get_sysinfo\": {}}}"), false);
            var dest = new IPEndPoint(IPAddress.Broadcast, 9999);
            try { u.Send(probe, probe.Length, dest); u.Send(probe, probe.Length, dest); } catch (Exception) { return new List<Dictionary<string, string>>(); }
            var end = DateTime.UtcNow.AddMilliseconds(1500);
            while (DateTime.UtcNow < end)
            {
                u.Client.ReceiveTimeout = Math.Max(1, (int)(end - DateTime.UtcNow).TotalMilliseconds);
                try
                {
                    var from = new IPEndPoint(IPAddress.Any, 0);
                    var data = u.Receive(ref from);
                    var j = Json.DeserializeObject(Encoding.UTF8.GetString(Xor(data, true))) as Dictionary<string, object>;
                    var info = Sub(Sub(j, "system"), "get_sysinfo");
                    if (info != null) found[from.Address.ToString()] = info;
                }
                catch (SocketException) { break; }
                catch (Exception) { }
            }
        }
        var plugs = new List<Dictionary<string, string>>();
        foreach (var kv in found)
        {
            var info = kv.Value;
            var alias = info.ContainsKey("alias") ? Convert.ToString(info["alias"]) : kv.Key;
            var children = Children(info).ToList();
            if (children.Count > 0)
                foreach (var c in children)
                {
                    var id = Convert.ToString(c.ContainsKey("id") ? c["id"] : "");
                    var cid = id.Length > 2 ? id : (info.ContainsKey("deviceId") ? Convert.ToString(info["deviceId"]) : "") + id;
                    plugs.Add(new Dictionary<string, string> { { "name", alias + ": " + (c.ContainsKey("alias") ? Convert.ToString(c["alias"]) : id) },
                                                              { "ip", kv.Key }, { "outlet", cid } });
                }
            else plugs.Add(new Dictionary<string, string> { { "name", alias }, { "ip", kv.Key }, { "outlet", "" } });
        }
        return plugs;
    }

    // ---------- Plug keys ----------

    static readonly Dictionary<string, Dictionary<string, string>> PlugUrls = new Dictionary<string, Dictionary<string, string>> {
        { "shelly", new Dictionary<string, string> { { "toggle", "/relay/0?turn=toggle" }, { "on", "/relay/0?turn=on" }, { "off", "/relay/0?turn=off" } } },
        { "shelly2", new Dictionary<string, string> { { "toggle", "/rpc/Switch.Toggle?id=0" }, { "on", "/rpc/Switch.Set?id=0&on=true" }, { "off", "/rpc/Switch.Set?id=0&on=false" } } },
        { "tasmota", new Dictionary<string, string> { { "toggle", "/cm?cmnd=Power%20Toggle" }, { "on", "/cm?cmnd=Power%20On" }, { "off", "/cm?cmnd=Power%20Off" } } },
    };

    public static bool ValidHost(string s) { return !string.IsNullOrEmpty(s) && Regex.IsMatch(s, @"^[A-Za-z0-9.\-:]+$"); }

    public static string Str(Dictionary<string, object> a, string key)
    {
        object v;
        return a != null && a.TryGetValue(key, out v) && v != null ? Convert.ToString(v) : "";
    }

    public static string RunPlug(Dictionary<string, object> a)
    {
        var ip = Str(a, "ip").Trim();
        var action = a.ContainsKey("do") ? Str(a, "do") : "toggle";
        if (!ValidHost(ip)) throw new Failure("Enter the plug's IP address (from its app or your router)");
        if (action != "toggle" && action != "on" && action != "off") throw new Failure("Unknown plug action '" + action + "'");
        var brand = Str(a, "brand");
        if (brand == "kasa")
        {
            var outlet = Str(a, "outlet");
            return KasaSwitch(ip, action, outlet.Length > 0 ? outlet : null);
        }
        Dictionary<string, string> paths;
        if (!PlugUrls.TryGetValue(brand, out paths)) throw new Failure("Unknown plug type '" + brand + "'");
        HttpSend("http://" + ip + paths[action], null, null);
        return "Plug: " + action;
    }

    /// Whether each plug {brand, ip, outlet} is on: true / false, or null if it didn't answer. Asked all at once.
    public static object[] States(object[] list)
    {
        var plugs = list.OfType<Dictionary<string, object>>().Take(40).ToList();
        var states = new object[plugs.Count];
        var infos = new Dictionary<string, Dictionary<string, object>>();
        var gate = new object();
        var threads = new List<Thread>();
        foreach (var ip in plugs.Where(p => Str(p, "brand") == "kasa").Select(p => Str(p, "ip")).Where(ValidHost).Distinct())
        {
            var host = ip;
            threads.Add(new Thread(() => { try { var info = KasaInfo(host); lock (gate) infos[host] = info; } catch (Exception) { } }));
        }
        for (var i = 0; i < plugs.Count; i++)
        {
            var p = plugs[i];
            var brand = Str(p, "brand");
            if (brand == "kasa") continue;
            var index = i;
            threads.Add(new Thread(() =>
            {
                try
                {
                    var ip = Str(p, "ip");
                    var path = brand == "shelly" ? "/relay/0" : brand == "shelly2" ? "/rpc/Switch.GetStatus?id=0" : brand == "tasmota" ? "/cm?cmnd=Power" : null;
                    if (path == null || !ValidHost(ip)) return;
                    var r = Fetch("http://" + ip + path, "GET", null, null, 3000);
                    if (r.Key != 200) return;
                    var j = Json.DeserializeObject(Encoding.UTF8.GetString(r.Value)) as Dictionary<string, object>;
                    if (j == null) return;
                    bool on = brand == "tasmota" ? Str(j, "POWER") == "ON" : On(j, j.ContainsKey("ison") ? "ison" : "output");
                    lock (gate) states[index] = on;
                }
                catch (Exception) { }
            }));
        }
        foreach (var t in threads) { t.IsBackground = true; t.Start(); }
        var until = DateTime.UtcNow.AddSeconds(6);
        foreach (var t in threads) t.Join(Math.Max(0, (int)(until - DateTime.UtcNow).TotalMilliseconds));
        lock (gate)
        {
            for (var i = 0; i < plugs.Count; i++)
            {
                var p = plugs[i];
                Dictionary<string, object> info;
                if (Str(p, "brand") != "kasa" || !infos.TryGetValue(Str(p, "ip"), out info)) continue;
                var outlet = Str(p, "outlet");
                if (outlet.Length == 0) states[i] = On(info, "relay_state");
                else { var c = Child(info, outlet); if (c != null) states[i] = On(c, "state"); }
            }
            return (object[])states.Clone();
        }
    }
}
