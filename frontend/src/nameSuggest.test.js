import assert from "node:assert/strict";
import test from "node:test";
import { completeUniqueFirstName, matchPeople, uniqueFirstName } from "./nameSuggest.js";

const people = [
  { id: 1, name: "Lila Cole" },
  { id: 2, name: "Adam Cole" },
  { id: 3, name: "Jordan Cole", nickname: "Jordy" },
];

test("matchPeople finds a first name prefix", () => {
  const hits = matchPeople("lil", people);
  assert.deepEqual(hits.map((p) => p.name), ["Lila Cole"]);
});

test("matchPeople uses a nickname without matching every nicknamed person", () => {
  const hits = matchPeople("jordy", people);
  assert.deepEqual(hits.map((p) => p.name), ["Jordan Cole"]);
});

test("uniqueFirstName fills a unique first name", () => {
  assert.equal(uniqueFirstName("lila", people)?.name, "Lila Cole");
  assert.equal(uniqueFirstName("lil", people)?.name, "Lila Cole");
  assert.equal(completeUniqueFirstName("lila", people)?.name, "Lila Cole");
  assert.equal(completeUniqueFirstName("lil", people), null);
});

test("uniqueFirstName stays quiet when two people share a prefix", () => {
  const two = [...people, { id: 4, name: "Lily Cruz" }];
  assert.equal(uniqueFirstName("lil", two), null);
  assert.equal(uniqueFirstName("lila", two)?.name, "Lila Cole");
});
