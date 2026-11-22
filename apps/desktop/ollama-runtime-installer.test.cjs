"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  INSTALLER_URL,
  REQUIRED_FREE_BYTES,
  getApiUrl,
  isAllowedDownloadUrl,
  isTrustedOllamaSignature,
} = require("./ollama-runtime-installer.cjs");

test("runtime API uses the app-managed local Ollama endpoint when configured", () => {
  const previous = process.env.OLLAMA_BASE_URL;
  process.env.OLLAMA_BASE_URL = "http://127.0.0.1:11435/";
  assert.equal(getApiUrl(), "http://127.0.0.1:11435/api/tags");
  if (previous === undefined) delete process.env.OLLAMA_BASE_URL;
  else process.env.OLLAMA_BASE_URL = previous;
});

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
