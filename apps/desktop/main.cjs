const { app, BrowserWindow, dialog, ipcMain, shell, session } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const net = require("net");
const { registerOllamaRuntimeInstaller } = require("./ollama-runtime-installer.cjs");

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
  webProcess = null;
  apiProcess = null;
}

function startProductionServices({ startApi = true, startWeb = true } = {}) {
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

  if (startApi) {
    apiProcess = spawn(apiExe, [], {
      cwd: resources,
      windowsHide: true,
      env: {
        ...process.env,
        APP_DATA_DIR: dataDir,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        FFMPEG_PATH: ffmpeg,
        FFPROBE_PATH: ffprobe
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

    startProductionServices({
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
  let result = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    if (browser.isDestroyed() || browser.webContents.isDestroyed()) throw new Error("AI 网页已关闭，请重新打开");
    const latestUrl = new URL(browser.webContents.getURL());
    if (latestUrl.protocol !== "https:" || !allowedHosts.has(latestUrl.hostname)) {
      throw new Error("AI 网页已跳转到未允许自动填入的站点，已停止操作");
    }
    result = await browser.webContents.executeJavaScript(script);
    if (result && result.ok) break;
    if (attempt < 4) await new Promise((resolve) => setTimeout(resolve, 600));
  }
  if (!result || !result.ok) throw new Error((result && result.reason) || "自动填入失败，请手动粘贴交接包");
  return { filled: true, length: result.length, submitted: false };
});
ipcMain.handle("ai-browser:capture-text-result", async (_event, payload = {}) => {
  const taskId = String(payload.taskId || "");
  if (!/^task-[a-zA-Z0-9-]{5,110}$/.test(taskId)) throw new Error("无效的任务编号");
  const entry = [...aiBrowserTasks.entries()].find(([, value]) => value === taskId);
  if (!entry) throw new Error("找不到该任务对应的 AI 网页，请先打开任务网页");
  const browser = aiBrowserWindows.get(entry[0]);
  if (!browser || browser.isDestroyed() || browser.webContents.isDestroyed()) throw new Error("AI 网页已关闭，请重新打开");
  const currentUrl = new URL(browser.webContents.getURL());
  const allowedHosts = new Set(["chatgpt.com", "chat.openai.com", "chat.deepseek.com", "doubao.com", "www.doubao.com", "grok.com", "www.grok.com"]);
  if (currentUrl.protocol !== "https:" || !allowedHosts.has(currentUrl.hostname)) {
    throw new Error("当前网页不在允许读取结果的 AI 网站范围内");
  }
  const script = "(" + function (host) {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const selectors = host.includes("chatgpt") || host.includes("openai")
      ? ['[data-message-author-role="assistant"]']
      : host.includes("deepseek")
        ? [".ds-markdown", '[data-message-author-role="assistant"]']
        : host.includes("doubao")
          ? ['[data-testid*="message-content"]', '[class*="message-content"]', '[data-message-author-role="assistant"]']
          : ['[data-message-author-role="assistant"]', '[data-testid*="message"]', 'main [class*="message"]'];
    const candidates = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (!visible(element)) continue;
        const role = (element.getAttribute("data-message-author-role") || element.getAttribute("data-role") || "").toLowerCase();
        if (role && role !== "assistant" && !host.includes("doubao")) continue;
        const text = (element.innerText || element.textContent || "").trim();
        if (text.length >= 8 && !candidates.includes(text)) candidates.push(text);
      }
      if (candidates.length) break;
    }
    const text = candidates[candidates.length - 1] || "";
    if (!text) return { ok: false, reason: "没有找到可读取的最新 AI 回复；请等待生成完成，或手动复制网页结果。" };
    if (text.length > 50000) return { ok: false, reason: "网页回复超过 50000 字，请手动分段复制保存。" };
    return { ok: true, text, length: text.length };
  }.toString() + ")(" + JSON.stringify(currentUrl.hostname) + ")";
  const result = await browser.webContents.executeJavaScript(script);
  if (!result || !result.ok) throw new Error((result && result.reason) || "读取网页回复失败，请手动复制");
  return { captured: true, text: result.text, length: result.length, saved: false };
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
