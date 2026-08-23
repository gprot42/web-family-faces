import assert from "node:assert/strict";
import test from "node:test";
import {
  LABEL_LAYOUTS,
  applyLabelLayout,
  cycleLabelLayout,
  labelLayoutInfo,
  readLabelLayout,
} from "./nametag.js";

test("cycleLabelLayout walks Smart → Rows → Halo → Numbers → Smart", () => {
  const mem = {};
  globalThis.Event = class {
    constructor(type) {
      this.type = type;
    }
  };
  globalThis.window = { dispatchEvent() {} };
  globalThis.localStorage = {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => {
      mem[k] = String(v);
    },
    removeItem: (k) => {
      delete mem[k];
    },
  };
  applyLabelLayout("smart");
  assert.equal(readLabelLayout(), "smart");
  assert.equal(cycleLabelLayout(), "rows");
  assert.equal(cycleLabelLayout(), "halo");
  assert.equal(cycleLabelLayout(), "numbers");
  assert.equal(cycleLabelLayout(), "smart");
  assert.equal(labelLayoutInfo("halo").label, "Halo");
  assert.equal(LABEL_LAYOUTS.length, 4);
});
