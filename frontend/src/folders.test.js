import assert from "node:assert/strict";
import test from "node:test";
import {
  folderAnchor,
  folderBreadcrumb,
  folderDisplayName,
  folderMatchesQuery,
  folderShortName,
  folderYear,
  folderSelectionState,
  FOLDER_TITLES_KEY,
  isFolderStarred,
  photoAlbumName,
  photosFolderHref,
  readFolderTitles,
  readStarredFolders,
  setFolderTitle,
  STARRED_FOLDERS_KEY,
  toggleFolderTick,
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

test("folderYear is the taken year in a leading date, not a scan-batch suffix", () => {
  assert.equal(folderYear("2016 - Bali"), "2016");
  assert.equal(folderYear("Evans_Scanned_Photos_2016"), "");
  assert.equal(folderYear("Evans_Scanned_Photos_2022_Apr"), "");
  assert.equal(folderShortName("2016 - Bali", "2016"), "Bali");
  assert.equal(folderShortName("Evans_Scanned_Photos_2016", ""), "Evans_Scanned_Photos_2016");
});

test("folderMatchesQuery finds a folder by path or name", () => {
  const path = "/Volumes/media_shared_photos/Photo_Collection_Darren_Evans/Evans_Scanned_Photos_2016";
  assert.equal(folderMatchesQuery("Evans_Scanned_Photos_2016", "Evans_Scanned_Photos_2016", path), true);
  assert.equal(folderMatchesQuery(path, "Evans_Scanned_Photos_2016", path), true);
  assert.equal(folderMatchesQuery("not-this-album", "Evans_Scanned_Photos_2016", path), false);
});

test("folderAnchor matches Folder View hashes for names with spaces", () => {
  assert.equal(folderAnchor("Mums iCloud Photos"), "folder-Mums20iCloud20Photos");
  assert.equal(photosFolderHref("Mums iCloud Photos"), "/photos#folder-Mums20iCloud20Photos");
  assert.equal(photosFolderHref(""), "/photos");
});

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

const ROOT = "/Volumes/photos/Photo_Collection_Darren_Evans";
const ALBUM_A = `${ROOT}/2001 - Vodka`;
const ALBUM_B = `${ROOT}/Old Documents`;

test("a selected parent includes every album inside it", () => {
  assert.equal(folderSelectionState(ROOT, [ROOT]).state, "all");
  assert.equal(folderSelectionState(ALBUM_A, [ROOT]).state, "all");
  assert.equal(folderSelectionState(ALBUM_B, [ROOT]).state, "all");
});

test("untick an album under a selected parent to skip only that album", () => {
  const skipped = toggleFolderTick(ALBUM_B, [ROOT], []);
  assert.deepEqual(skipped.picked, [ROOT]);
  assert.deepEqual(skipped.excluded, [ALBUM_B]);
  assert.equal(folderSelectionState(ROOT, skipped.picked, skipped.excluded).state, "partial");
  assert.equal(folderSelectionState(ALBUM_A, skipped.picked, skipped.excluded).state, "all");
  assert.equal(folderSelectionState(ALBUM_B, skipped.picked, skipped.excluded).state, "none");
});

test("tick a skipped album to include it again", () => {
  const skipped = toggleFolderTick(ALBUM_B, [ROOT], []);
  const back = toggleFolderTick(ALBUM_B, skipped.picked, skipped.excluded);
  assert.deepEqual(back.picked, [ROOT]);
  assert.deepEqual(back.excluded, []);
  assert.equal(folderSelectionState(ROOT, back.picked, back.excluded).state, "all");
  assert.equal(folderSelectionState(ALBUM_B, back.picked, back.excluded).state, "all");
});

test("untick a fully selected parent drops the whole collection", () => {
  const next = toggleFolderTick(ROOT, [ROOT], []);
  assert.deepEqual(next.picked, []);
  assert.deepEqual(next.excluded, []);
});

test("tick a parent with skipped albums includes every album again", () => {
  assert.equal(folderSelectionState(ROOT, [ROOT], [ALBUM_B]).state, "partial");
  const included = toggleFolderTick(ROOT, [ROOT], [ALBUM_B]);
  assert.deepEqual(included.picked, [ROOT]);
  assert.deepEqual(included.excluded, []);
});
