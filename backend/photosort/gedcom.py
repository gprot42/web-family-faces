"""Load and view a GEDCOM (.ged) family tree. Originals are never written."""

from __future__ import annotations

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

    if not people:
        raise GedcomError("That file has no people in it.")
    return {"source": source, "people": people, "families": families}


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
    }


def _person_ref(tree: dict[str, Any], pid: str) -> dict[str, Any] | None:
    return _brief((tree.get("people") or {}).get(pid))


def _catalog_index() -> dict[str, dict[str, Any]]:
    from .db import connect, init_db

    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            "SELECT id, name, nickname FROM people WHERE name IS NOT NULL AND name != ''"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        hit = {"id": int(row["id"]), "name": row["name"]}
        keys = [str(row["name"] or "").strip().lower()]
        nick = str(row["nickname"] if "nickname" in row.keys() else "") or ""
        for part in re.split(r"[,;/]", nick):
            keys.append(part.strip().lower())
        for key in keys:
            if key and key not in out:
                out[key] = hit
    return out


def _catalog_hit(person: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    names = []
    for value in (
        person.get("name"),
        person.get("nickname"),
        f"{person.get('given') or ''} {person.get('surname') or ''}".strip(),
    ):
        key = str(value or "").strip().lower()
        if key and key not in names:
            names.append(key)
    for key in names:
        hit = index.get(key)
        if hit:
            return hit
    return None


def person_detail(tree: dict[str, Any], person_id: str, catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
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
        "catalog": _catalog_hit(person, catalog),
        "chart": family_chart(tree, person_id, catalog),
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


def family_chart(tree: dict[str, Any], person_id: str, catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Nodes and family unions around one person, for drawing a tree."""
    people = tree.get("people") or {}
    families = tree.get("families") or {}
    if person_id not in people:
        raise KeyError(person_id)
    catalog = catalog if catalog is not None else _catalog_index()
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
        hit = _catalog_hit(raw, catalog)
        if hit:
            item["catalog_id"] = hit["id"]
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
        if generation:
            item["generation"] = generation
        unions.append(item)

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
    people = []
    for person in (tree.get("people") or {}).values():
        item = _brief(person) or {}
        hit = _catalog_hit(person, catalog)
        if hit:
            item["catalog_id"] = hit["id"]
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


def get_person(person_id: str) -> dict[str, Any]:
    loaded = load_tree()
    if not loaded:
        raise FileNotFoundError("No GEDCOM file is loaded.")
    return person_detail(loaded["_tree"], xref_id(person_id))
