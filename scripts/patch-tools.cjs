"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function canonicalize(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonicalize(value[key])).join(",") + "}";
  return JSON.stringify(value);
}

function generateKeys(directory) {
  const output = path.resolve(directory);
  fs.mkdirSync(output, { recursive: true });
  const privatePath = path.join(output, "patch-private.pem");
  const publicPath = path.join(output, "patch-public.pem");
  if (fs.existsSync(privatePath) || fs.existsSync(publicPath)) throw new Error("目标目录已存在密钥文件；拒绝覆盖。");
  const pair = crypto.generateKeyPairSync("ed25519", {
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
    publicKeyEncoding: { type: "spki", format: "pem" }
  });
  fs.writeFileSync(privatePath, pair.privateKey, { flag: "wx", mode: 0o600 });
  fs.writeFileSync(publicPath, pair.publicKey, { flag: "wx", mode: 0o644 });
  return { privatePath, publicPath };
}

function signPatch(inputPath, privateKeyPath, outputPath) {
  const input = JSON.parse(fs.readFileSync(path.resolve(inputPath), "utf8"));
  if (!/^\d+\.\d+\.\d+$/.test(String(input.target_version || "")) ||
      !Array.isArray(input.files) || input.files.length < 1 || input.files.length > 500) {
    throw new Error("输入文件必须包含有效 target_version 和 1–500 个 files。");
  }
  const files = input.files.map(file => {
    const content = Buffer.from(String(file.content_base64 || ""), "base64");
    if (!content.length || content.toString("base64") !== file.content_base64) throw new Error("文件内容必须是有效的 Base64。");
    return {
      path: String(file.path || ""),
      size_bytes: content.length,
      sha256: crypto.createHash("sha256").update(content).digest("hex"),
      content_base64: content.toString("base64")
    };
  });
  const signed = {
    schema_version: 1,
    product: "AI漫剧制作大师",
    package_id: crypto.randomUUID().replace(/-/g, ""),
    target_version: input.target_version,
    created_at: new Date().toISOString(),
    files
  };
  const privateKey = crypto.createPrivateKey(fs.readFileSync(path.resolve(privateKeyPath), "utf8"));
  if (privateKey.asymmetricKeyType !== "ed25519") throw new Error("补丁签名私钥必须为 Ed25519。");
  const signature = crypto.sign(null, Buffer.from(canonicalize(signed)), privateKey).toString("base64");
  const envelope = { signed, signature };
  const destination = path.resolve(outputPath);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, JSON.stringify(envelope, null, 2), { flag: "wx" });
  return { package_id: signed.package_id, file_count: files.length, output: destination };
}

function main(argv) {
  const [command, ...args] = argv;
  if (command === "keygen" && args.length === 1) {
    const result = generateKeys(args[0]);
    console.log("Ed25519 patch key pair generated.");
    console.log("PRIVATE KEY (keep offline; never commit/share): " + result.privatePath);
    console.log("PUBLIC KEY (pin in patch verifier): " + result.publicPath);
    return;
  }
  if (command === "sign" && args.length === 3) {
    const result = signPatch(args[0], args[1], args[2]);
    console.log("Signed patch created:", result.package_id, "files:", result.file_count, "output:", result.output);
    return;
  }
  console.log("Usage:");
  console.log("  node scripts/patch-tools.cjs keygen <secure-key-directory>");
  console.log("  node scripts/patch-tools.cjs sign <input.json> <patch-private.pem> <output.aicpatch>");
  process.exitCode = 2;
}

if (require.main === module) main(process.argv.slice(2));
module.exports = { canonicalize, generateKeys, signPatch };
