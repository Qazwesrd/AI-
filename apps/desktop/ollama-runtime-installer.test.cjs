"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  INSTALLER_URL,
  REQUIRED_FREE_BYTES,
  OLLAMA_PORTS,
  findRunningOllama,
  isAllowedDownloadUrl,
  isTrustedOllamaSignature,
} = require("./ollama-runtime-installer.cjs");

test("installer source is the fixed official HTTPS URL", () => {
  assert.equal(INSTALLER_URL, "https://ollama.com/download/OllamaSetup.exe");
  assert.equal(isAllowedDownloadUrl(INSTALLER_URL), true);
});

test("download redirects are limited to HTTPS official delivery hosts", () => {
  assert.equal(isAllowedDownloadUrl("https://github.com/ollama/ollama/releases/download/v1/OllamaSetup.exe"), true);
  assert.equal(isAllowedDownloadUrl("https://release-assets.githubusercontent.com/file"), true);
  assert.equal(isAllowedDownloadUrl("http://ollama.com/download/OllamaSetup.exe"), false);
  assert.equal(isAllowedDownloadUrl("https://evil.example/OllamaSetup.exe"), false);
  assert.equal(isAllowedDownloadUrl("https://ollama.com.evil.example/file"), false);
  assert.equal(isAllowedDownloadUrl("https://user:pass@ollama.com/file"), false);
});

test("signature requires both a valid Authenticode status and Ollama Inc organization", () => {
  assert.equal(isTrustedOllamaSignature({ status: "Valid", subject: "CN=Ollama Inc., O=Ollama Inc., L=Toronto" }), true);
  assert.equal(isTrustedOllamaSignature({ status: "Valid", subject: "CN=Ollama Inc., O=Not Ollama Inc." }), false);
  assert.equal(isTrustedOllamaSignature({ status: "HashMismatch", subject: "CN=Ollama Inc., O=Ollama Inc." }), false);
  assert.equal(isTrustedOllamaSignature(null), false);
});

test("runtime install disk reserve is at least five GiB", () => {
  assert.equal(REQUIRED_FREE_BYTES, 5 * 1024 ** 3);
});


test("existing Ollama is detected on either supported local port", async () => {
  assert.deepEqual(OLLAMA_PORTS, [11434, 11435]);
  const checked = [];
  const running = await findRunningOllama({
    request: async (url) => {
      checked.push(url);
      return url.includes(":11435/") ? { models: [{ name: "qwen3:4b" }] } : null;
    },
  });
  assert.deepEqual(checked, [
    "http://127.0.0.1:11434/api/tags",
    "http://127.0.0.1:11435/api/tags",
  ]);
  assert.equal(running.port, 11435);
  assert.deepEqual(running.payload.models, [{ name: "qwen3:4b" }]);
});

test("Ollama detection returns null when neither supported port is running", async () => {
  const running = await findRunningOllama({ request: async () => null });
  assert.equal(running, null);
});
