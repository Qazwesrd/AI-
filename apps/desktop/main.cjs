const { app, BrowserWindow, dialog } = require("electron");
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
        reject(new Error("本地 API 服务提前退出，请检查日志。"));
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
        reject(new Error("等待本地 API 健康检查超时。"));
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
        reject(new Error("Next.js 服务提前退出，请检查日志。"));
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
          reject(new Error("等待前端服务超时。"));
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
        "软件需要使用本地 8000 和 3000 端口，但端口已被其他程序占用。请关闭占用程序后重新启动。"
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
    console.error("应用启动失败：", error);
    stopServices();

    await dialog.showMessageBox({
      type: "error",
      title: "AI漫剧制作大师启动失败",
      message: "软件无法正常启动",
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