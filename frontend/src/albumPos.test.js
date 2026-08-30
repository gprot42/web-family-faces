import assert from "node:assert/strict";
import test from "node:test";
import {
  clusterHash,
  clusterIdFrom,
  folderHashFrom,
  personIdFrom,
  personShotHash,
  readAlbumPos,
  writeAlbumPos,
} from "./albumPos.js";

test("writeAlbumPos keeps scrollY when later updates omit it", () => {
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
  };
  writeAlbumPos({ hash: "folder-Mums20iCloud20Photos", photoId: 26785, scrollY: 2400 });
  writeAlbumPos({ hash: "folder-Mums20iCloud20Photos", photoId: 26785, count: 455 });
  const pos = readAlbumPos();
  assert.equal(pos.scrollY, 2400);
  assert.equal(pos.photoId, 26785);
  assert.equal(pos.count, 455);
});

test("folderHashFrom reads the album hash from a photos back link", () => {
  assert.equal(folderHashFrom("/photos#folder-202520-20Mexico"), "folder-202520-20Mexico");
  assert.equal(folderHashFrom("/photos#folder-Mums20iCloud20Photos"), "folder-Mums20iCloud20Photos");
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

test("clusterHash names a Clusters to name cluster", () => {
  assert.equal(clusterHash(52), "cluster-52");
  assert.equal(clusterHash(""), "");
});

test("clusterIdFrom reads a Clusters to name back link", () => {
  assert.equal(clusterIdFrom("/to-name#cluster-52"), 52);
  assert.equal(clusterIdFrom("#cluster-52"), 52);
  assert.equal(clusterIdFrom("cluster-52"), 52);
  assert.equal(clusterIdFrom("/to-name"), 0);
  assert.equal(clusterIdFrom(""), 0);
  assert.equal(clusterIdFrom(null), 0);
});
