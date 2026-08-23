import assert from "node:assert/strict";
import test from "node:test";
import { clusterHash, clusterIdFrom, folderHashFrom, personIdFrom, personShotHash } from "./albumPos.js";

test("folderHashFrom reads the album hash from a photos back link", () => {
  assert.equal(folderHashFrom("/photos#folder-202520-20Mexico"), "folder-202520-20Mexico");
});

test("folderHashFrom ignores missing hashes", () => {
  assert.equal(folderHashFrom(""), "");
  assert.equal(folderHashFrom("/photos"), "");
  assert.equal(folderHashFrom(null), "");
});

test("personIdFrom reads a person page back link", () => {
  assert.equal(personIdFrom("/people/3"), 3);
  assert.equal(personIdFrom("/people/3#photo-tile-17481"), 3);
  assert.equal(personIdFrom("/people/3?x=1"), 3);
});

test("personIdFrom ignores the people index and other routes", () => {
  assert.equal(personIdFrom("/people"), 0);
  assert.equal(personIdFrom("/photos/3"), 0);
  assert.equal(personIdFrom(""), 0);
  assert.equal(personIdFrom(null), 0);
});

test("personShotHash names the timeline tile", () => {
  assert.equal(personShotHash(17481), "photo-tile-17481");
  assert.equal(personShotHash(""), "");
});

test("clusterHash names a To name group", () => {
  assert.equal(clusterHash(52), "cluster-52");
  assert.equal(clusterHash(""), "");
});

test("clusterIdFrom reads a To name back link", () => {
  assert.equal(clusterIdFrom("/to-name#cluster-52"), 52);
  assert.equal(clusterIdFrom("#cluster-52"), 52);
  assert.equal(clusterIdFrom("cluster-52"), 52);
  assert.equal(clusterIdFrom("/to-name"), 0);
  assert.equal(clusterIdFrom(""), 0);
  assert.equal(clusterIdFrom(null), 0);
});
