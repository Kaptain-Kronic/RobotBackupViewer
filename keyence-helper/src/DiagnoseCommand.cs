using System;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// `diagnose <ip> [--timeout=5000]` - a ZERO-WRITE probe to settle the
    /// open questions in CameraSession/RemoteFileWalker against a real
    /// camera: does connect-by-IP work at all, and what does the on-card
    /// tree actually look like. The drive syntax is now KNOWN from the
    /// Terminal-Software's own config (DEFAULT_CONTROLLER_FILE_GET_DIRECTORY =
    /// "SD1:\", DRIVE1=SD1 / DRIVE2=SD2), and a real backup places the
    /// settings under SD1:\cv-x\setting - so these candidates lead with the
    /// card roots (to see everything) then the known settings subtree. Run
    /// this once against a live CV-X and send the JSON back - same role
    /// discover.py's diagnose_controller played for the FANUC side.
    /// </summary>
    internal static class DiagnoseCommand
    {
        private static readonly string[] RootCandidates =
        {
            @"SD1:\",
            @"SD1:\cv-x\setting",
            @"SD2:\",
            @"SD2:\cv-x\setting",
        };

        public static int Run(string ip, int timeoutMs)
        {
            string sdkDir = VapiRuntime.EnsureResolvable();
            Json.Line(Json.Obj("type", "start", "sdkDir", sdkDir, "ip", ip, "timeoutMs", timeoutMs));

            VapiStatus openStatus;
            var session = CameraSession.Open(ip, timeoutMs, out openStatus);
            if (session == null)
            {
                Json.Line(Json.Obj("type", "error", "stage", "connect", "status", VapiRuntime.StatusName(openStatus)));
                return 1;
            }
            Json.Line(Json.Obj("type", "connected", "systemId", session.SystemId));

            try
            {
                var walker = new RemoteFileWalker(session.SystemId);
                foreach (string candidate in RootCandidates)
                {
                    VapiStatus listStatus;
                    var entries = walker.ListRaw(candidate,
                        VapiCommRemoteFileService.NestCountCode.NEST_COUNT_MIN, timeoutMs, out listStatus);

                    var rows = new System.Collections.Generic.List<object>();
                    foreach (var e in entries)
                    {
                        rows.Add(Json.Obj(
                            "fileName", e.fileName ?? "",
                            "filePath", e.filePath ?? "",
                            "longFileName", e.longFileName ?? "",
                            "fileExtension", e.fileExtension ?? "",
                            "fileSize", e.fileSize,
                            "fileAttribute", e.fileAttribute,
                            "nestCount", e.nestCount
                        ));
                    }
                    Json.Line(Json.Obj(
                        "type", "probe",
                        "path", candidate,
                        "status", VapiRuntime.StatusName(listStatus),
                        "count", rows.Count,
                        "entries", rows
                    ));
                }
            }
            finally
            {
                session.Dispose();
            }

            Json.Line(Json.Obj("type", "done"));
            return 0;
        }
    }
}
