import assert from "node:assert/strict";
import test from "node:test";
import { estimateEta, formatEta } from "./jobEta.js";

test("formatEta uses minutes for work that is not nearly done", () => {
  assert.equal(formatEta(20), "less than a minute left");
  assert.equal(formatEta(180), "about 3 minutes left");
});

test("estimateEta ignores a huge progress jump so a mid-library walk is not 'less than a minute'", () => {
  const job = { progress: 11279, total: 24233 };
  const eta = estimateEta(job, [
    { t: 0, p: 1 },
    { t: 8000, p: 11279 },
  ]);
  assert.equal(eta, "");
});

test("estimateEta uses a steady recent rate", () => {
  const job = { progress: 200, total: 1200 };
  const eta = estimateEta(job, [
    { t: 0, p: 100 },
    { t: 20000, p: 200 },
  ]);
  assert.match(eta, /about 3 minutes left/);
});
