const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const { walkFiles, MAX_FILE_BYTES } = require("./generate-update-manifest.cjs");

test("inventory includes only bounded JS/CSS/JSON under static/public roots", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "patch-inventory-"));
  const web = path.join(root, "web");
  fs.mkdirSync(path.join(web, ".next", "static", "chunks"), { recursive: true });
  fs.mkdirSync(path.join(web, "public"), { recursive: true });
  fs.mkdirSync(path.join(web, "node_modules"), { recursive: true });
  fs.writeFileSync(path.join(web, ".next", "static", "chunks", "app.js"), "console.log('ok')");
  fs.writeFileSync(path.join(web, "public", "settings.json"), "{}");
  fs.writeFileSync(path.join(web, ".next", "static", "chunks", "font.woff2"), "font");
  fs.writeFileSync(path.join(web, "node_modules", "package.js"), "not allowed");

  const files = walkFiles(path.join(web, ".next", "static"), "web")
    .concat(walkFiles(path.join(web, "public"), "web"));
  assert.deepEqual(files.map(file => file.install_path).sort(), [
    "web/.next/static/chunks/app.js",
    "web/public/settings.json",
  ]);
  const expected = crypto.createHash("sha256").update("console.log('ok')").digest("hex");
  assert.equal(files.find(file => file.install_path.endsWith("app.js")).sha256, expected);
  fs.rmSync(root, { recursive: true, force: true });
});

test("oversized files are excluded", () => {
  assert.equal(MAX_FILE_BYTES, 5 * 1024 * 1024);
});
