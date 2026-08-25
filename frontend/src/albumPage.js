export const ALBUM_PAGE = 50;

export function albumHasMore(meta, photos) {
  if (!meta?.path || !(photos || []).length) return false;
  const fetched = Number(meta.fetched);
  const apiTotal = Number(meta.apiTotal);
  if (Number.isFinite(fetched) && Number.isFinite(apiTotal)) {
    return fetched < apiTotal;
  }
  return (Number(meta.total) || 0) > photos.length;
}

export function nextAlbumOffset(album) {
  const fetched = Number(album?.fetched);
  if (Number.isFinite(fetched) && fetched >= 0) return fetched;
  return (album?.items || []).length;
}
