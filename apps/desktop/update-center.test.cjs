"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  DEFAULT_UPDATE_PORT, RELEASES_API, resolveUpdatePort,
  isSafeModelName, compareVersions,
} = require("./update-center.cjs");

test("update center uses a dedicated loopback port by default", () => {
  assert.equal(DEFAULT_UPDATE_PORT, 17890);
  assert.equal(resolveUpdatePort(), 17890);
  assert.throws(() => resolveUpdatePort("80"));
  assert.throws(() => resolveUpdatePort("not-a-port"));
});

test("software update source is the project's GitHub Releases endpoint", () => {
  assert.equal(RELEASES_API, "https://api.github.com/repos/Qazwesrd/AI-/releases/latest");
});

test("only valid existing Ollama model identifiers are accepted", () => {
  assert.equal(isSafeModelName("qwen3:4b"), true);
  assert.equal(isSafeModelName("llama3.2:latest"), true);
  assert.equal(isSafeModelName("../secret"), false);
  assert.equal(isSafeModelName("model name"), false);
  assert.equal(isSafeModelName(""), false);
});

test("version comparison detects newer releases", () => {
  assert.equal(compareVersions("1.0.22", "1.0.21"), 1);
  assert.equal(compareVersions("v1.0.21", "1.0.21"), 0);
  assert.equal(compareVersions("1.0.20", "1.0.21"), -1);
});
