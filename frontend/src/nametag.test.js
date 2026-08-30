import assert from "node:assert/strict";
import test from "node:test";
import {
  LABEL_LAYOUTS,
  LABEL_SIZES,
  LABEL_STYLES,
  applyLabelLayout,
  applyLabelSize,
  applyLabelStyle,
  cycleLabelLayout,
  cycleLabelSize,
  cycleLabelStyle,
  labelLayoutInfo,
  readLabelLayout,
  readLabelSize,
  readLabelStyle,
} from "./nametag.js";

function mockDom() {
  const mem = {};
  globalThis.Event = class {
    constructor(type) {
      this.type = type;
    }
  };
  globalThis.window = { dispatchEvent() {} };
  globalThis.document = { documentElement: { setAttribute() {} } };
  globalThis.localStorage = {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => {
      mem[k] = String(v);
    },
    removeItem: (k) => {
      delete mem[k];
    },
  };
}

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

test("cycleLabelSize walks Small → Medium → Large → Small", () => {
  mockDom();
  applyLabelSize("small");
  assert.equal(readLabelSize(), "small");
  assert.equal(cycleLabelSize(), "medium");
  assert.equal(cycleLabelSize(), "large");
  assert.equal(cycleLabelSize(), "small");
  assert.equal(LABEL_SIZES.length, 3);
});

test("cycleLabelStyle walks Pill → Outline → Shadow → Pill", () => {
  mockDom();
  applyLabelStyle("pill");
  assert.equal(readLabelStyle(), "pill");
  assert.equal(cycleLabelStyle(), "outline");
  assert.equal(cycleLabelStyle(), "shadow");
  assert.equal(cycleLabelStyle(), "pill");
  assert.equal(LABEL_STYLES.length, 3);
});
