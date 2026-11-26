"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { inspectUpdateInventory, validateInstallPath } = require("./update-inventory.cjs");

function sha(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}
function fixture(t, { version = "1.0.20", relative = "web/.next/static/chunks/app.js", content = "bundle" } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "comic-update-inventory-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const target = path.join(root, ...relative.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content);
  const manifest = {
    schema_version: 1,
    product: "AI漫剧制作大师",
    product_version: version,
    generated_at: "2026-11-26T00:00:00.000Z",
    purpose: "inventory-only; does not authorize or perform file replacement",
    limits: { allowed_roots: ["web/.next/static/", "web/public/"] },
    files: [{
      source: relative.replace(/^web\//, ""),
      install_path: relative,
      size_bytes: Buffer.byteLength(content),
      sha256: sha(content),
      update_class: "web-static-text"
    }]
  };
  fs.mkdirSync(path.join(root, "update"), { recursive: true });
  fs.writeFileSync(path.join(root, "update", "manifest.json"), JSON.stringify(manifest));
  return { root, target, manifest };
}

test("validates and hashes an installed static file", (t) => {
  const { root } = fixture(t);
  const result = inspectUpdateInventory({ resourcesPath: root, appVersion: "1.0.20" });
  assert.equal(result.valid, true);
  assert.equal(result.verified_file_count, 1);
  assert.equal(result.updateable_file_count, 1);
  assert.equal(result.files[0].status, "verified");
});

test("rejects a mismatched application version", (t) => {
  const { root } = fixture(t);
  const result = inspectUpdateInventory({ resourcesPath: root, appVersion: "1.0.21" });
  assert.equal(result.valid, false);
  assert.match(result.message, /版本不匹配/);
});

test("detects changed content and missing files", (t) => {
  const { root, target } = fixture(t);
  fs.writeFileSync(target, "modified");
  const changed = inspectUpdateInventory({ resourcesPath: root, appVersion: "1.0.20" });
  assert.equal(changed.changed_file_count, 1);
  fs.rmSync(target);
  const missing = inspectUpdateInventory({ resourcesPath: root, appVersion: "1.0.20" });
  assert.equal(missing.missing_file_count, 1);
});

test("rejects traversal, absolute paths, unexpected roots and extensions", () => {
  assert.equal(validateInstallPath("../api/evil.js"), false);
  assert.equal(validateInstallPath("/web/public/a.js"), false);
  assert.equal(validateInstallPath("api/ai-comic-api.exe"), false);
  assert.equal(validateInstallPath("web/public/a.exe"), false);
  assert.equal(validateInstallPath("web/public/a.js"), true);
});

test("rejects a symlink that escapes the resource directory", (t) => {
  const { root } = fixture(t, { relative: "web/public/payload.json", content: "{}" });
  const outside = path.join(os.tmpdir(), "comic-update-outside-" + process.pid);
  fs.writeFileSync(outside, "{}");
  t.after(() => fs.rmSync(outside, { force: true }));
  const target = path.join(root, "web", "public", "payload.json");
  fs.rmSync(target);
  fs.symlinkSync(outside, target);
  const result = inspectUpdateInventory({ resourcesPath: root, appVersion: "1.0.20" });
  assert.equal(result.invalid_file_count, 1);
});
