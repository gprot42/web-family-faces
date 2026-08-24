import assert from "node:assert/strict";
import test from "node:test";
import { CARD_H, CARD_W, layoutFamilyTree, treePersonIdForCatalog, viewOnFocus } from "./familyChart.js";

test("layoutFamilyTree always places the selected person", () => {
  const layout = layoutFamilyTree({
    focus: "I9",
    nodes: [{ id: "I9", name: "Ada Cole" }],
    unions: [],
  });
  assert.equal(layout.nodes.length, 1);
  assert.equal(layout.nodes[0].id, "I9");
  assert.equal(layout.nodes[0].focus, true);
  assert.ok(Number.isFinite(layout.nodes[0].x));
  assert.ok(Number.isFinite(layout.nodes[0].y));
});

test("viewOnFocus keeps the selected person on screen", () => {
  const layout = {
    nodes: [
      { id: "up", x: 200, y: 0, focus: false },
      { id: "me", x: 200, y: 400, focus: true },
      { id: "kid", x: 200, y: 800, focus: false },
    ],
  };
  const view = viewOnFocus(layout, 800, 500);
  const cx = 200 + CARD_W / 2;
  const cy = 400 + CARD_H / 2;
  const screenX = cx * view.zoom + view.x;
  const screenY = cy * view.zoom + view.y;
  assert.ok(screenX > 80 && screenX < 720, `x ${screenX}`);
  assert.ok(screenY > 80 && screenY < 480, `y ${screenY}`);
  assert.ok(view.zoom >= 0.5, `zoom ${view.zoom}`);
});

test("treePersonIdForCatalog prefers a linked catalog id, then a unique name", () => {
  const people = [
    { id: "I1", name: "Ada Cole", catalog_id: 12 },
    { id: "I2", name: "Sam Reed" },
    { id: "I3", name: "Sam Reed" },
  ];
  assert.equal(treePersonIdForCatalog(people, 12), "I1");
  assert.equal(treePersonIdForCatalog(people, 99, "Ada Cole"), "I1");
  assert.equal(treePersonIdForCatalog(people, 99, "Sam Reed"), "");
  assert.equal(treePersonIdForCatalog(people, 0, "Ada Cole"), "");
});
