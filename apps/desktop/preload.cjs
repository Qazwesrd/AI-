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
  fillPrompt: (taskId, prompt) => ipcRenderer.invoke("ai-browser:fill-prompt", { taskId, prompt }),
  listOpenTasks: () => ipcRenderer.invoke("ai-browser:list-open-tasks"),
  uploadReferenceFiles: (taskId, fileIds) => ipcRenderer.invoke("ai-browser:upload-reference-files", { taskId, fileIds }),
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

 
contextBridge.exposeInMainWorld("localAIInstallerDesktop", {
  getStatus: () => ipcRenderer.invoke("local-ai:ollama-status"),
  install: (confirm) => ipcRenderer.invoke("local-ai:install-ollama", { confirm }),
  cancelDownload: () => ipcRenderer.invoke("local-ai:cancel-ollama-download"),
  onProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("local-ai:ollama-install-progress", listener);
    return () => ipcRenderer.removeListener("local-ai:ollama-install-progress", listener);
  }
});


// Local update center: software release checks and explicit local model refresh.
contextBridge.exposeInMainWorld("updatesDesktop", {
  getStatus: () => ipcRenderer.invoke("updates:status"),
  checkSoftware: () => ipcRenderer.invoke("updates:check-software"),
  updateModel: (modelName, confirm) => ipcRenderer.invoke("updates:update-model", { modelName, confirm }),
  onModelProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("updates:model-progress", listener);
    return () => ipcRenderer.removeListener("updates:model-progress", listener);
  }
});
