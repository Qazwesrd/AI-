"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  INSTALLER_URL,
  REQUIRED_FREE_BYTES,
  isAllowedDownloadUrl,
  isTrustedOllamaSignature,
  resolveAppDataDirectory,
  getBundledRuntimePath,
  getBundledModelsPath,
  getBundledRuntimeEnvironment,
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

test("configured app data directory overrides Electron default and supports E drive", () => {
  assert.equal(
    resolveAppDataDirectory("C:\\Users\\Administrator\\AppData\\Roaming\\ai-comic-studio-desktop", {
      AICOMICS_DATA_DIR: "E:\\123\\ai-comic-studio-desktop-Data",
    }),
    "E:\\123\\ai-comic-studio-desktop-Data"
  );
});

test("app-managed runtime path follows the Electron userData directory", () => {
  assert.equal(
    getBundledRuntimePath("E:\\123\\ai-comic-studio-desktop-Data"),
    "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\runtime\\ollama.exe"
  );
});

test("runtime install disk reserve is at least five GiB", () => {
  assert.equal(REQUIRED_FREE_BYTES, 5 * 1024 ** 3);
});

test("bundled runtime uses the sibling E-drive models directory", () => {
  const runtime = "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\runtime\\ollama.exe";
  assert.equal(
    getBundledModelsPath(runtime),
    "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\models"
  );
});

test("bundled runtime is pinned to port 11435 and offline model storage", () => {
  const runtime = "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\runtime\\ollama.exe";
  const env = getBundledRuntimeEnvironment(runtime, { PATH: "test-path" });
  assert.equal(env.OLLAMA_HOST, "127.0.0.1:11435");
  assert.equal(env.OLLAMA_MODELS, "E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\models");
  assert.equal(env.OLLAMA_NO_CLOUD, "1");
  assert.equal(env.PATH, "test-path");
});
