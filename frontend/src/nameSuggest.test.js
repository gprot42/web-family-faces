import assert from "node:assert/strict";
import test from "node:test";
import {
  completeUniqueFirstName,
  matchPeople,
  nameVariants,
  uniqueCatalogPerson,
  uniqueFirstName,
} from "./nameSuggest.js";

test("nameVariants lists married and birth forms", () => {
  assert.deepEqual(nameVariants("Nora Jane Hale", "Pike"), [
    "Nora Jane Hale",
    "Nora Hale",
    "Nora Jane Pike",
    "Nora Pike",
  ]);
  assert.deepEqual(nameVariants("Alan Robinson", ""), ["Alan Robinson"]);
  assert.deepEqual(nameVariants("Alan Robinson", "Robinson"), ["Alan Robinson"]);
});

const people = [
  { id: 1, name: "Lila Cole" },
  { id: 2, name: "Adam Cole" },
  { id: 3, name: "Jordan Cole", nickname: "Jordy" },
  { id: 4, name: "Mara Cole", birth_surname: "Finch" },
  { id: 5, name: "Nora Jane Hale", birth_surname: "Pike" },
];

test("matchPeople finds married and birth forms with or without a middle name", () => {
  for (const q of ["nora hale", "nora jane hale", "nora pike", "nora jane pike"]) {
    assert.deepEqual(matchPeople(q, people).map((p) => p.name), ["Nora Jane Hale"], q);
  }
});

test("matchPeople finds a person by birth surname", () => {
  assert.deepEqual(matchPeople("finch", people).map((p) => p.name), ["Mara Cole"]);
  assert.deepEqual(matchPeople("mara finch", people).map((p) => p.name), ["Mara Cole"]);
});

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
  assert.equal(completeUniqueFirstName("lila cole", people), null);
});

test("uniqueCatalogPerson still matches a unique name with a repeated last token", () => {
  assert.equal(uniqueCatalogPerson("lila cole cole", people)?.name, "Lila Cole");
  assert.equal(uniqueCatalogPerson("Darren Evans Evans", [{ id: 3, name: "Darren Evans" }])?.name, "Darren Evans");
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
