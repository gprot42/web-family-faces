import assert from "node:assert/strict";
import test from "node:test";
import {
  ALBUM_PAGE,
  ALBUM_PAGE_KEY,
  applyAlbumPage,
  albumHasMore,
  clampAlbumPage,
  nextAlbumOffset,
  readAlbumPage,
} from "./albumPage.js";

function mockStorage() {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
  };
  return store;
}

test("default album page is 500", () => {
  assert.equal(ALBUM_PAGE, 500);
  mockStorage();
  assert.equal(readAlbumPage(), 500);
});

test("clampAlbumPage stays between 50 and 500", () => {
  assert.equal(clampAlbumPage(10), 50);
  assert.equal(clampAlbumPage(500), 500);
  assert.equal(clampAlbumPage(900), 500);
  assert.equal(clampAlbumPage(120), 120);
});

test("applyAlbumPage stores the page size", () => {
  mockStorage();
  assert.equal(applyAlbumPage(200), 200);
  assert.equal(localStorage.getItem(ALBUM_PAGE_KEY), "200");
  assert.equal(readAlbumPage(), 200);
});


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
