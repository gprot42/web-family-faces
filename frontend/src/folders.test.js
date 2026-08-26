import assert from "node:assert/strict";
import test from "node:test";
import {
  folderBreadcrumb,
  folderDisplayName,
  FOLDER_TITLES_KEY,
  isFolderStarred,
  photoAlbumName,
  readFolderTitles,
  readStarredFolders,
  setFolderTitle,
  STARRED_FOLDERS_KEY,
  toggleStarredFolder,
  writeFolderTitles,
  writeStarredFolders,
} from "./folders.js";

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

test("toggleStarredFolder adds, then removes, a folder path", () => {
  const once = toggleStarredFolder("/Volumes/photos/2018 - Kyoto", []);
  assert.deepEqual(once, ["/Volumes/photos/2018 - Kyoto"]);
  const twice = toggleStarredFolder("/Volumes/photos/2018 - Kyoto/", once);
  assert.deepEqual(twice, []);
});

test("toggleStarredFolder keeps earlier stars in order", () => {
  let next = toggleStarredFolder("/a/Mexico", []);
  next = toggleStarredFolder("/a/Kyoto", next);
  next = toggleStarredFolder("/a/Mexico", next);
  assert.deepEqual(next, ["/a/Kyoto"]);
});

test("isFolderStarred ignores trailing slashes", () => {
  assert.equal(isFolderStarred("/a/Kyoto/", ["/a/Kyoto"]), true);
  assert.equal(isFolderStarred("/a/Mexico", ["/a/Kyoto"]), false);
});

test("starred folders round-trip through localStorage", () => {
  mockStorage();
  writeStarredFolders(["/a/Kyoto/", "/a/Kyoto", "/a/Mexico"]);
  assert.equal(localStorage.getItem(STARRED_FOLDERS_KEY), JSON.stringify(["/a/Kyoto", "/a/Mexico"]));
  assert.deepEqual(readStarredFolders(), ["/a/Kyoto", "/a/Mexico"]);
});

test("photoAlbumName is the folder that contains the file", () => {
  assert.equal(
    photoAlbumName("/Volumes/photos/2025 - London - Darrens 50th/1G8A0283.JPG"),
    "2025 - London - Darrens 50th",
  );
  assert.equal(photoAlbumName("1G8A0283.JPG"), "");
  assert.equal(photoAlbumName(""), "");
});

test("folderBreadcrumb keeps a short name with its parent album", () => {
  assert.equal(
    folderBreadcrumb("/Volumes/photos/Lulu Singing - 1st June 2016/Old"),
    "Lulu Singing - 1st June 2016 / Old",
  );
  assert.equal(folderBreadcrumb("/Volumes/photos/1999 - Pier"), "photos / 1999 - Pier");
  assert.equal(folderBreadcrumb("Old"), "Old");
});

test("folder titles are stored by path and do not rename the disk folder", () => {
  mockStorage();
  let titles = setFolderTitle("/a/2026 - Dubai/", "Dubai trip", {});
  titles = setFolderTitle("/a/2024 - Venice", "Venice carnival", titles);
  writeFolderTitles(titles);
  const stored = JSON.parse(localStorage.getItem(FOLDER_TITLES_KEY));
  assert.equal(stored["/a/2026 - Dubai"], "Dubai trip");
  assert.equal(folderDisplayName("/a/2026 - Dubai/", "2026 - Dubai", readFolderTitles()), "Dubai trip");
  assert.equal(folderDisplayName("/a/Mexico", "Mexico", readFolderTitles()), "Mexico");
  titles = setFolderTitle("/a/2026 - Dubai", "", readFolderTitles());
  writeFolderTitles(titles);
  assert.equal(folderDisplayName("/a/2026 - Dubai", "2026 - Dubai", readFolderTitles()), "2026 - Dubai");
});
