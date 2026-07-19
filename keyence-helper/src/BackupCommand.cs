using System;
using System.IO;
using System.Threading;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>
    /// `backup <ip> <destDir> [--root=SD1:\cv-x\setting] [--timeout=5000]`
    ///
    /// Pulls the tree under `--root` into `destDir`, one file at a time (this
    /// service allows exactly one outstanding transfer per session - same
    /// "gentle, ONE connection" ethic as ftpbackup.py). Every JSON progress
    /// line matches the shape ftpbackup.BackupJob already tracks
    /// (current/done/total/bytes) so the Python-side job wrapper can parse
    /// this subprocess's stdout the same way it already models FTP progress -
    /// just a different transport underneath.
    ///
    /// Drive/path syntax (SD1:\, SD2:\) is confirmed from the
    /// Terminal-Software config; the default scope matches what a real shop
    /// backup contains (SD1\cv-x\setting). `diagnose` against a live camera
    /// is still the way to confirm the exact subtree before trusting a full
    /// unattended run.
    /// </summary>
    internal static class BackupCommand
    {
        public static int Run(string ip, string destDir, string root, int timeoutMs)
        {
            string sdkDir = VapiRuntime.EnsureResolvable();
            Json.Line(Json.Obj("type", "start", "sdkDir", sdkDir, "ip", ip, "root", root, "dest", destDir));

            Directory.CreateDirectory(destDir);

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

                Json.Line(Json.Obj("type", "listing", "root", root));
                VapiStatus listStatus;
                var files = walker.ListFilesRecursive(root, timeoutMs, out listStatus);
                if (!VapiRuntime.IsSuccess(listStatus))
                {
                    Json.Line(Json.Obj("type", "error", "stage", "list", "status", VapiRuntime.StatusName(listStatus)));
                    return 1;
                }

                int total = files.Count;
                Json.Line(Json.Obj("type", "listed", "total", total));

                int done = 0;
                long bytesTotal = 0;
                foreach (var f in files)
                {
                    string relPath = f.Item1.TrimStart('\\', '/');
                    string remotePath = CombineRemote(root, relPath);
                    string localPath = Path.Combine(destDir, relPath.Replace('/', Path.DirectorySeparatorChar));
                    string localDir = Path.GetDirectoryName(localPath);
                    if (!string.IsNullOrEmpty(localDir)) Directory.CreateDirectory(localDir);

                    Json.Line(Json.Obj("type", "progress", "current", relPath, "done", done, "total", total));

                    string partPath = localPath + ".part";
                    VapiStatus dlStatus = default(VapiStatus);
                    long size = -1;
                    try
                    {
                        dlStatus = walker.DownloadFile(partPath, remotePath, timeoutMs);
                        if (VapiRuntime.IsSuccess(dlStatus))
                        {
                            if (File.Exists(localPath)) File.Delete(localPath);
                            File.Move(partPath, localPath);
                            size = new FileInfo(localPath).Length;
                            bytesTotal += size;
                        }
                    }
                    catch (Exception ex)
                    {
                        Json.Line(Json.Obj("type", "file_error", "path", relPath, "error", ex.Message));
                        if (File.Exists(partPath)) { try { File.Delete(partPath); } catch { } }
                        dlStatus = default(VapiStatus);
                    }

                    done++;
                    if (!VapiRuntime.IsSuccess(dlStatus) && size < 0)
                    {
                        Json.Line(Json.Obj("type", "file_error", "path", relPath, "status", VapiRuntime.StatusName(dlStatus)));
                    }
                    Json.Line(Json.Obj("type", "progress", "current", relPath, "done", done, "total", total, "bytes", bytesTotal));

                    Thread.Sleep(15); // small throttle, same spirit as ftpbackup.py's gentle-on-a-live-controller pacing
                }

                Json.Line(Json.Obj("type", "done", "files", done, "bytes", bytesTotal));
                return 0;
            }
            finally
            {
                session.Dispose();
            }
        }

        private static string CombineRemote(string root, string rel)
        {
            string r = root.TrimEnd('\\', '/');
            return r + "\\" + rel.Replace('/', '\\');
        }
    }
}
