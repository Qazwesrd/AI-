"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const {
  canonicalize, verifySignedPatch, createRollbackSnapshot, verifyRollbackSnapshot
} = require("./patch-package-verifier.cjs");

function signedEnvelope(pair, { content = "new bundle", targetVersion = "1.0.20" } = {}) {
  const bytes = Buffer.from(content);
  const signed = {
    schema_version: 1,
    product: "AI漫剧制作大师",
    package_id: "1234567890abcdef1234567890abcdef",
    target_version: targetVersion,
    created_at: "2026-11-26T00:00:00.000Z",
    files: [{
      path: "web/.next/static/chunks/app.js",
      size_bytes: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      content_base64: bytes.toString("base64")
    }]
  };
  const signature = crypto.sign(null, Buffer.from(canonicalize(signed)), pair.privateKey).toString("base64");
  return { signed, signature };
}

function keyPair() {
  return crypto.generateKeyPairSync("ed25519");
}

test("fails closed when no release trust anchor is configured", () => {
  const result = verifySignedPatch({ signed: {}, signature: "" }, "");
  assert.equal(result.valid, false);
  assert.equal(result.trusted, false);
  assert.match(result.error, /尚未配置正式发行/);
});

test("verifies a valid Ed25519 patch and all file hashes", () => {
  const pair = keyPair();
  const envelope = signedEnvelope(pair);
  const result = verifySignedPatch(envelope, pair.publicKey.export({ type: "spki", format: "pem" }));
  assert.equal(result.valid, true);
  assert.equal(result.trusted, true);
  assert.equal(result.files.length, 1);
  assert.equal(result.files[0].content.toString(), "new bundle");
});

test("rejects tampered payload and a different signing key", () => {
  const pair = keyPair();
  const other = keyPair();
  const envelope = signedEnvelope(pair);
  envelope.signed.files[0].content_base64 = Buffer.from("tampered").toString("base64");
  const pub = pair.publicKey.export({ type: "spki", format: "pem" });
  assert.equal(verifySignedPatch(envelope, pub).valid, false);
  assert.equal(verifySignedPatch(signedEnvelope(pair), other.publicKey.export({ type: "spki", format: "pem" })).valid, false);
});

test("rejects paths outside static web allowlist and duplicate paths", () => {
  const pair = keyPair();
  const envelope = signedEnvelope(pair);
  envelope.signed.files[0].path = "api/ai-comic-api.exe";
  const pub = pair.publicKey.export({ type: "spki", format: "pem" });
  assert.equal(verifySignedPatch(envelope, pub).valid, false);
  const duplicate = signedEnvelope(pair);
  duplicate.signed.files.push({ ...duplicate.signed.files[0] });
  const resign = crypto.sign(null, Buffer.from(canonicalize(duplicate.signed)), pair.privateKey).toString("base64");
  duplicate.signature = resign;
  assert.equal(verifySignedPatch(duplicate, pub).valid, false);
});

test("creates verified rollback snapshot without changing installed files", (t) => {
  const pair = keyPair();
  const envelope = signedEnvelope(pair);
  const pub = pair.publicKey.export({ type: "spki", format: "pem" });
  const verified = verifySignedPatch(envelope, pub);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "comic-resources-"));
  const backupRoot = fs.mkdtempSync(path.join(os.tmpdir(), "comic-backups-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  t.after(() => fs.rmSync(backupRoot, { recursive: true, force: true }));
  const target = path.join(root, "web", ".next", "static", "chunks", "app.js");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "old bundle");
  const snapshot = createRollbackSnapshot({ resourcesPath: root, backupRoot, verifiedPatch: verified });
  assert.equal(fs.readFileSync(target, "utf8"), "old bundle");
  assert.equal(snapshot.file_count, 1);
  const saved = path.join(backupRoot, snapshot.snapshot_id, ...snapshot.files[0].backup_path.split("/"));
  assert.equal(fs.readFileSync(saved, "utf8"), "old bundle");
  assert.equal(fs.existsSync(path.join(backupRoot, snapshot.snapshot_id, "rollback-manifest.json")), true);
  const checked = verifyRollbackSnapshot({ backupRoot, snapshotId: snapshot.snapshot_id });
  assert.equal(checked.valid, true);
  fs.writeFileSync(saved, "corrupt");
  const corrupted = verifyRollbackSnapshot({ backupRoot, snapshotId: snapshot.snapshot_id });
  assert.equal(corrupted.valid, false);

});
