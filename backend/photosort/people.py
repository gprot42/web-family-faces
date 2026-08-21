from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .config import CHILD_AGE, CLUSTER_PREVIEW_LIMIT, CROP_DIR, ELDER_AGE, TEEN_AGE
from .db import connect, init_db
from .originals import drop_preview_rows, preview_path_sql
from .util import bytes_to_embedding, l2_normalize, now_iso

_COVER_CANDIDATES = 12
_COVER_RECENT = 8


def age_band(age: float | None) -> str:
    if age is None:
        return "unknown"
    if age < CHILD_AGE:
        return "child"
    if age < TEEN_AGE:
        return "teen"
    if age < ELDER_AGE:
        return "adult"
    return "elder"


UNKNOWN_NAME = "Unknown name of person"
PERSON_CATEGORIES = ("family", "work", "other")


_PROTECTED_HOW = frozenset({"manual", "unknown_name", "merge"})
_SAME_FACE_IOU = 0.45


def _face_area(row: dict[str, Any] | Any) -> float:
    try:
        return max(1.0, (float(row["x2"] or 0) - float(row["x1"] or 0)) * (float(row["y2"] or 0) - float(row["y1"] or 0)))
    except (TypeError, KeyError):
        return 1.0


def _box_inter(a: Any, b: Any) -> float:
    x1 = max(float(a["x1"] or 0), float(b["x1"] or 0))
    y1 = max(float(a["y1"] or 0), float(b["y1"] or 0))
    x2 = min(float(a["x2"] or 0), float(b["x2"] or 0))
    y2 = min(float(a["y2"] or 0), float(b["y2"] or 0))
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _box_iou(a: Any, b: Any) -> float:
    inter = _box_inter(a, b)
    if not inter:
        return 0.0
    denom = _face_area(a) + _face_area(b) - inter
    return inter / denom if denom else 0.0


def _person_has_distinct_box(claimed: dict[int, list[Any]], person_id: int, box: Any) -> bool:
    existing = claimed.get(int(person_id)) or []
    if not existing:
        return False
    return not any(_box_iou(box, other) >= _SAME_FACE_IOU for other in existing)


def _occlusion(face: Any, others: list[Any] | None, *, crowd: bool = True) -> float:
    """0 = clear view, 1 = another face covers this one."""
    others = others or []
    area = _face_area(face)
    fx1, fy1 = float(face["x1"] or 0), float(face["y1"] or 0)
    fx2, fy2 = float(face["x2"] or 0), float(face["y2"] or 0)
    fcy = (fy1 + fy2) / 2.0
    penalty = 0.0
    fid = int(face["id"])
    for other in others:
        if int(other["id"]) == fid:
            continue
        inter = _box_inter(face, other)
        other_area = _face_area(other)
        if inter > 0:
            cover = inter / area
            # A smaller nested box is usually a duplicate detection, not someone in front.
            if other_area >= area * 0.85:
                front = 1.25 if other_area > area else 1.0
                penalty = max(penalty, min(1.0, cover * front))
            continue
        ocx = (float(other["x1"] or 0) + float(other["x2"] or 0)) / 2.0
        ocy = (float(other["y1"] or 0) + float(other["y2"] or 0)) / 2.0
        # Someone standing in front: their head sits in the lower half of this crop.
        if fx1 < ocx < fx2 and ocy > fcy and float(other["y1"] or 0) < fy2:
            penalty = max(penalty, min(1.0, 0.55 * other_area / area))
    extras = max(0, len(others) - 1)
    if crowd and extras:
        penalty = min(1.0, penalty + min(0.22, 0.03 * extras))
    return penalty


def _faces_on_photos(conn, photo_ids: list[int]) -> dict[int, list[Any]]:
    if not photo_ids:
        return {}
    marks = ",".join("?" * len(photo_ids))
    rows = conn.execute(
        f"""
        SELECT id, photo_id, x1, y1, x2, y2 FROM faces
        WHERE photo_id IN ({marks})
          AND quality = 'ok'
          AND IFNULL(assigned_how, '') != 'junk'
        """,
        [int(pid) for pid in photo_ids],
    ).fetchall()
    out: dict[int, list[Any]] = {}
    for row in rows:
        out.setdefault(int(row["photo_id"]), []).append(row)
    return out


_look_cache: dict[tuple[str, int], tuple[float, float]] = {}


def _crop_look_scores(face_id: int) -> tuple[float, float]:
    """(color 0-1, view 0-1) from one crop read. Missing crops stay neutral."""
    path = CROP_DIR / f"{int(face_id)}.jpg"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return 0.5, 0.45
    key = (str(path), mtime)
    cached = _look_cache.get(key)
    if cached:
        return cached
    try:
        from PIL import Image

        img = Image.open(path)
        img.load()
        small = img.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
        img.close()
        raw = small.tobytes()
    except Exception:
        return 0.5, 0.45
    n = max(1, len(raw) // 3)
    colorful = 0
    luma = []
    for i in range(0, n * 3, 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if max(r, g, b) - min(r, g, b) >= 18:
            colorful += 1
        luma.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
    frac = colorful / n
    if frac >= 0.16:
        color = 1.0
    elif frac >= 0.06:
        color = 0.35
    else:
        color = 0.0
    mean = sum(luma) / n
    var = sum((p - mean) ** 2 for p in luma) / n
    std = var ** 0.5
    if mean < 42:
        bright = 0.2 * (mean / 42.0)
    elif mean > 215:
        bright = max(0.15, 1.0 - (mean - 215) / 40.0)
    else:
        bright = 0.55 + 0.45 * (1.0 - abs(mean - 132) / 90.0)
    view = 0.5 * bright + 0.5 * min(1.0, std / 48.0)
    _look_cache[key] = (color, view)
    if len(_look_cache) > 8000:
        for old in list(_look_cache)[:2000]:
            _look_cache.pop(old, None)
    return color, view


def _crop_view_score(face_id: int) -> float:
    """0-1: prefer mid-bright, contrasty crops over dark or washed-out ones."""
    return _crop_look_scores(face_id)[1]


def _crop_color_score(face_id: int) -> float:
    """1 = colour crop, 0 = black-and-white. Missing crops stay neutral."""
    return _crop_look_scores(face_id)[0]


_MALE_FIRST = frozenset(
    """
    aaron adam adrian alan albert alexander alfred allan allen andrew anthony arthur
    barry ben benjamin bernard bill billy bob bobby brad bradley brandon brett brian
    bruce bryan carl charles chris christopher clarence clifford colin craig
    daniel darren darryl dave david dean dennis derek don donald douglas
    ed edward eric ernest eugene
    frank fred frederick
    gary gene george gerald gordon graham grant greg gregory
    harold harry henry howard hugh
    ian isaac ivan
    jack jacob james jamie jason jay jeff jeffrey jeremy jerry jim jimmy joe joel
    john johnny jon jonathan joseph josh joshua juan justin
    keith ken kenneth kevin kirk kyle
    larry lawrence lee leo leon leonard les leslie lewis lloyd louis
    marc marcus mario mark martin marty matt matthew maurice max melvin michael
    mike mitchell
    nathan neil nelson nicholas nick nigel norman
    oscar owen
    patrick paul perry pete peter phil philip
    ralph ramon randall randy ray raymond ricardo richard rick ricky rob robbie
    robert robin rod rodney roger roland ron ronald ross roy russell ryan
    sam samuel scott sean sebastian sergio seth shawn sidney stan stanley steve
    steven stewart stuart
    ted terry theodore thomas tim timothy todd tom tommy tony travis troy tyler
    vernon victor vincent
    wallace walter warren wayne wesley will william winston wyatt
    """.split()
)
_FEMALE_FIRST = frozenset(
    """
    abigail alice alicia allison amy andrea angela ann anna anne annie anita
    barbara bernice bertha beth betty beverly bonnie brenda britany
    caitlin candice carol carole carolina caroline carolyn catherine cathy
    charlotte cheryl chris christine cindy claire clara colette colleen
    concetta connie constance crystal cynthia
    daisy dana darlene dawn dawna debbie deborah debra denise diana diane
    dolores donna dora doreen doris dorothy
    edith edna eileen elaine eleanor elena elisa elisabeth elise eliza elizabeth
    ella ellen elsie emily emma erica erin esther ethel evelyn
    faith florence frances
    gail gayle genevieve georgia gina gladys gloria grace gwen
    hannah harriet hattie hazel heather heidi helen hilary holly hope
    irene iris irma isabel isabella
    jackie jane janet janice jean jeanette jeanne jennifer jenny jessica
    jill joan joann joanna joanne jocelyn jodi josephine joyce joy juanita
    judith judy julia julie june
    kara karen kari karla kate katelyn katherine kathleen kathryn kathy
    katie kay kayla kelly kim kimberly kristen kristin kristina
    laura laurel lauren laurie lena lenka leah leigh lena leslie lillian
    lily linda lindsay lisa lois lola loretta lori lorraine louise lucia
    lucille lucy lulu lynn lynne
    mabel madeline maggie marcia margaret margarita margery maria marian
    marianne marie marilyn marjorie marlene martha mary maryann maureen
    meghan melanie melinda melissa michelle mildred millie minnie
    monica myra myrtle
    nancy naomi natalie nora nikki nina noelle norma
    olga olive olivia
    pamela paula paulette pauline pearl peggy penelope penny persephone
    phyllis priscilla
    rachel rebecca renee rhonda rita roberts roberta robin rosa rosalie
    rose rosemarie rosemary rosalyn roxanne ruth
    sally samantha sandra sandy sarah selma sharon sheila shelley sherry
    shirley sonia sonya stacey stacy stella stephanie susan susie suzanne
    sylvia
    tamara tami tammy tanya tara teresa terri terry thelma theresa tiffany
    tina tonya tracey tracy tricia
    valerie vanessa vera verna vicki vickie victoria viola violet virginia
    vivian
    wanda wendy whitney wilma winifred
    yolanda yvonne
    """.split()
)


def _name_sex(name: str) -> str:
    first = str(name or "").strip().split()
    if not first:
        return ""
    token = first[0].lower()
    if token in _MALE_FIRST:
        return "M"
    if token in _FEMALE_FIRST:
        return "F"
    return ""


def _norm_sex(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in ("M", "MALE"):
        return "M"
    if raw in ("F", "FEMALE"):
        return "F"
    return ""


def _row_sex(row: Any) -> str:
    try:
        return _norm_sex(row["sex_est"])
    except (KeyError, IndexError, TypeError):
        return ""


def _majority_sex(rows: list[Any]) -> str:
    male = 0
    female = 0
    for row in rows:
        sex = _row_sex(row)
        if sex == "M":
            male += 1
        elif sex == "F":
            female += 1
    if male > female:
        return "M"
    if female > male:
        return "F"
    return ""


def _cover_rank(
    row: dict[str, Any] | Any,
    neighbors: list[Any] | None = None,
    want_sex: str = "",
    looks_sex: str = "",
    name_hit: float = 0.0,
) -> tuple[float, ...]:
    looks = looks_sex or _row_sex(row)
    if want_sex:
        if looks == want_sex:
            sex_match = 1.0
        elif looks:
            sex_match = 0.0
        else:
            sex_match = 0.4
    else:
        sex_match = 0.5
    blocked = _occlusion(row, neighbors, crowd=False)
    unobscured = 1.0 if blocked < 0.28 else 0.0
    det = float(row["det_score"] or 0)
    size = min(1.0, (_face_area(row) ** 0.5) / 160.0)
    color, view = _crop_look_scores(int(row["id"]))
    return (unobscured, color, sex_match, name_hit, 1.0 - blocked, view, det, size)


def _sex_centroids(conn, person_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not person_ids:
        return {}
    marks = ",".join("?" * len(person_ids))
    rows = conn.execute(
        f"""
        SELECT person_id, id, sex_est, embedding
        FROM faces
        WHERE person_id IN ({marks})
          AND quality = 'ok'
          AND IFNULL(assigned_how, '') != 'junk'
          AND embedding IS NOT NULL
        """,
        [int(pid) for pid in person_ids],
    ).fetchall()
    by_person: dict[int, list[tuple[int, str, Any]]] = {}
    for row in rows:
        vec = bytes_to_embedding(row["embedding"])
        if vec is None:
            continue
        sex = _norm_sex(row["sex_est"])
        if not sex:
            continue
        by_person.setdefault(int(row["person_id"]), []).append((int(row["id"]), sex, l2_normalize(vec)))
    out: dict[int, dict[str, Any]] = {}
    for pid, items in by_person.items():
        labels = [item[1] for item in items]
        stacked = np.stack([item[2] for item in items])
        for _ in range(4):
            means: dict[str, Any] = {}
            for sex in ("M", "F"):
                idx = [i for i, lab in enumerate(labels) if lab == sex]
                if len(idx) < 2:
                    continue
                means[sex] = l2_normalize(stacked[idx].mean(axis=0))
            if "M" not in means or "F" not in means:
                break
            nxt = []
            for i in range(len(items)):
                sim_m = float(np.dot(stacked[i], means["M"]))
                sim_f = float(np.dot(stacked[i], means["F"]))
                nxt.append("M" if sim_m >= sim_f else "F")
            if nxt == labels:
                break
            labels = nxt
        means = {}
        for sex in ("M", "F"):
            idx = [i for i, lab in enumerate(labels) if lab == sex]
            if len(idx) >= 2:
                means[sex] = l2_normalize(stacked[idx].mean(axis=0))
        if means:
            out[pid] = means
    return out


def _looks_sex(row: Any, means: dict[str, Any] | None, emb_by_id: dict[int, Any]) -> str:
    labeled = _row_sex(row)
    if not means or "M" not in means or "F" not in means:
        return labeled
    vec = emb_by_id.get(int(row["id"]))
    if vec is None:
        return labeled
    vec = l2_normalize(vec)
    sim_m = float(np.dot(vec, means["M"]))
    sim_f = float(np.dot(vec, means["F"]))
    if sim_m > sim_f + 0.02:
        return "M"
    if sim_f > sim_m + 0.02:
        return "F"
    return ""


def _best_cover_ids(
    conn, person_ids: list[int] | None = None, *, scan_embeddings: bool = True
) -> dict[int, int]:
    extra = ""
    params: list[Any] = []
    if person_ids:
        extra = "AND f.person_id IN (" + ",".join("?" * len(person_ids)) + ")"
        params = [int(pid) for pid in person_ids]
    rows = conn.execute(
        f"""
        WITH votes AS (
            SELECT f.person_id,
                   SUM(CASE WHEN UPPER(IFNULL(f.sex_est, '')) IN ('M', 'MALE') THEN 1 ELSE 0 END) AS male_n,
                   SUM(CASE WHEN UPPER(IFNULL(f.sex_est, '')) IN ('F', 'FEMALE') THEN 1 ELSE 0 END) AS female_n
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NOT NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
              AND {preview_path_sql("ph.path")}
              {extra}
            GROUP BY f.person_id
        )
        SELECT person_id, id, photo_id, det_score, x1, y1, x2, y2, sex_est FROM (
            SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2, f.sex_est,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.person_id
                       ORDER BY CASE
                           WHEN v.male_n > v.female_n AND UPPER(IFNULL(f.sex_est, '')) IN ('M', 'MALE') THEN 0
                           WHEN v.female_n > v.male_n AND UPPER(IFNULL(f.sex_est, '')) IN ('F', 'FEMALE') THEN 0
                           ELSE 1
                       END,
                       f.det_score DESC,
                       ((f.x2 - f.x1) * (f.y2 - f.y1)) DESC,
                       f.id
                   ) AS det_rn,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.person_id
                       ORDER BY ph.taken_at IS NULL, ph.taken_at DESC, f.det_score DESC, f.id
                   ) AS recent_rn
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            JOIN votes v ON v.person_id = f.person_id
            WHERE f.person_id IS NOT NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
              AND {preview_path_sql("ph.path")}
              {extra}
        ) ranked
        WHERE det_rn <= ? OR recent_rn <= ?
        """,
        (*params, *params, _COVER_CANDIDATES, _COVER_RECENT),
    ).fetchall()
    by_person: dict[int, list[Any]] = {}
    seen: set[int] = set()
    for row in rows:
        fid = int(row["id"])
        if fid in seen:
            continue
        seen.add(fid)
        by_person.setdefault(int(row["person_id"]), []).append(row)
    named = conn.execute(
        f"""
        SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2, f.sex_est
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        JOIN people p ON p.id = f.person_id
        WHERE f.quality = 'ok'
          AND IFNULL(f.assigned_how, '') != 'junk'
          AND IFNULL(ph.hidden, 0) = 0
          AND {preview_path_sql("ph.path")}
          AND instr(lower(ph.path), lower(p.name)) > 0
          {extra}
        """,
        params,
    ).fetchall()
    named_ids = {int(r["id"]) for r in named}
    for row in named:
        fid = int(row["id"])
        if fid in seen:
            continue
        seen.add(fid)
        by_person.setdefault(int(row["person_id"]), []).append(row)
    neighbors = _faces_on_photos(conn, [int(r["photo_id"]) for r in [*rows, *named]])
    pids = list(by_person)
    names = {}
    if pids:
        marks = ",".join("?" * len(pids))
        for row in conn.execute(f"SELECT id, name FROM people WHERE id IN ({marks})", pids):
            names[int(row["id"])] = row["name"]
    centroids = _sex_centroids(conn, pids) if scan_embeddings else {}
    emb_by_id: dict[int, Any] = {}
    if scan_embeddings and pids:
        marks = ",".join("?" * len(pids))
        for row in conn.execute(
            f"""
            SELECT id, embedding FROM faces
            WHERE person_id IN ({marks}) AND embedding IS NOT NULL
            """,
            pids,
        ):
            vec = bytes_to_embedding(row["embedding"])
            if vec is not None:
                emb_by_id[int(row["id"])] = vec
    picked: dict[int, int] = {}
    for pid, cands in by_person.items():
        want = _name_sex(names.get(pid, "")) or _majority_sex(cands)
        means = centroids.get(pid)
        pool = list(cands)
        clear = [
            row
            for row in pool
            if _occlusion(row, neighbors.get(int(row["photo_id"])), crowd=False) < 0.28
        ]
        if clear:
            pool = clear
        best = max(
            pool,
            key=lambda row, n=neighbors, w=want, m=means: _cover_rank(
                row,
                n.get(int(row["photo_id"])),
                w,
                _looks_sex(row, m, emb_by_id) if means else "",
                1.0 if int(row["id"]) in named_ids else 0.0,
            ),
        )
        picked[pid] = int(best["id"])
    return picked


def normalize_category(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in PERSON_CATEGORIES else ""


_BURST_GAP_SEC = 8.0


def _taken_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None


def display_faces(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One crop per distinct picture: drop extra faces, file copies, and burst frames."""
    best_by_photo: dict[int, dict[str, Any]] = {}
    photo_order: list[int] = []
    for face in faces:
        pid = face.get("photo_id")
        if pid is None:
            continue
        pid = int(pid)
        cur = best_by_photo.get(pid)
        if cur is None:
            photo_order.append(pid)
            best_by_photo[pid] = face
            continue
        if (face.get("det_score") or 0) > (cur.get("det_score") or 0):
            best_by_photo[pid] = face
    unique_photos = [best_by_photo[pid] for pid in photo_order]

    best_by_sha: dict[str, dict[str, Any]] = {}
    sha_order: list[str] = []
    no_sha: list[dict[str, Any]] = []
    for face in unique_photos:
        sha = str(face.get("sha256") or "")
        if not sha or sha.startswith("pending:"):
            no_sha.append(face)
            continue
        cur = best_by_sha.get(sha)
        if cur is None:
            sha_order.append(sha)
            best_by_sha[sha] = face
            continue
        if len(str(face.get("path") or "")) < len(str(cur.get("path") or "")):
            best_by_sha[sha] = face
    copies = [best_by_sha[sha] for sha in sha_order] + no_sha
    copies.sort(
        key=lambda face: (
            face.get("taken_at") is None,
            str(face.get("taken_at") or ""),
            int(face.get("photo_id") or 0),
        )
    )

    shown: list[dict[str, Any]] = []
    last_t: datetime | None = None
    last_folder: str | None = None
    for face in copies:
        taken = _taken_dt(face.get("taken_at"))
        folder = str(Path(face.get("path") or "").parent)
        if shown and taken and last_t and folder == last_folder:
            gap = abs((taken - last_t).total_seconds())
            if gap <= _BURST_GAP_SEC:
                if (face.get("det_score") or 0) > (shown[-1].get("det_score") or 0):
                    shown[-1] = face
                    last_t = taken
                continue
        shown.append(face)
        last_t = taken
        last_folder = folder
    return shown


def is_unknown_name(name: str | None) -> bool:
    if not name:
        return False
    return name == UNKNOWN_NAME or name.startswith(UNKNOWN_NAME + " ")


def normalize_nickname(value: str | None) -> str:
    """Comma-separated nicknames, trimmed, no duplicates."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,;/]", str(value or "")):
        text = " ".join(str(raw).split()).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        parts.append(text)
    return ", ".join(parts)[:120]


def nickname_keys(value: str | None) -> list[str]:
    nick = normalize_nickname(value)
    if not nick:
        return []
    return [part.casefold() for part in nick.split(", ")]


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 1:
        return 2
    prev = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        cur = [i]
        for j, other in enumerate(right, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (char != other)))
        prev = cur
    return prev[-1]


def _token_matches(word: str, token: str) -> bool:
    if not token or not word:
        return False
    if token in word or word.startswith(token):
        return True
    return len(token) >= 3 and len(word) >= 3 and _edit_distance(token, word) <= 1


def person_matches_query(person: dict[str, Any], query: str) -> bool:
    wanted = (query or "").strip().casefold()
    if not wanted:
        return False
    name = str(person.get("name") or "").casefold()
    nick = str(person.get("nickname") or "").casefold()
    hay = f"{name} {nick}".strip()
    if wanted in hay:
        return True
    words = hay.split() + nickname_keys(nick)
    return all(any(_token_matches(word, token) for word in words) for token in wanted.split())


def find_person_by_name(name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        return None
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute(
            """
            SELECT p.*, COUNT(f.id) AS named_faces
            FROM people p
            LEFT JOIN faces f ON f.person_id = p.id
            WHERE p.name = ? COLLATE NOCASE
            GROUP BY p.id
            ORDER BY named_faces DESC, p.id
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row:
            return dict(row)
        needle = name.casefold()
        rows = conn.execute(
            """
            SELECT p.*, COUNT(f.id) AS named_faces
            FROM people p
            LEFT JOIN faces f ON f.person_id = p.id
            WHERE IFNULL(p.nickname, '') != ''
            GROUP BY p.id
            ORDER BY named_faces DESC, p.id
            """
        ).fetchall()
        for item in rows:
            person = dict(item)
            if needle in nickname_keys(person.get("nickname")):
                return person
        return None
    finally:
        conn.close()


def create_person(
    name: str,
    notes: str = "",
    birth_year: int | None = None,
    category: str = "",
    nickname: str = "",
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("Name is required")
    conn = connect()
    init_db(conn)
    try:
        cur = conn.execute(
            "INSERT INTO people (name, nickname, notes, birth_year, category, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, normalize_nickname(nickname), notes.strip(), birth_year, normalize_category(category), now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM people WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def create_unknown_person() -> dict[str, Any]:
    """A real person whose name is not known yet. Each group gets its own identity."""
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            "SELECT name FROM people WHERE name = ? OR name LIKE ?",
            (UNKNOWN_NAME, UNKNOWN_NAME + " %"),
        ).fetchall()
        used = {r["name"] for r in rows}
    finally:
        conn.close()
    name = UNKNOWN_NAME
    n = 2
    while name in used:
        name = f"{UNKNOWN_NAME} {n}"
        n += 1
    return create_person(name, notes="Name not known yet.")


def list_people(folder: str | None = None, *, lite: bool = False) -> list[dict[str, Any]]:
    wanted = (folder or "").strip() or None
    people = _list_people_covers_lite() if lite and not wanted else _list_people_covers()
    if not wanted:
        return people
    stats = _folder_people_stats(wanted)
    out: list[dict[str, Any]] = []
    for person in people:
        info = stats.get(int(person["id"]))
        if not info:
            continue
        person["face_count"] = info["face_count"]
        person["cover_face_id"] = info["cover_face_id"]
        person["first_seen"] = info["first_seen"]
        person["last_seen"] = info["last_seen"]
        person["age_min"] = info["age_min"]
        person["age_max"] = info["age_max"]
        out.append(person)
    return out


def _list_people_covers_lite() -> list[dict[str, Any]]:
    """Picker lists: name, category, and a clear colour crop. No embedding scan."""
    conn = connect()
    init_db(conn)
    try:
        people = [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()]
        covers = {
            int(r["person_id"]): r
            for r in conn.execute(
                f"""
                SELECT person_id, cnt FROM (
                    SELECT f.person_id,
                           COUNT(*) OVER (PARTITION BY f.person_id) AS cnt,
                           ROW_NUMBER() OVER (
                               PARTITION BY f.person_id
                               ORDER BY f.det_score DESC, f.id
                           ) AS rn
                    FROM faces f
                    JOIN photos ph ON ph.id = f.photo_id
                    WHERE f.person_id IS NOT NULL
                      AND f.quality = 'ok'
                      AND IFNULL(f.assigned_how, '') != 'junk'
                      AND IFNULL(ph.hidden, 0) = 0
                      AND {preview_path_sql("ph.path")}
                ) ranked
                WHERE rn = 1
                """
            ).fetchall()
        }
        best_cover = _best_cover_ids(conn, scan_embeddings=False)
        out: list[dict[str, Any]] = []
        for person in people:
            info = covers.get(int(person["id"]))
            if not info:
                continue
            person["face_count"] = int(info["cnt"] or 0)
            person["cover_face_id"] = best_cover.get(int(person["id"]))
            out.append(person)
        return out
    finally:
        conn.close()


def _list_people_covers() -> list[dict[str, Any]]:
    """Named people plus one cover crop. Skips the full face scan used by Faces in DB View."""
    conn = connect()
    init_db(conn)
    try:
        people = [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()]
        covers = {
            int(r["person_id"]): r
            for r in conn.execute(
                f"""
                SELECT f.person_id,
                       COUNT(*) AS face_count,
                       MIN(ph.taken_at) AS first_seen,
                       MAX(ph.taken_at) AS last_seen,
                       MIN(f.age_est) AS age_min,
                       MAX(f.age_est) AS age_max
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.person_id IS NOT NULL
                  AND f.quality = 'ok'
                  AND IFNULL(f.assigned_how, '') != 'junk'
                  AND IFNULL(ph.hidden, 0) = 0
                  AND {preview_path_sql("ph.path")}
                GROUP BY f.person_id
                """
            ).fetchall()
        }
        best_cover = _best_cover_ids(conn)
        out: list[dict[str, Any]] = []
        for person in people:
            info = covers.get(int(person["id"]))
            if not info:
                continue
            person["face_count"] = int(info["face_count"] or 0)
            person["cover_face_id"] = best_cover.get(int(person["id"]))
            person["first_seen"] = info["first_seen"]
            person["last_seen"] = info["last_seen"]
            person["age_min"] = info["age_min"]
            person["age_max"] = info["age_max"]
            out.append(person)
        return out
    finally:
        conn.close()


def _folder_people_stats(wanted: str) -> dict[int, dict[str, Any]]:
    from .originals import is_preview_path

    conn = connect()
    init_db(conn)
    neighbors: dict[int, list[Any]] = {}
    try:
        rows = conn.execute(
            """
            SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2, f.age_est, f.sex_est, ph.path, ph.taken_at
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NOT NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
            """
        ).fetchall()
        neighbors = _faces_on_photos(conn, list({int(r["photo_id"]) for r in rows}))
    finally:
        conn.close()
    stats: dict[int, dict[str, Any]] = {}
    covers: dict[int, list[Any]] = {}
    for row in rows:
        if is_preview_path(row["path"]) or not path_in_folder(row["path"], wanted):
            continue
        pid = int(row["person_id"])
        item = stats.get(pid)
        if item is None:
            item = {
                "face_count": 0,
                "cover_face_id": int(row["id"]),
                "first_seen": row["taken_at"],
                "last_seen": row["taken_at"],
                "age_min": row["age_est"],
                "age_max": row["age_est"],
            }
            stats[pid] = item
        item["face_count"] += 1
        covers.setdefault(pid, []).append(row)
        if row["taken_at"] and (not item["first_seen"] or row["taken_at"] < item["first_seen"]):
            item["first_seen"] = row["taken_at"]
        if row["taken_at"] and (not item["last_seen"] or row["taken_at"] > item["last_seen"]):
            item["last_seen"] = row["taken_at"]
        if row["age_est"] is not None:
            age = float(row["age_est"])
            if item["age_min"] is None or age < item["age_min"]:
                item["age_min"] = age
            if item["age_max"] is None or age > item["age_max"]:
                item["age_max"] = age
    for pid, item in stats.items():
        cands = covers.get(pid) or []
        if cands:
            want = _majority_sex(cands)
            pool = [row for row in cands if _row_sex(row) == want] if want else list(cands)
            if not pool:
                pool = list(cands)
            pool.sort(key=lambda row: (float(row["det_score"] or 0), _face_area(row)), reverse=True)
            pool = pool[:_COVER_CANDIDATES]
            item["cover_face_id"] = int(
                max(
                    pool,
                    key=lambda row: _cover_rank(row, neighbors.get(int(row["photo_id"])), want),
                )["id"]
            )
    return stats


def list_people_folders() -> list[dict[str, Any]]:
    """Album names that still have an identified face."""
    from .originals import is_preview_path

    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NOT NULL
              AND IFNULL(ph.hidden, 0) = 0
            """
        ).fetchall()
        names = sorted(
            {folder_of(row["path"]) for row in rows if not is_preview_path(row["path"])},
            key=str.lower,
        )
        return [{"folder": name} for name in names]
    finally:
        conn.close()


def get_person(person_id: int) -> dict[str, Any] | None:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            return None
        person = dict(row)
        faces = conn.execute(
            """
            SELECT f.id, f.photo_id, f.x1, f.y1, f.x2, f.y2, f.det_score, f.quality,
                   f.age_est, f.sex_est, f.person_id, f.cluster_id, f.assigned_how,
                   f.created_at, ph.path, ph.taken_at, ph.width, ph.height, ph.sha256
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id = ?
              AND IFNULL(ph.hidden, 0) = 0
            ORDER BY ph.taken_at IS NULL, ph.taken_at, f.age_est IS NULL, f.age_est
            """,
            (person_id,),
        ).fetchall()
        face_dicts = drop_preview_rows([dict(f) for f in faces])
        from .photos import tags_for_photos

        tag_map = tags_for_photos(conn, [int(f["photo_id"]) for f in face_dicts])
        for face in face_dicts:
            face["tags"] = tag_map.get(int(face["photo_id"]), [])
        person["faces"] = face_dicts
        person["shots"] = display_faces(person["faces"])
        person["face_count"] = len(person["faces"])
        return person
    finally:
        conn.close()


def update_person(person_id: int, *, sync_sidecars: bool = True, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "nickname", "notes", "birth_year", "category"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if "name" in payload:
        payload["name"] = str(payload["name"]).strip()
        if not payload["name"]:
            raise ValueError("Name is required")
    if "nickname" in payload:
        payload["nickname"] = normalize_nickname(payload["nickname"])
    if "notes" in payload:
        payload["notes"] = str(payload["notes"] or "").strip()[:4000]
    if "category" in payload:
        payload["category"] = normalize_category(payload["category"])
    if not payload:
        return get_person(person_id)
    conn = connect()
    init_db(conn)
    try:
        sets = ", ".join(f"{k} = ?" for k in payload)
        conn.execute(f"UPDATE people SET {sets} WHERE id = ?", (*payload.values(), person_id))
        conn.commit()
    finally:
        conn.close()
    if sync_sidecars and "name" in payload:
        _sync_sidecars_for_people([person_id])
    return get_person(person_id)


def assign_faces(
    face_ids: list[int],
    person_id: int,
    how: str,
    *,
    rematch: bool = True,
    sync_sidecars: bool = True,
) -> int:
    if not face_ids:
        return 0
    conn = connect()
    init_db(conn)
    try:
        conn.executemany(
            "UPDATE faces SET person_id = ?, assigned_how = ? WHERE id = ?",
            [(person_id, how, fid) for fid in face_ids],
        )
        conn.commit()
        n = len(face_ids)
        named = conn.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
        marks = ",".join("?" * len(face_ids))
        photo_rows = conn.execute(
            f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({marks})",
            list(face_ids),
        ).fetchall()
        photo_ids = [int(r["photo_id"]) for r in photo_rows if r["photo_id"]]
    finally:
        conn.close()
    ids = list(face_ids)
    if sync_sidecars:
        _sync_sidecars_for_faces(ids)
    else:
        threading.Thread(target=_sync_sidecars_for_faces, args=(ids,), daemon=True).start()
    if rematch and how != "auto" and named and not is_unknown_name(named["name"]):
        from .jobs import active_job
        from . import match as match_mod

        if not active_job() and photo_ids:
            def follow_photos() -> None:
                for pid in photo_ids[:12]:
                    try:
                        match_mod.match_photo(pid)
                    except Exception:
                        from . import log as log_mod

                        log_mod.exception("follow-up match failed photo=%s", pid)

            threading.Thread(
                target=follow_photos,
                daemon=True,
                name="photosort-follow-photo-match",
            ).start()
    return n


def unassign_face_and_copies(face_id: int, *, sync_sidecars: bool = True) -> int:
    """Remove this face and the same person on the same picture or SHA copy."""
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute(
            """
            SELECT f.id, f.person_id, f.photo_id, ph.sha256
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.id = ?
            """,
            (int(face_id),),
        ).fetchone()
        if not row:
            return 0
        ids = [int(row["id"])]
        if row["person_id"] is not None:
            sha = str(row["sha256"] or "")
            extra = conn.execute(
                """
                SELECT f.id
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.person_id = ?
                  AND (
                    f.photo_id = ?
                    OR (
                      ? != ''
                      AND ph.sha256 = ?
                      AND ph.sha256 NOT LIKE 'pending:%'
                    )
                  )
                """,
                (int(row["person_id"]), int(row["photo_id"]), sha, sha),
            ).fetchall()
            ids = sorted({int(r["id"]) for r in extra} | {int(row["id"])})
    finally:
        conn.close()
    return unassign_faces(ids, sync_sidecars=sync_sidecars)


def unassign_photo_names(photo_id: int, *, sync_sidecars: bool = True) -> int:
    """Take every catalog name off this picture. Matcher will not put them back."""
    conn = connect()
    init_db(conn)
    try:
        ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM faces
                WHERE photo_id = ?
                  AND person_id IS NOT NULL
                  AND IFNULL(assigned_how, '') != 'junk'
                """,
                (int(photo_id),),
            )
        ]
    finally:
        conn.close()
    n = 0
    for fid in ids:
        n += unassign_face_and_copies(fid, sync_sidecars=False)
    if ids and sync_sidecars:
        threading.Thread(target=_sync_sidecars_for_faces, args=(ids,), daemon=True).start()
    return n


def unassign_faces(face_ids: list[int], *, sync_sidecars: bool = True) -> int:
    if not face_ids:
        return 0
    conn = connect()
    init_db(conn)
    try:
        conn.executemany(
            """
            UPDATE faces
            SET person_id = NULL, assigned_how = 'cleared', cluster_id = NULL
            WHERE id = ?
            """,
            [(fid,) for fid in face_ids],
        )
        conn.commit()
        n = len(face_ids)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces(face_ids)
    else:
        ids = list(face_ids)
        threading.Thread(target=_sync_sidecars_for_faces, args=(ids,), daemon=True).start()
    return n


def cluster_unnamed_count(cluster_id: int) -> int:
    conn = connect()
    init_db(conn)
    try:
        n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE cluster_id = ?
              AND person_id IS NULL
              AND quality = 'ok'
              AND IFNULL(assigned_how, '') != 'junk'
            """,
            (int(cluster_id),),
        ).fetchone()["n"]
        return int(n or 0)
    finally:
        conn.close()


@dataclass(frozen=True)
class ClusterAssignReport:
    assigned: int
    considered: int = 0
    skipped_protected: int = 0
    skipped_already: int = 0
    skipped_lookalike: int = 0

    @property
    def reason(self) -> str | None:
        if self.assigned:
            return None
        if self.considered <= 0:
            return "no_faces"
        parts: list[str] = []
        if self.skipped_protected:
            parts.append("protected")
        if self.skipped_already:
            parts.append("already_in_photo")
        if self.skipped_lookalike:
            parts.append("lookalike")
        if len(parts) == 1:
            return parts[0]
        if parts:
            return "skipped"
        return "no_faces"

    def message(self) -> str:
        code = self.reason
        if code == "protected":
            return (
                "Could not save — those faces already have a name you set by hand. "
                "Open the photo to change it."
            )
        if code == "already_in_photo":
            return "Could not save — that person is already named on those photos."
        if code == "lookalike":
            return (
                "Could not save — these faces look more like someone else already in the catalog. "
                "Click that person, or mark mixed faces Not this person."
            )
        if code == "skipped":
            bits: list[str] = []
            if self.skipped_protected:
                bits.append(f"{self.skipped_protected} already named by hand")
            if self.skipped_already:
                bits.append(f"{self.skipped_already} already on those photos")
            if self.skipped_lookalike:
                bits.append(f"{self.skipped_lookalike} look like someone else")
            detail = ", ".join(bits) if bits else "no faces could be named"
            return (
                f"Could not save — {detail}. Mark mixed faces Not this person, "
                "or open a photo to change a name."
            )
        return (
            "Could not save — those faces are no longer unnamed "
            "(they may have been regrouped). Try again."
        )


def cluster_preview_face_ids(cluster_id: int, limit: int | None = None) -> list[int]:
    """Faces the To name page would have shown — never a mega-cluster."""
    if limit is None:
        limit = CLUSTER_PREVIEW_LIMIT
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.id, ph.path
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.cluster_id = ?
              AND f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(assigned_how, '') != 'junk'
            ORDER BY f.det_score DESC, f.id
            LIMIT ?
            """,
            (int(cluster_id), int(limit)),
        ).fetchall()
        return [int(r["id"]) for r in drop_preview_rows([dict(r) for r in rows])]
    finally:
        conn.close()


def assign_cluster(
    cluster_id: int,
    person_id: int,
    face_ids: list[int] | None = None,
    *,
    sync_sidecars: bool = True,
) -> int:
    return assign_cluster_report(
        cluster_id, person_id, face_ids=face_ids, sync_sidecars=sync_sidecars
    ).assigned


def assign_cluster_report(
    cluster_id: int,
    person_id: int,
    face_ids: list[int] | None = None,
    *,
    sync_sidecars: bool = True,
) -> ClusterAssignReport:
    conn = connect()
    init_db(conn)
    keep: list[int] = []
    considered = 0
    skipped_protected = 0
    skipped_already = 0
    skipped_lookalike = 0
    try:
        wanted = [int(fid) for fid in (face_ids or []) if fid]
        if not wanted:
            unnamed_n = conn.execute(
                """
                SELECT COUNT(*) AS n FROM faces
                WHERE cluster_id = ?
                  AND person_id IS NULL
                  AND quality = 'ok'
                  AND IFNULL(assigned_how, '') != 'junk'
                """,
                (cluster_id,),
            ).fetchone()["n"]
            if int(unnamed_n or 0) > CLUSTER_PREVIEW_LIMIT:
                wanted = cluster_preview_face_ids(int(cluster_id))
        if wanted:
            placeholders = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"""
                SELECT f.id, f.photo_id, f.x1, f.y1, f.x2, f.y2, f.assigned_how, f.embedding, f.sex_est, f.age_est, ph.path
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.id IN ({placeholders}) AND f.quality = 'ok'
                """,
                wanted,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT f.id, f.photo_id, f.x1, f.y1, f.x2, f.y2, f.assigned_how, f.embedding, f.sex_est, f.age_est, ph.path
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.cluster_id = ? AND f.quality = 'ok'
                """,
                (cluster_id,),
            ).fetchall()
        visible = drop_preview_rows([dict(r) for r in rows])
        considered = len(visible)
        from . import match as match_mod

        gallery = match_mod.load_named_gallery(conn)
        gallery_pids = gallery.get("person_ids")
        if gallery_pids is None or getattr(gallery_pids, "size", 0) == 0:
            known_ids: set[int] = set()
        else:
            known_ids = {int(x) for x in gallery_pids}
        claimed_by_photo: dict[int, dict[int, list[Any]]] = {}
        for face in visible:
            how = str(face.get("assigned_how") or "")
            if how in _PROTECTED_HOW:
                skipped_protected += 1
                continue
            photo_id = int(face["photo_id"])
            claimed = claimed_by_photo.get(photo_id)
            if claimed is None:
                named_rows = conn.execute(
                    """
                    SELECT person_id, x1, y1, x2, y2 FROM faces
                    WHERE photo_id = ?
                      AND person_id IS NOT NULL
                      AND IFNULL(assigned_how, '') != 'junk'
                    """,
                    (photo_id,),
                ).fetchall()
                claimed = {}
                for named in named_rows:
                    claimed.setdefault(int(named["person_id"]), []).append(named)
                claimed_by_photo[photo_id] = claimed
            if _person_has_distinct_box(claimed, int(person_id), face):
                skipped_already += 1
                continue
            vec = bytes_to_embedding(face.get("embedding"))
            if (
                vec is not None
                and int(person_id) in known_ids
                and match_mod.nn_disagrees_with_person(
                    vec,
                    int(person_id),
                    gallery,
                    exclude_face_ids={int(face["id"])},
                )
            ):
                skipped_lookalike += 1
                continue
            keep.append(int(face["id"]))
            claimed.setdefault(int(person_id), []).append(face)
        if not keep:
            return ClusterAssignReport(
                assigned=0,
                considered=considered,
                skipped_protected=skipped_protected,
                skipped_already=skipped_already,
                skipped_lookalike=skipped_lookalike,
            )
        cur = conn.execute(
            f"UPDATE faces SET person_id = ?, assigned_how = 'cluster' WHERE id IN ({','.join('?' * len(keep))})",
            (person_id, *keep),
        )
        marks = ",".join("?" * len(keep))
        conn.execute(
            f"""
            UPDATE clusters SET status = 'named'
            WHERE status = 'unknown'
              AND id IN (SELECT cluster_id FROM faces WHERE id IN ({marks}) AND cluster_id IS NOT NULL)
              AND NOT EXISTS (
                SELECT 1 FROM faces f
                WHERE f.cluster_id = clusters.id
                  AND f.person_id IS NULL
                  AND f.quality = 'ok'
                  AND IFNULL(f.assigned_how, '') != 'junk'
              )
            """,
            keep,
        )
        if not wanted:
            conn.execute("UPDATE clusters SET status = 'named' WHERE id = ?", (cluster_id,))
        conn.commit()
        n = int(cur.rowcount)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces(keep)
    else:
        ids = list(keep)
        threading.Thread(target=_sync_sidecars_for_faces, args=(ids,), daemon=True).start()
    return ClusterAssignReport(
        assigned=n,
        considered=considered,
        skipped_protected=skipped_protected,
        skipped_already=skipped_already,
        skipped_lookalike=skipped_lookalike,
    )


def revoke_cluster_names(person_id: int, *, sync_sidecars: bool = True) -> int:
    """Undo To-name group stamps for one person. Manual names stay."""
    conn = connect()
    init_db(conn)
    try:
        rows = conn.execute(
            "SELECT id FROM faces WHERE person_id = ? AND assigned_how = 'cluster'",
            (int(person_id),),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        conn.execute(
            """
            UPDATE faces
            SET person_id = NULL, assigned_how = NULL, cluster_id = NULL
            WHERE person_id = ? AND assigned_how = 'cluster'
            """,
            (int(person_id),),
        )
        conn.commit()
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces(ids)
    else:
        threading.Thread(target=_sync_sidecars_for_faces, args=(ids,), daemon=True).start()
    return len(ids)


def folder_of(path: str) -> str:
    parts = [p for p in Path(path).parts if p and p != "/"]
    return parts[-2] if len(parts) >= 2 else (Path(path).name or "Photos")


def path_in_folder(path: str, wanted: str) -> bool:
    """True if this photo lives in `wanted`, including nested albums inside it.

    `wanted` may be a full path or a folder name. Name match looks at every
    path segment so Scanned_Album_1994 includes Set 3, not only files
    sitting in the container root.
    """
    text = str(wanted or "").strip()
    if not text:
        return False
    if "/" in text:
        prefix = text.rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return text in Path(path).parts


def visible_unnamed_summary() -> dict[str, Any]:
    """Unnamed faces and To name groups, ignoring preview copies and junk."""
    preview = preview_path_sql("ph.path")
    conn = connect()
    init_db(conn)
    try:
        faces = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
              AND {preview}
            """
        ).fetchone()["n"]
        clusters = conn.execute(
            f"""
            SELECT f.cluster_id AS id, COUNT(*) AS n
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            JOIN clusters c ON c.id = f.cluster_id
            WHERE f.person_id IS NULL
              AND f.quality = 'ok'
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
              AND c.status = 'unknown'
              AND {preview}
            GROUP BY f.cluster_id
            ORDER BY n DESC, id ASC
            """
        ).fetchall()
        top = clusters[0] if clusters else None
        return {
            "faces": int(faces or 0),
            "clusters": len(clusters),
            "top_cluster_id": int(top["id"]) if top else None,
            "top_cluster_faces": int(top["n"]) if top else 0,
        }
    finally:
        conn.close()


def list_name_folders() -> list[dict[str, Any]]:
    """Indexed folders with how many named faces they still have."""
    from .originals import is_preview_path

    conn = connect()
    init_db(conn)
    try:
        photos = conn.execute("SELECT id, path, hidden FROM photos").fetchall()
        counts: dict[str, dict[str, int]] = {}
        photo_folder: dict[int, str] = {}
        for row in photos:
            if int(row["hidden"] or 0):
                continue
            if is_preview_path(row["path"]):
                continue
            parent = str(Path(row["path"]).parent)
            name = Path(parent).name or folder_of(row["path"])
            photo_folder[int(row["id"])] = parent
            bucket = counts.setdefault(
                parent, {"folder": name, "path": parent, "photos": 0, "named_faces": 0}
            )
            bucket["photos"] += 1
        for row in conn.execute(
            """
            SELECT photo_id, COUNT(*) AS n
            FROM faces
            WHERE person_id IS NOT NULL
            GROUP BY photo_id
            """
        ).fetchall():
            folder = photo_folder.get(int(row["photo_id"]))
            if folder and folder in counts:
                counts[folder]["named_faces"] += int(row["n"])
        return sorted(counts.values(), key=lambda r: (r["folder"].lower(), r["path"].lower()))
    finally:
        conn.close()


def _album_subdirs(path: Path) -> list[Path]:
    from .originals import is_preview_dir_name, skip_dir

    try:
        return [
            child
            for child in path.iterdir()
            if child.is_dir()
            and not child.name.startswith(".")
            and not child.name.startswith("._")
            and not is_preview_dir_name(child.name)
            and not skip_dir(child)
        ]
    except OSError:
        return []


def list_albums_under(folders: list[str] | None = None) -> list[dict[str, Any]]:
    """Albums under the selected folders, including ones not imported yet.

    A folder that itself contains albums is expanded so those nested albums
    show up. Photo counts include files in nested subfolders of that album.
    Preview-size copies (1024 x 768) are skipped.
    """
    from .originals import is_preview_path

    roots = [Path(item).expanduser() for item in (folders or []) if str(item or "").strip()]
    if not roots:
        return list_name_folders()

    albums: list[tuple[Path, Path | None]] = []
    groups_for: dict[str, Path | None] = {}
    seen: set[str] = set()

    def add_album(path: Path, group: Path | None = None) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            albums.append((path, group))
        elif group is not None and groups_for.get(key) is None:
            groups_for[key] = group
        if key not in groups_for:
            groups_for[key] = group

    for root in roots:
        if not root.is_dir():
            add_album(root)
            continue
        children = _album_subdirs(root)
        if not children:
            add_album(root)
            continue
        for child in children:
            add_album(child)

    conn = connect()
    init_db(conn)
    try:
        photos = conn.execute("SELECT id, path, hidden FROM photos").fetchall()
        named = {
            int(row["photo_id"]): int(row["n"])
            for row in conn.execute(
                """
                SELECT photo_id, COUNT(*) AS n
                FROM faces
                WHERE person_id IS NOT NULL
                GROUP BY photo_id
                """
            )
        }
    finally:
        conn.close()

    root_s = [(root, str(root).rstrip("/")) for root in roots]
    for row in photos:
        path = row["path"]
        if int(row["hidden"] or 0):
            continue
        if is_preview_path(path):
            continue
        parent = Path(path).parent
        for root, prefix in root_s:
            if path != prefix and not path.startswith(f"{prefix}/"):
                continue
            try:
                rel = parent.relative_to(root)
            except ValueError:
                break
            parts = rel.parts
            if len(parts) >= 2:
                group = root.joinpath(*parts[:1])
                album = root.joinpath(*parts[:2])
                add_album(group, group)
                add_album(album, group)
            elif len(parts) == 1:
                add_album(parent)
            else:
                add_album(root)
            break

    prefixes = []
    counts = {}
    for album, group in albums:
        group = groups_for.get(str(album), group)
        prefixes.append((album, str(album).rstrip("/"), group))
        counts[str(album)] = {
            "folder": album.name,
            "path": str(album),
            "photos": 0,
            "named_faces": 0,
            "group": group.name if group else "",
            "group_path": str(group) if group else "",
        }
    prefixes.sort(key=lambda item: -len(item[1]))
    for row in photos:
        path = row["path"]
        if int(row["hidden"] or 0):
            continue
        if is_preview_path(path):
            continue
        photo_id = int(row["id"])
        for album, prefix, _group in prefixes:
            if path == prefix or path.startswith(f"{prefix}/"):
                bucket = counts[str(album)]
                bucket["photos"] += 1
                bucket["named_faces"] += named.get(photo_id, 0)
                break

    items = list(counts.values())
    nested_groups = {
        row["group_path"]
        for row in items
        if row.get("group_path") and row["group_path"] != row["path"]
    }
    # Empty parent folders are groups, not unscanned albums.
    items = [
        row for row in items if not (row["photos"] == 0 and row["path"] in nested_groups)
    ]
    return sorted(
        items,
        key=lambda r: ((r.get("group") or r["folder"]).lower(), r["path"].lower()),
    )


def _reset_folder_names(*values: Any) -> list[str]:
    names: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            names.extend(_reset_folder_names(*value))
            continue
        text = str(value).strip()
        if text and text not in names:
            names.append(text)
    return names


def reset_names(folder: str | list[str] | None = None, folders: list[str] | None = None) -> dict[str, Any]:
    """Purge names from the app database. Photos, face boxes, statue marks, and
    each album's `.photosort.json` stay on disk.

    With folder(s) set, only faces in those album folders are unnamed. People who
    still have names in other folders are kept.
    """
    from .originals import is_preview_path

    wanted = _reset_folder_names(folders, folder)
    conn = connect()
    init_db(conn)
    try:
        people_before = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        photo_ids: list[int] = []
        if wanted:
            wanted_set = set(wanted)
            for row in conn.execute("SELECT id, path FROM photos").fetchall():
                if is_preview_path(row["path"]):
                    continue
                if any(path_in_folder(row["path"], name) for name in wanted_set):
                    photo_ids.append(int(row["id"]))
            if not photo_ids:
                label = ", ".join(wanted)
                raise ValueError(f"No indexed photos in folder {label}")
            placeholders = ",".join("?" * len(photo_ids))
            conn.execute(
                f"""
                UPDATE faces
                SET person_id = NULL, assigned_how = NULL, cluster_id = NULL
                WHERE photo_id IN ({placeholders})
                  AND IFNULL(assigned_how, '') != 'junk'
                """,
                photo_ids,
            )
        else:
            conn.execute(
                """
                UPDATE faces
                SET person_id = NULL, assigned_how = NULL, cluster_id = NULL
                WHERE IFNULL(assigned_how, '') != 'junk'
                """
            )
        conn.execute(
            """
            DELETE FROM people
            WHERE id NOT IN (SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)
            """
        )
        conn.execute(
            """
            DELETE FROM person_merges
            WHERE source_id NOT IN (SELECT id FROM people)
               OR target_id NOT IN (SELECT id FROM people)
            """
        )
        conn.execute("DELETE FROM clusters WHERE status != 'junk'")
        people_after = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        conn.commit()
    finally:
        conn.close()
    from .cluster import run_clustering

    clustered = run_clustering()
    return {
        "folder": wanted[0] if len(wanted) == 1 else (None if not wanted else wanted),
        "folders": wanted or None,
        "photos_cleared": len(photo_ids) if wanted else None,
        "people_removed": int(people_before) - int(people_after),
        "people_kept": int(people_after),
        "clusters": clustered.get("clusters", 0),
        "writes_originals": False,
        "writes_sidecars": False,
    }


AUTO_ASSIGN_HOWS = ("auto", "sidecar")


def search_catalog(query: str, limit: int = 48) -> dict[str, Any]:
    """People and photos whose names match the typed query."""
    from .originals import is_preview_path

    needle = (query or "").strip()
    people = []
    photos: list[dict[str, Any]] = []
    if needle:
        wanted = needle.casefold()
        people = [p for p in list_people() if person_matches_query(p, wanted)]
        ids = {int(p["id"]) for p in people}
        conn = connect()
        init_db(conn)
        try:
            rows = conn.execute(
                """
                SELECT ph.id, ph.path, ph.taken_at, ph.width, ph.height, ph.sha256, ph.scanned_at,
                       f.id AS face_id, f.person_id, p.name AS person_name
                FROM photos ph
                JOIN faces f ON f.photo_id = ph.id
                JOIN people p ON p.id = f.person_id
                WHERE f.person_id IS NOT NULL
                ORDER BY ph.taken_at IS NULL, ph.taken_at, ph.id
                """
            ).fetchall()
        finally:
            conn.close()
        seen: set[int] = set()
        for row in rows:
            if is_preview_path(row["path"]):
                continue
            if int(row["person_id"]) not in ids:
                continue
            pid = int(row["id"])
            if pid in seen:
                continue
            seen.add(pid)
            photos.append(
                {
                    "id": pid,
                    "path": row["path"],
                    "taken_at": row["taken_at"],
                    "width": row["width"],
                    "height": row["height"],
                    "sha256": row["sha256"],
                    "scanned_at": row["scanned_at"],
                    "match_person_id": int(row["person_id"]),
                    "match_person_name": row["person_name"],
                    "cover_face_id": int(row["face_id"]),
                }
            )
            if len(photos) >= limit:
                break
    return {"query": needle, "people": people, "photos": photos}


def search_photos(query: str, limit: int = 48) -> dict[str, Any]:
    """Photos whose filename or folder path match the typed query."""
    from .originals import is_preview_path

    needle = (query or "").strip()
    photos: list[dict[str, Any]] = []
    if needle:
        conn = connect()
        init_db(conn)
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM photos
                WHERE path LIKE ?
                   OR IFNULL(comment, '') LIKE ?
                   OR id IN (
                        SELECT photo_id FROM faces WHERE IFNULL(comment, '') LIKE ?
                   )
                ORDER BY taken_at IS NULL, taken_at, id
                """,
                (f"%{needle}%", f"%{needle}%", f"%{needle}%"),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            if is_preview_path(row["path"]):
                continue
            photos.append(dict(row))
            if len(photos) >= limit:
                break
    return {"query": needle, "people": [], "photos": photos}


REVIEW_PAGE = 24
REVIEW_MORE_CAP = 2000

# One Check names card per person+photo (or byte-identical SHA copy).
_REVIEW_PHOTO_KEY = """
    CASE
      WHEN IFNULL(ph.sha256, '') != '' AND ph.sha256 NOT LIKE 'pending:%'
      THEN ph.sha256
      ELSE 'p:' || f.photo_id
    END
"""


def list_auto_faces(
    person_id: int | None = None,
    offset: int = 0,
    limit: int | None = None,
    after_id: int | None = None,
) -> list[dict[str, Any]]:
    """Faces the matcher named, still waiting for a keep/reject.

    `limit` caps how many photos are returned per person (Check names first paint).
    `after_id` returns later photos for that person (Show more), ignoring `offset`.
    Duplicate detections on the same picture are one card.
    """
    hows = ",".join("?" * len(AUTO_ASSIGN_HOWS))
    preview = preview_path_sql("ph.path")
    photo_key = _REVIEW_PHOTO_KEY
    conn = connect()
    init_db(conn)
    try:
        where = f"""
            f.person_id IS NOT NULL
            AND f.assigned_how IN ({hows})
            AND IFNULL(ph.hidden, 0) = 0
            AND {preview}
        """
        params: list[Any] = list(AUTO_ASSIGN_HOWS)
        if person_id is not None:
            where += " AND f.person_id = ?"
            params.append(int(person_id))
        counts = conn.execute(
            f"""
            SELECT f.person_id, COUNT(DISTINCT {photo_key}) AS n
            FROM faces f
            JOIN people p ON p.id = f.person_id
            JOIN photos ph ON ph.id = f.photo_id
            WHERE {where}
            GROUP BY f.person_id
            ORDER BY p.name COLLATE NOCASE, f.person_id
            """,
            params,
        ).fetchall()
        order = [int(row["person_id"]) for row in counts]
        totals = {int(row["person_id"]): int(row["n"]) for row in counts}
        if not order:
            return []
        ranked = f"""
            SELECT f.id, f.photo_id, f.person_id, ph.path, ph.taken_at,
                   {photo_key} AS photo_key,
                   ROW_NUMBER() OVER (
                     PARTITION BY f.person_id, {photo_key}
                     ORDER BY IFNULL(f.det_score, 0) DESC,
                              (IFNULL(f.x2, 0) - IFNULL(f.x1, 0))
                              * (IFNULL(f.y2, 0) - IFNULL(f.y1, 0)) DESC,
                              f.id
                   ) AS photo_rn
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            JOIN people p ON p.id = f.person_id
            WHERE {where}
        """
        if limit is None:
            face_sql = f"""
                SELECT id, photo_id, person_id, path, taken_at, photo_key
                FROM ({ranked}) t
                WHERE t.photo_rn = 1
                ORDER BY t.person_id, t.id
            """
            face_params: list[Any] = params
        elif after_id is not None:
            lim = max(1, int(limit))
            face_sql = f"""
                SELECT id, photo_id, person_id, path, taken_at, photo_key
                FROM ({ranked}) t
                WHERE t.photo_rn = 1 AND t.id > ?
                ORDER BY t.person_id, t.id
                LIMIT ?
            """
            face_params = [*params, int(after_id), lim]
        else:
            off = max(0, int(offset or 0))
            lim = max(1, int(limit))
            face_sql = f"""
                SELECT id, photo_id, person_id, path, taken_at, photo_key FROM (
                    SELECT id, photo_id, person_id, path, taken_at, photo_key,
                           ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY id) AS rn
                    FROM ({ranked}) u
                    WHERE u.photo_rn = 1
                ) t
                WHERE t.rn > ? AND t.rn <= ?
                ORDER BY t.person_id, t.id
            """
            face_params = [*params, off, off + lim]
        rows = [dict(r) for r in conn.execute(face_sql, face_params)]
        siblings: dict[tuple[int, str], list[int]] = {}
        if rows:
            sibs = conn.execute(
                f"""
                SELECT f.id, f.person_id, {photo_key} AS photo_key
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                JOIN people p ON p.id = f.person_id
                WHERE {where}
                """,
                params,
            )
            for sib in sibs:
                key = (int(sib["person_id"]), str(sib["photo_key"]))
                siblings.setdefault(key, []).append(int(sib["id"]))
        by_person: dict[int, list[dict[str, Any]]] = {pid: [] for pid in order}
        for face in rows:
            key = (int(face["person_id"]), str(face["photo_key"]))
            ids = sorted(set(siblings.get(key) or [int(face["id"])]))
            face["face_ids"] = ids
            by_person.setdefault(int(face["person_id"]), []).append(face)
        people_rows: dict[int, dict[str, Any]] = {}
        marks = ",".join("?" * len(order))
        for person in conn.execute(f"SELECT * FROM people WHERE id IN ({marks})", order):
            people_rows[int(person["id"])] = dict(person)
    finally:
        conn.close()
    groups = []
    for pid in order:
        person = people_rows.get(pid)
        if not person:
            continue
        groups.append(
            {
                "person": person,
                "faces": by_person.get(pid) or [],
                "face_count": totals.get(pid, 0),
            }
        )
    return groups


def _expand_auto_ids_on_same_photos(conn, ids: list[int]) -> list[int]:
    """Keep/reject one Check names card applies to every auto face on that picture."""
    if not ids:
        return []
    hows = ",".join("?" * len(AUTO_ASSIGN_HOWS))
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT f.id, f.person_id, f.photo_id, ph.sha256
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        WHERE f.id IN ({marks})
        """,
        ids,
    ).fetchall()
    extra: set[int] = {int(x) for x in ids}
    for row in rows:
        if row["person_id"] is None:
            continue
        sha = str(row["sha256"] or "")
        found = conn.execute(
            f"""
            SELECT f.id
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id = ?
              AND f.assigned_how IN ({hows})
              AND (
                f.photo_id = ?
                OR (
                  ? != ''
                  AND ph.sha256 = ?
                  AND ph.sha256 NOT LIKE 'pending:%'
                )
              )
            """,
            (int(row["person_id"]), *AUTO_ASSIGN_HOWS, int(row["photo_id"]), sha, sha),
        ).fetchall()
        extra.update(int(item["id"]) for item in found)
    return sorted(extra)


def confirm_faces(face_ids: list[int] | None = None, person_id: int | None = None) -> int:
    """Mark auto/sidecar names as checked. Does not rematch."""
    from .originals import is_preview_path

    hows = ",".join("?" * len(AUTO_ASSIGN_HOWS))
    conn = connect()
    init_db(conn)
    try:
        ids: list[int] = []
        if face_ids:
            ids = _expand_auto_ids_on_same_photos(conn, [int(x) for x in face_ids])
        elif person_id:
            rows = conn.execute(
                f"""
                SELECT f.id, ph.path
                FROM faces f
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.person_id = ?
                  AND f.assigned_how IN ({hows})
                """,
                (int(person_id), *AUTO_ASSIGN_HOWS),
            ).fetchall()
            ids = [int(r["id"]) for r in rows if not is_preview_path(r["path"])]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""
            UPDATE faces
            SET assigned_how = 'manual'
            WHERE id IN ({marks})
              AND person_id IS NOT NULL
              AND assigned_how IN ({hows})
            """,
            (*ids, *AUTO_ASSIGN_HOWS),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def auto_face_count() -> int:
    """How many auto-named photos still need a keep/reject. Same filter as list_auto_faces()."""
    hows = ",".join("?" * len(AUTO_ASSIGN_HOWS))
    preview = preview_path_sql("ph.path")
    photo_key = _REVIEW_PHOTO_KEY
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM (
                SELECT 1
                FROM faces f
                JOIN people p ON p.id = f.person_id
                JOIN photos ph ON ph.id = f.photo_id
                WHERE f.person_id IS NOT NULL
                  AND f.assigned_how IN ({hows})
                  AND IFNULL(ph.hidden, 0) = 0
                  AND {preview}
                GROUP BY f.person_id, {photo_key}
            )
            """,
            AUTO_ASSIGN_HOWS,
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def reset_matching(folder: str | list[str] | None = None, folders: list[str] | None = None) -> dict[str, Any]:
    """Undo auto-matched names. Manual, cluster, and merge names stay."""
    from .originals import is_preview_path

    wanted = _reset_folder_names(folders, folder)
    hows = ",".join("?" * len(AUTO_ASSIGN_HOWS))
    conn = connect()
    init_db(conn)
    try:
        photo_ids: list[int] = []
        if wanted:
            wanted_set = set(wanted)
            for row in conn.execute("SELECT id, path FROM photos").fetchall():
                if is_preview_path(row["path"]):
                    continue
                if any(path_in_folder(row["path"], name) for name in wanted_set):
                    photo_ids.append(int(row["id"]))
            if not photo_ids:
                label = ", ".join(wanted)
                raise ValueError(f"No indexed photos in folder {label}")
            placeholders = ",".join("?" * len(photo_ids))
            cur = conn.execute(
                f"""
                UPDATE faces
                SET person_id = NULL, assigned_how = NULL, cluster_id = NULL
                WHERE photo_id IN ({placeholders})
                  AND assigned_how IN ({hows})
                """,
                (*photo_ids, *AUTO_ASSIGN_HOWS),
            )
        else:
            cur = conn.execute(
                f"""
                UPDATE faces
                SET person_id = NULL, assigned_how = NULL, cluster_id = NULL
                WHERE assigned_how IN ({hows})
                """
            )
        cleared = int(cur.rowcount or 0)
        people_before = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        conn.execute(
            """
            DELETE FROM people
            WHERE id NOT IN (SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)
            """
        )
        conn.execute(
            """
            DELETE FROM person_merges
            WHERE source_id NOT IN (SELECT id FROM people)
               OR target_id NOT IN (SELECT id FROM people)
            """
        )
        people_after = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        conn.commit()
    finally:
        conn.close()
    from .cluster import run_clustering
    from . import sidecar as sidecar_mod

    if wanted:
        sidecar_mod.write_for_photo_ids(photo_ids)
    else:
        sidecar_mod.write_all()
    clustered = run_clustering()
    return {
        "folder": wanted[0] if len(wanted) == 1 else (None if not wanted else wanted),
        "folders": wanted or None,
        "faces_cleared": cleared,
        "people_removed": int(people_before) - int(people_after),
        "people_kept": int(people_after),
        "clusters": clustered.get("clusters", 0),
        "writes_originals": False,
    }


def restore_faces(face_ids: list[int]) -> int:
    """Undo Not a person so the face can be named again."""
    ids = [int(fid) for fid in (face_ids or []) if fid]
    if not ids:
        return 0
    conn = connect()
    init_db(conn)
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""
            UPDATE faces
            SET quality = 'ok', assigned_how = NULL
            WHERE id IN ({marks})
              AND assigned_how = 'junk'
            """,
            ids,
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def set_face_tag(
    face_id: int,
    tag_x: float | None = None,
    tag_y: float | None = None,
    *,
    clear: bool = False,
    sync_sidecars: bool = True,
) -> dict[str, Any]:
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not row:
            raise KeyError("Face not found")
        if clear or tag_x is None or tag_y is None:
            conn.execute("UPDATE faces SET tag_x = NULL, tag_y = NULL WHERE id = ?", (face_id,))
        else:
            tx = max(0.0, min(100.0, float(tag_x)))
            ty = max(0.0, min(100.0, float(tag_y)))
            conn.execute("UPDATE faces SET tag_x = ?, tag_y = ? WHERE id = ?", (tx, ty, face_id))
        conn.commit()
        updated = conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        out = dict(updated)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces([face_id])
    return out


def set_face_comment(face_id: int, comment: str, *, sync_sidecars: bool = True) -> dict[str, Any]:
    """Store a catalog note on this face. The original file is not touched."""
    text = str(comment or "").strip()
    if len(text) > 4000:
        text = text[:4000].rstrip()
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not row:
            raise KeyError("Face not found")
        conn.execute("UPDATE faces SET comment = ? WHERE id = ?", (text, face_id))
        conn.commit()
        updated = conn.execute(
            """
            SELECT f.*, p.name AS person_name
            FROM faces f
            LEFT JOIN people p ON p.id = f.person_id
            WHERE f.id = ?
            """,
            (face_id,),
        ).fetchone()
        out = dict(updated)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces([face_id])
    return out


def junk_faces(face_ids: list[int], *, sync_sidecars: bool = True) -> int:
    """Mark these detections as not a person (statue, painting, object)."""
    ids = [int(fid) for fid in (face_ids or []) if fid]
    if not ids:
        return 0
    conn = connect()
    init_db(conn)
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""
            UPDATE faces
            SET person_id = NULL, cluster_id = NULL,
                quality = 'unidentifiable', assigned_how = 'junk'
            WHERE id IN ({marks})
            """,
            ids,
        )
        conn.commit()
        n = int(cur.rowcount)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces(ids)
    return n


def junk_cluster(
    cluster_id: int,
    face_ids: list[int] | None = None,
    *,
    sync_sidecars: bool = True,
) -> int:
    """Mark this group as not a person and keep it out of future grouping."""
    ids: list[int] = []
    conn = connect()
    init_db(conn)
    try:
        wanted = [int(fid) for fid in (face_ids or []) if fid]
        if wanted:
            ids = wanted
            marks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"""
                UPDATE faces
                SET cluster_id = NULL, quality = 'unidentifiable', assigned_how = 'junk'
                WHERE id IN ({marks})
                """,
                ids,
            )
        else:
            ids = [
                int(r["id"])
                for r in conn.execute("SELECT id FROM faces WHERE cluster_id = ?", (cluster_id,)).fetchall()
            ]
            cur = conn.execute(
                """
                UPDATE faces
                SET cluster_id = NULL, quality = 'unidentifiable', assigned_how = 'junk'
                WHERE cluster_id = ?
                """,
                (cluster_id,),
            )
        conn.execute("UPDATE clusters SET status = 'junk' WHERE id = ?", (cluster_id,))
        conn.commit()
        n = int(cur.rowcount)
    finally:
        conn.close()
    if sync_sidecars:
        _sync_sidecars_for_faces(ids)
    else:
        threading.Thread(target=_sync_sidecars_for_faces, args=(list(ids),), daemon=True).start()
    return n


def split_cluster(cluster_id: int, face_ids: list[int]) -> int:
    """Pull faces out of a cluster into their own unknown cluster."""
    if not face_ids:
        return 0
    conn = connect()
    init_db(conn)
    try:
        cur = conn.execute(
            "INSERT INTO clusters (status, created_at) VALUES ('unknown', ?)",
            (now_iso(),),
        )
        new_id = int(cur.lastrowid)
        conn.executemany(
            "UPDATE faces SET cluster_id = ? WHERE id = ? AND cluster_id = ?",
            [(new_id, fid, cluster_id) for fid in face_ids],
        )
        conn.commit()
        return new_id
    finally:
        conn.close()


def merge_people(source_id: int, target_id: int) -> dict[str, Any]:
    if source_id == target_id:
        raise ValueError("Cannot merge a person into themselves")
    conn = connect()
    init_db(conn)
    try:
        src = conn.execute("SELECT * FROM people WHERE id = ?", (source_id,)).fetchone()
        dst = conn.execute("SELECT * FROM people WHERE id = ?", (target_id,)).fetchone()
        if not src or not dst:
            raise ValueError("Person not found")
        conn.execute(
            "UPDATE faces SET person_id = ?, assigned_how = 'merge' WHERE person_id = ?",
            (target_id, source_id),
        )
        conn.execute(
            "INSERT INTO person_merges (source_id, target_id, source_name, created_at) VALUES (?, ?, ?, ?)",
            (source_id, target_id, src["name"], now_iso()),
        )
        notes = dst["notes"] or ""
        extra = f"Merged '{src['name']}' into this identity."
        merged_notes = (notes + "\n" + extra).strip() if extra not in notes else notes
        dst_cat = normalize_category(dst["category"] if "category" in dst.keys() else "")
        src_cat = normalize_category(src["category"] if "category" in src.keys() else "")
        keep_cat = dst_cat or src_cat
        keep_nick = normalize_nickname(
            ", ".join(
                [
                    dst["nickname"] if "nickname" in dst.keys() else "",
                    src["nickname"] if "nickname" in src.keys() else "",
                ]
            )
        )
        conn.execute(
            "UPDATE people SET notes = ?, category = ?, nickname = ? WHERE id = ?",
            (merged_notes, keep_cat, keep_nick, target_id),
        )
        conn.execute("DELETE FROM people WHERE id = ?", (source_id,))
        conn.commit()
    finally:
        conn.close()

    def follow_up() -> None:
        try:
            _sync_sidecars_for_people([target_id])
        except Exception:
            pass

    if os.environ.get("PYTEST_CURRENT_TEST"):
        follow_up()
    else:
        threading.Thread(target=follow_up, daemon=True, name="photosort-merge-sidecar").start()
    person = get_person(target_id)
    if not person:
        raise ValueError("Merge target missing after merge")
    return person


def _strip_merge_note(notes: str, name: str) -> str:
    line = f"Merged '{name}' into this identity."
    kept = [row for row in (notes or "").splitlines() if row.strip() and row.strip() != line]
    return "\n".join(kept).strip()


def split_person_cluster(person_id: int, cluster_id: int, name: str) -> dict[str, Any]:
    """Move one original name-group out of a person into its own identity."""
    name = name.strip()
    if not name:
        raise ValueError("Name is required")
    conn = connect()
    init_db(conn)
    try:
        src = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if not src:
            raise ValueError("Person not found")
        remaining = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE person_id = ? AND quality = 'ok'
              AND (cluster_id IS NULL OR cluster_id != ?)
            """,
            (person_id, cluster_id),
        ).fetchone()["n"]
        moving = conn.execute(
            """
            SELECT COUNT(*) AS n FROM faces
            WHERE person_id = ? AND cluster_id = ? AND quality = 'ok'
            """,
            (person_id, cluster_id),
        ).fetchone()["n"]
        if moving <= 0:
            raise ValueError("No faces in that group")
        if remaining <= 0:
            raise ValueError("That would leave this person with no photos")
        cur = conn.execute(
            "INSERT INTO people (name, notes, birth_year, created_at) VALUES (?, ?, ?, ?)",
            (name, "", None, now_iso()),
        )
        new_id = int(cur.lastrowid)
        conn.execute(
            """
            UPDATE faces
            SET person_id = ?, assigned_how = 'split'
            WHERE person_id = ? AND cluster_id = ? AND quality = 'ok'
            """,
            (new_id, person_id, cluster_id),
        )
        notes = _strip_merge_note(src["notes"] or "", name)
        conn.execute("UPDATE people SET notes = ? WHERE id = ?", (notes, person_id))
        conn.commit()
    finally:
        conn.close()
    _sync_sidecars_for_people([person_id, new_id])
    person = get_person(new_id)
    if not person:
        raise ValueError("Split person missing after split")
    return person


def _sync_sidecars_for_faces(face_ids: list[int]) -> None:
    if not face_ids:
        return
    from . import sidecar as sidecar_mod

    sidecar_mod.write_for_face_ids(face_ids)


def _sync_sidecars_for_people(person_ids: list[int]) -> None:
    if not person_ids:
        return
    from . import sidecar as sidecar_mod

    sidecar_mod.write_for_person_ids(person_ids)


def _centroids_from_face_rows(
    rows, allow_ids: set[int] | None = None
) -> dict[int, dict[str, np.ndarray]]:
    buckets: dict[int, dict[str, list[np.ndarray]]] = {}
    all_vecs: dict[int, list[np.ndarray]] = {}
    for row in rows:
        pid = int(row["person_id"])
        if allow_ids is not None and pid not in allow_ids:
            continue
        vec = bytes_to_embedding(row["embedding"])
        if vec is None or getattr(vec, "size", 0) == 0:
            continue
        vec = l2_normalize(vec)
        all_vecs.setdefault(pid, []).append(vec)
        buckets.setdefault(pid, {}).setdefault(age_band(row["age_est"]), []).append(vec)
    out: dict[int, dict[str, np.ndarray]] = {}
    for pid, vecs in all_vecs.items():
        item = {"all": l2_normalize(np.mean(np.stack(vecs), axis=0))}
        for band, bvecs in buckets.get(pid, {}).items():
            item[band] = l2_normalize(np.mean(np.stack(bvecs), axis=0))
        out[pid] = item
    return out


def person_centroids(conn, person_id: int) -> dict[str, np.ndarray]:
    rows = conn.execute(
        """
        SELECT person_id, embedding, age_est FROM faces
        WHERE person_id = ? AND quality = 'ok' AND embedding IS NOT NULL
        """,
        (person_id,),
    ).fetchall()
    return _centroids_from_face_rows(rows).get(int(person_id), {})


def all_person_centroids(conn=None) -> dict[int, dict[str, np.ndarray]]:
    own = conn is None
    if own:
        conn = connect()
        init_db(conn)
    try:
        allow = {
            int(p["id"])
            for p in conn.execute("SELECT id, name FROM people")
            if not is_unknown_name(p["name"])
        }
        rows = conn.execute(
            """
            SELECT person_id, embedding, age_est FROM faces
            WHERE person_id IS NOT NULL AND quality = 'ok' AND embedding IS NOT NULL
              AND IFNULL(assigned_how, '') NOT IN ('junk', 'auto', 'cleared')
            """
        ).fetchall()
        return _centroids_from_face_rows(rows, allow)
    finally:
        if own:
            conn.close()
