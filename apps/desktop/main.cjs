const { app, BrowserWindow, dialog, ipcMain } = require("electron");
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
      throw new Error(`瀹夎鍖呯己灏戝繀瑕佹枃浠讹細${file}`);
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
