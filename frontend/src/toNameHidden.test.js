import assert from "node:assert/strict";
import test from "node:test";
import { hidePerson, readHiddenPeople, showPerson, writeHiddenPeople } from "./toNameHidden.js";

const mem = new Map();

function mockStorage() {
  mem.clear();
  globalThis.localStorage = {
    getItem: (key) => (mem.has(key) ? mem.get(key) : null),
    setItem: (key, value) => mem.set(key, String(value)),
    removeItem: (key) => mem.delete(key),
  };
}

test("hidePerson keeps a person out of the tagging list", () => {
  mockStorage();
  assert.deepEqual(hidePerson(12), [12]);
  assert.deepEqual(hidePerson(12), [12]);
  assert.deepEqual(hidePerson(7), [12, 7]);
  assert.deepEqual(readHiddenPeople(), [12, 7]);
});

test("showPerson puts a hidden person back", () => {
  mockStorage();
  writeHiddenPeople([12, 7, 3]);
  assert.deepEqual(showPerson(7), [12, 3]);
  assert.deepEqual(showPerson(99), [12, 3]);
});

test("writeHiddenPeople drops junk ids", () => {
  mockStorage();
  assert.deepEqual(writeHiddenPeople(["12", 0, -1, 12, "nope", null]), [12]);
});
