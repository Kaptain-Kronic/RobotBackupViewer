using System;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// `selftest` - loads Vapi.Net.dll and pokes it WITHOUT any networking, to
    /// split a load-time crash from a call-time crash. If this succeeds but
    /// `discover` crashes, the fault is in the network/callback path (delegate
    /// lifetime, marshaling), not the DLL or its native dependencies. If this
    /// itself crashes, the mixed-mode DLL or one of its sibling native DLLs
    /// isn't loading on this machine.
    ///
    /// Each step is logged BEFORE it runs, so the crash log's last line names
    /// exactly which call took the process down.
    /// </summary>
    internal static class SelfTestCommand
    {
        public static int Run()
        {
            var p = System.Diagnostics.Process.GetCurrentProcess();
            Json.Line(Json.Obj(
                "type", "env",
                "is64BitProcess", Environment.Is64BitProcess,
                "clr", Environment.Version.ToString(),
                "os", Environment.OSVersion.VersionString,
                "pid", p.Id,
                "log", Log.Path));

            Log.Write("selftest: EnsureResolvable");
            string sdkDir = VapiRuntime.EnsureResolvable();
            Json.Line(Json.Obj("type", "sdk", "dir", sdkDir));

            // Touch the assembly by naming a type - forces the mixed-mode load
            // (managed + its native half + native dependents) right here, where
            // a load failure is attributable, not deep inside a command.
            Log.Write("selftest: get VapiCommControllerFindService instance");
            var find = VapiCommControllerFindService.getInstance();
            Json.Line(Json.Obj("type", "instance", "service", "VapiCommControllerFindService",
                               "ok", find != null));

            Log.Write("selftest: get VapiCommServices instance");
            var svc = VapiCommServices.getInstance();
            Json.Line(Json.Obj("type", "instance", "service", "VapiCommServices",
                               "ok", svc != null));

            Log.Write("selftest: get VapiCommRemoteFileService instance");
            var rf = VapiCommRemoteFileService.getInstance();
            Json.Line(Json.Obj("type", "instance", "service", "VapiCommRemoteFileService",
                               "ok", rf != null));

            Log.Write("selftest: done");
            Json.Line(Json.Obj("type", "done", "result", "loaded-ok"));
            return 0;
        }
    }
}
