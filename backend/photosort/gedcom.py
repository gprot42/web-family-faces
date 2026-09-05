"""Load and view a GEDCOM (.ged) family tree. Originals are never written."""

from __future__ import annotations

import threading

import json
import re
from pathlib import Path
from typing import Any

from . import config as config_mod
from .util import now_iso

MAX_BYTES = 20 * 1024 * 1024
LINE_RE = re.compile(r"^(\d+)\s+(?:(@[^@]+@)\s+)?([A-Za-z0-9_]+)(?:\s(.*))?$")
NAME_RE = re.compile(r"^(.*?)\s*/([^/]*)/\s*(.*)$")
YEAR_RE = re.compile(r"\b(\d{3,4})\b")

_cache: dict[str, Any] | None = None
_cache_stamp: tuple[Any, ...] | None = None


class GedcomError(ValueError):
    pass


def xref_id(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith("@") and text.endswith("@") and len(text) > 2:
        return text[1:-1]
    return text


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be")
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _child(node: dict[str, Any], tag: str) -> dict[str, Any] | None:
    for item in node.get("children") or []:
        if item.get("tag") == tag:
            return item
    return None


def _children(node: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    return [item for item in (node.get("children") or []) if item.get("tag") == tag]


def _text(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    return str(node.get("value") or "").strip()


def parse_name(value: str) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    match = NAME_RE.match(raw)
    if not match:
        return raw, raw, ""
    given = match.group(1).strip()
    surname = match.group(2).strip()
    suffix = match.group(3).strip()
    display = " ".join(part for part in (given, surname, suffix) if part)
    return display or raw, given, surname


def parse_year(value: str) -> int | None:
    match = YEAR_RE.search(str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    if year < 100 or year > 2100:
        return None
    return year


def _event(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node:
        return None
    date = _text(_child(node, "DATE"))
    place = _text(_child(node, "PLAC"))
    if not date and not place:
        extra = _text(node)
        if extra:
            date = extra
    if not date and not place:
        return None
    return {"date": date, "place": place, "year": parse_year(date)}


def _lifespan(birth: dict[str, Any] | None, death: dict[str, Any] | None) -> str:
    born = (birth or {}).get("year")
    died = (death or {}).get("year")
    if born and died:
        return f"{born}–{died}"
    if born:
        return f"b. {born}"
    if died:
        return f"d. {died}"
    return ""


def parse_gedcom(text: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for raw in str(text or "").splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        match = LINE_RE.match(line)
        if not match:
            continue
        level = int(match.group(1))
        xref = match.group(2)
        tag = match.group(3).upper()
        value = match.group(4) or ""
        if tag in ("CONC", "CONT") and stack:
            parent = stack[-1][1]
            extra = value
            if tag == "CONT":
                parent["value"] = f"{parent.get('value') or ''}\n{extra}"
            else:
                parent["value"] = f"{parent.get('value') or ''}{extra}"
            continue
        node = {"tag": tag, "xref": xref, "value": value, "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        if not stack:
            records.append(node)
        else:
            stack[-1][1]["children"].append(node)
        stack.append((level, node))

    people: dict[str, dict[str, Any]] = {}
    families: dict[str, dict[str, Any]] = {}
    source = ""
    for rec in records:
        if rec["tag"] == "HEAD":
            sour = _child(rec, "SOUR")
            source = _text(sour) or _text(_child(sour, "NAME") if sour else None)
        elif rec["tag"] == "INDI":
            pid = xref_id(rec.get("xref"))
            if not pid:
                continue
            name_node = _child(rec, "NAME")
            display, given, surname = parse_name(_text(name_node))
            if name_node:
                given = _text(_child(name_node, "GIVN")) or given
                surname = _text(_child(name_node, "SURN")) or surname
                if given or surname:
                    display = " ".join(part for part in (given, surname) if part) or display
            sex = _text(_child(rec, "SEX")).upper()[:1]
            birth = _event(_child(rec, "BIRT"))
            death = _event(_child(rec, "DEAT"))
            note = _text(_child(rec, "NOTE"))
            nick = _text(_child(name_node, "NICK")) if name_node else ""
            if not nick:
                nick = _text(_child(rec, "NICK"))
            people[pid] = {
                "id": pid,
                "name": display or pid,
                "nickname": nick,
                "given": given,
                "surname": surname,
                "sex": sex if sex in ("M", "F") else "",
                "birth": birth,
                "death": death,
                "lifespan": _lifespan(birth, death),
                "occupation": _text(_child(rec, "OCCU")),
                "note": note,
                "famc": [xref_id(_text(item)) for item in _children(rec, "FAMC") if xref_id(_text(item))],
                "fams": [xref_id(_text(item)) for item in _children(rec, "FAMS") if xref_id(_text(item))],
            }
        elif rec["tag"] == "FAM":
            fid = xref_id(rec.get("xref"))
            if not fid:
                continue
            families[fid] = {
                "id": fid,
                "husband": xref_id(_text(_child(rec, "HUSB"))),
                "wife": xref_id(_text(_child(rec, "WIFE"))),
                "children": [xref_id(_text(item)) for item in _children(rec, "CHIL") if xref_id(_text(item))],
                "marriage": _event(_child(rec, "MARR")),
            }

    _link_family_pointers(people, families)
    _fill_married_surnames(people, families)
    if not people:
        raise GedcomError("That file has no people in it.")
    return {"source": source, "people": people, "families": families}


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _link_family_pointers(people: dict[str, dict[str, Any]], families: dict[str, dict[str, Any]]) -> None:
    """Fill FAMC/FAMS from FAM records. Some exports only write HUSB/WIFE/CHIL."""
    for fam in families.values():
        fid = fam.get("id") or ""
        if not fid:
            continue
        for pid in (fam.get("husband"), fam.get("wife")):
            person = people.get(pid or "")
            if person:
                _append_unique(person["fams"], fid)
        for cid in fam.get("children") or []:
            person = people.get(cid or "")
            if person:
                _append_unique(person["famc"], fid)


def _brief(person: dict[str, Any] | None) -> dict[str, Any] | None:
    if not person:
        return None
    return {
        "id": person["id"],
        "name": person["name"],
        "nickname": person.get("nickname") or "",
        "sex": person.get("sex") or "",
        "lifespan": person.get("lifespan") or "",
        "birth_year": (person.get("birth") or {}).get("year"),
        "surname": person.get("surname") or "",
        "married_surname": person.get("married_surname") or "",
        "birth": person.get("birth"),
        "death": person.get("death"),
        "occupation": person.get("occupation") or "",
    }


def _fill_married_surnames(people: dict[str, dict[str, Any]], families: dict[str, dict[str, Any]]) -> None:
    """A wife recorded under her birth surname also carries her husband's surname."""
    for fam in families.values():
        husband = people.get(fam.get("husband") or "")
        wife = people.get(fam.get("wife") or "")
        if not husband or not wife:
            continue
        his = (husband.get("surname") or "").strip()
        hers = (wife.get("surname") or "").strip()
        if his and his.casefold() != hers.casefold() and not wife.get("married_surname"):
            wife["married_surname"] = his


def _person_ref(tree: dict[str, Any], pid: str) -> dict[str, Any] | None:
    return _brief((tree.get("people") or {}).get(pid))


_cover_cache: dict[int, int] = {}
_cover_cache_stamp: tuple[Any, ...] | None = None
_cover_lock = threading.Lock()
_cover_refreshing = False


def _catalog_cover_stamp(conn) -> tuple[Any, ...]:
    """Changes whenever a face is added, renamed, junked, or a cover is pinned."""
    faces = conn.execute(
        "SELECT COUNT(*), IFNULL(MAX(id), 0), IFNULL(SUM(person_id), 0), "
        "SUM(CASE WHEN assigned_how = 'junk' THEN 1 ELSE 0 END) FROM faces"
    ).fetchone()
    people = conn.execute("SELECT COUNT(*), IFNULL(SUM(cover_face_id), 0) FROM people").fetchone()
    return tuple(faces) + tuple(people)


def _catalog_covers(conn) -> dict[int, int]:
    """The same cover crop Faces in DB View shows: a pinned cover, else the ranked pick.

    The ranking scans every named face, so it is cached until the catalog changes."""
    global _cover_cache, _cover_cache_stamp, _cover_refreshing
    stamp = _catalog_cover_stamp(conn)
    if _cover_cache_stamp == stamp:
        return _cover_cache
    if _cover_cache_stamp is not None:
        # Naming or junking a face changes the stamp on nearly every click in
        # the rest of the app. Answer with the last map now and refresh it in
        # the background, so picking a tree person never waits for a rescan.
        with _cover_lock:
            if not _cover_refreshing:
                _cover_refreshing = True
                threading.Thread(target=_refresh_covers, args=(stamp,), daemon=True, name="photosort-tree-covers").start()
        return _cover_cache
    # First time (startup warm-up): compute now, one ranking at a time.
    with _cover_lock:
        if _cover_cache_stamp == stamp:
            return _cover_cache
        _cover_cache = _rank_covers()
        _cover_cache_stamp = stamp
        return _cover_cache


def _rank_covers() -> dict[int, int]:
    from . import people as people_mod

    covers: dict[int, int] = {}
    for row in people_mod._list_people_covers_lite():
        face_id = row.get("cover_face_id")
        if face_id:
            covers[int(row["id"])] = int(face_id)
    return covers


def _refresh_covers(stamp: tuple[Any, ...]) -> None:
    global _cover_cache, _cover_cache_stamp, _cover_refreshing
    try:
        covers = _rank_covers()
        with _cover_lock:
            _cover_cache = covers
            _cover_cache_stamp = stamp
    except Exception:
        from . import log as log_mod

        log_mod.exception("tree cover refresh failed")
    finally:
        with _cover_lock:
            _cover_refreshing = False


LOOSE = "~"
PEOPLE_KEY = "*people"
PAIRS_KEY = "*pairs"
_SUFFIXES = {"snr", "jnr", "sr", "jr", "sr.", "jr.", "senior", "junior", "ii", "iii", "iv"}


def _name_words(name: str) -> list[str]:
    """Lower-case words without a generational suffix: "George Barnes Snr" -> george barnes."""
    words = str(name or "").lower().split()
    while len(words) > 1 and words[-1].strip(".,") in _SUFFIXES:
        words.pop()
    return words


def _pick_by_birth(person: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Among same-name catalog people, the one whose birth year clearly fits."""
    hits = [h for h in hits if _plausible(person, h)]
    if len(hits) == 1:
        return hits[0]
    born = (person.get("birth") or {}).get("year")
    if not born or len(hits) < 2:
        return None
    ranked = sorted(
        ((abs(int(born) - int(h["born"])), h) for h in hits if h.get("born")),
        key=lambda item: item[0],
    )
    if not ranked or ranked[0][0] > BORN_TOLERANCE:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 10:
        return None
    return ranked[0][1]
MIN_DATED_FACES = 3
BORN_TOLERANCE = 70


def _plausible(person: dict[str, Any], hit: dict[str, Any] | None) -> bool:
    """A same-name catalog person born about a century apart is someone else.

    The catalog's birth estimate comes from photo dates minus estimated ages,
    and scanned prints carry the scan date, so it runs decades late for older
    relatives. Only a very wide gap is decisive; closer calls are settled by
    competition between tree people in `_catalog_links`."""
    if not hit:
        return False
    born_tree = (person.get("birth") or {}).get("year")
    born_hit = hit.get("born")
    if not born_tree or not born_hit:
        return True
    return abs(int(born_tree) - int(born_hit)) <= BORN_TOLERANCE


def _catalog_index() -> dict[str, dict[str, Any]]:
    """Catalog names to link tree people with photos.

    Exact keys: the full name, its birth-surname forms, and nicknames. Loose
    keys (prefixed "~"): first name plus surname, only when that pair is unique
    in the catalog. A "*people" entry lists everyone for a last token-based pass.
    People with named faces come first so a duplicate with no photos never wins.
    """
    from .db import connect, init_db
    from .people import name_variants

    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.nickname, p.birth_surname, p.birth_year,
                   (SELECT COUNT(*) FROM faces f
                    WHERE f.person_id = p.id AND IFNULL(f.assigned_how, '') != 'junk') AS faces,
                   (SELECT COUNT(*) FROM faces f JOIN photos ph ON ph.id = f.photo_id
                    WHERE f.person_id = p.id AND f.age_est IS NOT NULL AND ph.taken_at IS NOT NULL
                      AND IFNULL(f.assigned_how, '') != 'junk') AS dated,
                   (SELECT AVG(CAST(substr(ph.taken_at, 1, 4) AS INTEGER) - f.age_est)
                    FROM faces f JOIN photos ph ON ph.id = f.photo_id
                    WHERE f.person_id = p.id AND f.age_est IS NOT NULL AND ph.taken_at IS NOT NULL
                      AND IFNULL(f.assigned_how, '') != 'junk') AS born_est
            FROM people p
            WHERE p.name IS NOT NULL AND p.name != ''
            ORDER BY faces DESC, p.id
            """
        ).fetchall()
        covers = _catalog_covers(conn)
    finally:
        conn.close()
    from .serialize import face_crop_url

    out: dict[str, Any] = {}
    loose: dict[str, list[dict[str, Any]]] = {}
    pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    everyone: list[dict[str, Any]] = []
    for row in rows:
        hit = {"id": int(row["id"]), "name": row["name"]}
        catalog_nick = str(row["nickname"] if "nickname" in row.keys() else "") or ""
        if catalog_nick.strip():
            hit["nickname"] = catalog_nick.strip()
        face_id = covers.get(int(row["id"]))
        if face_id:
            hit["cover_url"] = face_crop_url(face_id, 0, 192)
        # When they were born: the saved year, else photo dates minus estimated ages.
        if row["birth_year"]:
            hit["born"] = int(row["birth_year"])
            hit["born_exact"] = True
        elif row["born_est"] is not None and int(row["dated"] or 0) >= MIN_DATED_FACES:
            hit["born"] = int(round(float(row["born_est"])))
        name = str(row["name"] or "").strip()
        birth = row["birth_surname"] if "birth_surname" in row.keys() else ""
        nick = str(row["nickname"] if "nickname" in row.keys() else "") or ""
        exact = [name.lower()]
        exact.extend(v.lower() for v in name_variants(name, birth))
        for part in re.split(r"[,;/]", nick):
            exact.append(part.strip().lower())
        words = _name_words(name)
        exact.append(" ".join(words))
        if len(words) > 2:
            # "darren evans" for "Darren James Evans": exact for the catalog person,
            # loose when a tree name carries a different middle name.
            exact = [k for k in exact if k != f"{words[0]} {words[-1]}"]
        for key in exact:
            if key and key not in out:
                out[key] = hit
        for surname in {words[-1], str(birth or "").lower()} if len(words) > 1 else set():
            if not surname:
                continue
            key = f"{words[0]} {surname}"
            bucket = loose.setdefault(key, [])
            if all(h["id"] != hit["id"] for h in bucket):
                bucket.append(hit)
        if birth and len(words) > 1:
            pairs.setdefault((str(birth).lower(), words[-1]), []).append(hit)
        everyone.append({**hit, "words": words, "surnames": {words[-1], str(birth or "").lower()} - {""}})
    for key, hits in loose.items():
        out[LOOSE + key] = hits
    out[PEOPLE_KEY] = everyone
    out[PAIRS_KEY] = pairs
    return out


def _tree_name_forms(person: dict[str, Any]) -> tuple[list[str], list[str], list[str], set[str]]:
    """Exact keys, loose "first surname" keys, and given-name tokens for a tree person."""
    exact: list[str] = []
    for value in (
        person.get("name"),
        person.get("nickname"),
        f"{person.get('given') or ''} {person.get('surname') or ''}".strip(),
    ):
        key = str(value or "").strip().lower()
        if key and key not in exact:
            exact.append(key)
    given = str(person.get("given") or "").lower().split()
    if not given:
        words = str(person.get("name") or "").lower().split()
        given = words[:-1] if len(words) > 1 else words
    surnames = [
        str(person.get("surname") or "").lower(),
        str(person.get("married_surname") or "").lower(),
    ]
    surnames = [x for x in surnames if x]
    for surname in surnames:
        full = " ".join([*given, surname])
        if full and full not in exact:
            exact.append(full)
    loose = [f"{given[0]} {surname}" for surname in surnames] if given else []
    # A married-name form made of one first name plus the husband's surname is
    # no stronger than a loose "first surname" key: "Sarah Unknown" who married
    # an Evans must not outrank "Sarah Louise Baum" who did the same.
    weak = set()
    married = str(person.get("married_surname") or "").lower()
    if married and len(given) == 1:
        weak.add(f"{given[0]} {married}")
    return exact, loose, given, weak


def _first_names_agree(a: str, b: str) -> bool:
    from .people import _edit_distance

    if a.startswith(b) or b.startswith(a):
        return True
    shortest = min(len(a), len(b))
    if shortest >= 8:
        return _edit_distance(a, b) <= 2
    if shortest >= 5:
        return _edit_distance(a, b) <= 1
    return False


def _catalog_hit(person: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    claim = _catalog_claim(person, index)
    return claim[1] if claim else None


def _catalog_claim(person: dict[str, Any], index: dict[str, Any]) -> tuple[int, dict[str, Any], int] | None:
    """(tier, hit, matched given names): tier 0 exact name, 1 first name plus
    surname, 2 token match. More matched given names is stronger evidence."""
    from .people import _token_matches

    exact, loose, given, weak = _tree_name_forms(person)
    for key in exact:
        hit = index.get(key)
        if hit and _plausible(person, hit):
            return (1 if key in weak else 0), hit, max(1, len(given))
    for key in loose:
        hits = index.get(LOOSE + key) or []
        if isinstance(hits, dict):
            hits = [hits]
        hit = _pick_by_birth(person, hits)
        if hit:
            return 1, hit, 1
    # Last pass: same surname, every given name matches a token of the other
    # ("Alexandre Carl" ~ "Alex Carl"), and only one catalog person fits.
    surnames = {
        str(person.get("surname") or "").lower(),
        str(person.get("married_surname") or "").lower(),
    } - {""}
    if not given or not surnames:
        return None
    found: list[dict[str, Any]] = []
    matched = 0
    for cand in index.get(PEOPLE_KEY) or []:
        if not (cand["surnames"] & surnames) or not _plausible(person, cand):
            continue
        theirs = cand["words"][:-1]
        if not theirs:
            continue
        short, long_ = (given, theirs) if len(given) <= len(theirs) else (theirs, given)
        # First names must agree: by prefix ("Alexandre" ~ "Alex") or, for
        # longer names, by a spelling slip ("Alexandre" ~ "Alexander",
        # "Catherine" ~ "Katherine"). Short names never slip: "Mary" is not
        # "Mark". A middle name alone must not pull in "Doris Evans" for
        # "Claire Doris Evans".
        if not _first_names_agree(long_[0], short[0]):
            continue
        rest_long = long_[1:]
        if all(any(_token_matches(w, t) or _token_matches(t, w) for w in rest_long) for t in short[1:]):
            if cand["id"] not in {f["id"] for f in found}:
                found.append(cand)
                matched = len(short)
    if len(found) == 1:
        return 2, {k: v for k, v in found[0].items() if k not in ("words", "surnames")}, matched
    return None


def _catalog_links(tree: dict[str, Any], index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tree id -> catalog hit, one tree person per catalog person.

    When several tree people claim the same catalog person (three Sarahs for
    one "Sarah Evans"), keep the one born closest to the catalog estimate,
    then the tighter name tier. The rest stay unlinked rather than wrong."""
    people = tree.get("people") or {}
    # Birth surname plus married surname, counted on the tree side. A pair that
    # is unique in both the tree and the catalog identifies a woman even when
    # the two records spell her first name differently (Eleanor / Helena).
    tree_pairs: dict[tuple[str, str], list[str]] = {}
    for pid, person in people.items():
        birth = str(person.get("surname") or "").lower()
        married = str(person.get("married_surname") or "").lower()
        if birth and married:
            tree_pairs.setdefault((birth, married), []).append(pid)
    catalog_pairs = index.get(PAIRS_KEY) or {}
    claims: dict[int, list[tuple[float, int, int, str, dict[str, Any]]]] = {}
    for order, (pid, person) in enumerate(people.items()):
        claim = _catalog_claim(person, index)
        if not claim:
            birth = str(person.get("surname") or "").lower()
            married = str(person.get("married_surname") or "").lower()
            pair = (birth, married)
            hits = catalog_pairs.get(pair) or []
            # The estimate from scanned prints runs decades late, so only a
            # saved birth year can veto a unique surname pair.
            if (
                len(hits) == 1
                and len(tree_pairs.get(pair) or []) == 1
                and (not hits[0].get("born_exact") or _plausible(person, hits[0]))
            ):
                claim = (3, hits[0], 0)
        if not claim:
            continue
        tier, hit, matched = claim
        born_tree = (person.get("birth") or {}).get("year")
        born_hit = hit.get("born")
        gap = abs(int(born_tree) - int(born_hit)) if born_tree and born_hit else 10_000
        # A gap of decades rules a claimant out; within that, the name decides
        # (how many given names agree, then the tier) because the catalog's
        # birth estimate is only good to about a decade.
        far = 1 if gap > 40 else 0
        claims.setdefault(int(hit["id"]), []).append((far, -matched, tier, gap, order, pid, hit))
    links: dict[str, dict[str, Any]] = {}
    for items in claims.values():
        items.sort(key=lambda item: item[:5])
        pid, hit = items[0][5], items[0][6]
        links[pid] = hit
    return links


def person_detail(
    tree: dict[str, Any],
    person_id: str,
    catalog: dict[str, dict[str, Any]] | None = None,
    entire: bool = False,
) -> dict[str, Any]:
    people = tree.get("people") or {}
    families = tree.get("families") or {}
    person = people.get(person_id)
    if not person:
        raise KeyError(person_id)
    parents: list[dict[str, Any]] = []
    seen_parents: set[str] = set()
    for fid in person.get("famc") or []:
        fam = families.get(fid) or {}
        for role in ("husband", "wife"):
            ref = _person_ref(tree, fam.get(role) or "")
            if ref and ref["id"] not in seen_parents:
                seen_parents.add(ref["id"])
                parents.append(ref)
    spouses: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    seen_kids: set[str] = set()
    for fid in person.get("fams") or []:
        fam = families.get(fid) or {}
        other = fam.get("wife") if fam.get("husband") == person_id else fam.get("husband")
        spouse = _person_ref(tree, other or "")
        item = {
            "spouse": spouse,
            "marriage": fam.get("marriage"),
            "children": [],
        }
        for cid in fam.get("children") or []:
            kid = _person_ref(tree, cid)
            if not kid:
                continue
            item["children"].append(kid)
            if kid["id"] not in seen_kids:
                seen_kids.add(kid["id"])
                children.append(kid)
        spouses.append(item)
    catalog = catalog if catalog is not None else _catalog_index()
    payload = {
        **person,
        "parents": parents,
        "spouses": spouses,
        "children": children,
        "catalog": _catalog_links(tree, catalog).get(person_id),
        "chart": family_chart(tree, person_id, catalog, entire=entire),
    }
    return payload


MAX_ANCESTOR_GENS = 16
MAX_CHART_UNIONS = 256


def _ancestor_role(generation: int) -> str:
    if generation <= 1:
        return "parents"
    if generation == 2:
        return "grandparents"
    return "ancestors"


def _person_generations(tree: dict[str, Any], focus_id: str) -> dict[str, int]:
    """Generation of each person relative to focus: 0 = same generation, -1 = parents."""
    people = tree.get("people") or {}
    families = tree.get("families") or {}
    gen: dict[str, int] = {focus_id: 0}
    queue = [focus_id]
    i = 0
    while i < len(queue):
        pid = queue[i]
        i += 1
        g = gen[pid]
        person = people.get(pid) or {}

        def offer(nid: str | None, ng: int) -> None:
            if not nid or nid not in people or nid in gen:
                return
            gen[nid] = ng
            queue.append(nid)

        for fid in person.get("famc") or []:
            fam = families.get(fid) or {}
            for parent in (fam.get("husband"), fam.get("wife")):
                offer(parent, g - 1)
            for kid in fam.get("children") or []:
                offer(kid, g)
        for fid in person.get("fams") or []:
            fam = families.get(fid) or {}
            other = fam.get("wife") if fam.get("husband") == pid else fam.get("husband")
            offer(other, g)
            for kid in fam.get("children") or []:
                offer(kid, g + 1)
    for _ in range(len(families) + 2):
        changed = False
        for fam in families.values():
            parts = [pid for pid in (fam.get("husband"), fam.get("wife")) if pid in gen]
            if len(parts) == 2 and gen[parts[0]] != gen[parts[1]]:
                g = gen[parts[0]] if abs(gen[parts[0]]) <= abs(gen[parts[1]]) else gen[parts[1]]
                if gen[parts[0]] != g or gen[parts[1]] != g:
                    gen[parts[0]] = g
                    gen[parts[1]] = g
                    changed = True
            if parts:
                pg = min(gen[pid] for pid in parts)
                for kid in fam.get("children") or []:
                    if kid in gen and gen[kid] != pg + 1:
                        gen[kid] = pg + 1
                        changed = True
                    elif kid in people and kid not in gen:
                        gen[kid] = pg + 1
                        changed = True
        if not changed:
            break
    focus_year = ((people.get(focus_id) or {}).get("birth") or {}).get("year")
    for pid, person in people.items():
        if pid in gen:
            continue
        year = (person.get("birth") or {}).get("year")
        if focus_year and year:
            gen[pid] = int(round((int(year) - int(focus_year)) / 28.0))
        else:
            gen[pid] = 0
    return gen


def family_chart(
    tree: dict[str, Any],
    person_id: str,
    catalog: dict[str, dict[str, Any]] | None = None,
    entire: bool = False,
) -> dict[str, Any]:
    """Nodes and family unions around one person, for drawing a tree."""
    people = tree.get("people") or {}
    families = tree.get("families") or {}
    if person_id not in people:
        raise KeyError(person_id)
    catalog = catalog if catalog is not None else _catalog_index()
    links = _catalog_links(tree, catalog)
    nodes: dict[str, dict[str, Any]] = {}
    unions: list[dict[str, Any]] = []
    seen_unions: set[str] = set()

    def add_person(pid: str) -> None:
        if not pid or pid in nodes:
            return
        raw = people.get(pid)
        if not raw:
            return
        item = _brief(raw) or {"id": pid, "name": pid}
        hit = links.get(pid)
        if hit:
            item["catalog_id"] = hit["id"]
            if hit.get("cover_url"):
                item["cover_url"] = hit["cover_url"]
        nodes[pid] = item

    def add_union(fid: str, role: str, generation: int | None = None) -> None:
        if not fid or fid in seen_unions or len(unions) >= MAX_CHART_UNIONS:
            return
        fam = families.get(fid)
        if not fam:
            return
        seen_unions.add(fid)
        partners = [pid for pid in (fam.get("husband"), fam.get("wife")) if pid]
        kids = [cid for cid in (fam.get("children") or []) if cid]
        for pid in partners + kids:
            add_person(pid)
        item = {
            "id": fid,
            "role": role,
            "partners": partners,
            "children": kids,
            "marriage": fam.get("marriage"),
        }
        if generation is not None:
            item["generation"] = generation
        unions.append(item)

    if entire:
        gens = _person_generations(tree, person_id)
        for pid, g in gens.items():
            add_person(pid)
            if pid in nodes:
                nodes[pid]["generation"] = g
        for fid, fam in families.items():
            parts = [pid for pid in (fam.get("husband"), fam.get("wife")) if pid]
            couple = [gens[pid] for pid in parts if pid in gens]
            g = min(couple) if couple else 0
            if person_id in parts:
                role = "own"
            elif g < 0:
                role = _ancestor_role(-g)
            elif g > 0:
                role = "descendants"
            else:
                role = "kin"
            add_union(fid, role, g)
        return {
            "focus": person_id,
            "scope": "all",
            "nodes": list(nodes.values()),
            "unions": unions,
        }

    add_person(person_id)
    seen_walk: set[str] = set()

    def walk_ancestors(pid: str, generation: int) -> None:
        if (
            not pid
            or pid in seen_walk
            or generation > MAX_ANCESTOR_GENS
            or len(unions) >= MAX_CHART_UNIONS
        ):
            return
        seen_walk.add(pid)
        person = people.get(pid) or {}
        for fid in person.get("famc") or []:
            if fid in seen_unions:
                continue
            add_union(fid, _ancestor_role(generation), generation)
            fam = families.get(fid) or {}
            for parent_id in (fam.get("husband"), fam.get("wife")):
                walk_ancestors(parent_id, generation + 1)

    walk_ancestors(person_id, 1)
    focus = people[person_id]
    for fid in focus.get("fams") or []:
        add_union(fid, "own")
        fam = families.get(fid) or {}
        for cid in fam.get("children") or []:
            child = people.get(cid) or {}
            for gfid in child.get("fams") or []:
                add_union(gfid, "grandchildren")
    return {"focus": person_id, "nodes": list(nodes.values()), "unions": unions}


def tree_public(tree: dict[str, Any], *, filename: str = "", loaded_at: str = "") -> dict[str, Any]:
    catalog = _catalog_index()
    links = _catalog_links(tree, catalog)
    people = []
    for person in (tree.get("people") or {}).values():
        item = _brief(person) or {}
        hit = links.get(person["id"])
        if hit:
            item["catalog_id"] = hit["id"]
            # Nicknames recorded in the catalog help find tree people too.
            if hit.get("nickname"):
                parts = [x.strip() for x in (item.get("nickname") or "").split(",") if x.strip()]
                for part in hit["nickname"].split(","):
                    if part.strip() and part.strip().casefold() not in {x.casefold() for x in parts}:
                        parts.append(part.strip())
                item["nickname"] = ", ".join(parts)
        people.append(item)
    people.sort(key=lambda row: ((row.get("surname") or row.get("name") or "").lower(), row.get("name") or ""))
    return {
        "loaded": True,
        "filename": filename,
        "loaded_at": loaded_at,
        "source": tree.get("source") or "",
        "people_count": len(tree.get("people") or {}),
        "families_count": len(tree.get("families") or {}),
        "people": people,
    }


def _stamp() -> tuple[Any, ...]:
    path = config_mod.GEDCOM_PATH
    meta = config_mod.GEDCOM_META_PATH
    if not path.is_file():
        return (str(path), 0, 0)
    stat = path.stat()
    extra = meta.stat().st_mtime if meta.is_file() else 0
    return (str(path), int(stat.st_mtime), int(stat.st_size), extra)


def _meta() -> dict[str, Any]:
    if not config_mod.GEDCOM_META_PATH.is_file():
        return {}
    try:
        return json.loads(config_mod.GEDCOM_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_tree() -> dict[str, Any] | None:
    global _cache, _cache_stamp
    if not config_mod.GEDCOM_PATH.is_file():
        _cache = None
        _cache_stamp = None
        return None
    stamp = _stamp()
    if _cache is not None and _cache_stamp == stamp:
        return _cache
    text = _decode(config_mod.GEDCOM_PATH.read_bytes())
    tree = parse_gedcom(text)
    meta = _meta()
    payload = tree_public(
        tree,
        filename=str(meta.get("filename") or config_mod.GEDCOM_PATH.name),
        loaded_at=str(meta.get("loaded_at") or ""),
    )
    payload["_tree"] = tree
    _cache = payload
    _cache_stamp = stamp
    return payload


def save_file(data: bytes, filename: str) -> dict[str, Any]:
    global _cache, _cache_stamp
    raw = data or b""
    if len(raw) > MAX_BYTES:
        raise GedcomError("That file is too large (20 MB limit).")
    if not raw.strip():
        raise GedcomError("That file is empty.")
    text = _decode(raw)
    tree = parse_gedcom(text)
    name = Path(filename or "family.ged").name
    config_mod.GEDCOM_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_mod.GEDCOM_PATH.write_bytes(raw)
    meta = {"filename": name, "loaded_at": now_iso()}
    config_mod.GEDCOM_META_PATH.write_text(json.dumps(meta), encoding="utf-8")
    payload = tree_public(tree, filename=name, loaded_at=meta["loaded_at"])
    payload["_tree"] = tree
    _cache = payload
    _cache_stamp = _stamp()
    return {k: v for k, v in payload.items() if k != "_tree"}


def clear_file() -> None:
    global _cache, _cache_stamp
    for path in (config_mod.GEDCOM_PATH, config_mod.GEDCOM_META_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _cache = None
    _cache_stamp = None


def summary() -> dict[str, Any]:
    loaded = load_tree()
    if not loaded:
        return {"loaded": False, "people": [], "people_count": 0, "families_count": 0}
    return {k: v for k, v in loaded.items() if k != "_tree"}


def get_person(person_id: str, entire: bool = False) -> dict[str, Any]:
    loaded = load_tree()
    if not loaded:
        raise FileNotFoundError("No GEDCOM file is loaded.")
    return person_detail(loaded["_tree"], xref_id(person_id), entire=entire)
