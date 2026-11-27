"use strict";

const http = require("node:http");
const https = require("node:https");
const { URL } = require("node:url");

const DEFAULT_UPDATE_PORT = 17890;
const RELEASES_API = "https://api.github.com/repos/Qazwesrd/AI-/releases/latest";
const OLLAMA_PORTS = [11435, 11434];

function resolveUpdatePort(value = process.env.AICOMICS_UPDATE_PORT) {
  const port = value === undefined || value === "" ? DEFAULT_UPDATE_PORT : Number(value);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error("更新服务端口必须是 1024-65535 之间的整数。");
  }
  return port;
}

function isSafeModelName(value) {
  return typeof value === "string" &&
    value.length > 0 && value.length <= 200 &&
    /^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$/.test(value) &&
    !value.includes("..");
}

function compareVersions(a, b) {
  const parse = value => String(value || "").replace(/^v/i, "").split(/[.+-]/).slice(0, 3).map(part => Number.parseInt(part, 10) || 0);
  const left = parse(a), right = parse(b);
  for (let i = 0; i < 3; i++) {
    if ((left[i] || 0) > (right[i] || 0)) return 1;
    if ((left[i] || 0) < (right[i] || 0)) return -1;
  }
  return 0;
}

function requestJson(url, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.hostname !== "api.github.com") {
      return reject(new Error("更新检查仅允许访问配置的官方 HTTPS 更新源。"));
    }
    const request = https.get(parsed, {
      headers: { "Accept": "application/vnd.github+json", "User-Agent": "AI-Comic-Studio-Updater" },
      timeout: timeoutMs,
    }, response => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", chunk => {
        body += chunk;
        if (body.length > 2 * 1024 * 1024) request.destroy(new Error("更新信息超过允许大小。"));
      });
      response.on("end", () => {
        if (response.statusCode !== 200) return reject(new Error("更新源返回 HTTP " + response.statusCode));
        try { resolve(JSON.parse(body)); } catch { reject(new Error("更新源返回的数据不是有效 JSON。")); }
      });
    });
    request.on("error", reject);
    request.on("timeout", () => request.destroy(new Error("更新检查连接超时。")));
  });
}

function requestOllama(port, route, method = "GET", payload = null, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const body = payload ? Buffer.from(JSON.stringify(payload)) : null;
    const request = http.request({
      hostname: "127.0.0.1", port, path: route, method, timeout: timeoutMs,
      headers: body ? { "Content-Type": "application/json", "Content-Length": body.length } : {},
    }, response => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", chunk => { raw += chunk; });
      response.on("end", () => {
        if ((response.statusCode || 500) >= 400) return reject(new Error("本地 Ollama 返回 HTTP " + response.statusCode));
        try { resolve(JSON.parse(raw)); } catch { reject(new Error("本地 Ollama 返回了无法识别的数据。")); }
      });
    });
    request.on("error", reject);
    request.on("timeout", () => request.destroy(new Error("本地模型服务响应超时。")));
    if (body) request.write(body);
    request.end();
  });
}

async function findOllama() {
  for (const port of OLLAMA_PORTS) {
    try {
      const data = await requestOllama(port, "/api/tags", "GET", null, 1800);
      if (Array.isArray(data.models)) return { port, models: data.models };
    } catch {}
  }
  return null;
}

async function updateExistingModel(modelName, onProgress = () => {}) {
  if (!isSafeModelName(modelName)) throw new Error("模型名称格式无效。");
  const service = await findOllama();
  if (!service) throw new Error("未检测到本机 Ollama 服务；不会自动下载或安装运行环境。");
  const installed = service.models.some(model => model.name === modelName || model.model === modelName);
  if (!installed) throw new Error("只能更新本机已安装的模型。请先在模型管理页确认模型名称。");

  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify({ name: modelName, stream: true }));
    const request = http.request({
      hostname: "127.0.0.1", port: service.port, path: "/api/pull", method: "POST",
      timeout: 0,
      headers: { "Content-Type": "application/json", "Content-Length": body.length },
    }, response => {
      let buffer = "";
      let last = null;
      response.setEncoding("utf8");
      response.on("data", chunk => {
        buffer += chunk;
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            last = JSON.parse(line);
            onProgress({
              model: modelName,
              status: String(last.status || "正在更新"),
              completed: Number(last.completed) || 0,
              total: Number(last.total) || 0,
              percent: last.total ? Math.min(100, Math.floor(last.completed * 100 / last.total)) : null,
            });
            if (last.error) return reject(new Error(String(last.error)));
          } catch (error) {
            if (error instanceof SyntaxError) return reject(new Error("模型更新进度数据格式异常。"));
            return reject(error);
          }
        }
      });
      response.on("end", () => {
        if ((response.statusCode || 500) >= 400) return reject(new Error("模型更新失败，HTTP " + response.statusCode));
        if (buffer.trim()) {
          try { last = JSON.parse(buffer); } catch {}
        }
        if (last?.error) return reject(new Error(String(last.error)));
        resolve({ model: modelName, status: "success", port: service.port });
      });
      response.on("error", reject);
    });
    request.on("error", reject);
    request.write(body);
    request.end();
  });
}

function sendJson(response, status, payload) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  response.end(JSON.stringify(payload));
}

function registerUpdateCenter({ app, ipcMain, BrowserWindow, port = resolveUpdatePort() }) {
  let activeModelUpdate = false;
  let server;

  const emitProgress = payload => {
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.webContents.send("updates:model-progress", payload);
    }
  };

  ipcMain.handle("updates:status", async () => {
    const ollama = await findOllama();
    return {
      port, address: "127.0.0.1", localOnly: true, softwareVersion: app.getVersion(),
      softwareUpdateSource: RELEASES_API, modelServicePort: ollama?.port || null,
      installedModels: (ollama?.models || []).map(model => model.name || model.model).filter(Boolean),
      softwareUpdateMode: "release-manifest",
      modelUpdateMode: "ollama-in-place",
    };
  });

  ipcMain.handle("updates:check-software", async () => {
    const release = await requestJson(RELEASES_API);
    const latestVersion = String(release.tag_name || "").replace(/^v/i, "");
    if (!latestVersion) throw new Error("官方更新源没有提供版本号。");
    return {
      currentVersion: app.getVersion(), latestVersion,
      updateAvailable: compareVersions(latestVersion, app.getVersion()) > 0,
      name: release.name || latestVersion, notes: release.body || "",
      publishedAt: release.published_at || null, pageUrl: release.html_url || "",
      assets: (release.assets || []).map(asset => ({
        name: asset.name, size: asset.size, downloadUrl: asset.browser_download_url,
      })),
      notice: "当前仅检查正式 Release 元数据；应用组件热更新包尚未发布，不会自动运行安装程序。",
    };
  });

  ipcMain.handle("updates:update-model", async (_event, payload = {}) => {
    if (payload.confirm !== true) throw new Error("更新模型前必须由用户明确确认。");
    if (activeModelUpdate) throw new Error("已有模型更新任务正在运行。");
    const modelName = String(payload.modelName || "");
    activeModelUpdate = true;
    try {
      return await updateExistingModel(modelName, emitProgress);
    } finally {
      activeModelUpdate = false;
    }
  });

  const serverHandler = async (request, response) => {
    const host = String(request.headers.host || "").split(":")[0].toLowerCase();
    if (!["127.0.0.1", "localhost"].includes(host)) return sendJson(response, 403, { error: "仅允许本机访问。" });
    if (request.method === "GET" && request.url === "/health") {
      return sendJson(response, 200, { status: "ok", service: "ai-comic-update-center", port, localOnly: true });
    }
    if (request.method === "GET" && request.url === "/api/status") {
      const ollama = await findOllama();
      return sendJson(response, 200, {
        service: "AI漫剧制作大师更新中心", port, address: "127.0.0.1",
        softwareVersion: app.getVersion(), softwareUpdateSource: RELEASES_API,
        modelServicePort: ollama?.port || null,
        installedModelCount: ollama?.models.length || 0,
      });
    }
    return sendJson(response, 404, { error: "接口不存在。" });
  };

  server = http.createServer((request, response) => {
    Promise.resolve(serverHandler(request, response)).catch(error => {
      if (!response.headersSent) sendJson(response, 500, { error: error.message || "更新中心内部错误。" });
      else response.destroy();
    });
  });
  server.on("error", error => console.error("[UPDATE CENTER]", error));
  server.listen(port, "127.0.0.1");
  return {
    port,
    close: () => new Promise(resolve => server.close(() => resolve())),
  };
}

module.exports = {
  DEFAULT_UPDATE_PORT, RELEASES_API, OLLAMA_PORTS,
  resolveUpdatePort, isSafeModelName, compareVersions,
  findOllama, updateExistingModel, registerUpdateCenter,
};
