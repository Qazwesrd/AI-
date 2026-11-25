"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { generateKeys, signPatch } = require("./patch-tools.cjs");
const { verifySignedPatch } = require("../apps/desktop/patch-package-verifier.cjs");

test("offline key generation and signer produce a verifier-compatible package", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "comic-patch-signing-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const keys = generateKeys(path.join(root, "keys"));
  const inputPath = path.join(root, "input.json");
  const outputPath = path.join(root, "output.aicpatch");
  const bytes = Buffer.from("window.patchBuild = 'signed';");
  fs.writeFileSync(inputPath, JSON.stringify({
    target_version: "1.0.20",
    files: [{
      path: "web/.next/static/chunks/app.js",
      content_base64: bytes.toString("base64")
    }]
  }));
  const signed = signPatch(inputPath, keys.privatePath, outputPath);
  const envelope = JSON.parse(fs.readFileSync(outputPath, "utf8"));
  const publicKey = fs.readFileSync(keys.publicPath, "utf8");
  const verified = verifySignedPatch(envelope, publicKey);
  assert.equal(signed.file_count, 1);
  assert.equal(verified.valid, true);
  assert.equal(verified.files[0].content.toString(), bytes.toString());
});
