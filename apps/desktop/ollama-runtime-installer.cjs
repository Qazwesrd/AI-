"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const https = require("node:https");
const { spawn, execFile } = require("node:child_process");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe";
const ALLOWED_DOWNLOAD_HOSTS = new Set([
  "ollama.com",
  "github.com",
  "objects.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);
const REQUIRED_FREE_BYTES = 5 * 1024 ** 3;
const API_PORTS = [11435, 11434];
const API_PATH = "/api/tags";

function resolveAppDataDirectory(defaultDirectory, env = process.env) {
  const configured = String(env.AICOMICS_DATA_DIR || env.APP_DATA_DIR || "").trim();
  return path.resolve(configured || defaultDirectory);
}

function getBundledRuntimePath(userDataDirectory) {
  return path.join(userDataDirectory, "engines", "Ollama", "runtime", "ollama.exe");
}

function getBundledModelsPath(runtimePath) {
  return path.resolve(path.dirname(runtimePath), "..", "models");
}

function getBundledRuntimeEnvironment(runtimePath, baseEnv = process.env) {
  return {
    ...baseEnv,
    OLLAMA_HOST: "127.0.0.1:11435",
    OLLAMA_MODELS: getBundledModelsPath(runtimePath),
    OLLAMA_NO_CLOUD: "1",
    OLLAMA_NUM_PARALLEL: "1",
    OLLAMA_MAX_LOADED_MODELS: "1",
  };
}

async function findOllamaEndpoint() {
  const configured = String(process.env.OLLAMA_HOST || "").trim();
  const candidates = [];
  if (configured) {
    const normalized = configured.startsWith("http") ? configured : "http://" + configured;
    candidates.push(normalized.replace(/\\/$/, "") + API_PATH);
  }
  for (const port of API_PORTS) candidates.push(`http://127.0.0.1:${port}${API_PATH}`);
  for (const url of [...new Set(candidates)]) {
    const data = await requestJson(url);
    if (data && Array.isArray(data.models)) return { url, baseUrl: url.slice(0, -API_PATH.length), data };
  }
  return null;
}

function startBundledRuntime(runtimePath) {
  if (!fs.existsSync(runtimePath)) return null;
  const child = spawn(runtimePath, ["serve"], {
    windowsHide: true,
    detached: true,
    stdio: "ignore",
    env: getBundledRuntimeEnvironment(runtimePath),
  });
  child.unref();
  return child;
}

async function waitForBundledEndpoint(timeoutMs = 30000) {
  const baseUrl = "http://127.0.0.1:11435";
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const data = await requestJson(baseUrl + API_PATH);
    if (data && Array.isArray(data.models)) return { url: baseUrl + API_PATH, baseUrl, data };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return null;
}

async function ensureBundledRuntime(runtimePath) {
  const baseUrl = "http://127.0.0.1:11435";
  const existing = await requestJson(baseUrl + API_PATH);
  if (existing && Array.isArray(existing.models)) {
    return { url: baseUrl + API_PATH, baseUrl, data: existing };
  }
  if (!fs.existsSync(runtimePath)) return null;
  startBundledRuntime(runtimePath);
  return waitForBundledEndpoint();
}

function isAllowedDownloadUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && ALLOWED_DOWNLOAD_HOSTS.has(url.hostname) && !url.username && !url.password;
  } catch {
    return false;
  }
}

function isTrustedOllamaSignature(result) {
  if (!result || result.status !== "Valid" || typeof result.subject !== "string") return false;
  return result.subject.split(",").some(part => part.trim().toLowerCase() === "o=ollama inc.");
}

function getFreeBytes(directory) {
  const stats = fs.statfsSync(directory);
  return Number(stats.bavail) * Number(stats.bsize);
}

function requestJson(urlString, timeoutMs = 2500) {
  return new Promise((resolve) => {
    const request = http.get(urlString, { timeout: timeoutMs }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", chunk => { body += chunk; });
      response.on("end", () => {
        try {
          resolve(response.statusCode === 200 ? JSON.parse(body) : null);
        } catch {
          resolve(null);
        }
      });
    });
    request.on("error", () => resolve(null));
    request.on("timeout", () => { request.destroy(); resolve(null); });
  });
}

function downloadFile(urlString, destination, onProgress, setRequest, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (!isAllowedDownloadUrl(urlString)) return reject(new Error("下载地址不在 Ollama 官方允许域名范围内。"));
    if (redirects > 5) return reject(new Error("官方安装包重定向次数过多。"));
    const request = https.get(urlString, (response) => {
      const code = response.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(code)) {
        const next = new URL(response.headers.location || "", urlString).toString();
        response.resume();
        return downloadFile(next, destination, onProgress, setRequest, redirects + 1).then(resolve, reject);
      }
      if (code !== 200) {
        response.resume();
        return reject(new Error("下载 Ollama 安装程序失败，HTTP " + code));
      }
      const total = Number(response.headers["content-length"]) || 0;
      let received = 0;
      const output = fs.createWriteStream(destination, { flags: "w" });
      response.on("data", chunk => {
        received += chunk.length;
        onProgress({ stage: "downloading", receivedBytes: received, totalBytes: total, percent: total ? Math.min(99, Math.floor(received * 100 / total)) : null });
      });
      response.on("error", error => output.destroy(error));
      output.on("error", reject);
      output.on("finish", () => resolve({ receivedBytes: received, totalBytes: total }));
      response.pipe(output);
    });
    setRequest(request);
    request.on("error", reject);
    request.setTimeout(30000, () => request.destroy(new Error("下载连接超时，请检查网络后重试。")));
  });
}

async function verifyInstallerSignature(filePath) {
  const script = "$ErrorActionPreference='Stop'; $s=Get-AuthenticodeSignature -LiteralPath $env:OLLAMA_INSTALLER_PATH; [ordered]@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress";
  const { stdout } = await execFileAsync("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "AllSigned", "-Command", script], {
    windowsHide: true,
    timeout: 30000,
    env: { ...process.env, OLLAMA_INSTALLER_PATH: filePath },
    maxBuffer: 1024 * 1024,
  });
  let result;
  try { result = JSON.parse(stdout.trim()); } catch { throw new Error("无法读取 Ollama 安装包的数字签名信息。"); }
  if (!isTrustedOllamaSignature(result)) {
    throw new Error("Ollama 安装包数字签名未通过验证，软件已阻止安装。不会跳过安全检查。");
  }
  return result;
}

function runInstaller(filePath) {
  return new Promise((resolve, reject) => {
    const child = spawn(filePath, ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], {
      windowsHide: true,
      detached: false,
      stdio: "ignore",
    });
    child.once("error", reject);
    child.once("close", code => code === 0 ? resolve() : reject(new Error("Ollama 安装程序退出代码：" + code)));
  });
}

async function waitForOllama(timeoutMs = 90000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const endpoint = await findOllamaEndpoint();
    if (endpoint) return endpoint;
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
  return null;
}

function registerOllamaRuntimeInstaller({ ipcMain, app, BrowserWindow }) {
  let job = { status: "idle", stage: "idle", percent: 0, message: "尚未安装" };
  let activeRequest = null;
  let cancelRequested = false;

  const emit = () => {
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.webContents.send("local-ai:ollama-install-progress", { ...job });
    }
  };
  const setJob = (patch) => { job = { ...job, ...patch }; emit(); };

  ipcMain.handle("local-ai:ollama-status", async () => {
    const home = app.getPath("home");
    const runtimePath = getBundledRuntimePath(app.getPath("userData"));
    const hasBundledRuntime = fs.existsSync(runtimePath);
    let freeBytes = null;
    try { freeBytes = getFreeBytes(hasBundledRuntime ? path.dirname(runtimePath) : home); } catch {}
    const endpoint = hasBundledRuntime
      ? await ensureBundledRuntime(runtimePath)
      : await findOllamaEndpoint();
    return {
      installed: Boolean(endpoint) || hasBundledRuntime,
      status: endpoint ? "ready" : hasBundledRuntime ? "runtime_stopped" : "not_installed",
      endpoint: endpoint?.baseUrl || null,
      runtimePath: hasBundledRuntime ? runtimePath : null,
      modelsPath: hasBundledRuntime ? getBundledModelsPath(runtimePath) : null,
      freeBytes,
      requiredFreeBytes: REQUIRED_FREE_BYTES,
      enoughSpace: freeBytes !== null && freeBytes >= REQUIRED_FREE_BYTES,
      installDirectory: hasBundledRuntime
        ? path.resolve(path.dirname(runtimePath), "..")
        : path.join(home, "AppData", "Local", "Programs", "Ollama"),
    };
  });

  ipcMain.handle("local-ai:install-ollama", async (_event, payload = {}) => {
    if (process.platform !== "win32") throw new Error("Ollama 自动安装目前仅支持 Windows 桌面版。");
    if (payload.confirm !== true) throw new Error("必须先由用户明确确认，才会下载或安装 Ollama。");
    if (["downloading", "verifying", "installing", "checking"].includes(job.status)) throw new Error("Ollama 安装任务正在进行。");
    const runtimePath = getBundledRuntimePath(app.getPath("userData"));
    if (fs.existsSync(runtimePath)) {
      setJob({ status: "checking", stage: "正在启动 E 盘已有本地 Ollama", percent: 10, message: "复用 E 盘运行程序和现有模型，不重新下载" });
      const ready = await ensureBundledRuntime(runtimePath);
      if (ready) {
        setJob({ status: "success", stage: "启动完成", percent: 100, message: "已启动并复用 E 盘 Ollama 运行环境及模型。" });
        return { status: "already_installed", endpoint: ready.baseUrl };
      }
      throw new Error("检测到 E 盘 Ollama 程序，但 11435 服务未就绪。已保留现有模型文件，不会重新下载或覆盖。");
    }
    const existingEndpoint = await findOllamaEndpoint();
    if (existingEndpoint) return { status: "already_installed", endpoint: existingEndpoint.baseUrl };
    const home = app.getPath("home");
    const freeBytes = getFreeBytes(home);
    if (freeBytes < REQUIRED_FREE_BYTES) throw new Error("可用磁盘空间不足。安装新的 Ollama 运行环境至少需要预留 5 GiB；已有 E 盘运行环境会直接复用，不需要此步骤。");

    cancelRequested = false;
    const installerPath = path.join(app.getPath("temp"), "AI-Comic-Studio-OllamaSetup.exe");
    setJob({ status: "downloading", stage: "正在从 Ollama 官方站点下载", percent: 0, receivedBytes: 0, totalBytes: 0, message: "正在下载安装程序" });
    try {
      await downloadFile(INSTALLER_URL, installerPath, (progress) => {
        if (cancelRequested) return;
        setJob({ ...progress, message: "正在下载 Ollama 官方安装程序" });
      }, request => { activeRequest = request; });
      if (cancelRequested) throw new Error("用户已取消下载。");
      const stat = fs.statSync(installerPath);
      if (!stat.isFile() || stat.size < 10 * 1024 * 1024) throw new Error("下载文件大小异常，已阻止安装。");

      setJob({ status: "verifying", stage: "正在验证数字签名", percent: 100, message: "检查签名是否由 Ollama Inc. 签发" });
      await verifyInstallerSignature(installerPath);
      if (cancelRequested) throw new Error("用户已取消安装。");

      setJob({ status: "installing", stage: "正在安装本地 AI 运行环境", percent: 100, message: "请保持电脑开机，安装过程可能需要几分钟" });
      await runInstaller(installerPath);

      setJob({ status: "checking", stage: "正在检查本地服务", percent: 100, message: "验证本机 Ollama 服务是否启动" });
      const ready = await waitForOllama();
      if (!ready) throw new Error("安装程序已结束，但本地 Ollama 服务尚未就绪。请重启软件或在系统托盘启动 Ollama 后重试检测。");
      setJob({ status: "success", stage: "安装完成", percent: 100, message: "Ollama 本地运行环境已就绪。下一步可确认下载 Qwen3 4B 模型。" });
      return { status: "success", endpoint: ready.baseUrl };
    } catch (error) {
      const cancelled = cancelRequested;
      setJob({ status: cancelled ? "cancelled" : "failed", stage: cancelled ? "已取消" : "安装失败", message: error instanceof Error ? error.message : "Ollama 安装失败" });
      throw error;
    } finally {
      activeRequest = null;
      try { fs.rmSync(installerPath, { force: true }); } catch {}
    }
  });

  ipcMain.handle("local-ai:cancel-ollama-download", async () => {
    if (job.status !== "downloading") return { cancelled: false };
    cancelRequested = true;
    if (activeRequest) activeRequest.destroy(new Error("用户取消下载"));
    return { cancelled: true };
  });
}

module.exports = {
  INSTALLER_URL,
  ALLOWED_DOWNLOAD_HOSTS,
  REQUIRED_FREE_BYTES,
  isAllowedDownloadUrl,
  isTrustedOllamaSignature,
  getFreeBytes,
  resolveAppDataDirectory,
  getBundledRuntimePath,
  getBundledModelsPath,
  getBundledRuntimeEnvironment,
  findOllamaEndpoint,
  ensureBundledRuntime,
  registerOllamaRuntimeInstaller,
};
