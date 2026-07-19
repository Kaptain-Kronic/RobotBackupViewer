using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// Locates the vendor's Vapi.Net.dll on this machine at runtime (we never
    /// ship a copy - it's Keyence's proprietary interop library, installed
    /// alongside the CV-X Series Simulation-Software the shop already runs)
    /// and provides the STA message-pump helper every async Vapi call needs.
    ///
    /// Why a pump at all: every VapiComm*Service call here is fire-and-forget
    /// (it returns VapiStatus immediately) with the real result delivered via
    /// a callback delegate - the classic COM/native-interop async pattern.
    /// Those callbacks marshal onto the calling thread's Windows message
    /// queue, so a plain ManualResetEvent.WaitOne() would deadlock: nothing
    /// pumps the queue that would let the marshaled call land. Application.
    /// DoEvents() on an [STAThread] Main works without any actual Form.
    /// </summary>
    internal static class VapiRuntime
    {
        // Search order: the Terminal-Software build FIRST. It's the one the
        // shop uses to talk to LIVE cameras (its bundled Vapi.Net.dll is the
        // hardware-proven build), it ships identical Keyence.Ve.Interop types
        // to the Simulation build we reflected against (verified by name +
        // signature), and it's the install guaranteed present on a PC that
        // actually backs cameras up. The Simulation-Software bin_X### trees
        // are fallbacks - handy on a dev box that only has the simulator, but
        // its networking may behave differently from the live build.
        private static readonly string[] DefaultDirs =
        {
            @"C:\Program Files (x86)\KEYENCE\CV-X Series Terminal-Software\bin",
            @"C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X400",
            @"C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X200",
            @"C:\Program Files (x86)\KEYENCE\CV-X Series Simulation-Software\bin_X100",
        };

        private static bool _hooked;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetDllDirectory(string lpPathName);

        /// <summary>Must be called once, before touching any Keyence.Ve.Interop
        /// type, so the CLR's assembly probe finds Vapi.Net.dll (and its
        /// native-side dependents) at the vendor's install location instead
        /// of failing with a bare FileNotFoundException.</summary>
        public static string EnsureResolvable()
        {
            string dir = ResolveDir();
            if (dir == null)
            {
                throw new InvalidOperationException(
                    "Could not find Vapi.Net.dll. Install the KEYENCE CV-X Series " +
                    "Terminal-Software (or Simulation-Software) on this machine, or " +
                    "set KEYENCE_SDK_DIR to the bin folder that contains Vapi.Net.dll.");
            }
            if (!_hooked)
            {
                // The MANAGED half of Vapi.Net.dll is found by the resolve hook
                // below. But it's a mixed-mode (C++/CLI) assembly whose NATIVE
                // half pulls in sibling native DLLs from the vendor bin - and
                // those are resolved by the Windows loader, not the CLR. An
                // assembly loaded via LoadFrom does NOT add its own folder to
                // the native search path, so without this the native load can
                // fail deep inside the first Vapi call (an unattributable
                // crash). Putting the vendor bin on the native search path
                // fixes that. Best-effort: a false return just means we fall
                // back to the default search order.
                try { SetDllDirectory(dir); }
                catch (Exception ex) { Log.Exception("SetDllDirectory", ex); }

                AppDomain.CurrentDomain.AssemblyResolve += (sender, args) =>
                {
                    string name = new AssemblyName(args.Name).Name;
                    string candidate = Path.Combine(dir, name + ".dll");
                    return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
                };
                _hooked = true;
            }
            return dir;
        }

        private static string ResolveDir()
        {
            string env = Environment.GetEnvironmentVariable("KEYENCE_SDK_DIR");
            if (!string.IsNullOrEmpty(env) && File.Exists(Path.Combine(env, "Vapi.Net.dll")))
                return env;
            foreach (string dir in DefaultDirs)
                if (File.Exists(Path.Combine(dir, "Vapi.Net.dll")))
                    return dir;
            return null;
        }

        /// <summary>Pump the STA message queue until `handle` signals or the
        /// timeout elapses. Returns whether it signaled.</summary>
        public static bool WaitWithPump(WaitHandle handle, int timeoutMs)
        {
            const int sliceMs = 15;
            int waited = 0;
            while (waited < timeoutMs)
            {
                if (handle.WaitOne(0)) return true;
                System.Windows.Forms.Application.DoEvents();
                Thread.Sleep(sliceMs);
                waited += sliceMs;
            }
            return handle.WaitOne(0);
        }

        public static bool IsSuccess(VapiStatus status)
        {
            return status.Equals(VapiStatus.Status.SUCCESS);
        }

        public static string StatusName(VapiStatus status)
        {
            // val is the only field; ToString on the boxed enum gives us the name.
            return status.val.ToString();
        }
    }
}
