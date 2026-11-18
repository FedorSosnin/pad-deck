// The deck itself, for any browser on the network: http://this-pc:8099. Serves the Pad Deck web app (packed into
// the .exe as web.zip by build.cmd) and the same /api the Pad's server.py answers, so the web app works unchanged.
// The contract is docs/server-api.md; tools/check-server.py checks it. Saved data lives in
// %APPDATA%\Pad Deck Helper\. Plain C# 5 (see build.cmd).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;

static class Deck
{
    public const int Port = 8099;
    static readonly string DataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Pad Deck Helper");
    static readonly string LayoutPath = Path.Combine(DataDir, "layout.json");
    static readonly string SettingsPath = Path.Combine(DataDir, "settings.json");
    static readonly string LayoutsDir = Path.Combine(DataDir, "layouts");
    static readonly string IconsDir = Path.Combine(DataDir, "icons");
    static readonly string[] IconTypes = { ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif" };
    const string BackupName = "Before last load";
    static readonly object FileLock = new object();

    /// The web app's files: path ("index.html", "icons/deck-mic.svg") → bytes.
    static readonly Dictionary<string, byte[]> Web = LoadWeb();

    static Dictionary<string, byte[]> LoadWeb()
    {
        var files = new Dictionary<string, byte[]>(StringComparer.Ordinal);
        using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("web.zip"))
        {
            if (s == null) return files;
            using (var zip = new ZipArchive(s, ZipArchiveMode.Read))
                foreach (var e in zip.Entries)
                {
                    if (e.Length == 0 && e.FullName.EndsWith("/")) continue;
                    using (var es = e.Open())
                    using (var m = new MemoryStream()) { es.CopyTo(m); files[e.FullName.Replace('\\', '/')] = m.ToArray(); }
                }
        }
        return files;
    }

    static Dictionary<string, object> DefaultSettings()
    {
        return new Dictionary<string, object> {
            { "sound", true }, { "vibrate", true }, { "volume", 0.6 }, { "leds", true }, { "obsHost", "" }, { "obsPort", 4455 },
            { "obsPassword", "" }, { "brightness", 180 }, { "helperHost", "" }, { "helperPort", 8098 }, { "helperCode", "" } };
    }

    static readonly Dictionary<string, string> Types = new Dictionary<string, string> {
        { ".html", "text/html; charset=utf-8" }, { ".js", "text/javascript; charset=utf-8" }, { ".css", "text/css; charset=utf-8" },
        { ".json", "application/json" }, { ".webmanifest", "application/manifest+json" }, { ".svg", "image/svg+xml" },
        { ".png", "image/png" }, { ".jpg", "image/jpeg" }, { ".jpeg", "image/jpeg" }, { ".gif", "image/gif" }, { ".webp", "image/webp" },
        { ".woff2", "font/woff2" }, { ".mp4", "video/mp4" }, { ".ico", "image/x-icon" } };

    // ---------- Files ----------

    static object ReadJson(string path)
    {
        try { return Http.Json.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)); } catch (Exception) { return null; }
    }

    static void WriteAtomic(string path, byte[] data)
    {
        var tmp = path + ".tmp";
        File.WriteAllBytes(tmp, data);
        if (File.Exists(path)) File.Replace(tmp, path, null);
        else File.Move(tmp, path);
    }

    static void WriteJson(object obj, string path) { WriteAtomic(path, Encoding.UTF8.GetBytes(Http.Json.Serialize(obj))); }

    static object EmptyLayout()
    {
        return new Dictionary<string, object> { { "columns", 4 }, { "rows", 3 },
            { "pages", new object[] { new Dictionary<string, object> { { "buttons", new object[0] } } } } };
    }

    /// First run: an empty layout, plus the example layouts that ship with Pad Deck.
    public static void Prepare()
    {
        Directory.CreateDirectory(IconsDir);
        if (!Directory.Exists(LayoutsDir))
        {
            Directory.CreateDirectory(LayoutsDir);
            foreach (var kv in Web.Where(kv => kv.Key.StartsWith("layouts/") && kv.Key.EndsWith(".json")))
                File.WriteAllBytes(Path.Combine(LayoutsDir, kv.Key.Substring(8)), kv.Value);
        }
        if (!File.Exists(LayoutPath)) WriteJson(EmptyLayout(), LayoutPath);
    }

    // ---------- Settings ----------

    static Dictionary<string, object> LoadSettings()
    {
        var s = DefaultSettings();
        var saved = ReadJson(SettingsPath) as Dictionary<string, object>;
        if (saved != null) foreach (var kv in saved) s[kv.Key] = kv.Value;
        return s;
    }

    /// A value in the type of the default (like server.py's type(default)(value)), or null.
    static object Coerce(object v, object d)
    {
        try
        {
            if (d is bool) return v is string ? (object)(((string)v).Length > 0) : Convert.ToBoolean(v, CultureInfo.InvariantCulture);
            if (d is string) return Convert.ToString(v, CultureInfo.InvariantCulture);
            if (d is double) return Convert.ToDouble(v, CultureInfo.InvariantCulture);
            if (d is int) return (int)Convert.ToDouble(v, CultureInfo.InvariantCulture);
        }
        catch (Exception) { }
        return null;
    }

    // ---------- Layouts ----------

    static string LayoutFile(string name)
    {
        var slug = Regex.Replace(name.ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-');
        if (slug.Length > 60) slug = slug.Substring(0, 60);
        if (slug.Length == 0) throw new Failure("Give the layout a name");
        return Path.Combine(LayoutsDir, slug + ".json");
    }

    static void SaveLayoutAs(string name, object layout)
    {
        var path = LayoutFile(name);
        if (layout == null) layout = ReadJson(LayoutPath);
        if (layout == null) throw new Failure("There's no layout to save");
        WriteJson(new Dictionary<string, object> {
            { "name", name }, { "saved", DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) }, { "layout", layout } }, path);
    }

    static object Get(Dictionary<string, object> d, string key, object fallback)
    {
        object v;
        return d != null && d.TryGetValue(key, out v) && v != null ? v : fallback;
    }

    static List<Dictionary<string, object>> ListLayouts()
    {
        var out_ = new List<Dictionary<string, object>>();
        if (!Directory.Exists(LayoutsDir)) return out_;
        foreach (var f in Directory.GetFiles(LayoutsDir, "*.json"))
        {
            var e = ReadJson(f) as Dictionary<string, object>;
            var layout = Get(e, "layout", null) as Dictionary<string, object>;
            if (layout == null) continue;
            var pages = Get(layout, "pages", null) as object[] ??
                new object[] { new Dictionary<string, object> { { "buttons", Get(layout, "buttons", new object[0]) } } };
            var buttons = pages.OfType<Dictionary<string, object>>()
                .Sum(p => (Get(p, "buttons", new object[0]) as object[] ?? new object[0]).Count(b => b != null));
            out_.Add(new Dictionary<string, object> {
                { "name", Get(e, "name", "") }, { "saved", Get(e, "saved", "") }, { "pages", pages.Length },
                { "columns", Get(layout, "columns", 4) }, { "rows", Get(layout, "rows", 3) }, { "buttons", buttons } });
        }
        return out_.OrderByDescending(e => Convert.ToString(e["saved"]), StringComparer.Ordinal).ToList();
    }

    /// Make a layout current (null: a fresh, empty 4×3 one); the one it replaces is kept as "Before last load".
    static void MakeCurrent(object layout)
    {
        var current = ReadJson(LayoutPath);
        if (current != null) SaveLayoutAs(BackupName, current);
        WriteJson(layout ?? EmptyLayout(), LayoutPath);
    }

    static bool ValidLayout(object v)
    {
        var l = v as Dictionary<string, object>;
        var pages = Get(l, "pages", null) as object[];
        return pages != null && pages.Length > 0 &&
               pages.All(p => p is Dictionary<string, object> && Get((Dictionary<string, object>)p, "buttons", null) is object[]);
    }

    // ---------- Icons ----------

    static bool IsIcon(string name) { return !name.StartsWith(".") && IconTypes.Contains(Path.GetExtension(name).ToLowerInvariant()); }

    static List<string> IconNames()
    {
        var names = new HashSet<string>(Web.Keys.Where(k => k.StartsWith("icons/")).Select(k => k.Substring(6)).Where(IsIcon));
        if (Directory.Exists(IconsDir)) foreach (var f in Directory.GetFiles(IconsDir)) if (IsIcon(Path.GetFileName(f))) names.Add(Path.GetFileName(f));
        var list = names.ToList();
        list.Sort(StringComparer.Ordinal);
        return list;
    }

    static string SafeIconName(string raw)
    {
        var b = Path.GetFileName((raw ?? "").Replace('\\', '/').Split('/').Last());
        return Regex.Replace(b, @"[^\w.-]", "_");
    }

    /// Copy the layout (and the icons it uses that this PC doesn't have) from a Pad on the network.
    static int Import(string raw)
    {
        var host = (raw ?? "").Trim().Replace("http://", "").Split('/')[0];
        if (!Plugs.ValidHost(host)) throw new Failure("Enter the Pad's address, e.g. pad7.local");
        var b = "http://" + (host.Contains(":") ? host : host + ":8099");
        KeyValuePair<int, byte[]> r;
        try { r = Plugs.Fetch(b + "/api/layout", "GET", null, new Dictionary<string, string> { { "X-Pad-Deck-Code", Settings.Code } }, 6000); }
        catch (Exception) { throw new Failure("Can't reach a Pad at " + host); }
        if (r.Key == 401) throw new Failure("That Pad has a different pairing code. Set this Helper's code on it (Menu → Helper).");
        object layout = null;
        try { layout = Http.Json.DeserializeObject(Encoding.UTF8.GetString(r.Value)); } catch (Exception) { }
        if (r.Key != 200 || !ValidLayout(layout)) throw new Failure(host + " didn't send a Pad Deck layout");
        var have = new HashSet<string>(IconNames());
        var wanted = new HashSet<string>();
        foreach (var p in ((object[])((Dictionary<string, object>)layout)["pages"]).OfType<Dictionary<string, object>>())
            foreach (var btn in (Get(p, "buttons", new object[0]) as object[] ?? new object[0]).OfType<Dictionary<string, object>>())
            {
                var icon = Get(btn, "icon", null) as string;
                if (icon != null) wanted.Add(icon);
            }
        var copied = 0;
        foreach (var name in wanted.Where(n => !have.Contains(n)))
        {
            if (SafeIconName(name) != name || !IsIcon(name)) continue;
            try
            {
                var img = Plugs.Fetch(b + "/icons/" + Uri.EscapeDataString(name), "GET", null, null, 6000);
                if (img.Key != 200) continue;
                File.WriteAllBytes(Path.Combine(IconsDir, name), img.Value);
                copied++;
            }
            catch (Exception) { }
        }
        lock (FileLock) MakeCurrent(layout);
        return copied;
    }

    // ---------- Addresses (for the phone set-up page) ----------

    static List<string> Addresses()
    {
        var list = new List<string>();
        try
        {
            foreach (var ni in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != OperationalStatus.Up) continue;
                if (ni.NetworkInterfaceType != NetworkInterfaceType.Ethernet && ni.NetworkInterfaceType != NetworkInterfaceType.Wireless80211) continue;
                var d = ni.Description.ToLowerInvariant();
                if (d.Contains("virtual") || d.Contains("hyper-v") || d.Contains("vpn")) continue;
                foreach (var a in ni.GetIPProperties().UnicastAddresses)
                    if (a.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(a.Address)) list.Add(a.Address.ToString());
            }
        }
        catch (Exception) { }
        return list;
    }

    // ---------- Routes ----------

    static Resp Err(string message, int status) { return Http.JsonResp(new Dictionary<string, object> { { "error", message } }, status); }
    static Resp Ok() { return Http.JsonResp(new Dictionary<string, object> { { "ok", true } }); }

    public static Resp Handle(Req r)
    {
        var paired = r.Local || Http.SameCode(r.Header("X-Pad-Deck-Code"), Settings.Code);
        if (!r.Path.StartsWith("/api/")) return ServeFile(r.Path);
        if (r.Path == "/api/device")
            return Http.JsonResp(new Dictionary<string, object> { { "device", "helper" }, { "os", "windows" }, { "local", r.Local }, { "paired", paired } });
        if (!paired) return Http.JsonResp(new Dictionary<string, object> { { "error", "Enter the pairing code" }, { "pair", true } }, 401);
        try { return Route(r); }
        catch (Failure e) { return Err(e.Message, 400); }
        catch (Exception e) { return Err(e.Message, 500); }
    }

    static Resp Route(Req r)
    {
        var body = r.Json as Dictionary<string, object>;
        var key = r.Method + " " + r.Path;
        switch (key)
        {
            case "GET /api/layout":
                return Http.JsonResp(ReadJson(LayoutPath) ?? EmptyLayout());
            case "PUT /api/layout":
                if (!ValidLayout(body)) return Err("invalid layout", 400);
                lock (FileLock) WriteJson(body, LayoutPath);
                return Ok();
            case "GET /api/settings":
                return Http.JsonResp(LoadSettings());
            case "PUT /api/settings":
            {
                if (body == null) return Err("invalid settings", 400);
                lock (FileLock)
                {
                    var s = LoadSettings();
                    foreach (var d in DefaultSettings())
                    {
                        object v;
                        if (!body.TryGetValue(d.Key, out v) || v == null) continue;
                        var c = Coerce(v, d.Value);
                        if (c != null) s[d.Key] = c;
                    }
                    WriteJson(s, SettingsPath);
                }
                return Ok();
            }
            case "GET /api/layouts":
                return Http.JsonResp(ListLayouts());
            case "POST /api/layouts":
                lock (FileLock) SaveLayoutAs(Plugs.Str(body, "name").Trim(), null);
                return Ok();
            case "POST /api/layouts/load":
            {
                var e = ReadJson(LayoutFile(Plugs.Str(body, "name").Trim())) as Dictionary<string, object>;
                var layout = Get(e, "layout", null);
                if (layout == null) return Err("That layout no longer exists", 404);
                lock (FileLock) MakeCurrent(layout);
                return Ok();
            }
            case "POST /api/layouts/new":
                lock (FileLock) MakeCurrent(null);
                return Ok();
            case "POST /api/layouts/import":
                return Http.JsonResp(new Dictionary<string, object> { { "ok", true }, { "icons", Import(Plugs.Str(body, "host")) } });
            case "DELETE /api/layouts":
            {
                string name;
                r.Query.TryGetValue("name", out name);
                try { var f = LayoutFile(name ?? ""); if (File.Exists(f)) File.Delete(f); } catch (Failure) { }
                return Ok();
            }
            case "GET /api/icons":
                return Http.JsonResp(IconNames());
            case "POST /api/icons":
            {
                var name = SafeIconName(r.Header("X-Filename"));
                if (!IsIcon(name) || r.Body.Length == 0) return Err("unsupported file type", 400);
                WriteAtomic(Path.Combine(IconsDir, name), r.Body);
                return Http.JsonResp(new Dictionary<string, object> { { "name", name } });
            }
            case "POST /api/run":
            {
                if (body == null) throw new Failure("Unknown action 'None'");
                if (Settings.Paused) throw new Failure("Pad Deck Helper is paused (tray icon on the PC)");
                string message;
                var type = Plugs.Str(body, "type");
                if (type == "http")
                {
                    var url = Plugs.Str(body, "url").Trim();
                    if (!url.StartsWith("http://") && !url.StartsWith("https://")) throw new Failure("The web address should start with http:// or https://");
                    var method = Plugs.Str(body, "method");
                    message = Plugs.HttpSend(url, method.Length > 0 ? method : null, Plugs.Str(body, "body"));
                }
                else if (type == "plug") message = Plugs.RunPlug(body);
                else message = Actions.Run(body);
                Server.NotifyRan();
                return Http.JsonResp(new Dictionary<string, object> { { "ok", true }, { "message", message } });
            }
            case "GET /api/helper":
            {
                string what;
                if (!r.Query.TryGetValue("what", out what)) what = "ping";
                if (what == "ping")
                    return Http.JsonResp(new Dictionary<string, object> {
                        { "ok", true }, { "computer", Environment.MachineName }, { "mac", false }, { "windows", true }, { "paused", Settings.Paused } });
                if (what == "apps") return Http.JsonResp(new Dictionary<string, object> { { "apps", Actions.Apps().Keys.ToList() } });
                if (what == "shortcuts") return Http.JsonResp(new Dictionary<string, object> { { "shortcuts", new string[0] } });
                return Err("Not found", 404);
            }
            case "GET /api/plugs":
                return Http.JsonResp(new Dictionary<string, object> { { "plugs", Plugs.KasaFind() } });
            case "POST /api/plugs/state":
                return Http.JsonResp(new Dictionary<string, object> { { "states", Plugs.States(r.Json as object[] ?? new object[0]) } });
            case "POST /api/buzz":
                return Ok();
            case "GET /api/connect":
                if (!r.Local) return Err("Only on this PC", 403);
                return Http.JsonResp(new Dictionary<string, object> {
                    { "addresses", Addresses() }, { "port", Port }, { "code", Settings.Code },
                    { "computer", Environment.MachineName }, { "hostname", Environment.MachineName } });
        }
        return Err("Not found", 404);
    }

    static Resp ServeFile(string path)
    {
        var rel = path == "/" ? "index.html" : path.TrimStart('/');
        if (rel.Split('/').Any(p => p == ".." || p.StartsWith("."))) return Err("Not found", 404);
        string type;
        if (!Types.TryGetValue(Path.GetExtension(rel).ToLowerInvariant(), out type)) type = "application/octet-stream";
        if (rel.StartsWith("icons/"))
        {
            var user = Path.Combine(IconsDir, rel.Substring(6));
            if (rel.IndexOf('/', 6) < 0 && File.Exists(user)) return new Resp { Type = type, Body = File.ReadAllBytes(user) };
        }
        byte[] data;
        if (Web.TryGetValue(rel, out data)) return new Resp { Type = type, Body = data };
        return new Resp { Status = 404, Type = "text/plain", Body = Encoding.UTF8.GetBytes("Not found") };
    }
}
