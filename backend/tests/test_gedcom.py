from photosort import config, db, gedcom
from photosort.db import connect, init_db
from photosort.people import create_person


SAMPLE = b"""0 HEAD
1 SOUR Family Tree Maker
1 GEDC
2 VERS 5.5
0 @I1@ INDI
1 NAME John /Smith/
2 NICK Jack
1 SEX M
1 BIRT
2 DATE 12 MAR 1850
2 PLAC London, England
1 DEAT
2 DATE 4 JAN 1921
1 FAMS @F1@
1 FAMC @F0@
1 NOTE Worked as a printer.
2 CONC  Lived near the river.
0 @I2@ INDI
1 NAME Jane /Doe/
1 SEX F
1 BIRT
2 DATE 1852
1 FAMS @F1@
0 @I3@ INDI
1 NAME Alice /Smith/
1 SEX F
1 BIRT
2 DATE 1874
1 FAMC @F1@
0 @I4@ INDI
1 NAME William /Smith/
1 SEX M
1 FAMS @F0@
1 FAMC @F2@
0 @I5@ INDI
1 NAME Mary /Jones/
1 SEX F
1 FAMS @F0@
0 @I6@ INDI
1 NAME Thomas /Smith/
1 SEX M
1 FAMS @F2@
1 FAMC @F3@
0 @I7@ INDI
1 NAME Ann /Clark/
1 SEX F
1 FAMS @F2@
0 @I8@ INDI
1 NAME Henry /Smith/
1 SEX M
1 FAMS @F3@
0 @I9@ INDI
1 NAME Martha /Green/
1 SEX F
1 FAMS @F3@
0 @F0@ FAM
1 HUSB @I4@
1 WIFE @I5@
1 CHIL @I1@
0 @F2@ FAM
1 HUSB @I6@
1 WIFE @I7@
1 CHIL @I4@
0 @F3@ FAM
1 HUSB @I8@
1 WIFE @I9@
1 CHIL @I6@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1872
2 PLAC Kent
1 CHIL @I3@
0 TRLR
"""


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(config, "GEDCOM_PATH", tmp_path / "family.ged")
    monkeypatch.setattr(config, "GEDCOM_META_PATH", tmp_path / "family.ged.json")
    gedcom._cache = None
    gedcom._cache_stamp = None
    conn = connect()
    init_db(conn)
    conn.close()


def test_parse_gedcom_reads_people_and_families():
    tree = gedcom.parse_gedcom(SAMPLE.decode())
    assert tree["source"] == "Family Tree Maker"
    assert set(tree["people"]) >= {"I1", "I2", "I3", "I4", "I5", "I8", "I9"}
    john = tree["people"]["I1"]
    assert john["name"] == "John Smith"
    assert john["nickname"] == "Jack"
    assert john["surname"] == "Smith"
    assert john["sex"] == "M"
    assert john["birth"]["year"] == 1850
    assert john["birth"]["place"] == "London, England"
    assert john["lifespan"] == "1850–1921"
    assert "printer" in john["note"]
    assert "river" in john["note"]
    fam = tree["families"]["F1"]
    assert fam["husband"] == "I1"
    assert fam["wife"] == "I2"
    assert fam["children"] == ["I3"]
    assert fam["marriage"]["year"] == 1872


def test_person_detail_parents_spouse_children():
    tree = gedcom.parse_gedcom(SAMPLE.decode())
    john = gedcom.person_detail(tree, "I1", catalog={})
    assert [p["name"] for p in john["parents"]] == ["William Smith", "Mary Jones"]
    assert john["spouses"][0]["spouse"]["name"] == "Jane Doe"
    assert john["spouses"][0]["marriage"]["year"] == 1872
    assert [c["name"] for c in john["children"]] == ["Alice Smith"]
    chart = john["chart"]
    assert chart["focus"] == "I1"
    roles = {u["role"] for u in chart["unions"]}
    assert "parents" in roles
    assert "own" in roles
    assert "grandparents" in roles
    assert "ancestors" in roles
    gens = {u.get("generation") for u in chart["unions"] if u.get("generation")}
    assert gens >= {1, 2, 3}
    names = {n["name"] for n in chart["nodes"]}
    assert names >= {
        "John Smith",
        "Jane Doe",
        "Alice Smith",
        "William Smith",
        "Mary Jones",
        "Thomas Smith",
        "Ann Clark",
        "Henry Smith",
        "Martha Green",
    }


def test_save_and_http_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from photosort.main import app

    _setup(tmp_path, monkeypatch)
    create_person("John Smith")
    client = TestClient(app)
    empty = client.get("/api/gedcom").json()
    assert empty["loaded"] is False
    bad = client.post("/api/gedcom", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad.status_code == 400
    uploaded = client.post("/api/gedcom", files={"file": ("family.ged", SAMPLE, "text/plain")}).json()
    assert uploaded["loaded"] is True
    assert uploaded["filename"] == "family.ged"
    assert uploaded["people_count"] == 9
    assert uploaded["families_count"] == 4
    names = {p["name"] for p in uploaded["people"]}
    assert "John Smith" in names
    john = next(p for p in uploaded["people"] if p["name"] == "John Smith")
    assert john["catalog_id"]
    detail = client.get("/api/gedcom/people/I1").json()
    assert detail["name"] == "John Smith"
    wrapped = client.get("/api/gedcom/people/%40I1%40").json()
    assert wrapped["id"] == "I1"
    assert wrapped["chart"]["focus"] == "I1"
    assert wrapped["chart"]["nodes"]
    assert detail["catalog"]["name"] == "John Smith"
    assert detail["parents"][0]["name"] == "William Smith"
    missing = client.get("/api/gedcom/people/NOPE")
    assert missing.status_code == 404
    client.delete("/api/gedcom")
    assert client.get("/api/gedcom").json()["loaded"] is False


def test_entire_chart_includes_the_whole_file():
    tree = gedcom.parse_gedcom(SAMPLE.decode())
    around = gedcom.family_chart(tree, "I3", catalog={})
    entire = gedcom.family_chart(tree, "I3", catalog={}, entire=True)
    assert around.get("scope") != "all"
    assert entire["scope"] == "all"
    assert {n["id"] for n in entire["nodes"]} == set(tree["people"])
    assert len(entire["unions"]) == len(tree["families"])
    alice = next(n for n in entire["nodes"] if n["id"] == "I3")
    assert alice["generation"] == 0
    john = next(n for n in entire["nodes"] if n["id"] == "I1")
    assert john["generation"] == -1
    henry = next(n for n in entire["nodes"] if n["id"] == "I8")
    assert henry["generation"] == -4


def test_family_chart_walks_great_grandparents():
    tree = gedcom.parse_gedcom(SAMPLE.decode())
    chart = gedcom.family_chart(tree, "I1", catalog={})
    by_role = {}
    for union in chart["unions"]:
        by_role.setdefault(union["role"], []).append(union)
    assert by_role["parents"][0]["partners"] == ["I4", "I5"]
    assert by_role["grandparents"][0]["partners"] == ["I6", "I7"]
    assert by_role["ancestors"][0]["partners"] == ["I8", "I9"]
    assert by_role["ancestors"][0]["generation"] == 3


FAM_ONLY = b"""0 HEAD
1 SOUR Grok
0 @I1@ INDI
1 NAME John /DENNIS/
1 SEX M
0 @I2@ INDI
1 NAME Elizabeth //
1 SEX F
0 @I3@ INDI
1 NAME Frederick William /DENNIS/
1 SEX M
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""


def test_parse_gedcom_infers_links_from_fam_records():
    tree = gedcom.parse_gedcom(FAM_ONLY.decode())
    assert tree["people"]["I1"]["fams"] == ["F1"]
    assert tree["people"]["I2"]["fams"] == ["F1"]
    assert tree["people"]["I3"]["famc"] == ["F1"]
    frederick = gedcom.person_detail(tree, "I3", catalog={})
    assert [p["name"] for p in frederick["parents"]] == ["John DENNIS", "Elizabeth"]
    john = gedcom.person_detail(tree, "I1", catalog={})
    assert john["spouses"][0]["spouse"]["name"] == "Elizabeth"
    assert [c["name"] for c in john["children"]] == ["Frederick William DENNIS"]
    chart = john["chart"]
    assert any(u["role"] == "own" and u["children"] == ["I3"] for u in chart["unions"])


def test_rejects_file_with_no_people(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    try:
        gedcom.save_file(b"0 HEAD\n1 SOUR x\n0 TRLR\n", "empty.ged")
    except gedcom.GedcomError as exc:
        assert "no people" in str(exc).lower()
    else:
        raise AssertionError("expected GedcomError")


def test_brief_carries_places_work_and_married_surname():
    ged = b"""0 HEAD
1 SOUR Grok
0 @I1@ INDI
1 NAME Roger /Harold/
1 SEX M
1 BIRT
2 DATE 27 MAR 1938
2 PLAC Atlanta, Georgia
1 OCCU Printer
0 @I2@ INDI
1 NAME Clarissa /Flemming/
1 SEX F
1 BIRT
2 DATE 1941
0 @I3@ INDI
1 NAME Nancy /Harold/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""
    tree = gedcom.parse_gedcom(ged.decode())
    chart = gedcom.family_chart(tree, "I3", catalog={})
    by_id = {n["id"]: n for n in chart["nodes"]}
    assert by_id["I1"]["birth"] == {"date": "27 MAR 1938", "place": "Atlanta, Georgia", "year": 1938}
    assert by_id["I1"]["occupation"] == "Printer"
    assert by_id["I1"]["married_surname"] == ""
    assert by_id["I2"]["married_surname"] == "Harold"
    assert by_id["I3"]["married_surname"] == ""
    assert by_id["I2"]["death"] is None


def test_catalog_hit_fallbacks():
    def idx(*people):
        out = {}
        loose = {}
        everyone = []
        pairs = {}
        for pid, name, birth, *rest in people:
            hit = {"id": pid, "name": name}
            if rest:
                hit["born"] = rest[0]
            words = gedcom._name_words(name)
            out.setdefault(name.lower(), hit)
            out.setdefault(" ".join(words), hit)
            for surname in {words[-1], birth.lower()} - {""}:
                loose.setdefault(f"{words[0]} {surname}", []).append(hit)
            if birth:
                pairs.setdefault((birth.lower(), words[-1]), []).append(hit)
            everyone.append({**hit, "words": words, "surnames": {words[-1], birth.lower()} - {""}})
        for key, hits in loose.items():
            out[gedcom.LOOSE + key] = hits
        out[gedcom.PEOPLE_KEY] = everyone
        out[gedcom.PAIRS_KEY] = pairs
        return out

    index = idx(
        (3, "Darren Evans", ""),
        (33, "Ronald Allen Evans", ""),
        (61, "Alex Carl Evans", ""),
        (34, "Claire Evans", ""),
        (7, "John Evans", ""),
        (8, "John Evans", ""),
        (107, "Doris Evans", ""),
        (53, "Sarah Evans", "", 1966),
        (318, "George Barnes Snr", "", 1891),
        (268, "George Barnes Jnr", "", 1929),
        (279, "Helena Barnes", "Webb", 1976),  # estimate from scanned prints
        (278, "Eleanor Wilkinson", "Barnes", 1982),
    )
    hit = lambda **p: (gedcom._catalog_hit(p, index) or {}).get("id")
    assert hit(name="Darren James Evans", given="Darren James", surname="Evans") == 3
    assert hit(name="Ronald Allan Evans", given="Ronald Allan", surname="Evans") == 33
    assert hit(name="Alexandre Carl Evans", given="Alexandre Carl", surname="Evans") == 61
    assert hit(name="Claire Doris Evans", given="Claire Doris", surname="Evans", married_surname="Griffiths") == 34
    assert hit(name="Nicholas Ronald Evans", given="Nicholas Ronald", surname="Evans") is None
    assert hit(name="Mary Doris Evans", given="Mary Doris", surname="Evans") is None, "middle name alone is not a match"
    assert hit(name="John Peter Evans", given="John Peter", surname="Evans") is None, "two Johns: never guess"
    assert gedcom._catalog_hit({"name": "Anyone"}, {}) is None
    victorian = {"name": "Sarah Jane Evans", "given": "Sarah Jane", "surname": "Evans", "birth": {"year": 1864}}
    assert gedcom._catalog_hit(victorian, index) is None, "born a century apart"
    modern = {**victorian, "birth": {"year": 1970}}
    assert gedcom._catalog_hit(modern, index)["id"] == 53
    undated = {**victorian, "birth": None}
    assert gedcom._catalog_hit(undated, index)["id"] == 53, "no year to compare: keep the name match"
    assert gedcom._catalog_hit({"name": "Mary Evans", "given": "Mary", "surname": "Evans"}, idx((56, "Mark Evans", ""))) is None

    # Several tree people claim one catalog person: the closest birth year wins.
    tree = {
        "people": {
            "I1": {"id": "I1", "name": "Sarah Jane Evans", "given": "Sarah Jane", "surname": "Evans", "birth": {"year": 1920}},
            "I2": {"id": "I2", "name": "Sarah Louise Baum", "given": "Sarah Louise", "surname": "Baum",
                   "married_surname": "Evans", "birth": {"year": 1972}},
            "I3": {"id": "I3", "name": "Sarah Evans", "given": "Sarah", "surname": "Evans", "birth": None},
            "I4": {"id": "I4", "name": "Darren James Evans", "given": "Darren James", "surname": "Evans", "birth": {"year": 1975}},
        }
    }
    links = gedcom._catalog_links(tree, index)
    assert {k: v["id"] for k, v in links.items()} == {"I2": 53, "I4": 3}

    # A suffix in the catalog name, two Georges, and a birth year that decides.
    george = {"name": "George William Barnes", "given": "George William", "surname": "Barnes", "birth": {"year": 1891}}
    assert gedcom._catalog_hit(george, index)["id"] == 318
    george_jr = {**george, "birth": {"year": 1929}}
    assert gedcom._catalog_hit(george_jr, index)["id"] == 268
    undated_george = {**george, "birth": None}
    assert gedcom._catalog_hit(undated_george, index) is None, "two Georges and no year: never guess"

    # Different first names, unique birth + married surname pair: Eleanor Webb is Helena Barnes née Webb.
    tree2 = {
        "people": {
            "E1": {"id": "E1", "name": "Eleanor Webb", "given": "Eleanor", "surname": "Webb",
                   "married_surname": "Barnes", "birth": {"year": 1890}},
            "E2": {"id": "E2", "name": "Ann Webb", "given": "Ann", "surname": "Webb",
                   "married_surname": "Cole", "birth": {"year": 1900}},
        }
    }
    links2 = gedcom._catalog_links(tree2, index)
    assert {k: v["id"] for k, v in links2.items()} == {"E1": 279}


def test_tree_list_carries_catalog_nicknames(tmp_path, monkeypatch):
    from photosort import config, db
    from photosort.db import connect, init_db
    from photosort.people import create_person

    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    init_db(connect())
    create_person("Nicholas Evans", nickname="Nick")
    ged = b"""0 HEAD
1 SOUR Grok
0 @I1@ INDI
1 NAME Nicholas Ronald /Evans/
1 SEX M
0 TRLR
"""
    tree = gedcom.parse_gedcom(ged.decode())
    listed = gedcom.tree_public(tree)["people"]
    assert listed[0]["catalog_id"]
    assert listed[0]["nickname"] == "Nick"
