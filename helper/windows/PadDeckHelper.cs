// Pad Deck Helper for Windows: a system tray app that lets Pad Deck keys do things on this PC: open a website
// or an app, press a key combination (including media keys). The Pad sends it requests over the network; each
// must carry the pairing code shown in the tray menu. Same protocol as the Mac app (../PadDeckHelper.swift).
//
// Plain C# 5 for the .NET Framework 4 that comes with Windows 10 and 11, so it builds with the compiler that's
// already on every Windows PC (see build.cmd) and needs nothing installed to run.
//
// GET  /ping        is the helper there, and is the pairing code right?
// GET  /apps        Start menu app names (for the key editor)
// GET  /shortcuts   (Mac only: always empty here)
// POST /run         {"type": "url" | "app" | "hotkey", ...}
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle("Pad Deck Helper")]
[assembly: System.Reflection.AssemblyProduct("Pad Deck Helper")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

class Failure : Exception { public Failure(string m) : base(m) { } }

static class Settings
{
    const string Key = @"Software\Pad Deck Helper";

    static RegistryKey Open() { return Registry.CurrentUser.CreateSubKey(Key); }

    public static string Get(string name, string fallback)
    {
        using (var k = Open()) { var v = k.GetValue(name); return v == null ? fallback : v.ToString(); }
    }

    public static void Set(string name, string value) { using (var k = Open()) k.SetValue(name, value); }

    /// The pairing code, made up once and kept.
    public static string Code
    {
        get
        {
            var c = Get("Code", null);
            if (c != null) return c;
            var bytes = new byte[4];
            using (var rng = System.Security.Cryptography.RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            c = (BitConverter.ToUInt32(bytes, 0) % 1000000).ToString("D6");
            Set("Code", c);
            return c;
        }
    }

    public static bool Paused
    {
        get { return Get("Paused", "0") == "1"; }
        set { Set("Paused", value ? "1" : "0"); }
    }

    // Start with Windows: the per-user Run key.
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

    public static bool StartWithWindows
    {
        get { using (var k = Registry.CurrentUser.OpenSubKey(RunKey)) return k != null && k.GetValue("Pad Deck Helper") != null; }
        set
        {
            using (var k = Registry.CurrentUser.CreateSubKey(RunKey))
            {
                if (value) k.SetValue("Pad Deck Helper", "\"" + Application.ExecutablePath + "\"");
                else k.DeleteValue("Pad Deck Helper", false);
            }
        }
    }
}

// ---------- What a key can do ----------

static class Actions
{
    /// Start menu apps (all users and this user): name → shortcut file.
    public static SortedDictionary<string, string> Apps()
    {
        var apps = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var root in new[] { Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms),
                                     Environment.GetFolderPath(Environment.SpecialFolder.Programs) })
        {
            if (!Directory.Exists(root)) continue;
            try
            {
                foreach (var f in Directory.EnumerateFiles(root, "*.*", SearchOption.AllDirectories))
                {
                    var ext = Path.GetExtension(f).ToLowerInvariant();
                    var name = Path.GetFileNameWithoutExtension(f);
                    if ((ext == ".lnk" || ext == ".url" || ext == ".appref-ms") && name.IndexOf("uninstall", StringComparison.OrdinalIgnoreCase) < 0)
                        apps[name] = f;
                }
            }
            catch (Exception) { }
        }
        return apps;
    }

    [DllImport("user32.dll")]
    static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);

    static readonly Dictionary<string, byte> KeyCodes = BuildKeys();

    static Dictionary<string, byte> BuildKeys()
    {
        var k = new Dictionary<string, byte> {
            { "return", 0x0D }, { "enter", 0x0D }, { "tab", 0x09 }, { "space", 0x20 }, { "backspace", 0x08 },
            { "delete", 0x08 }, { "forwarddelete", 0x2E }, { "del", 0x2E }, { "escape", 0x1B }, { "esc", 0x1B },
            { "left", 0x25 }, { "up", 0x26 }, { "right", 0x27 }, { "down", 0x28 }, { "home", 0x24 }, { "end", 0x23 },
            { "pageup", 0x21 }, { "pagedown", 0x22 }, { "insert", 0x2D }, { "printscreen", 0x2C },
            { ";", 0xBA }, { "=", 0xBB }, { ",", 0xBC }, { "-", 0xBD }, { ".", 0xBE }, { "/", 0xBF }, { "`", 0xC0 },
            { "[", 0xDB }, { "\\", 0xDC }, { "]", 0xDD }, { "'", 0xDE },
            // Media keys
            { "playpause", 0xB3 }, { "next", 0xB0 }, { "previous", 0xB1 }, { "prev", 0xB1 }, { "stop", 0xB2 },
            { "mute", 0xAD }, { "volumedown", 0xAE }, { "volumeup", 0xAF },
        };
        for (var c = 'a'; c <= 'z'; c++) k[c.ToString()] = (byte)char.ToUpperInvariant(c);
        for (var c = '0'; c <= '9'; c++) k[c.ToString()] = (byte)c;
        for (var n = 1; n <= 24; n++) k["f" + n] = (byte)(0x6F + n);
        return k;
    }

    // cmd means Ctrl here, so Mac-style combos (cmd+c) do the same thing on Windows.
    static readonly Dictionary<string, byte> Modifiers = new Dictionary<string, byte> {
        { "ctrl", 0x11 }, { "control", 0x11 }, { "cmd", 0x11 }, { "command", 0x11 }, { "shift", 0x10 },
        { "alt", 0x12 }, { "option", 0x12 }, { "opt", 0x12 }, { "win", 0x5B }, { "windows", 0x5B }, { "super", 0x5B },
    };

    static readonly HashSet<byte> Extended = new HashSet<byte> { 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B };

    static void Key(byte vk, bool up)
    {
        keybd_event(vk, 0, (up ? 2u : 0u) | (Extended.Contains(vk) ? 1u : 0u), UIntPtr.Zero);
    }

    /// Press a key combination like "ctrl+shift+s", "alt+f4" or "playpause".
    public static void PressKeys(string combo)
    {
        var parts = combo.ToLowerInvariant().Replace(" ", "").Split('+').Where(p => p.Length > 0).ToList();
        if (parts.Count == 0) throw new Failure("No keys given");
        var mods = new List<byte>();
        foreach (var m in parts.Take(parts.Count - 1))
        {
            byte vk;
            if (!Modifiers.TryGetValue(m, out vk)) throw new Failure("Unknown modifier: " + m + " (use ctrl, shift, alt, win)");
            mods.Add(vk);
        }
        byte key;
        if (!KeyCodes.TryGetValue(parts[parts.Count - 1], out key)) throw new Failure("Unknown key: " + parts[parts.Count - 1]);
        foreach (var m in mods) Key(m, false);
        Key(key, false);
        Key(key, true);
        for (var i = mods.Count - 1; i >= 0; i--) Key(mods[i], true);
    }

    public static string Run(Dictionary<string, object> a)
    {
        var type = Str(a, "type");
        if (type == "url")
        {
            var s = Str(a, "url").Trim();
            if (!s.Contains("://")) s = "https://" + s;
            Uri uri;
            if (!Uri.TryCreate(s, UriKind.Absolute, out uri) || (uri.Scheme != "http" && uri.Scheme != "https"))
                throw new Failure("That doesn't look like a web address");
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
            return "Opened " + uri.Host;
        }
        if (type == "app")
        {
            var name = Str(a, "app").Trim();
            string path;
            if (!Apps().TryGetValue(name, out path)) throw new Failure("Couldn't find the app “" + name + "” in the Start menu");
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            return "Opened " + name;
        }
        if (type == "hotkey")
        {
            var keys = Str(a, "keys");
            PressKeys(keys);
            return "Pressed " + keys;
        }
        if (type == "shortcut") throw new Failure("Shortcuts are a Mac feature: on Windows, use Open an app or Press keys");
        throw new Failure("Unknown action “" + type + "”");
    }

    static string Str(Dictionary<string, object> a, string key)
    {
        object v;
        return a.TryGetValue(key, out v) && v != null ? v.ToString() : "";
    }
}

// ---------- Web server (a TcpListener: HttpListener would need admin rights to listen on the network) ----------

static class Server
{
    const int Port = 8098;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    public static event Action Ran;

    public static void Start()
    {
        var t = new Thread(() =>
        {
            while (true)
            {
                try
                {
                    var l = new TcpListener(IPAddress.Any, Port);
                    l.Start();
                    while (true)
                    {
                        var c = l.AcceptTcpClient();
                        ThreadPool.QueueUserWorkItem(_ => Serve(c));
                    }
                }
                catch (Exception) { Thread.Sleep(3000); }        // e.g. the port is still busy: try again
            }
        });
        t.IsBackground = true;
        t.Start();
    }

    static void Serve(TcpClient c)
    {
        using (c)
        {
            try
            {
                c.ReceiveTimeout = 5000;
                var s = c.GetStream();
                var buf = new List<byte>();
                var chunk = new byte[8192];
                int end = -1, length = 0;
                while (true)
                {
                    var n = s.Read(chunk, 0, chunk.Length);
                    if (n <= 0) return;
                    buf.AddRange(chunk.Take(n));
                    if (buf.Count > 1000000) return;
                    if (end < 0)
                    {
                        for (var i = 3; i < buf.Count; i++)
                            if (buf[i - 3] == 13 && buf[i - 2] == 10 && buf[i - 1] == 13 && buf[i] == 10) { end = i + 1; break; }
                        if (end < 0) continue;
                        var head = Encoding.ASCII.GetString(buf.Take(end).ToArray());
                        foreach (var line in head.Split(new[] { "\r\n" }, StringSplitOptions.None))
                            if (line.StartsWith("content-length:", StringComparison.OrdinalIgnoreCase)) int.TryParse(line.Substring(15).Trim(), out length);
                    }
                    if (buf.Count - end >= length) break;
                }
                var headText = Encoding.ASCII.GetString(buf.Take(end).ToArray());
                var lines = headText.Split(new[] { "\r\n" }, StringSplitOptions.None);
                var first = lines[0].Split(' ');
                var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var line in lines.Skip(1))
                {
                    var i = line.IndexOf(':');
                    if (i > 0) headers[line.Substring(0, i).Trim()] = line.Substring(i + 1).Trim();
                }
                var body = Encoding.UTF8.GetString(buf.Skip(end).Take(length).ToArray());
                int status;
                var reply = Handle(first[0], first.Length > 1 ? first[1] : "/", headers, body, out status);
                var data = Encoding.UTF8.GetBytes(Json.Serialize(reply));
                var top = Encoding.ASCII.GetBytes("HTTP/1.1 " + status + " " + (status == 200 ? "OK" : "Error") +
                    "\r\nContent-Type: application/json\r\nContent-Length: " + data.Length + "\r\nConnection: close\r\n\r\n");
                s.Write(top, 0, top.Length);
                s.Write(data, 0, data.Length);
            }
            catch (Exception) { }
        }
    }

    static bool SameCode(string given)
    {
        var a = Encoding.UTF8.GetBytes(given ?? "");
        var b = Encoding.UTF8.GetBytes(Settings.Code);
        var diff = a.Length ^ b.Length;
        for (var i = 0; i < b.Length; i++) diff |= b[i] ^ (i < a.Length ? a[i] : 0);
        return diff == 0;
    }

    static object Handle(string method, string path, Dictionary<string, string> headers, string body, out int status)
    {
        string code;
        headers.TryGetValue("X-Pad-Deck-Code", out code);
        status = 200;
        if (!SameCode(code)) { status = 401; return new { error = "Wrong pairing code" }; }
        if (method == "GET" && path == "/ping")
            return new { ok = true, computer = Environment.MachineName, mac = false, windows = true, paused = Settings.Paused };
        if (method == "GET" && path == "/apps") return new { apps = Actions.Apps().Keys.ToList() };
        if (method == "GET" && path == "/shortcuts") return new { shortcuts = new string[0] };
        if (method == "POST" && path == "/run")
        {
            if (Settings.Paused) { status = 503; return new { error = "Pad Deck Helper is paused (tray icon on the PC)" }; }
            try
            {
                var a = Json.Deserialize<Dictionary<string, object>>(body.Length > 0 ? body : "{}") ?? new Dictionary<string, object>();
                var msg = Actions.Run(a);
                if (Ran != null) Ran();
                return new { ok = true, message = msg };
            }
            catch (Failure e) { status = 400; return new { error = e.Message }; }
            catch (Exception e) { status = 400; return new { error = e.Message }; }
        }
        status = 404;
        return new { error = "Not found" };
    }
}

// ---------- Tray icon ----------

class Tray : ApplicationContext
{
    readonly NotifyIcon icon = new NotifyIcon();
    readonly Control ui = new Control();
    readonly Icon onIcon = Draw(Color.White, false), pausedIcon = Draw(Color.Gray, true), flashIcon = Draw(Color.FromArgb(77, 255, 134), false);

    public Tray()
    {
        ui.CreateControl();
        icon.ContextMenuStrip = new ContextMenuStrip();
        icon.ContextMenuStrip.Opening += (s, e) => { BuildMenu(); e.Cancel = false; };
        icon.MouseUp += (s, e) =>
        {
            if (e.Button != MouseButtons.Left) return;
            // Left click opens the menu too.
            var show = typeof(NotifyIcon).GetMethod("ShowContextMenu", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
            if (show != null) show.Invoke(icon, null);
        };
        BuildMenu();
        UpdateIcon();
        icon.Visible = true;
        Server.Ran += () => ui.BeginInvoke(new Action(Flash));
        Server.Start();
        if (Settings.Get("Welcomed", "0") != "1")
        {
            Settings.Set("Welcomed", "1");
            Welcome();
        }
    }

    // A grid of six rounded keys, drawn so there's no icon file to ship.
    static Icon Draw(Color color, bool paused)
    {
        var bmp = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using (var brush = new SolidBrush(color))
            {
                for (var r = 0; r < 2; r++)
                    for (var c = 0; c < 3; c++)
                    {
                        if (paused && r == 1) continue;
                        using (var path = Rounded(new RectangleF(1 + c * 10.5f, 6 + r * 11, 8.5f, 9), 2.5f)) g.FillPath(brush, path);
                    }
                if (paused)
                {
                    g.FillRectangle(brush, 10, 19, 4, 11);
                    g.FillRectangle(brush, 18, 19, 4, 11);
                }
            }
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    static GraphicsPath Rounded(RectangleF r, float radius)
    {
        var p = new GraphicsPath();
        var d = radius * 2;
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    void UpdateIcon()
    {
        icon.Icon = Settings.Paused ? pausedIcon : onIcon;
        icon.Text = "Pad Deck Helper: " + (Settings.Paused ? "Paused" : "On") + " (code " + Settings.Code + ")";
    }

    void Flash()
    {
        icon.Icon = flashIcon;
        var t = new System.Windows.Forms.Timer { Interval = 400 };
        t.Tick += (s, e) => { t.Dispose(); UpdateIcon(); };
        t.Start();
    }

    void BuildMenu()
    {
        var m = icon.ContextMenuStrip;
        m.Items.Clear();
        m.Items.Add(new ToolStripMenuItem("Pad Deck Helper: " + (Settings.Paused ? "Paused" : "On")) { Enabled = false });
        m.Items.Add("Pairing code: " + Settings.Code + "  (Copy)", null, (s, e) => Clipboard.SetText(Settings.Code));
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add(Settings.Paused ? "Resume" : "Pause", null, (s, e) => { Settings.Paused = !Settings.Paused; UpdateIcon(); });
        var start = new ToolStripMenuItem("Start with Windows") { Checked = Settings.StartWithWindows };
        start.Click += (s, e) => { Settings.StartWithWindows = !Settings.StartWithWindows; };
        m.Items.Add(start);
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add("Quit Pad Deck Helper", null, (s, e) => { icon.Visible = false; Environment.Exit(0); });
    }

    void Welcome()
    {
        var r = MessageBox.Show(
            "Pad Deck Helper is running. It lives in the system tray (by the clock). To have it start with Windows, turn on Start with Windows in its menu.\n\n" +
            "On the Pad: Menu → Helper, enter this pairing code:\n\n      " + Settings.Code + "\n\n" +
            "and this PC's name: " + Environment.MachineName + "\n\n" +
            "If Windows asks whether to allow it on networks, allow Private networks.\n\nCopy the code now?",
            "Pad Deck Helper", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
        if (r == DialogResult.Yes) Clipboard.SetText(Settings.Code);
    }

    [STAThread]
    static void Main()
    {
        bool fresh;
        using (new Mutex(true, "PadDeckHelper-single-instance", out fresh))
        {
            if (!fresh) { MessageBox.Show("Pad Deck Helper is already running: look for its icon by the clock.", "Pad Deck Helper"); return; }
            Application.EnableVisualStyles();
            Application.Run(new Tray());
        }
    }
}
