using System;
using System.Threading;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// `discover [--addr=<ip>] [--timeout=3000]` - sweeps for CV-X controllers
    /// over Ethernet using the same VapiCommControllerFindService the vendor's
    /// own Simulation-Software uses for its "Auto Detect" dialog.
    ///
    /// ASSUMPTION (needs live-hardware confirmation): passing "" for the
    /// detect-address broadcasts across the local subnet; a specific IP
    /// narrows the probe to just that host. The vendor tool's saved
    /// AddressList (see setting.xml in a real backup tree) suggests targeted
    /// probing is also a supported, maybe more reliable, path - if broadcast
    /// discovery doesn't turn up a camera you know is up, try
    /// `discover --addr=<that IP>` as a direct probe.
    /// </summary>
    internal static class DiscoverCommand
    {
        public static int Run(string addr, int timeoutMs)
        {
            string sdkDir = VapiRuntime.EnsureResolvable();
            Json.Line(Json.Obj("type", "start", "sdkDir", sdkDir, "addr", addr ?? "", "timeoutMs", timeoutMs));

            var find = VapiCommControllerFindService.getInstance();
            var done = new ManualResetEvent(false);
            VapiStatus finalStatus = default(VapiStatus);

            // Root the delegate in a LOCAL that outlives the whole native
            // operation. `setCallbackFunc` hands its function pointer to native
            // code; if the only reference were the inline `new ...(...)`
            // argument, the GC could collect the delegate the moment
            // setCallbackFunc returns - and then, when the camera actually
            // replies and native code invokes the callback, it calls into
            // freed memory (access violation, "the exe just crashed"). This
            // never fires when nothing replies (the callback is never called),
            // which is exactly why it passed on a camera-less dev box. The
            // GC.KeepAlive after the wait makes the lifetime explicit so a
            // future edit can't "optimize" the local away.
            var cb = new VapiCommControllerFindService.VapiCommControllerFindServiceCallbackFuncDelegate(
                (status) =>
                {
                    // NEVER let a managed exception unwind into native code -
                    // that's undefined behavior and its own kind of crash.
                    try
                    {
                        Log.Write("discover callback: status=" + VapiRuntime.StatusName(status));
                        finalStatus = status;
                    }
                    catch (Exception ex) { Log.Exception("discover callback", ex); }
                    finally { done.Set(); }
                });
            find.setCallbackFunc(cb);

            Log.Write("discover: findControllerEther addr='" + (addr ?? "") + "' timeout=" + timeoutMs);
            VapiStatus rc = find.findControllerEther(timeoutMs, addr ?? "", VapiCommControllerFindService.FindModel.FIND_MODEL_CVX);
            Log.Write("discover: findControllerEther returned " + VapiRuntime.StatusName(rc));
            if (!VapiRuntime.IsSuccess(rc))
            {
                Json.Line(Json.Obj("type", "error", "stage", "findControllerEther", "status", VapiRuntime.StatusName(rc)));
                GC.KeepAlive(cb);
                return 1;
            }

            bool signaled = VapiRuntime.WaitWithPump(done, timeoutMs + 3000);
            Log.Write("discover: wait signaled=" + signaled);
            GC.KeepAlive(cb);   // keep the delegate alive until the callback can no longer fire
            if (!signaled)
            {
                Json.Line(Json.Obj("type", "error", "stage", "wait", "status", "TIMEOUT"));
                find.cancelFindRequest();
                return 1;
            }
            if (!VapiRuntime.IsSuccess(finalStatus))
            {
                Json.Line(Json.Obj("type", "error", "stage", "callback", "status", VapiRuntime.StatusName(finalStatus)));
                return 1;
            }

            int count = 0;
            find.getControllerCountInfo(out count);
            VapiCommSystemInfo[] table = null;
            find.getControllerInfo(out table);

            int found = 0;
            if (table != null)
            {
                foreach (var info in table)
                {
                    if (info == null) continue;
                    Json.Line(Json.Obj(
                        "type", "found",
                        "ip", info._addressName ?? "",
                        "name", info._controllerName ?? "",
                        "systemId", info._systemId,
                        "device", info._commDeviceType.ToString(),
                        "controllerType", SafeCall(() => info.getControllerType()),
                        "protocolVersion", SafeCall(() => info.getProtocolVersion())
                    ));
                    found++;
                }
            }
            find.clearInfo();
            Json.Line(Json.Obj("type", "done", "found", found, "reportedCount", count));
            return 0;
        }

        private static int SafeCall(Func<int> f)
        {
            try { return f(); } catch { return -1; }
        }
    }
}
