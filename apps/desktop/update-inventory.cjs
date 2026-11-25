"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set([".js", ".css", ".json"]);
const ALLOWED_ROOTS = ["web/.next/static/", "web/public/"];

function isInside(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function validateInstallPath(value) {
  if (typeof value !== "string" || !value || value.includes("\\") || value.startsWith("/")) return false;
  if (value.split("/").some(part => !part || part === "." || part === "..")) return false;
  if (!ALLOWED_ROOTS.some(root => value.startsWith(root))) return false;
  return ALLOWED_EXTENSIONS.has(path.posix.extname(value).toLowerCase());
}

function hashFile(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function inspectUpdateInventory({ resourcesPath, appVersion }) {
  const result = {
    available: false,
    valid: false,
    app_version: String(appVersion || ""),
    manifest_version: null,
    generated_at: null,
    declared_file_count: 0,
    verified_file_count: 0,
    updateable_file_count: 0,
    missing_file_count: 0,
    changed_file_count: 0,
    invalid_file_count: 0,
    files: [],
    message: "",
  };

  try {
    const root = fs.realpathSync(resourcesPath);
    const manifestPath = path.join(root, "update", "manifest.json");
    if (!isInside(root, manifestPath) || !fs.existsSync(manifestPath)) {
      result.message = "安装包未包含更新清单；当前安装版本无法进行本机文件核验。";
      return result;
    }
    const manifestStat = fs.lstatSync(manifestPath);
    if (!manifestStat.isFile() || manifestStat.isSymbolicLink() || manifestStat.size > 2 * 1024 * 1024) {
      result.message = "更新清单文件类型或大小不符合安全要求。";
      return result;
    }
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    result.available = true;
    result.manifest_version = String(manifest.product_version || "");
    result.generated_at = manifest.generated_at || null;
    result.declared_file_count = Array.isArray(manifest.files) ? manifest.files.length : 0;

    if (manifest.schema_version !== 1 ||
        manifest.product !== "AI漫剧制作大师" ||
        manifest.purpose !== "inventory-only; does not authorize or perform file replacement" ||
        !Array.isArray(manifest.files) ||
        manifest.files.length > 10000 ||
        !manifest.limits ||
        !Array.isArray(manifest.limits.allowed_roots) ||
        !ALLOWED_ROOTS.every(rootPath => manifest.limits.allowed_roots.includes(rootPath)) ||
        String(manifest.product_version) !== String(appVersion)) {
      result.message = "清单格式、产品标识、允许目录或程序版本不匹配，已停止核验。";
      return result;
    }

    result.valid = true;
    for (const entry of manifest.files) {
      const record = { path: String(entry?.install_path || ""), status: "invalid" };
      if (!validateInstallPath(entry?.install_path) ||
          !Number.isInteger(entry.size_bytes) || entry.size_bytes < 1 || entry.size_bytes > MAX_FILE_BYTES ||
          !/^[a-f0-9]{64}$/i.test(String(entry.sha256 || "")) ||
          entry.update_class !== "web-static-text") {
        result.invalid_file_count++;
        result.files.push(record);
        continue;
      }

      const target = path.resolve(root, ...entry.install_path.split("/"));
      if (!isInside(root, target)) {
        result.invalid_file_count++;
        result.files.push(record);
        continue;
      }

      let current = root;
      let hasSymlink = false;
      for (const part of entry.install_path.split("/")) {
        current = path.join(current, part);
        try {
          if (fs.lstatSync(current).isSymbolicLink()) hasSymlink = true;
        } catch {
          // Missing files are classified below.
          break;
        }
      }
      if (hasSymlink) {
        result.invalid_file_count++;
        result.files.push(record);
        continue;
      }
      if (!fs.existsSync(target)) {
        record.status = "missing";
        result.missing_file_count++;
        result.files.push(record);
        continue;
      }

      const stat = fs.statSync(target);
      if (!stat.isFile() || stat.size !== entry.size_bytes) {
        record.status = "changed";
        result.changed_file_count++;
        result.files.push(record);
        continue;
      }
      const digest = hashFile(target);
      if (digest !== String(entry.sha256).toLowerCase()) {
        record.status = "changed";
        result.changed_file_count++;
      } else {
        record.status = "verified";
        result.verified_file_count++;
        result.updateable_file_count++;
      }
      result.files.push(record);
    }

    result.message = result.invalid_file_count
      ? "清单中存在不合规文件记录，已拒绝将其列为可更新文件。"
      : result.missing_file_count || result.changed_file_count
        ? "已完成本机核验；部分文件缺失或与安装基线不同，暂不具备补丁应用条件。"
        : "本机安装文件与构建清单一致；当前仅完成核验，尚未启用补丁应用。";
    return result;
  } catch (error) {
    result.valid = false;
    result.message = "读取或核验安装清单失败：" + String(error && error.message || error);
    return result;
  }
}

module.exports = { inspectUpdateInventory, validateInstallPath, MAX_FILE_BYTES };
