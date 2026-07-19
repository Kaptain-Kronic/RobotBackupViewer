using System;
using System.Threading;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// Opens a session against one camera by IP and connects the remote-file
    /// service to it, mirroring ftpbackup.py's single-connection-per-job
    /// discipline (never parallel ops against one controller).
    ///
    /// ASSUMPTION (needs live-hardware confirmation): connecting straight to
    /// a known IP - rather than going through DiscoverCommand first - means
    /// constructing a VapiCommSystemInfo by hand (address + ETHERNET device
    /// type, no systemId yet) and calling VapiCommServices.assignSystemId to
    /// have the SDK mint and fill in a systemId for it. This is the
    /// best-supported reading of the reflected API (VapiCommSystemInfo's
    /// _systemId is read/write, and "assign" strongly implies "give this
    /// description an id"), but it's inferred, not documented - if `diagnose`
    /// fails at the assign/connect step on real hardware, this is the first
    /// place to look.
    /// </summary>
    internal sealed class CameraSession : IDisposable
    {
        public readonly int SystemId;
        private readonly bool _sessionCreated;

        private CameraSession(int systemId, bool sessionCreated)
        {
            SystemId = systemId;
            _sessionCreated = sessionCreated;
        }

        public static CameraSession Open(string ip, int timeoutMs, out VapiStatus status)
        {
            var info = new VapiCommSystemInfo();
            info._addressName = ip;
            info._commDeviceType = VapiCommSystemInfo.CommDeviceType.ETHERNET;

            status = VapiCommServices.getInstance().assignSystemId(info);
            if (!VapiRuntime.IsSuccess(status)) return null;

            int systemId = info._systemId;
            status = VapiCommServices.getInstance().createSession(systemId);
            if (!VapiRuntime.IsSuccess(status)) return null;

            var session = new CameraSession(systemId, true);

            status = VapiCommRemoteFileService.getInstance().connect(systemId);
            if (!VapiRuntime.IsSuccess(status))
            {
                session.Dispose();
                return null;
            }

            return session;
        }

        public void Dispose()
        {
            try { VapiCommRemoteFileService.getInstance().disconnect(SystemId, true); } catch { /* best effort */ }
            if (_sessionCreated)
            {
                try { VapiCommServices.getInstance().deleteSession(SystemId); } catch { /* best effort */ }
            }
        }
    }
}
