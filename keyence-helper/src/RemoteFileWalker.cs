using System;
using System.Collections.Generic;
using System.Threading;
using Keyence.Ve.Interop;

namespace KeyenceHelper
{
    /// <summary>Read-only wrapper around VapiCommRemoteFileService's async
    /// list/download calls: turns "call, then wait for the matching callback
    /// event" into a plain synchronous method, one call at a time (this
    /// service allows exactly one outstanding transfer per systemId).</summary>
    internal sealed class RemoteFileWalker
    {
        // DOS/FAT-style attribute bit, same convention FANUC's own embedded
        // controllers use for their file listings. ASSUMPTION pending
        // hardware confirmation - see README "Open questions".
        private const ushort ATTR_DIRECTORY = 0x10;

        private readonly int _systemId;
        private readonly VapiCommRemoteFileService _svc = VapiCommRemoteFileService.getInstance();

        public RemoteFileWalker(int systemId)
        {
            _systemId = systemId;
        }

        /// <summary>Full recursive listing of every file (not folders) under
        /// `remoteRoot`, as (relativePath, sizeBytes) pairs. NEST_COUNT_ALL
        /// asks the controller for the whole subtree in one round trip -
        /// no manual per-directory recursion needed.</summary>
        public List<Tuple<string, uint>> ListFilesRecursive(string remoteRoot, int timeoutMs, out VapiStatus status)
        {
            var done = new ManualResetEvent(false);
            VapiStatus cbStatus = default(VapiStatus);
            VapiCommRemoteFileService.VapiCommRemoteFileServiceCallbackFuncDelegate cb = (systemId, eventType, st) =>
            {
                if (systemId != _systemId) return;
                if (eventType != VapiCommRemoteFileService.CallbackEventType.CALLBACK_REMOTE_FILE_REQUEST_FILE_LIST) return;
                try { cbStatus = st; }             // never let an exception unwind into native code
                catch (Exception ex) { Log.Exception("list callback", ex); }
                finally { done.Set(); }
            };
            _svc.addCallback(cb);
            try
            {
                status = _svc.requestFileList(_systemId, remoteRoot,
                    VapiCommRemoteFileService.NestCountCode.NEST_COUNT_ALL, false);
                if (!VapiRuntime.IsSuccess(status)) return new List<Tuple<string, uint>>();

                if (!VapiRuntime.WaitWithPump(done, timeoutMs))
                {
                    status = default(VapiStatus); // caller treats non-success as failure; TIMEOUT has no direct ctor here
                    return new List<Tuple<string, uint>>();
                }
                status = cbStatus;
                if (!VapiRuntime.IsSuccess(status)) return new List<Tuple<string, uint>>();

                VapiRemoteFileInfo[] list = null;
                _svc.getFileList(out list);
                var files = new List<Tuple<string, uint>>();
                if (list != null)
                {
                    foreach (var entry in list)
                    {
                        if (entry == null) continue;
                        bool isDir = (entry.fileAttribute & ATTR_DIRECTORY) != 0;
                        if (isDir) continue;
                        string rel = string.IsNullOrEmpty(entry.filePath) ? entry.fileName : entry.filePath;
                        files.Add(Tuple.Create(rel, entry.fileSize));
                    }
                }
                return files;
            }
            finally
            {
                _svc.removeCallback();
                GC.KeepAlive(cb);   // delegate must outlive every native callback invocation
            }
        }

        /// <summary>Raw listing (files AND folders, one level or full nest)
        /// for `diagnose` - dumps every field so a human can see what the
        /// controller actually returns before backup logic trusts it.</summary>
        public List<VapiRemoteFileInfo> ListRaw(string remotePath, VapiCommRemoteFileService.NestCountCode nest,
            int timeoutMs, out VapiStatus status)
        {
            var done = new ManualResetEvent(false);
            VapiStatus cbStatus = default(VapiStatus);
            VapiCommRemoteFileService.VapiCommRemoteFileServiceCallbackFuncDelegate cb = (systemId, eventType, st) =>
            {
                if (systemId != _systemId) return;
                if (eventType != VapiCommRemoteFileService.CallbackEventType.CALLBACK_REMOTE_FILE_REQUEST_FILE_LIST) return;
                try { cbStatus = st; }
                catch (Exception ex) { Log.Exception("listraw callback", ex); }
                finally { done.Set(); }
            };
            _svc.addCallback(cb);
            try
            {
                status = _svc.requestFileList(_systemId, remotePath, nest, false);
                if (!VapiRuntime.IsSuccess(status)) return new List<VapiRemoteFileInfo>();
                if (!VapiRuntime.WaitWithPump(done, timeoutMs))
                {
                    status = default(VapiStatus);
                    return new List<VapiRemoteFileInfo>();
                }
                status = cbStatus;
                var result = new List<VapiRemoteFileInfo>();
                if (VapiRuntime.IsSuccess(status))
                {
                    VapiRemoteFileInfo[] list = null;
                    _svc.getFileList(out list);
                    if (list != null) result.AddRange(list);
                }
                return result;
            }
            finally
            {
                _svc.removeCallback();
                GC.KeepAlive(cb);
            }
        }

        public VapiStatus DownloadFile(string localPath, string remotePath, int timeoutMs)
        {
            var done = new ManualResetEvent(false);
            VapiStatus cbStatus = default(VapiStatus);
            VapiCommRemoteFileService.VapiCommRemoteFileServiceCallbackFuncDelegate cb = (systemId, eventType, st) =>
            {
                if (systemId != _systemId) return;
                if (eventType != VapiCommRemoteFileService.CallbackEventType.CALLBACK_REMOTE_FILE_DOWNLOAD_FILE) return;
                try { cbStatus = st; }
                catch (Exception ex) { Log.Exception("download callback", ex); }
                finally { done.Set(); }
            };
            _svc.addCallback(cb);
            try
            {
                VapiStatus rc = _svc.downloadFile(_systemId, localPath, remotePath,
                    VapiCommRemoteFileService.OptionCode.OPT_DEFAULT);
                if (!VapiRuntime.IsSuccess(rc)) return rc;
                if (!VapiRuntime.WaitWithPump(done, timeoutMs)) return default(VapiStatus);
                return cbStatus;
            }
            finally
            {
                _svc.removeCallback();
                GC.KeepAlive(cb);
            }
        }
    }
}
