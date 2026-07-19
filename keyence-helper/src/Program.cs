using System;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;

namespace KeyenceHelper
{
    internal static class Program
    {
        // HandleProcessCorruptedStateExceptions + the matching
        // <legacyCorruptedStateExceptionsPolicy> in KeyenceHelper.exe.config
        // let the catch below see an ACCESS VIOLATION raised inside the native
        // side of Vapi.Net.dll (e.g. a callback firing into a collected
        // delegate) instead of the process just vanishing. We can't reliably
        // *recover* from a corrupted state, but we CAN log it and exit with a
        // message - which is the whole point when the only symptom reported is
        // "the exe just crashed."
        [STAThread]
        [HandleProcessCorruptedStateExceptions]
        private static int Main(string[] args)
        {
            Log.Reset("KeyenceHelper start pid=" + System.Diagnostics.Process.GetCurrentProcess().Id
                      + " args=[" + string.Join(" ", args) + "]");

            // Catch anything that escapes on ANY thread - the SDK may invoke
            // its callbacks on a native worker thread, and an exception there
            // would otherwise tear down the process with nothing logged.
            AppDomain.CurrentDomain.UnhandledException += (s, e) =>
            {
                Log.Write("UNHANDLED (terminating=" + e.IsTerminating + ")");
                Log.Exception("AppDomain.UnhandledException", e.ExceptionObject as Exception);
            };

            try
            {
                // Must happen before any call into a method whose signature/body
                // references Keyence.Ve.Interop types (DiscoverCommand.Run etc.):
                // the CLR resolves an assembly reference when the JIT compiles
                // the METHOD, not when the method's own first statement runs -
                // so calling this from inside those methods is too late. Program
                // itself references no Vapi type, so Main can safely go first.
                string sdkDir = VapiRuntime.EnsureResolvable();
                Log.Write("SDK resolved: " + sdkDir);

                if (args.Length == 0)
                {
                    PrintUsage();
                    return 2;
                }

                string verb = args[0];
                var pos = new List<string>();
                var opts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                for (int i = 1; i < args.Length; i++)
                {
                    string a = args[i];
                    if (a.StartsWith("--"))
                    {
                        int eq = a.IndexOf('=');
                        if (eq >= 0) opts[a.Substring(2, eq - 2)] = a.Substring(eq + 1);
                        else opts[a.Substring(2)] = "true";
                    }
                    else
                    {
                        pos.Add(a);
                    }
                }

                Log.Write("verb=" + verb);
                switch (verb.ToLowerInvariant())
                {
                    case "selftest":
                        return SelfTestCommand.Run();

                    case "discover":
                        return DiscoverCommand.Run(
                            OptStr(opts, "addr", ""),
                            OptInt(opts, "timeout", 4000));

                    case "diagnose":
                        if (pos.Count < 1) { PrintUsage(); return 2; }
                        return DiagnoseCommand.Run(pos[0], OptInt(opts, "timeout", 5000));

                    case "backup":
                        if (pos.Count < 2) { PrintUsage(); return 2; }
                        // Default scope matches what the shop's existing tool
                        // captures (a real backup contains SD1\cv-x\setting).
                        // Pass --root=SD1:\ to grab the whole first card, or
                        // SD2:\... for the second - drive syntax per the
                        // Terminal-Software config.
                        return BackupCommand.Run(
                            pos[0], pos[1],
                            OptStr(opts, "root", @"SD1:\cv-x\setting"),
                            OptInt(opts, "timeout", 5000));

                    default:
                        PrintUsage();
                        return 2;
                }
            }
            catch (Exception ex)
            {
                Log.Exception("Main", ex);
                Json.Line(Json.Obj("type", "error", "stage", "fatal", "message", ex.ToString()));
                return 1;
            }
        }

        private static string OptStr(Dictionary<string, string> opts, string key, string def)
        {
            string v;
            return opts.TryGetValue(key, out v) ? v : def;
        }

        private static int OptInt(Dictionary<string, string> opts, string key, int def)
        {
            string v;
            int n;
            return opts.TryGetValue(key, out v) && int.TryParse(v, out n) ? n : def;
        }

        private static void PrintUsage()
        {
            Console.Error.WriteLine("KeyenceHelper.exe selftest");
            Console.Error.WriteLine("KeyenceHelper.exe discover [--addr=<ip>] [--timeout=ms]");
            Console.Error.WriteLine("KeyenceHelper.exe diagnose <ip> [--timeout=ms]");
            Console.Error.WriteLine("KeyenceHelper.exe backup <ip> <destDir> [--root=<remotePath>] [--timeout=ms]");
        }
    }
}
