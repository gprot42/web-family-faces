import assert from "node:assert/strict";
import test from "node:test";
import { completeUniqueFirstName, matchPeople, uniqueCatalogPerson, uniqueFirstName } from "./nameSuggest.js";

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

test("uniqueCatalogPerson uses a unique full-name prefix", () => {
  const many = [
    { id: 1, name: "Margaret Ann Cole" },
    { id: 2, name: "Margaret Price" },
    { id: 3, name: "Margaret Robin" },
    { id: 4, name: "Margaret Shaw" },
  ];
  assert.equal(uniqueFirstName("margaret", many), null);
  assert.equal(uniqueCatalogPerson("margaret", many), null);
  assert.equal(uniqueCatalogPerson("margaret ann", many)?.name, "Margaret Ann Cole");
  assert.equal(uniqueCatalogPerson("Margaret Ann Cole", many)?.name, "Margaret Ann Cole");
  assert.equal(uniqueCatalogPerson("margaret p", many)?.name, "Margaret Price");
});

test("uniqueCatalogPerson stays quiet when two people share a longer prefix", () => {
  const two = [
    { id: 1, name: "Margaret Ann Cole" },
    { id: 2, name: "Margaret Ann Smith" },
  ];
  assert.equal(uniqueCatalogPerson("margaret ann", two), null);
  assert.equal(uniqueCatalogPerson("margaret ann c", two)?.name, "Margaret Ann Cole");
});

test("uniqueCatalogPerson uses a unique catalog match", () => {
  assert.equal(uniqueCatalogPerson("jordy", people)?.name, "Jordan Cole");
});
