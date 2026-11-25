"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const MAX_PACKAGE_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 500;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ALLOWED_ROOTS = ["web/.next/static/", "web/public/"];
const ALLOWED_EXTENSIONS = new Set([".js", ".css", ".json"]);

// Release engineering must pin the production Ed25519 public key here.
// An empty key deliberately fails closed; test keys are injected only by tests.
const TRUSTED_PATCH_PUBLIC_KEY_PEM = "";

function canonicalize(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.keys(value).sort().map(key =>
      JSON.stringify(key) + ":" + canonicalize(value[key])
    ).join(",") + "}";
  }
  return JSON.stringify(value);
}

function validPath(value) {
  if (typeof value !== "string" || !value || value.includes("\\") || value.startsWith("/")) return false;
  const parts = value.split("/");
  if (parts.some(part => !part || part === "." || part === "..")) return false;
  return ALLOWED_ROOTS.some(root => value.startsWith(root)) &&
    ALLOWED_EXTENSIONS.has(path.posix.extname(value).toLowerCase());
}

function verifySignedPatch(envelope, publicKeyPem = TRUSTED_PATCH_PUBLIC_KEY_PEM) {
  if (!publicKeyPem || !String(publicKeyPem).trim()) {
    return { valid: false, trusted: false, error: "尚未配置正式发行 Ed25519 公钥，拒绝导入未验证补丁。" };
  }
  try {
    if (!envelope || typeof envelope !== "object" || !envelope.signed || typeof envelope.signed !== "object") {
      throw new Error("补丁包结构无效。");
    }
    const signed = envelope.signed;
    if (signed.schema_version !== 1 || signed.product !== "AI漫剧制作大师" ||
        typeof signed.package_id !== "string" || !/^[a-f0-9-]{16,64}$/i.test(signed.package_id) ||
        typeof signed.target_version !== "string" || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(signed.target_version) ||
        !Array.isArray(signed.files) || signed.files.length < 1 || signed.files.length > MAX_FILES) {
      throw new Error("补丁包版本、产品标识或文件清单不符合规范。");
    }
    let totalBytes = 0;
    const seen = new Set();
    const validatedFiles = [];
    for (const file of signed.files) {
      if (!file || !validPath(file.path) || seen.has(file.path)) throw new Error("补丁包含重复或不允许的目标路径。");
      seen.add(file.path);
      if (!Number.isInteger(file.size_bytes) || file.size_bytes < 1 || file.size_bytes > MAX_FILE_BYTES ||
          !/^[a-f0-9]{64}$/i.test(String(file.sha256 || "")) ||
          typeof file.content_base64 !== "string" || file.content_base64.length > Math.ceil(MAX_FILE_BYTES * 4 / 3) + 8) {
        throw new Error("补丁文件大小、哈希或内容编码无效。");
      }
      const content = Buffer.from(file.content_base64, "base64");
      if (content.length !== file.size_bytes || content.toString("base64") !== file.content_base64 ||
          crypto.createHash("sha256").update(content).digest("hex") !== file.sha256.toLowerCase()) {
        throw new Error("补丁文件内容与大小或 SHA-256 不一致。");
      }
      totalBytes += content.length;
      if (totalBytes > MAX_PACKAGE_BYTES) throw new Error("补丁包总大小超过 20 MiB。");
      validatedFiles.push({ path: file.path, size_bytes: content.length, sha256: file.sha256.toLowerCase(), content });
    }
    const signature = Buffer.from(String(envelope.signature || ""), "base64");
    if (!signature.length || signature.toString("base64") !== envelope.signature) throw new Error("补丁签名编码无效。");
    const publicKey = crypto.createPublicKey(publicKeyPem);
    if (publicKey.asymmetricKeyType !== "ed25519") throw new Error("补丁信任公钥必须为 Ed25519。");
    const message = Buffer.from(canonicalize(signed), "utf8");
    if (!crypto.verify(null, message, publicKey, signature)) throw new Error("补丁签名验证失败，文件可能被篡改或签发者不可信。");
    return {
      valid: true, trusted: true, package_id: signed.package_id,
      target_version: signed.target_version, files: validatedFiles,
      total_bytes: totalBytes, signed_payload: signed,
      message: "签名与全部文件哈希验证通过；补丁仅已验证，尚未应用。"
    };
  } catch (error) {
    return { valid: false, trusted: false, error: String(error && error.message || error) };
  }
}

function createRollbackSnapshot({ resourcesPath, backupRoot, verifiedPatch }) {
  if (!verifiedPatch || !verifiedPatch.valid || !verifiedPatch.trusted) {
    throw new Error("只有通过可信签名验证的补丁才能生成回滚备份。");
  }
  const root = fs.realpathSync(resourcesPath);
  const backupBase = path.resolve(backupRoot);
  fs.mkdirSync(backupBase, { recursive: true });
  const backupReal = fs.realpathSync(backupBase);
  const snapshotId = crypto.randomUUID();
  const snapshotDir = path.join(backupReal, snapshotId);
  fs.mkdirSync(snapshotDir, { recursive: false });
  const files = [];

  try {
    for (const item of verifiedPatch.files) {
      const target = path.resolve(root, ...item.path.split("/"));
      const relative = path.relative(root, target);
      if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("备份目標路径越界。");
      let cursor = root;
      let symlink = false;
      for (const segment of item.path.split("/")) {
        cursor = path.join(cursor, segment);
        try { if (fs.lstatSync(cursor).isSymbolicLink()) symlink = true; }
        catch { break; }
      }
      if (symlink) throw new Error("目标路径包含符号链接，拒绝备份。");
      if (!fs.existsSync(target)) {
        files.push({ path: item.path, existed: false, backup_path: null, sha256: null });
        continue;
      }
      const stat = fs.statSync(target);
      if (!stat.isFile() || stat.size > MAX_FILE_BYTES) throw new Error("现有目标不是允许大小的普通文件。");
      const bytes = fs.readFileSync(target);
      const digest = crypto.createHash("sha256").update(bytes).digest("hex");
      const backupPath = path.join(snapshotDir, ...item.path.split("/"));
      fs.mkdirSync(path.dirname(backupPath), { recursive: true });
      fs.writeFileSync(backupPath, bytes, { flag: "wx" });
      files.push({ path: item.path, existed: true, backup_path: path.relative(backupReal, backupPath).split(path.sep).join("/"), sha256: digest, size_bytes: bytes.length });
    }
    const manifest = {
      schema_version: 1, snapshot_id: snapshotId, package_id: verifiedPatch.package_id,
      created_at: new Date().toISOString(), purpose: "backup-only; no target files changed",
      files
    };
    fs.writeFileSync(path.join(snapshotDir, "rollback-manifest.json"), JSON.stringify(manifest, null, 2), { flag: "wx" });
    return { snapshot_id: snapshotId, file_count: files.length, backup_root: backupReal, files, message: "已生成原文件回滚备份；尚未修改安装目录。" };
  } catch (error) {
    fs.rmSync(snapshotDir, { recursive: true, force: true });
    throw error;
  }
}

module.exports = {
  canonicalize, validPath, verifySignedPatch, createRollbackSnapshot,
  MAX_PACKAGE_BYTES, MAX_FILE_BYTES, TRUSTED_PATCH_PUBLIC_KEY_PEM
};
