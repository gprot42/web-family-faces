import assert from "node:assert/strict";
import test from "node:test";
import { CARD_H, CARD_W, ROW, layoutFamilyTree, treePersonIdForCatalog, viewOnFocus } from "./familyChart.js";

test("layoutEntireTree places every generation of the file", () => {
  const layout = layoutFamilyTree({
    scope: "all",
    focus: "I2",
    nodes: [
      { id: "I1", name: "Dad", generation: -1, sex: "M" },
      { id: "I4", name: "Mom", generation: -1, sex: "F" },
      { id: "I2", name: "Me", generation: 0, sex: "F" },
      { id: "I3", name: "Kid", generation: 1, sex: "M" },
    ],
    unions: [
      { id: "F1", role: "parents", generation: -1, partners: ["I1", "I4"], children: ["I2"] },
      { id: "F2", role: "own", generation: 0, partners: ["I2"], children: ["I3"] },
    ],
  });
  assert.equal(layout.nodes.length, 4);
  const y = Object.fromEntries(layout.nodes.map((n) => [n.id, n.y]));
  assert.equal(y.I1, y.I4);
  assert.ok(y.I1 < y.I2, `parents ${y.I1} me ${y.I2}`);
  assert.ok(y.I2 < y.I3, `me ${y.I2} kid ${y.I3}`);
  assert.equal(layout.nodes.find((n) => n.id === "I2").focus, true);
  const x = Object.fromEntries(layout.nodes.map((n) => [n.id, n.x]));
  assert.ok(Math.abs(x.I1 - x.I4) < CARD_W + 40, `spouses ${x.I1} ${x.I4}`);
  const parentMid = (x.I1 + x.I4) / 2;
  assert.ok(Math.abs(x.I2 - parentMid) < CARD_W, `child ${x.I2} under ${parentMid}`);
  const row = layout.nodes.filter((n) => n.y === y.I2).sort((a, b) => a.x - b.x);
  for (let i = 1; i < row.length; i += 1) {
    assert.ok(row[i].x >= row[i - 1].x + CARD_W, `overlap ${row[i - 1].id} ${row[i].id}`);
  }
});

test("entire tree sibling cards do not stack on the same spot", () => {
  const layout = layoutFamilyTree({
    scope: "all",
    focus: "P",
    nodes: [
      { id: "P", name: "Parent", generation: 0, sex: "M" },
      { id: "A", name: "Ann", generation: 1, sex: "F" },
      { id: "B", name: "Bob", generation: 1, sex: "M" },
      { id: "C", name: "Cat", generation: 1, sex: "F" },
      { id: "S", name: "Spouse", generation: 1, sex: "M" },
    ],
    unions: [
      { id: "F1", generation: 0, partners: ["P"], children: ["A", "B", "C"] },
      { id: "F2", generation: 1, partners: ["A", "S"], children: [] },
    ],
  });
  const row = layout.nodes.filter((n) => n.id !== "P").sort((a, b) => a.x - b.x);
  assert.equal(row.length, 4);
  for (let i = 1; i < row.length; i += 1) {
    assert.ok(
      row[i].x >= row[i - 1].x + CARD_W,
      `overlap ${row[i - 1].id}@${row[i - 1].x} ${row[i].id}@${row[i].x}`,
    );
  }
});

test("cousin children stay under their own parents on the entire tree", () => {
  const layout = layoutFamilyTree({
    scope: "all",
    focus: "GP",
    nodes: [
      { id: "GP", name: "GP", generation: 0 },
      { id: "P1", name: "Ann", generation: 1 },
      { id: "S1", name: "Andrew", generation: 1 },
      { id: "P2", name: "Margaret", generation: 1 },
      { id: "S2", name: "Roland", generation: 1 },
      { id: "C1", name: "Rachael", generation: 2 },
      { id: "C2a", name: "Darren", generation: 2 },
      { id: "C2b", name: "Jonathan", generation: 2 },
      { id: "C2c", name: "Alexandre", generation: 2 },
    ],
    unions: [
      { id: "F0", partners: ["GP"], children: ["P1", "P2"] },
      { id: "F1", partners: ["P1", "S1"], children: ["C1"] },
      { id: "F2", partners: ["P2", "S2"], children: ["C2a", "C2b", "C2c"] },
    ],
  });
  const x = Object.fromEntries(layout.nodes.map((n) => [n.id, n.x]));
  const y = Object.fromEntries(layout.nodes.map((n) => [n.id, n.y]));
  assert.equal(y.C1, y.C2a);
  const c2 = ["C2a", "C2b", "C2c"].map((id) => x[id]).sort((a, b) => a - b);
  const c2Lo = c2[0];
  const c2Hi = c2[2] + CARD_W;
  assert.ok(c2[1] >= c2[0] + CARD_W, "Evans siblings overlap");
  assert.ok(c2[2] >= c2[1] + CARD_W, "Evans siblings overlap");
  assert.ok(x.C1 + CARD_W <= c2Lo || x.C1 >= c2Hi, `Rachael ${x.C1} inside Evans ${c2Lo}-${c2Hi}`);
  const p2Mid = (Math.min(x.P2, x.S2) + Math.max(x.P2, x.S2) + CARD_W) / 2;
  const c2Mid = (c2Lo + c2Hi) / 2;
  assert.ok(Math.abs(p2Mid - c2Mid) < CARD_W, `Evans kids ${c2Mid} not under Margaret/Roland ${p2Mid}`);
  const p1Mid = (Math.min(x.P1, x.S1) + Math.max(x.P1, x.S1) + CARD_W) / 2;
  assert.ok(Math.abs(p1Mid - (x.C1 + CARD_W / 2)) < CARD_W, `Rachael not under Ann/Andrew`);
  const c1cx = x.C1 + CARD_W / 2;
  const c2cx = ["C2a", "C2b", "C2c"].map((id) => x[id] + CARD_W / 2);
  const barY = y.C1 - 26;
  const mixed = layout.edges.filter((edge) => {
    if (edge.type !== "stem" || Math.abs(edge.y1 - edge.y2) > 1) return false;
    if (Math.abs(edge.y1 - barY) > 2) return false;
    const lo = Math.min(edge.x1, edge.x2);
    const hi = Math.max(edge.x1, edge.x2);
    return c1cx >= lo - 1 && c1cx <= hi + 1 && c2cx.some((cx) => cx >= lo - 1 && cx <= hi + 1);
  });
  assert.equal(mixed.length, 0, "one child-bar must not cover both families");
});

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
      { id: "me", x: 200, y: ROW, focus: true },
      { id: "kid", x: 200, y: 2 * ROW, focus: false },
    ],
  };
  const view = viewOnFocus(layout, 800, 500);
  const cx = 200 + CARD_W / 2;
  const cy = ROW + CARD_H / 2;
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

test("squeezeEmptyBands closes wide empty bands but keeps order and overlaps", async () => {
  const { squeezeEmptyBands } = await import("./familyChart.js");
  const pos = {
    a: { x: 0, y: 0 },
    b: { x: 5000, y: 0 },
    c: { x: 5100, y: 260 },
    d: { x: 20000, y: 520 },
  };
  squeezeEmptyBands(pos, 100);
  assert.equal(pos.a.x, 0);
  assert.equal(pos.b.x, CARD_W + 100, "first gap closed to the max gap");
  assert.equal(pos.c.x - pos.b.x, 100, "overlapping band keeps its internal spacing");
  assert.equal(pos.d.x, pos.c.x + CARD_W + 100, "second gap closed");
  assert.ok(pos.a.x < pos.b.x && pos.b.x < pos.c.x && pos.c.x < pos.d.x);
});


test("layoutFamilyTree places brothers and sisters beside the focus person", () => {
  const chart = {
    focus: "me",
    nodes: [
      { id: "dad", name: "Dad" },
      { id: "mum", name: "Mum" },
      { id: "big", name: "Older" },
      { id: "me", name: "Me" },
      { id: "wee", name: "Younger" },
      { id: "wife", name: "Wife" },
    ],
    unions: [
      { id: "F1", role: "parents", generation: 1, partners: ["dad", "mum"], children: ["big", "me", "wee"] },
      { id: "F2", role: "own", partners: ["me", "wife"], children: [] },
    ],
  };
  const layout = layoutFamilyTree(chart);
  const byId = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
  assert.equal(byId.big.y, byId.me.y, "older sibling on the focus row");
  assert.equal(byId.wee.y, byId.me.y, "younger sibling on the focus row");
  assert.ok(byId.big.x < byId.me.x, "older sibling to the left");
  assert.ok(byId.wife.x > byId.me.x && byId.wee.x > byId.wife.x, "younger sibling after the spouse");
  const row = layout.nodes.filter((n) => n.y === byId.me.y).sort((a, b) => a.x - b.x);
  for (let i = 1; i < row.length; i += 1) {
    assert.ok(row[i].x >= row[i - 1].x + CARD_W, `overlap ${row[i - 1].id} ${row[i].id}`);
  }
  const arrows = layout.edges.filter((e) => e.type === "arrow" && e.y2 < byId.me.y);
  assert.equal(arrows.length, 3, "one descent arrow per child");
});

test("layoutDoubleAncestorChart fans the father right and the mother left", async () => {
  const { layoutDoubleAncestorChart, DCARD_W, DCARD_H } = await import("./familyChart.js");
  const chart = {
    focus: "me",
    nodes: [
      { id: "me", name: "Me", sex: "M" },
      { id: "sis", name: "Sister", sex: "F" },
      { id: "dad", name: "Dad", sex: "M" },
      { id: "mum", name: "Mum", sex: "F" },
      { id: "gf", name: "Grandad", sex: "M" },
      { id: "gm", name: "Granny", sex: "F" },
      { id: "mgm", name: "Nana", sex: "F" },
    ],
    unions: [
      { id: "F1", role: "parents", generation: 1, partners: ["dad", "mum"], children: ["sis", "me"], marriage: { year: 1970 } },
      { id: "F2", role: "grandparents", generation: 2, partners: ["gf", "gm"], children: ["dad"] },
      { id: "F3", role: "grandparents", generation: 2, partners: ["mgm"], children: ["mum"] },
    ],
  };
  const layout = layoutDoubleAncestorChart(chart);
  const byId = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
  assert.ok(byId.dad.x > byId.me.x && byId.gf.x > byId.dad.x, "father's line goes right");
  assert.ok(byId.mum.x < byId.me.x && byId.mgm.x < byId.mum.x, "mother's line goes left");
  assert.equal(byId.sis.x, byId.me.x, "sister in the middle column");
  assert.ok(byId.sis.y < byId.me.y, "older sister above");
  assert.ok(byId.gf.y < byId.dad.y + DCARD_H / 2 && byId.gm.y > byId.dad.y - DCARD_H / 2, "father between his parents");
  assert.ok(Math.abs(byId.gm.y - byId.gf.y) >= DCARD_H, "grandparents do not overlap");
  assert.equal(byId.me.w, DCARD_W);
  assert.ok(layout.edges.length >= 5, "connectors drawn");
  const wed = layout.edges.find((e) => e.type === "marriage");
  assert.equal(wed?.label, "married 1970", "parents shown as a couple");
  assert.ok(layout.nodes.every((n) => n.x >= 0 && n.y >= 0));
});
