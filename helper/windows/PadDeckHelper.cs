// Pad Deck Helper for Windows: a system tray app that lets Pad Deck keys do things on this PC: open a website
// or an app, press a key combination (including media keys). It also serves the whole deck to any browser on the
// network on port 8099 (DeckServer.cs), so a phone or tablet can be a Pad Deck. Every request from another device
// must carry the pairing code shown in the tray menu. Same protocol as the Mac app (../PadDeckHelper.swift).
//
// Plain C# 5 for the .NET Framework 4 that comes with Windows 10 and 11, so it builds with the compiler that's
// already on every Windows PC (see build.cmd) and needs nothing installed to run.
//
// Port 8098, for a Pad:
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
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle("Pad Deck Helper")]
[assembly: System.Reflection.AssemblyProduct("Pad Deck Helper")]
[assembly: System.Reflection.AssemblyVersion("1.3.0.0")]

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

// ---------- Keys sent from a Pad (port 8098) ----------

static class Server
{
    public static event Action Ran;

    public static void NotifyRan() { var r = Ran; if (r != null) r(); }

    public static void Start()
    {
        Http.Listen(8098, 1000000, Handle);
        Deck.Prepare();
        Http.Listen(Deck.Port, 8000000, Deck.Handle);
    }

    static Resp Handle(Req r)
    {
        if (!Http.SameCode(r.Header("X-Pad-Deck-Code"), Settings.Code))
            return Http.JsonResp(new Dictionary<string, object> { { "error", "Wrong pairing code" } }, 401);
        var key = r.Method + " " + r.Path;
        if (key == "GET /ping")
            return Http.JsonResp(new Dictionary<string, object> {
                { "ok", true }, { "computer", Environment.MachineName }, { "mac", false }, { "windows", true }, { "paused", Settings.Paused } });
        if (key == "GET /apps") return Http.JsonResp(new Dictionary<string, object> { { "apps", Actions.Apps().Keys.ToList() } });
        if (key == "GET /shortcuts") return Http.JsonResp(new Dictionary<string, object> { { "shortcuts", new string[0] } });
        if (key == "POST /run")
        {
            if (Settings.Paused) return Http.JsonResp(new Dictionary<string, object> { { "error", "Pad Deck Helper is paused (tray icon on the PC)" } }, 503);
            try
            {
                var msg = Actions.Run(r.Json as Dictionary<string, object> ?? new Dictionary<string, object>());
                NotifyRan();
                return Http.JsonResp(new Dictionary<string, object> { { "ok", true }, { "message", msg } });
            }
            catch (Exception e) { return Http.JsonResp(new Dictionary<string, object> { { "error", e.Message } }, 400); }
        }
        return Http.JsonResp(new Dictionary<string, object> { { "error", "Not found" } }, 404);
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
        m.Items.Add("Open Pad Deck", null, (s, e) => Open("http://127.0.0.1:" + Deck.Port + "/"));
        m.Items.Add("Use on a phone or tablet…", null, (s, e) => Open("http://127.0.0.1:" + Deck.Port + "/connect.html"));
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add(Settings.Paused ? "Resume" : "Pause", null, (s, e) => { Settings.Paused = !Settings.Paused; UpdateIcon(); });
        var start = new ToolStripMenuItem("Start with Windows") { Checked = Settings.StartWithWindows };
        start.Click += (s, e) => { Settings.StartWithWindows = !Settings.StartWithWindows; };
        m.Items.Add(start);
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add("Quit Pad Deck Helper", null, (s, e) => { icon.Visible = false; Environment.Exit(0); });
    }

    static void Open(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch (Exception) { }
    }

    void Welcome()
    {
        var r = MessageBox.Show(
            "Pad Deck Helper is running. It lives in the system tray (by the clock). To have it start with Windows, turn on Start with Windows in its menu.\n\n" +
            "Pairing code:   " + Settings.Code + "\n\n" +
            "On a Pad: Menu → Helper, enter the code and this PC's name (" + Environment.MachineName + ").\n\n" +
            "No Pad? Use a phone or tablet: “Use on a phone or tablet…” in the tray menu.\n\n" +
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
