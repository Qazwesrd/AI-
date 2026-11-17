const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopAPI", {
  platform: process.platform,
  isDesktop: true
});

// AI漫剧制作大师：发布中心文件选择接口
const { contextBridge: publishingContextBridge, ipcRenderer: publishingIpcRenderer } = require("electron");

contextBridge.exposeInMainWorld("publishingDesktop", {
    selectFile: (kind) => publishingIpcRenderer.invoke("publishing:select-file", kind)
});

