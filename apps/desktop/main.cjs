const { app, BrowserWindow, dialog, ipcMain, shell, session } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const net = require("net");

const API_PORT = 8000;
const WEB_PORT = 3000;

let mainWindow = null;
let apiProcess = null;
let webProcess = null;
let quitting = false;
let startupFailed = false;
let aiBrowserSession = null;
const aiBrowserTasks = new Map();
const aiBrowserWindows = new Map();

const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

function checkPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

function requestHealth() {
  return new Promise((resolve) => {
    const req = http.get(
      `http://127.0.0.1:${API_PORT}/health`,
      { timeout: 2000 },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => body += chunk);
        res.on("end", () => {
          try {
            const data = JSON.parse(body);
            resolve(res.statusCode === 200 ? data : null);
          } catch {
            resolve(null);
          }
        });
      }
    );
    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
  });
}

function waitForApi(timeout = 120000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();

    const timer = setInterval(async () => {
      if (apiProcess && apiProcess.exitCode !== null) {
        clearInterval(timer);
        reject(new Error("Local API service exited before becoming ready. Check the application logs."));
        return;
      }

      const health = await requestHealth();
      if (
        health &&
        health.status === "ok" &&
        Array.isArray(health.loaded_routers) &&
        health.loaded_routers.length >= 6 &&
        (!Array.isArray(health.missing_routers) || health.missing_routers.length === 0)
      ) {
        clearInterval(timer);
        resolve(health);
        return;
      }

      if (Date.now() - started > timeout) {
        clearInterval(timer);
        reject(new Error("Local API health check timed out."));
      }
    }, 800);
  });
}

function waitForWeb(timeout = 120000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();

    const timer = setInterval(() => {
      if (webProcess && webProcess.exitCode !== null) {
        clearInterval(timer);
        reject(new Error("A local application service failed."));
        return;
      }

      const req = http.get("http://127.0.0.1:3000", (res) => {
        res.resume();
        clearInterval(timer);
        resolve();
      });

      req.on("error", () => {
        if (Date.now() - started > timeout) {
          clearInterval(timer);
          reject(new Error("A local application service failed."));
        }
      });
    }, 800);
  });
}

function logChild(name, child) {
  child.on("error", (error) => {
    console.error(`[${name}]`, error);
  });
  child.stdout?.on("data", (data) => {
    console.log(`[${name}] ${data}`);
  });
  child.stderr?.on("data", (data) => {
    console.error(`[${name}] ${data}`);
  });
}

function stopChild(child) {
  if (!child || !child.pid) return;

  if (process.platform === "win32") {
    execFile(
      "taskkill",
      ["/PID", String(child.pid), "/T", "/F"],
      () => {}
    );
  } else {
    try { child.kill("SIGTERM"); } catch {}
  }
}

function stopServices() {
  stopChild(webProcess);
  stopChild(apiProcess);
  webProcess = null;
  apiProcess = null;
}

function startProductionServices() {
  const resources = process.resourcesPath;
  const apiExe = path.join(resources, "api", "ai-comic-api.exe");
  const webServer = path.join(resources, "web", "server.js");
  const webNodeModules = path.join(resources, "web", "node_modules", "next");
  const ffmpeg = path.join(resources, "media", "ffmpeg.exe");
  const ffprobe = path.join(resources, "media", "ffprobe.exe");
  const dataDir = app.getPath("userData");

  for (const file of [apiExe, webServer, webNodeModules, ffmpeg, ffprobe]) {
    if (!fs.existsSync(file)) {
      throw new Error(`安装包缺少必要文件：${file}`);
    }
  }

  fs.mkdirSync(dataDir, { recursive: true });

  if (fs.existsSync(dataDir)) {
    const storageDir = path.join(dataDir, "storage");
    fs.mkdirSync(storageDir, { recursive: true });
  }

  apiProcess = spawn(apiExe, [], {
    cwd: resources,
    windowsHide: true,
    env: {
      ...process.env,
      APP_DATA_DIR: dataDir,
      PYTHONUNBUFFERED: "1",
      FFMPEG_PATH: ffmpeg,
      FFPROBE_PATH: ffprobe
    }
  });

  logChild("API", apiProcess);

  webProcess = spawn(process.execPath, [webServer], {
    cwd: path.join(resources, "web"),
    windowsHide: true,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "1",
      PORT: String(WEB_PORT),
      HOSTNAME: "127.0.0.1",
      NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8000"
    }
  });

  logChild("WEB", webProcess);
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());

  if (app.isPackaged) {
    const apiPortFree = await checkPort(API_PORT);
    const webPortFree = await checkPort(WEB_PORT);

    if (!apiPortFree || !webPortFree) {
      throw new Error(
        "The application requires local ports 8000 and 3000. They are already in use. Close the conflicting processes and restart the application."
      );
    }

    startProductionServices();
    await waitForApi();
    await waitForWeb();
    await mainWindow.loadURL("http://127.0.0.1:3000");
  } else {
    await mainWindow.loadURL("http://127.0.0.1:3000");
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

if (gotLock) {
  app.whenReady().then(createWindow).catch(async (error) => {
    startupFailed = true;
    console.error("Application startup failed:", error);
    stopServices();

    await dialog.showMessageBox({
      type: "error",
      title: "AI漫剧制作大师启动失败",
      message: "应用启动失败，请检查本地 API 和前端服务。",
      detail: String(error.message || error)
    });

    app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
    stopServices();
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && !startupFailed) {
      createWindow().catch(console.error);
    }
  });
}

// AI漫剧制作大师：视频与封面文件选择
const { ipcMain: publishingIpcMain, dialog: publishingDialog } = require("electron");

publishingIpcMain.handle("publishing:select-file", async (_event, kind) => {
    const filters = kind === "cover"
        ? [{ name: "图片文件", extensions: ["png", "jpg", "jpeg", "webp"] }]
        : [{ name: "视频文件", extensions: ["mp4", "mov", "mkv", "webm", "avi"] }];

    const result = await publishingDialog.showOpenDialog({
        title: kind === "cover" ? "选择视频封面" : "选择发布视频",
        properties: ["openFile"],
        filters
    });

    if (result.canceled || !result.filePaths.length) {
        return null;
    }

    return result.filePaths[0];
});


// Publishing center: native desktop file picker.
ipcMain.handle("dialog:openFile", async (_event, options = {}) => {
  const allowed = {
    video: ["mp4", "mov", "mkv", "webm", "avi"],
    cover: ["png", "jpg", "jpeg", "webp"]
  };

  const requested = Array.isArray(options.filters) ? options.filters : [];
  const extensions = requested
    .flatMap(item => Array.isArray(item.extensions) ? item.extensions : [])
    .map(item => String(item).toLowerCase())
    .filter(item => /^[a-z0-9]+$/.test(item));

  const safeExtensions = extensions.filter(item =>
    Object.values(allowed).some(group => group.includes(item))
  );

  if (!safeExtensions.length) {
    throw new Error("不支持的文件格式");
  }

  const result = await dialog.showOpenDialog({
    properties: ["openFile"],
    filters: [{
      name: "支持的文件",
      extensions: safeExtensions
    }]
  });

  if (result.canceled || !result.filePaths.length) {
    return null;
  }

  return result.filePaths[0];
});


// AI Web Tool Hub: open only explicitly requested HTTP(S) URLs in the system browser.
ipcMain.handle("external:open", async (_event, rawUrl) => {
  let parsed;
  try {
    parsed = new URL(String(rawUrl));
  } catch {
    throw new Error("无效的网址");
  }

  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("只允许打开 HTTP 或 HTTPS 网页地址");
  }

  await shell.openExternal(parsed.toString());
  return true;
});


// Embedded AI browser: persistent login session and task-aware download collection.
function getAiBrowserSession() {
  if (aiBrowserSession) return aiBrowserSession;
  aiBrowserSession = session.fromPartition("persist:ai-web-tools");
  aiBrowserSession.on("will-download", (event, item, webContents) => {
    const taskKey = aiBrowserTasks.get(webContents.id);
    if (!taskKey) return;
    const allowed = new Set([
      ".png", ".jpg", ".jpeg", ".webp", ".gif",
      ".mp4", ".mov", ".webm", ".mkv",
      ".mp3", ".wav", ".m4a", ".aac",
      ".txt", ".md", ".json", ".srt", ".vtt",
      ".pdf", ".docx", ".csv"
    ]);
    const originalName = path.basename(item.getFilename() || "ai-result.bin").slice(0, 255);
    const extension = path.extname(originalName).toLowerCase();
    if (!allowed.has(extension)) {
      event.preventDefault();
      webContents.send("ai-browser:download-rejected", { taskId: taskKey, filename: originalName, reason: "该文件类型暂不支持自动归档。" });
      return;
    }
    const resultDir = path.join(app.getPath("userData"), "storage", "web_ai_results");
    fs.mkdirSync(resultDir, { recursive: true });
    const storedName = require("crypto").randomUUID().replace(/-/g, "") + extension;
    const targetPath = path.join(resultDir, storedName);
    item.setSavePath(targetPath);
    item.once("done", async (_downloadEvent, state) => {
      if (state !== "completed") {
        webContents.send("ai-browser:download-failed", { taskId: taskKey, filename: originalName, state });
        return;
      }
      try {
        const stat = fs.statSync(targetPath);
        const response = await fetch("http://127.0.0.1:8000/api/web-ai/files/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            taskId: taskKey,
            filename: originalName,
            storedName,
            contentType: item.getMimeType() || "application/octet-stream",
            size: stat.size
          })
        });
        if (!response.ok) throw new Error("本地 API 未能登记下载文件：" + response.status);
        const registered = await response.json();
        webContents.send("ai-browser:downloaded", registered);
      } catch (error) {
        webContents.send("ai-browser:download-failed", {
          taskId: taskKey,
          filename: originalName,
          savedPath: targetPath,
          reason: String(error.message || error)
        });
      }
    });
  });
  return aiBrowserSession;
}

ipcMain.handle("ai-browser:open", async (_event, payload = {}) => {
  const taskId = String(payload.taskId || "");
  const rawUrl = String(payload.url || "");
  if (!/^task-[a-zA-Z0-9-]{5,110}$/.test(taskId)) throw new Error("无效的任务编号");
  let parsed;
  try { parsed = new URL(rawUrl); } catch { throw new Error("无效的网址"); }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("只允许打开 HTTP 或 HTTPS 网页地址");
  }
  const browserSession = getAiBrowserSession();
  const browser = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 900,
    minHeight: 650,
    autoHideMenuBar: true,
    title: "AI 工具浏览器 - " + parsed.hostname,
    webPreferences: {
      partition: "persist:ai-web-tools",
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  aiBrowserTasks.set(browser.webContents.id, taskId);
  aiBrowserWindows.set(browser.webContents.id, browser);
  browser.on("closed", () => {
    aiBrowserTasks.delete(browser.webContents.id);
    aiBrowserWindows.delete(browser.webContents.id);
  });
  await browser.loadURL(parsed.toString());
  return { opened: true, taskId };
});
