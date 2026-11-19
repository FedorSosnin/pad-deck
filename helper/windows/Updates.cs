// Updates for Pad Deck Helper on Windows: checks GitHub for a newer release a few times a day, and installs it.
// A running .exe can't be overwritten but can be renamed: the new one takes its place and starts (with
// --updated, so it waits for this one to quit), and cleans up the old file. With "Update automatically" on (the
// deck's System section) it installs by itself between 3 and 5 at night. Plain C# 5 (see build.cmd).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class Updater
{
    const string Repo = "FedorSosnin/pad-deck";
    static readonly object Gate = new object();
    public static readonly string Version = ReadVersion();
    public static string Latest, Notes = "", Error;
    public static DateTime Checked = DateTime.MinValue;
    public static bool Busy;
    public static DateTime OnAir = DateTime.MinValue;   // a deck said OBS is streaming or recording (POST /api/busy)
    static string asset;

    static string ReadVersion()
    {
        using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("VERSION"))
        {
            if (s == null) return "0";
            using (var r = new StreamReader(s)) return r.ReadToEnd().Trim();
        }
    }

    static int[] Parts(string v)
    {
        return Regex.Matches(v ?? "", @"\d+").Cast<Match>().Take(3).Select(m => int.Parse(m.Value)).ToArray();
    }

    public static bool IsNewer(string a, string b)
    {
        int[] x = Parts(a), y = Parts(b);
        for (var i = 0; i < Math.Max(x.Length, y.Length); i++)
        {
            int p = i < x.Length ? x[i] : 0, q = i < y.Length ? y[i] : 0;
            if (p != q) return p > q;
        }
        return false;
    }

    public static bool Available { get { return Latest != null && IsNewer(Latest, Version); } }

    /// Ask GitHub for the newest release (blocking).
    public static void Check()
    {
        try
        {
            ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;   // TLS 1.2
            var r = FetchWithAgent("https://api.github.com/repos/" + Repo + "/releases/latest", 15000);
            if (r.Key != 200) throw new Failure("GitHub answered " + r.Key);
            var rel = Http.Json.DeserializeObject(Encoding.UTF8.GetString(r.Value)) as Dictionary<string, object>;
            if (rel == null || !rel.ContainsKey("tag_name")) throw new Failure("GitHub sent something unexpected");
            string url = null;
            object assets;
            if (rel.TryGetValue("assets", out assets) && assets is object[])
                foreach (var a in ((object[])assets).OfType<Dictionary<string, object>>())
                    if (Plugs.Str(a, "name") == "Pad-Deck-Helper-Windows.zip") url = Plugs.Str(a, "browser_download_url");
            lock (Gate)
            {
                Latest = Plugs.Str(rel, "tag_name").TrimStart('v');
                var body = Plugs.Str(rel, "body");
                Notes = body.Length > 4000 ? body.Substring(0, 4000) : body;
                asset = url;
                Error = null;
            }
        }
        catch (Exception e) { Error = "Couldn't check for updates (" + e.Message + ")"; }
        Checked = DateTime.UtcNow;
    }

    // GitHub needs a User-Agent, which HttpWebRequest only takes through its property.
    static KeyValuePair<int, byte[]> FetchWithAgent(string url, int timeout)
    {
        var req = (HttpWebRequest)WebRequest.Create(url);
        req.UserAgent = "Pad-Deck-Updater";
        req.Accept = "application/vnd.github+json";
        req.Timeout = timeout;
        req.ReadWriteTimeout = timeout;
        try
        {
            using (var res = (HttpWebResponse)req.GetResponse())
            using (var s = res.GetResponseStream())
            using (var m = new MemoryStream()) { s.CopyTo(m); return new KeyValuePair<int, byte[]>((int)res.StatusCode, m.ToArray()); }
        }
        catch (WebException e)
        {
            var res = e.Response as HttpWebResponse;
            if (res != null) using (res) return new KeyValuePair<int, byte[]>((int)res.StatusCode, new byte[0]);
            throw new Failure(e.Message);
        }
    }

    public static Dictionary<string, object> State(bool auto)
    {
        return new Dictionary<string, object> {
            { "version", Version }, { "latest", Latest }, { "notes", Notes }, { "error", Error }, { "available", Available },
            { "busy", Busy }, { "checked", Checked == DateTime.MinValue ? 0 : (Checked - new DateTime(1970, 1, 1)).TotalSeconds }, { "auto", auto } };
    }

    /// Install in the background. False if it's already installing.
    public static bool Start()
    {
        lock (Gate)
        {
            if (Busy) return false;
            Busy = true;
        }
        ThreadPool.QueueUserWorkItem(_ =>
        {
            try { Install(); }
            catch (Exception e) { Error = "The update didn't install: " + e.Message; Busy = false; }
        });
        return true;
    }

    static void Install()
    {
        if (asset == null) throw new Failure("this release has no Windows app yet");
        var r = FetchWithAgent(asset, 300000);
        if (r.Key != 200) throw new Failure("the download failed (" + r.Key + ")");
        byte[] exe = null;
        using (var zip = new ZipArchive(new MemoryStream(r.Value), ZipArchiveMode.Read))
        {
            var e = zip.Entries.FirstOrDefault(x => x.Name.Equals("PadDeckHelper.exe", StringComparison.OrdinalIgnoreCase));
            if (e == null) throw new Failure("the download doesn't look like Pad Deck Helper");
            using (var s = e.Open())
            using (var m = new MemoryStream()) { s.CopyTo(m); exe = m.ToArray(); }
        }
        var here = Application.ExecutablePath;
        var fresh = here + ".new";
        var old = Path.ChangeExtension(here, ".old.exe");
        File.WriteAllBytes(fresh, exe);
        try { if (File.Exists(old)) File.Delete(old); } catch (Exception) { }
        try
        {
            File.Move(here, old);                                       // allowed while it runs
            File.Move(fresh, here);
        }
        catch (Exception)
        {
            if (!File.Exists(here) && File.Exists(old)) File.Move(old, here);
            throw new Failure("couldn't replace " + Path.GetFileName(here) + " (is the folder read-only?)");
        }
        Process.Start(new ProcessStartInfo(here, "--updated") { UseShellExecute = false });
        Environment.Exit(0);
    }

    /// After an update: the old copy can go once it has quit.
    public static void CleanUp()
    {
        var old = Path.ChangeExtension(Application.ExecutablePath, ".old.exe");
        ThreadPool.QueueUserWorkItem(_ =>
        {
            for (var i = 0; i < 20 && File.Exists(old); i++)
            {
                try { File.Delete(old); } catch (Exception) { Thread.Sleep(1000); }
            }
        });
    }

    /// Check a few times a day; with "Update automatically" on, install between 3 and 5 at night.
    public static void Watch(Func<bool> auto)
    {
        var t = new Thread(() =>
        {
            Thread.Sleep(60000);
            while (true)
            {
                try
                {
                    if ((DateTime.UtcNow - Checked).TotalHours > 6) Check();
                    var hour = DateTime.Now.Hour;
                    if (Available && auto() && hour >= 3 && hour < 5 && (DateTime.UtcNow - OnAir).TotalMinutes > 5) Start();
                }
                catch (Exception) { }
                Thread.Sleep(15 * 60000);
            }
        });
        t.IsBackground = true;
        t.Start();
    }
}
