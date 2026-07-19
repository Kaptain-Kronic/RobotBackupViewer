using System;
using System.IO;
using System.Reflection;
using System.Text;

namespace KeyenceHelper
{
    /// <summary>
    /// A dead-simple, flush-every-write crash log next to the exe
    /// (KeyenceHelper.log). Because this tool is launched as a subprocess and
    /// may die in a native access violation with nothing on stdout, "it just
    /// crashed" is otherwise undiagnosable. Every line is timestamped; the
    /// file is truncated at the start of each run so it always reflects the
    /// LAST run only (no unbounded growth on a shop PC).
    ///
    /// Writes are best-effort and never throw - logging must not be the thing
    /// that brings the process down.
    /// </summary>
    internal static class Log
    {
        private static readonly object _gate = new object();
        private static string _path;

        public static string Path
        {
            get
            {
                if (_path == null)
                {
                    try
                    {
                        string dir = System.IO.Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
                        _path = System.IO.Path.Combine(dir, "KeyenceHelper.log");
                    }
                    catch
                    {
                        _path = "KeyenceHelper.log";
                    }
                }
                return _path;
            }
        }

        public static void Reset(string header)
        {
            try
            {
                File.WriteAllText(Path, "", Encoding.UTF8);
            }
            catch { /* best effort */ }
            Write("=== " + header + " ===");
        }

        public static void Write(string message)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message;
            lock (_gate)
            {
                try
                {
                    using (var fs = new FileStream(Path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                    using (var sw = new StreamWriter(fs, Encoding.UTF8))
                    {
                        sw.WriteLine(line);
                        sw.Flush();
                        fs.Flush(true);   // push to disk NOW - a crash is coming
                    }
                }
                catch { /* best effort */ }
            }
        }

        public static void Exception(string where, Exception ex)
        {
            Write("EXCEPTION in " + where + ": " + (ex == null ? "<null>" : ex.ToString()));
        }
    }
}
