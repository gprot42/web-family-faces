import assert from "node:assert/strict";
import test from "node:test";
import { albumHasMore, nextAlbumOffset } from "./albumPage.js";

test("albumHasMore hides the button after every API row is fetched", () => {
  assert.equal(albumHasMore({ path: "/a", fetched: 28, apiTotal: 28, total: 28 }, [{ id: 1 }]), false);
});

test("albumHasMore keeps paging while the API still has rows", () => {
  assert.equal(albumHasMore({ path: "/a", fetched: 50, apiTotal: 438, total: 410 }, new Array(36)), true);
});

test("albumHasMore falls back to shown vs catalog total", () => {
  assert.equal(albumHasMore({ path: "/a", total: 28 }, new Array(14)), true);
  assert.equal(albumHasMore({ path: "/a", total: 14 }, new Array(14)), false);
});

test("nextAlbumOffset uses fetched API rows, not unique copies", () => {
  assert.equal(nextAlbumOffset({ fetched: 50, items: new Array(36) }), 50);
  assert.equal(nextAlbumOffset({ items: new Array(36) }), 36);
});
