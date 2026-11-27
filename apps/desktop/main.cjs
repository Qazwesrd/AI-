const { app, BrowserWindow, dialog, ipcMain, shell, session } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const net = require("net");
const { registerOllamaRuntimeInstaller } = require("./ollama-runtime-installer.cjs");
const { registerUpdateCenter, resolveUpdatePort } = require("./update-center.cjs");

 
// Discover existing user data and AI runtimes without moving, copying,
// overwriting, or deleting any model files. This runs before services start.
function uniquePaths(values) {
  const seen = new Set();
  return values.filter((value) => {
    if (!value) return false;
    const resolved = path.resolve(String(value).trim());
    const key = process.platform === "win32" ? resolved.toLowerCase() : resolved;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map((value) => path.resolve(String(value).trim()));
}

function firstExisting(paths) {
  return uniquePaths(paths).find((candidate) => {
    try { return fs.existsSync(candidate); } catch { return false; }
  }) || "";
}

function comfyLayouts(root) {
  if (!root) return [];
  return [
    { root, python: path.join(root, "python_embeded", "python.exe"), main: path.join(root, "ComfyUI", "main.py"), cwd: root },
    { root, python: path.join(root, "python_embeded", "python.exe"), main: path.join(root, "main.py"), cwd: root },
    { root, python: path.join(root, "venv", "Scripts", "python.exe"), main: path.join(root, "main.py"), cwd: root },
    { root, python: path.join(root, ".venv", "Scripts", "python.exe"), main: path.join(root, "main.py"), cwd: root }
  ];
}

function resolveAiResourcePaths(dataDir) {
  const userProfile = process.env.USERPROFILE || process.env.HOME || "";
  const modelsCandidates = uniquePaths([
    process.env.OLLAMA_MODELS,
    path.join(dataDir, "engines", "Ollama", "models"),
    path.join(dataDir, "Ollama", "models"),
    "E:\\AI\\数据\\engines\\Ollama\\models",
    "E:\\AI\\数据\\Ollama\\models",
    "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\models",
    path.join(userProfile, ".ollama", "models")
  ]);
  const ollamaModels = modelsCandidates.find((candidate) =>
    fs.existsSync(path.join(candidate, "blobs")) || fs.existsSync(path.join(candidate, "manifests"))
  ) || "";
  const localAppData = process.env.LOCALAPPDATA || "";
  const programFiles = process.env.ProgramFiles || "C:\\Program Files";
  const programFilesX86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  const ollamaRoots = uniquePaths([
    process.env.AICOMICS_OLLAMA_DIR,
    process.env.OLLAMA_DIR,
    path.join(dataDir, "engines", "Ollama"),
    path.join(dataDir, "Ollama"),
    path.join(localAppData, "Programs", "Ollama"),
    path.join(programFiles, "Ollama"),
    path.join(programFilesX86, "Ollama"),
    path.join(userProfile, ".ollama")
  ]);
  let ollamaExe = "";
  let ollamaRoot = "";
  for (const root of ollamaRoots) {
    const candidate = firstExisting([path.join(root, "runtime", "ollama.exe"), path.join(root, "ollama.exe")]);
    if (candidate) { ollamaExe = candidate; ollamaRoot = root; break; }
  }

  const comfyRoots = uniquePaths([
    process.env.AICOMICS_COMFYUI_DIR,
    process.env.COMFYUI_PATH,
    path.join(dataDir, "engines", "ComfyUI"),
    path.join(dataDir, "ComfyUI"),
    // Common official Windows portable bundle directory names.
    path.join(dataDir, "engines", "ComfyUI_windows_portable"),
    path.join(dataDir, "ComfyUI_windows_portable"),
    path.join(dataDir, "engines", "ComfyUI_windows_portable_nvidia"),
    path.join(dataDir, "ComfyUI_windows_portable_nvidia"),
    path.join(dataDir, "engines", "ComfyUI_windows_portable_amd"),
    path.join(dataDir, "ComfyUI_windows_portable_amd"),
    "E:\\AI\\数据\\engines\\ComfyUI",
    "E:\\AI\\数据\\ComfyUI",
    "E:\\AI\\数据\\engines\\ComfyUI_windows_portable",
    "E:\\AI\\数据\\ComfyUI_windows_portable",
    "E:\\123\\ai-comic-studio-desktop-Data\\engines\\ComfyUI",
    "E:\\123\\ai-comic-studio-desktop-Data\\engines\\ComfyUI_windows_portable",
    path.join(path.dirname(dataDir), "ComfyUI"),
    path.join(path.dirname(dataDir), "ComfyUI_windows_portable"),
    path.join(userProfile, "ComfyUI"),
    path.join(userProfile, "ComfyUI_windows_portable")
  ]);
  let comfy = null;
  for (const root of comfyRoots) {
    comfy = comfyLayouts(root).find((layout) =>
      fs.existsSync(layout.python) && fs.existsSync(layout.main)
    ) || null;
    if (comfy) break;
  }
  return { ollamaExe, ollamaRoot, ollamaModels, comfy };
}

function hasManagedAiResources(directory) {
  if (!directory) return false;
  // Only count assets physically inside this candidate directory. Global
  // USERPROFILE/.ollama or Program Files runtimes must never make an empty
  // candidate look like the user's data folder.
  const localModels = [
    path.join(directory, "engines", "Ollama", "models"),
    path.join(directory, "Ollama", "models")
  ].some((models) =>
    fs.existsSync(path.join(models, "blobs")) || fs.existsSync(path.join(models, "manifests"))
  );
  const localComfyRoots = [
    "ComfyUI",
    "ComfyUI_windows_portable",
    "ComfyUI_windows_portable_nvidia",
    "ComfyUI_windows_portable_amd"
  ].flatMap((name) => [
    path.join(directory, "engines", name),
    path.join(directory, name)
  ]);
  const localComfy = localComfyRoots.some((root) =>
    comfyLayouts(root).some((layout) =>
      fs.existsSync(layout.python) && fs.existsSync(layout.main)
    )
  );
  return localModels || localComfy;
}

function resolveManagedDataDirectory() {
  const configured = String(process.env.AICOMICS_DATA_DIR || process.env.APP_DATA_DIR || "").trim();
  const executableDir = process.platform === "win32" && app.isPackaged
    ? path.dirname(app.getPath("exe")) : "";
  const adjacentData = executableDir ? path.resolve(executableDir, "..", "数据") : "";
  const legacyUserData = app.getPath("userData");
  const appData = app.getPath("appData");
  const candidates = uniquePaths([
    configured,
    // Known legacy data locations are searched before creating a new empty profile.
    "E:\\AI\\数据",
    "E:\\123\\ai-comic-studio-desktop-Data",
    adjacentData,
    legacyUserData,
    path.join(appData, "AI漫剧制作大师"),
    path.join(appData, "ai-comic-studio-desktop")
  ]);
  const existing = candidates.find(hasManagedAiResources);
  const selected = existing || firstExisting([configured]) || adjacentData || legacyUserData;
  fs.mkdirSync(selected, { recursive: true });
  process.env.AICOMICS_DATA_DIR = selected;
  process.env.APP_DATA_DIR = selected;
  app.setPath("userData", selected);
  const resources = resolveAiResourcePaths(selected);
  console.log("[DATA] Persistent user data directory:", selected);
  console.log("[DATA] Existing Ollama models:", resources.ollamaModels || "not detected");
  console.log("[DATA] Existing ComfyUI:", resources.comfy?.root || "not detected");
  return selected;
}

resolveManagedDataDirectory();

const API_PORT = 8000;
const WEB_PORT = 3000;

let mainWindow = null;
let apiProcess = null;
let webProcess = null;
let ollamaProcess = null;
let comfyProcess = null;
let quitting = false;
let startupFailed = false;
let updateCenter = null;
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

function requestWebHealth() {
  return new Promise((resolve) => {
    const req = http.get(
      `http://127.0.0.1:${WEB_PORT}/`,
      { timeout: 2500 },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => body += chunk);
        res.on("end", () => {
          resolve(
            res.statusCode === 200 &&
            body.includes("AI漫剧制作大师")
          );
        });
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
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
  stopChild(comfyProcess);
  stopChild(ollamaProcess);
  webProcess = null;
  apiProcess = null;
  comfyProcess = null;
  ollamaProcess = null;
}


function findFfmpegPair() {
  const roots = [
    path.join(process.resourcesPath, "media"),
    process.env.AICOMICS_FFMPEG_DIR,
    process.env.FFMPEG_PATH && path.dirname(process.env.FFMPEG_PATH),
    path.join(app.getPath("userData"), "ffmpeg"),
    process.env.AICOMICS_DATA_DIR && path.join(process.env.AICOMICS_DATA_DIR, "engines", "FFmpeg"),
    "D:\\下载"
  ].filter(Boolean);
  const visited = new Set();
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const stat = fs.statSync(root);
    if (stat.isFile()) {
      const dir = path.dirname(root);
      const ffmpeg = path.basename(root).toLowerCase() === "ffmpeg.exe" ? root : path.join(dir, "ffmpeg.exe");
      const ffprobe = process.env.FFPROBE_PATH || path.join(dir, "ffprobe.exe");
      if (fs.existsSync(ffmpeg) && fs.existsSync(ffprobe)) return { ffmpeg, ffprobe };
      continue;
    }
    const queue = [{ dir: root, depth: 0 }];
    while (queue.length && visited.size < 12000) {
      const current = queue.shift();
      const normalized = path.resolve(current.dir).toLowerCase();
      if (visited.has(normalized)) continue;
      visited.add(normalized);
      let entries;
      try { entries = fs.readdirSync(current.dir, { withFileTypes: true }); } catch { continue; }
      const names = new Map(entries.filter(entry => entry.isFile()).map(entry => [entry.name.toLowerCase(), entry.name]));
      if (names.has("ffmpeg.exe") && names.has("ffprobe.exe")) {
        return {
          ffmpeg: path.join(current.dir, names.get("ffmpeg.exe")),
          ffprobe: path.join(current.dir, names.get("ffprobe.exe"))
        };
      }
      if (current.depth < 6) {
        for (const entry of entries) {
          if (entry.isDirectory() && !["node_modules", ".git", "cache", "temp"].includes(entry.name.toLowerCase())) {
            queue.push({ dir: path.join(current.dir, entry.name), depth: current.depth + 1 });
          }
        }
      }
    }
  }
  return null;
}

function localServiceResponds(port, route) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}${route}`, { timeout: 1800 }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; if (body.length > 1024 * 1024) req.destroy(); });
      res.on("end", () => {
        if (res.statusCode !== 200) return resolve(false);
        try {
          const payload = JSON.parse(body);
          if (route === "/api/tags") return resolve(Array.isArray(payload.models));
          return resolve(Boolean(payload && typeof payload === "object"));
        } catch {
          resolve(false);
        }
      });
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

async function waitForLocalService(port, route, child, timeout = 45000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (child && (child.exitCode !== null || child.signalCode !== null)) {
      return false;
    }
    if (await localServiceResponds(port, route)) return true;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  return false;
}

async function startManagedAiEngines(dataDir) {
  if (process.platform !== "win32") return;
  const resources = resolveAiResourcePaths(dataDir);

  // Prefer an already-running Ollama service on either supported local port.
  // Reusing it preserves its existing model store and avoids downloading or copying models.
  let ollamaPort = 0;
  if (await localServiceResponds(11435, "/api/tags")) ollamaPort = 11435;
  else if (await localServiceResponds(11434, "/api/tags")) ollamaPort = 11434;

  if (ollamaPort) {
    process.env.OLLAMA_HOST = `http://127.0.0.1:${ollamaPort}`;
    console.log(`[OLLAMA] Reusing the existing local service on port ${ollamaPort}; model files will not be changed.`);
  } else if (resources.ollamaExe && resources.ollamaModels) {
    process.env.OLLAMA_HOST = "http://127.0.0.1:11435";
    const ollamaEnv = {
      ...process.env,
      OLLAMA_HOST: "127.0.0.1:11435",
      OLLAMA_MODELS: resources.ollamaModels,
      OLLAMA_NO_CLOUD: "1"
    };
    ollamaProcess = spawn(resources.ollamaExe, ["serve"], {
      cwd: resources.ollamaRoot || path.dirname(resources.ollamaExe),
      windowsHide: true,
      env: ollamaEnv,
      stdio: ["ignore", "pipe", "pipe"]
    });
    logChild("OLLAMA", ollamaProcess);
    void waitForLocalService(11435, "/api/tags", ollamaProcess, 30000).then((ready) => {
      if (ready) console.log("[OLLAMA] Local service is ready on 127.0.0.1:11435.");
      else console.warn("[OLLAMA] Process was started but the service did not become ready within 30 seconds. Existing model files were not changed; check the local engine status and logs.");
    });
  } else {
    console.warn("[OLLAMA] Existing runtime/models were not both detected; no model download, replacement, or migration will be attempted.");
  }

  if (await localServiceResponds(8188, "/system_stats")) {
    console.log("[COMFYUI] Reusing the already-running local ComfyUI service and installed models.");
  } else if (resources.comfy) {
    // Preserve the installed Python/GPU backend; do not force CPU-only startup.
    comfyProcess = spawn(resources.comfy.python, [
      resources.comfy.main, "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"
    ], {
      cwd: resources.comfy.cwd,
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"]
    });
    logChild("COMFYUI", comfyProcess);
    void waitForLocalService(8188, "/system_stats", comfyProcess, 45000).then((ready) => {
      if (ready) console.log("[COMFYUI] Local service is ready on 127.0.0.1:8188.");
      else console.warn("[COMFYUI] Process was started but the service did not become ready within 45 seconds. Existing files were not changed; check the local engine status and logs.");
    });
  } else {
    console.warn("[COMFYUI] Existing runtime was not detected; no runtime download, replacement, or migration will be attempted.");
  }
}

async function startProductionServices({ startApi = true, startWeb = true } = {}) {
  const resources = process.resourcesPath;
  const apiExe = path.join(resources, "api", "ai-comic-api.exe");
  const webServer = path.join(resources, "web", "server.js");
  const webNodeModules = path.join(resources, "web", "node_modules", "next");
  const ffmpegPair = findFfmpegPair();
  const ffmpeg = ffmpegPair?.ffmpeg;
  const ffprobe = ffmpegPair?.ffprobe;
  const dataDir = app.getPath("userData");

  for (const file of [apiExe, webServer, webNodeModules]) {
    if (!fs.existsSync(file)) {
      throw new Error(`安装包缺少必要文件：${file}`);
    }
  }

  fs.mkdirSync(dataDir, { recursive: true });
  await startManagedAiEngines(dataDir);

  if (fs.existsSync(dataDir)) {
    const storageDir = path.join(dataDir, "storage");
    fs.mkdirSync(storageDir, { recursive: true });
  }

  if (startApi) {
    apiProcess = spawn(apiExe, [], {
      cwd: resources,
      windowsHide: true,
      env: {
        ...process.env,
        APP_DATA_DIR: dataDir,
        AICOMICS_DATA_DIR: dataDir,
        OLLAMA_HOST: process.env.OLLAMA_HOST || "http://127.0.0.1:11435",
        OLLAMA_MODELS: resolveAiResourcePaths(dataDir).ollamaModels || process.env.OLLAMA_MODELS || path.join(dataDir, "engines", "Ollama", "models"),
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        ...(ffmpegPair ? { FFMPEG_PATH: ffmpeg, FFPROBE_PATH: ffprobe } : {})
      }
    });
    logChild("API", apiProcess);
  }

  if (startWeb) {
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
}

async function createWindow() {
  if (!updateCenter) {
    updateCenter = registerUpdateCenter({ app, ipcMain, BrowserWindow, port: resolveUpdatePort() });
    console.log(`[UPDATE CENTER] Local update interface: http://127.0.0.1:${updateCenter.port}`);
  }
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

    // Reuse healthy orphaned services from a previous unclean exit instead of
    // forcing users to find and kill processes manually.
    if (!apiPortFree) {
      const health = await requestHealth();
      if (
        !health ||
        health.status !== "ok" ||
        !Array.isArray(health.loaded_routers) ||
        health.loaded_routers.length < 6 ||
        (Array.isArray(health.missing_routers) && health.missing_routers.length > 0)
      ) {
        throw new Error(
          "本地端口 8000 已被其他程序占用，而且不是可用的 AI 漫剧制作大师 API。请关闭占用程序后重新启动。软件不会强行结束其他程序。"
        );
      }
    }

    if (!webPortFree && !(await requestWebHealth())) {
      throw new Error(
        "本地端口 3000 已被其他程序占用，而且不是 AI 漫剧制作大师网页服务。请关闭占用程序后重新启动。软件不会强行结束其他程序。"
      );
    }

    await startProductionServices({
      startApi: apiPortFree,
      startWeb: webPortFree
    });
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
    if (updateCenter) { void updateCenter.close(); updateCenter = null; }
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
    item.on("updated", () => {
      if (item.getReceivedBytes() > 2 * 1024 * 1024 * 1024) item.cancel();
    });
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

ipcMain.handle("ai-browser:fill-prompt", async (_event, payload = {}) => {
  const taskId = String(payload.taskId || "");
  const prompt = String(payload.prompt || "");
  if (!/^task-[a-zA-Z0-9-]{5,110}$/.test(taskId)) throw new Error("无效的任务编号");
  if (!prompt.trim() || prompt.length > 50000) throw new Error("提示词不能为空且不能超过 50000 个字符");
  const entry = [...aiBrowserTasks.entries()].find(([, value]) => value === taskId);
  if (!entry) throw new Error("找不到该任务对应的 AI 网页，请先打开任务网页");
  const browser = aiBrowserWindows.get(entry[0]);
  if (!browser || browser.isDestroyed() || browser.webContents.isDestroyed()) throw new Error("AI 网页已关闭，请重新打开");
  const currentUrl = new URL(browser.webContents.getURL());
  const allowedHosts = new Set(["chatgpt.com", "chat.openai.com", "chat.deepseek.com", "doubao.com", "www.doubao.com", "grok.com", "www.grok.com"]);
  if (currentUrl.protocol !== "https:" || !allowedHosts.has(currentUrl.hostname)) throw new Error("当前网页不在允许自动填入的 AI 网站范围内");
  const script = "(" + function (text) {
    const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    const candidates = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')].filter(visible).filter((el) => !el.disabled && el.getAttribute("aria-disabled") !== "true");
    const preferred = candidates.find((el) => /message|prompt|发送|消息|输入|问/i.test([el.getAttribute("placeholder"), el.getAttribute("aria-label"), el.getAttribute("data-placeholder"), el.getAttribute("name")].filter(Boolean).join(" ")));
    const target = preferred || candidates[candidates.length - 1];
    if (!target) return { ok: false, reason: "没有找到消息输入框；请先登录并打开对话后重试。" };
    target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) { const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(target), "value")?.set; if (setter) setter.call(target, text); else target.value = text; }
    else target.textContent = text;
    target.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, length: text.length };
  }.toString() + ")(" + JSON.stringify(prompt) + ")";
  const result = await browser.webContents.executeJavaScript(script);
  if (!result || !result.ok) throw new Error((result && result.reason) || "自动填入失败，请手动粘贴交接包");
  return { filled: true, length: result.length, submitted: false };
});
ipcMain.handle("ai-browser:list-open-tasks", async () => {
  const allowedHosts = new Set(["chatgpt.com", "chat.openai.com", "chat.deepseek.com", "doubao.com", "www.doubao.com", "grok.com", "www.grok.com"]);
  const response = await fetch("http://127.0.0.1:8000/api/web-ai/tasks?limit=500");
  if (!response.ok) throw new Error("读取 AI 任务清单失败：" + response.status);
  const data = await response.json();
  const taskMap = new Map((data.items || []).map((task) => [task.id, task]));
  return [...aiBrowserWindows.entries()].flatMap(([webContentsId, browser]) => {
    if (!browser || browser.isDestroyed() || browser.webContents.isDestroyed()) return [];
    try {
      const currentUrl = new URL(browser.webContents.getURL());
      const taskId = aiBrowserTasks.get(webContentsId);
      const task = taskMap.get(taskId);
      if (!taskId || !task || task.category !== "video" || currentUrl.protocol !== "https:" || !allowedHosts.has(currentUrl.hostname)) return [];
      return [{ taskId, host: currentUrl.hostname, title: task.title || browser.getTitle() || currentUrl.hostname, category: task.category, novelId: task.novelId }];
    } catch { return []; }
  });
});

ipcMain.handle("ai-browser:upload-reference-files", async (_event, payload = {}) => {
  const taskId = String(payload.taskId || "");
  const fileIds = Array.isArray(payload.fileIds) ? payload.fileIds : [];
  if (!/^task-[a-zA-Z0-9-]{5,110}$/.test(taskId)) throw new Error("无效的目标 AI 任务编号");
  if (!fileIds.length || fileIds.length > 10 || fileIds.some((id) => !Number.isInteger(id) || id < 1)) {
    throw new Error("请选择 1 到 10 张已归档的参考图片");
  }
  if (new Set(fileIds).size !== fileIds.length) throw new Error("参考图片列表存在重复项目");
  const entry = [...aiBrowserTasks.entries()].find(([, value]) => value === taskId);
  if (!entry) throw new Error("目标 AI 网页未打开，请先打开对应的视频分镜提示词任务");
  const browser = aiBrowserWindows.get(entry[0]);
  if (!browser || browser.isDestroyed() || browser.webContents.isDestroyed()) throw new Error("目标 AI 网页已关闭，请重新打开");
  const currentUrl = new URL(browser.webContents.getURL());
  const allowedHosts = new Set(["chatgpt.com", "chat.openai.com", "chat.deepseek.com", "doubao.com", "www.doubao.com", "grok.com", "www.grok.com"]);
  if (currentUrl.protocol !== "https:" || !allowedHosts.has(currentUrl.hostname)) throw new Error("当前网页不在允许上传参考图的 AI 网站范围内");
  const tasksResponse = await fetch("http://127.0.0.1:8000/api/web-ai/tasks?limit=500");
  if (!tasksResponse.ok) throw new Error("无法核实目标 AI 任务信息");
  const tasksData = await tasksResponse.json();
  const targetTask = (tasksData.items || []).find((task) => task.id === taskId);
  if (!targetTask || targetTask.category !== "video" || !targetTask.novelId) throw new Error("参考图片只能上传到已登记小说的视频分镜任务");

  const resultDir = path.resolve(app.getPath("userData"), "storage", "web_ai_results");
  const uploadPaths = [];
  let totalBytes = 0;
  for (const fileId of fileIds) {
    const response = await fetch("http://127.0.0.1:8000/api/web-ai/files/" + fileId + "/upload-info");
    const info = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(info.detail || ("读取参考图片失败：" + response.status));
    if (!info.novelId || info.novelId !== targetTask.novelId) throw new Error("参考图片必须来自同一部小说，不能跨小说上传");
    const storedName = String(info.storedName || "");
    if (!/^[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif)$/.test(storedName)) throw new Error("参考图片文件标识无效");
    const targetPath = path.resolve(resultDir, storedName);
    if (path.dirname(targetPath) !== resultDir || !fs.existsSync(targetPath)) throw new Error("参考图片文件不存在或路径无效");
    const realResultDir = fs.realpathSync(resultDir);
    const realTargetPath = fs.realpathSync(targetPath);
    if (path.dirname(realTargetPath) !== realResultDir) throw new Error("参考图片不能通过目录外的符号链接访问");
    const stat = fs.statSync(realTargetPath);
    if (!stat.isFile() || stat.size !== info.size || stat.size > 100 * 1024 * 1024) throw new Error("参考图片大小校验失败");
    totalBytes += stat.size;
    uploadPaths.push(realTargetPath);
  }
  if (totalBytes > 200 * 1024 * 1024) throw new Error("本次参考图片总大小不能超过 200 MB");

  const debuggerSession = browser.webContents.debugger;
  const attachedHere = !debuggerSession.isAttached();
  try {
    if (attachedHere) debuggerSession.attach("1.3");
    await debuggerSession.sendCommand("DOM.enable");
    const document = await debuggerSession.sendCommand("DOM.getDocument", { depth: -1, pierce: true });
    const result = await debuggerSession.sendCommand("DOM.querySelectorAll", {
      nodeId: document.root.nodeId,
      selector: 'input[type="file"]',
    });
    if (!result.nodeIds || !result.nodeIds.length) throw new Error("当前 AI 网页没有可用的文件上传入口，请先点击网页上的附件/上传按钮后重试");
    let chosen = null;
    for (const nodeId of result.nodeIds) {
      const described = await debuggerSession.sendCommand("DOM.describeNode", { nodeId });
      const attributes = described.node.attributes || [];
      const values = {};
      for (let i = 0; i + 1 < attributes.length; i += 2) values[attributes[i]] = attributes[i + 1];
      const accept = String(values.accept || "").toLowerCase();
      if (!accept || accept.includes("image") || accept.includes("*/*")) {
        chosen = { nodeId, multiple: Object.prototype.hasOwnProperty.call(values, "multiple") };
        break;
      }
    }
    if (!chosen) throw new Error("没有找到接受图片的文件上传入口");
    if (uploadPaths.length > 1 && !chosen.multiple) throw new Error("当前网页上传入口一次只接受一张图片，请减少选择数量后重试");
    await debuggerSession.sendCommand("DOM.setFileInputFiles", { nodeId: chosen.nodeId, files: uploadPaths });
    return { selected: uploadPaths.length, submitted: false, taskId };
  } finally {
    if (attachedHere && debuggerSession.isAttached()) debuggerSession.detach();
  }
});

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


// Install Ollama only after explicit user confirmation from the local model page.
registerOllamaRuntimeInstaller({ ipcMain, app, BrowserWindow });
