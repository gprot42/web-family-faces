export function tip(text) {
  if (!text) return {};
  return { "data-tip": String(text) };
}
