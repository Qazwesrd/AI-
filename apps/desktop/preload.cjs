const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopAPI", {
  platform: process.platform,
  isDesktop: true,
  openExternal: (url) => ipcRenderer.invoke("external:open", url)
});

// AI漫剧制作大师：发布中心文件选择接口
const { contextBridge: publishingContextBridge, ipcRenderer: publishingIpcRenderer } = require("electron");

contextBridge.exposeInMainWorld("publishingDesktop", {
    selectFile: (kind) => publishingIpcRenderer.invoke("publishing:select-file", kind)
});


contextBridge.exposeInMainWorld("aiBrowserDesktop", {
  openTask: (taskId, url) => ipcRenderer.invoke("ai-browser:open", { taskId, url }),
  onDownloaded: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("ai-browser:downloaded", listener);
    return () => ipcRenderer.removeListener("ai-browser:downloaded", listener);
  },
  onDownloadFailed: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("ai-browser:download-failed", listener);
    return () => ipcRenderer.removeListener("ai-browser:download-failed", listener);
  },
  onDownloadRejected: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("ai-browser:download-rejected", listener);
    return () => ipcRenderer.removeListener("ai-browser:download-rejected", listener);
  }
});
