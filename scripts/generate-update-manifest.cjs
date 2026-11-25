const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const root = path.resolve(__dirname, "..");
const webRoot = path.join(root, "apps", "web");
const desktopRoot = path.join(root, "apps", "desktop");
const output = path.join(desktopRoot, "build-resources", "update-manifest.json");
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set([".js", ".css", ".json"]);

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function walkFiles(directory, prefix, results = [], baseRoot = webRoot) {
  if (!fs.existsSync(directory)) return results;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      walkFiles(absolute, prefix, results, baseRoot);
      continue;
    }
    if (!entry.isFile() || !ALLOWED_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) continue;
    const stat = fs.statSync(absolute);
    if (stat.size < 1 || stat.size > MAX_FILE_BYTES) continue;
    const relative = path.relative(baseRoot, absolute).split(path.sep).join("/");
    if (!(relative.startsWith(".next/static/") || relative.startsWith("public/"))) continue;
    const content = fs.readFileSync(absolute);
    results.push({
      source: relative,
      install_path: "web/" + relative,
      size_bytes: content.length,
      sha256: sha256(content),
      update_class: "web-static-text",
    });
  }
  return results;
}

function generateManifest() {
  const desktopPackage = JSON.parse(fs.readFileSync(path.join(desktopRoot, "package.json"), "utf8"));
  const files = [
    ...walkFiles(path.join(webRoot, ".next", "static"), "web"),
    ...walkFiles(path.join(webRoot, "public"), "web"),
  ].sort((a, b) => a.install_path.localeCompare(b.install_path));

  const manifest = {
    schema_version: 1,
    product: "AI漫剧制作大师",
    product_version: desktopPackage.version,
    generated_at: new Date().toISOString(),
    install_root: "process.resourcesPath",
    purpose: "inventory-only; does not authorize or perform file replacement",
    limits: {
      max_file_bytes: MAX_FILE_BYTES,
      allowed_extensions: [...ALLOWED_EXTENSIONS],
      allowed_roots: ["web/.next/static/", "web/public/"],
    },
    explicitly_excluded: [
      "api/ai-comic-api.exe",
      "app.asar and Electron main/preload code",
      "web/server.js",
      "web/node_modules",
      "media and FFmpeg executables",
      "AI models, ComfyUI, Ollama, Python runtimes and other engines",
      "user data, projects, generated media and local databases",
    ],
    file_count: files.length,
    files,
  };

  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(manifest, null, 2) + "\n", { flag: "w" });
  return manifest;
}

if (require.main === module) {
  const manifest = generateManifest();
  if (manifest.file_count === 0) {
    throw new Error("No eligible web static text files found; refusing to create an empty update inventory.");
  }
  console.log(`Generated update inventory: ${manifest.file_count} files -> ${output}`);
}

module.exports = { generateManifest, walkFiles, MAX_FILE_BYTES };
