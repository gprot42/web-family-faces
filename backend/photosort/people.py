from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .config import CHILD_AGE, CLUSTER_PREVIEW_LIMIT, CROP_DIR, CROP_PAD, ELDER_AGE, IMAGE_EXTS, TEEN_AGE
from .db import connect, init_db
from .originals import drop_preview_rows, preview_path_sql
from .util import bytes_to_embedding, l2_normalize, now_iso

_COVER_CANDIDATES = 64
_COVER_RECENT = 16
_COVER_LARGE = 8


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
TO_NAME_MIN_FACES = 2


def to_name_cluster_sql() -> str:
    """Groups on To name: two or more unnamed faces, not junk, not hidden, not preview copies."""
    preview = preview_path_sql("ph.path")
    return f"""
        FROM clusters c
        JOIN faces f ON f.cluster_id = c.id
        JOIN photos ph ON ph.id = f.photo_id
        WHERE c.status != 'junk'
          AND f.person_id IS NULL
          AND f.quality = 'ok'
          AND IFNULL(f.assigned_how, '') != 'junk'
          AND IFNULL(ph.hidden, 0) = 0
          AND {preview}
        GROUP BY c.id
        HAVING COUNT(f.id) >= {int(TO_NAME_MIN_FACES)}
    """


def _named_visible_sql(face_alias: str = "f", photo_alias: str = "ph") -> str:
    """Faces named as this person on a real album photo: include blurry manual tags."""
    return (
        f"{face_alias}.person_id IS NOT NULL"
        f" AND IFNULL({face_alias}.assigned_how, '') != 'junk'"
        f" AND IFNULL({photo_alias}.hidden, 0) = 0"
        f" AND {preview_path_sql(f'{photo_alias}.path')}"
    )


def _named_people_stats(conn) -> dict[int, dict[str, Any]]:
    """Photo counts match the person page: named faces, one shot per picture."""
    rows = conn.execute(
        f"""
        SELECT f.person_id, f.id, f.photo_id, f.det_score, f.age_est, ph.path, ph.taken_at, ph.sha256
        FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        WHERE {_named_visible_sql()}
        """
    ).fetchall()
    by_person: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_person.setdefault(int(row["person_id"]), []).append(dict(row))
    out: dict[int, dict[str, Any]] = {}
    for pid, faces in by_person.items():
        taken = [f["taken_at"] for f in faces if f.get("taken_at")]
        ages = [float(f["age_est"]) for f in faces if f.get("age_est") is not None]
        out[pid] = {
            "face_count": len(display_faces(faces)),
            "first_seen": min(taken) if taken else None,
            "last_seen": max(taken) if taken else None,
            "age_min": min(ages) if ages else None,
            "age_max": max(ages) if ages else None,
        }
    return out


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


def _cover_crop_box(face: Any) -> tuple[float, float, float, float]:
    """Padded square used as the Faces in DB View cover, not just the detector box."""
    x1, y1 = float(face["x1"] or 0), float(face["y1"] or 0)
    x2, y2 = float(face["x2"] or 0), float(face["y2"] or 0)
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    half = max(bw, bh) * (1.0 + 2.0 * CROP_PAD) / 2.0
    return cx - half, cy - half, cx + half, cy + half


def _occlusion(face: Any, others: list[Any] | None, *, crowd: bool = True) -> float:
    """0 = clear view, 1 = another face covers this one."""
    others = others or []
    area = _face_area(face)
    fx1, fy1 = float(face["x1"] or 0), float(face["y1"] or 0)
    fx2, fy2 = float(face["x2"] or 0), float(face["y2"] or 0)
    fcy = (fy1 + fy2) / 2.0
    penalty = 0.0
    fid = int(face["id"])
    try:
        self_pid = int(face["person_id"]) if face["person_id"] is not None else None
    except (KeyError, TypeError):
        self_pid = None

    def _other_is_self(other: Any) -> bool:
        if int(other["id"]) == fid:
            return True
        if self_pid is None:
            return False
        try:
            return other["person_id"] is not None and int(other["person_id"]) == self_pid
        except (KeyError, TypeError):
            return False

    for other in others:
        if _other_is_self(other):
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
    px1, py1, px2, py2 = _cover_crop_box(face)
    pw = max(1.0, px2 - px1)
    ph = max(1.0, py2 - py1)
    for other in others:
        if _other_is_self(other):
            continue
        ox1, oy1 = float(other["x1"] or 0), float(other["y1"] or 0)
        ox2, oy2 = float(other["x2"] or 0), float(other["y2"] or 0)
        oh = max(1.0, oy2 - oy1)
        ow = max(1.0, ox2 - ox1)
        # Detector boxes sit on the face, so hair and forehead sit above y1
        # and still show in the padded cover crop.
        oy1 -= 0.28 * oh
        ox1 -= 0.20 * ow
        ox2 += 0.20 * ow
        ocx = (ox1 + ox2) / 2.0
        ocy = (oy1 + oy2) / 2.0
        ix1, iy1 = max(px1, ox1), max(py1, oy1)
        ix2, iy2 = min(px2, ox2), min(py2, oy2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if inter <= 0:
            continue
        crop_frac = inter / (pw * ph)
        in_crop = px1 < ocx < px2 and py1 < ocy < py2
        lower = ocy >= py1 + 0.42 * ph
        if in_crop and lower:
            penalty = max(penalty, 0.85)
        elif in_crop:
            penalty = max(penalty, 0.45)
        elif lower and crop_frac >= 0.02:
            penalty = max(penalty, min(1.0, 0.5 + 2.0 * crop_frac))
        elif crop_frac >= 0.04:
            # Neighbor whose centre sits just outside the pad still shows in the crop
            # (group-shot shoulder / cap on the right of a portrait).
            penalty = max(penalty, 0.45)
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
        SELECT id, photo_id, person_id, x1, y1, x2, y2 FROM faces
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


_look_cache: dict[tuple[str, int], tuple[float, float, float, float, float, float, float]] = {}


def _smile_from_small(arr: np.ndarray) -> float:
    """1 = visible teeth/smile in the mouth band, 0 = closed mouth. 32x32 RGB."""
    if arr.shape[0] < 24 or arr.shape[1] < 24:
        return 0.0
    band = arr[17:25, 6:26].astype(np.float32)
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    teeth = (luma >= 160) & (chroma <= 48)
    row_frac = teeth.mean(axis=1)
    best_row = float(row_frac.max()) if row_frac.size else 0.0
    mid_teeth = float(teeth[:, 4:16].mean()) if teeth.size else 0.0
    width = 0.0
    if bool(teeth.any()):
        xs = np.where(teeth)[1]
        width = (float(xs.max()) - float(xs.min()) + 1.0) / max(1, band.shape[1])
    raw = 0.35 * best_row + 0.55 * mid_teeth + 0.10 * min(width, 0.7)
    # Ignore faint teeth-from-a-squint; a real grin has a wide bright mouth band.
    if best_row >= 0.28 and mid_teeth >= 0.08:
        return min(1.0, raw)
    return 0.0


def _grown_from_small(arr: np.ndarray) -> float:
    """1 = adult lower face (beard/shadow on the chin), 0 = child or a dark collar."""
    if arr.shape[0] < 24 or arr.shape[1] < 24:
        return 0.0
    a = arr.astype(np.float32)
    luma = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
    mid = float(luma[12:20, 10:22].mean())
    chin = luma[22:29, 12:20]
    lower = luma[22:31, :]
    if chin.size == 0 or lower.size == 0:
        return 0.0
    chin_m = float(chin.mean())
    chin_dark = float((chin < mid - 15).mean())
    lower_dark = float((lower < mid - 15).mean())
    if mid - chin_m >= 22 and 0.25 <= lower_dark <= 0.55 and chin_dark >= 0.45:
        return 1.0
    return 0.0


def _front_from_small(arr: np.ndarray) -> float:
    """1 = looking at the camera, 0 = profile or turned away."""
    if arr.shape[0] < 24 or arr.shape[1] < 24:
        return 0.45
    luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    band = luma[7:15, :]
    if band.shape[1] >= 29:
        left_min = float(band[:, 3:14].min())
        right_min = float(band[:, 18:29].min())
        if abs(left_min - right_min) >= 80 and max(left_min, right_min) >= 120:
            return 0.0
    inner = luma[:, 8:24]
    if inner.shape[1] < 16:
        return 1.0
    left = inner[:, :8]
    right = np.fliplr(inner[:, 8:16])
    diff = float(np.mean(np.abs(left.astype(np.float32) - right.astype(np.float32))))
    if diff >= 58:
        return 0.0
    if diff >= 48:
        return 0.4
    return 1.0


def _sharp_from_gray(gray: np.ndarray) -> float:
    """1 = in-focus crop, 0 = smear. Laplacian variance on a 64x64 luma grid."""
    if gray.size < 16:
        return 0.0
    g = np.pad(gray.astype(np.float32), 1, mode="edge")
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]
    var = float(lap.var())
    if var >= 520:
        return 1.0
    if var >= 220:
        return 0.35
    return 0.0


def _crop_look_scores(face_id: int) -> tuple[float, float, float, float, float, float, float]:
    """(color, lit, view, smile, sharp, grown, front) 0-1 from one crop read. Missing crops stay neutral."""
    path = CROP_DIR / f"{int(face_id)}.jpg"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return 0.5, 0.5, 0.45, 0.0, 0.5, 0.0, 0.45
    key = (str(path), mtime)
    cached = _look_cache.get(key)
    if cached:
        return cached
    try:
        from PIL import Image

        img = Image.open(path)
        img.load()
        rgb = img.convert("RGB")
        small = rgb.resize((32, 32), Image.Resampling.BILINEAR)
        mid = rgb.resize((64, 64), Image.Resampling.BILINEAR)
        img.close()
        raw = small.tobytes()
        arr = np.asarray(small, dtype=np.uint8)
        mid_arr = np.asarray(mid, dtype=np.float32)
    except Exception:
        return 0.5, 0.5, 0.45, 0.0, 0.5, 0.0, 0.45
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
    # Faces in DB View covers should be readable: dim indoor shots lose to well-lit ones.
    if mean < 72:
        lit = 0.0
        bright = 0.18 * (mean / 72.0)
    elif mean < 105:
        lit = 0.4
        bright = 0.25 + 0.4 * ((mean - 72) / 33.0)
    elif mean > 215:
        lit = 0.55
        bright = max(0.15, 1.0 - (mean - 215) / 40.0)
    else:
        lit = 1.0
        bright = 0.7 + 0.3 * (1.0 - abs(mean - 140) / 110.0)
    view = 0.7 * bright + 0.3 * min(1.0, std / 48.0)
    gray64 = 0.2126 * mid_arr[:, :, 0] + 0.7152 * mid_arr[:, :, 1] + 0.0722 * mid_arr[:, :, 2]
    sharp = _sharp_from_gray(gray64)
    clip = float(sum(1 for p in luma if p >= 242) / n)
    if clip >= 0.18:
        lit = 0.0
        sharp = 0.0
    elif clip >= 0.08:
        lit = min(lit, 0.4)
    front = _front_from_small(arr)
    smile = _smile_from_small(arr) if sharp and clip < 0.10 and front >= 0.25 else 0.0
    grown = _grown_from_small(arr)
    scores = (color, lit, view, smile, sharp, grown, front)
    _look_cache[key] = scores
    if len(_look_cache) > 8000:
        for old in list(_look_cache)[:2000]:
            _look_cache.pop(old, None)
    return scores


def _crop_view_score(face_id: int) -> float:
    """0-1: prefer mid-bright, contrasty crops over dark or washed-out ones."""
    return _crop_look_scores(face_id)[2]


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
    row_s = _row_sex(row)
    # Detector sex that already matches the name beats a noisy embedding split.
    looks = row_s if (want_sex and row_s == want_sex) else (looks_sex or row_s)
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
    color, lit, view, smile, sharp, grown, front = _crop_look_scores(int(row["id"]))
    if want_sex != "M":
        grown = 0.0
    return (
        unobscured,
        color,
        front,
        sharp,
        sex_match,
        grown,
        _cover_freshness(row),
        lit,
        smile,
        name_hit,
        1.0 - blocked,
        view,
        det,
        size,
    )


def _cover_freshness(row: Any) -> float:
    """Prefer recent camera originals over old prints and dated scans."""
    try:
        w = float(row["width"] or 0)
        h = float(row["height"] or 0)
    except (KeyError, TypeError, ValueError):
        w = h = 0.0
    mp = (w * h) / 1_000_000.0
    if mp >= 20:
        pixels = 1.0
    elif mp >= 12:
        pixels = 0.7
    elif mp >= 8:
        pixels = 0.4
    else:
        pixels = 0.15
    try:
        taken = row["taken_at"]
    except (KeyError, TypeError):
        taken = None
    year = str(taken or "")[:4]
    if year.isdigit():
        age = datetime.now().year - int(year)
        if age <= 2:
            when = 1.0
        elif age >= 12:
            when = 0.0
        else:
            when = max(0.0, 1.0 - (age - 2) / 10.0)
        if mp < 12:
            when *= 0.4
    else:
        when = 0.45
    return 0.55 * when + 0.45 * pixels


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


def _rank_cover_rows(
    cands: list[Any],
    neighbors: dict[int, list[Any]],
    want: str,
    means: Any,
    emb_by_id: dict[int, Any],
    named_ids: set[int],
    *,
    prefer_clear: bool = True,
) -> list[Any]:
    pool = list(cands)
    if prefer_clear:
        clear = [
            row
            for row in pool
            if _occlusion(row, neighbors.get(int(row["photo_id"])), crowd=False) < 0.28
        ]
        if clear:
            pool = clear
    return sorted(
        pool,
        key=lambda row, n=neighbors, w=want, m=means: _cover_rank(
            row,
            n.get(int(row["photo_id"])),
            w,
            _looks_sex(row, m, emb_by_id) if means else "",
            1.0 if int(row["id"]) in named_ids else 0.0,
        ),
        reverse=True,
    )


def _load_cover_candidates(
    conn, person_ids: list[int] | None = None, *, scan_embeddings: bool = True
) -> tuple[dict[int, list[Any]], dict[int, list[Any]], dict[int, str], dict[int, Any], dict[int, Any], set[int]]:
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
        SELECT person_id, id, photo_id, det_score, x1, y1, x2, y2, sex_est, taken_at, width, height FROM (
            SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2, f.sex_est,
                   ph.taken_at, ph.width, ph.height,
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
                   ) AS recent_rn,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.person_id
                       ORDER BY ((f.x2 - f.x1) * (f.y2 - f.y1)) DESC, f.det_score DESC, f.id
                   ) AS size_rn
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
        WHERE det_rn <= ? OR recent_rn <= ? OR size_rn <= ?
        """,
        (*params, *params, _COVER_CANDIDATES, _COVER_RECENT, _COVER_LARGE),
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
        SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2, f.sex_est,
               ph.taken_at, ph.width, ph.height
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
    return by_person, neighbors, names, centroids, emb_by_id, named_ids


def _best_cover_ids(
    conn, person_ids: list[int] | None = None, *, scan_embeddings: bool = True
) -> dict[int, int]:
    by_person, neighbors, names, centroids, emb_by_id, named_ids = _load_cover_candidates(
        conn, person_ids, scan_embeddings=scan_embeddings
    )
    picked: dict[int, int] = {}
    for pid, cands in by_person.items():
        want = _name_sex(names.get(pid, "")) or _majority_sex(cands)
        ranked = _rank_cover_rows(
            cands, neighbors, want, centroids.get(pid), emb_by_id, named_ids, prefer_clear=True
        )
        if ranked:
            picked[pid] = int(ranked[0]["id"])
    return picked


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_pinned_covers(conn, pinned: dict[int, int]) -> dict[int, int]:
    """Keep a stored cover only while that face still belongs to this person."""
    ids = [fid for fid in pinned.values() if fid]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT f.id, f.person_id FROM faces f
        JOIN photos ph ON ph.id = f.photo_id
        WHERE f.id IN ({marks})
          AND f.quality = 'ok'
          AND IFNULL(f.assigned_how, '') != 'junk'
          AND IFNULL(ph.hidden, 0) = 0
          AND {preview_path_sql("ph.path")}
        """,
        ids,
    ).fetchall()
    by_face = {int(row["id"]): int(row["person_id"]) for row in rows}
    return {pid: fid for pid, fid in pinned.items() if by_face.get(fid) == pid}


def _apply_cover_choice(
    conn, people: list[dict[str, Any]], auto: dict[int, int], fallback: dict[int, int]
) -> None:
    pinned: dict[int, int] = {}
    for person in people:
        pin = _int_or_none(person.get("cover_face_id"))
        if pin:
            pinned[int(person["id"])] = pin
    valid = _valid_pinned_covers(conn, pinned)
    for person in people:
        pid = int(person["id"])
        person["cover_face_id"] = valid.get(pid) or auto.get(pid) or fallback.get(pid)


def advance_person_cover(person_id: int) -> dict[str, Any] | None:
    """Pin the next-ranked cover crop for Faces in DB View. Repeats wrap around."""
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT * FROM people WHERE id = ?", (int(person_id),)).fetchone()
        if not row:
            return None
        pid = int(person_id)
        by_person, neighbors, names, centroids, emb_by_id, named_ids = _load_cover_candidates(
            conn, [pid], scan_embeddings=False
        )
        cands = by_person.get(pid) or []
        want = _name_sex(names.get(pid, "")) or _majority_sex(cands)
        ranked = _rank_cover_rows(
            cands, neighbors, want, centroids.get(pid), emb_by_id, named_ids, prefer_clear=False
        )
        ids = [int(item["id"]) for item in ranked]
        if not ids:
            fallback = _fallback_cover_ids(conn, [pid]).get(pid)
            if fallback:
                ids = [int(fallback)]
        if not ids:
            return get_person(pid)
        cur = _int_or_none(row["cover_face_id"] if "cover_face_id" in row.keys() else None)
        if cur not in ids:
            auto = _best_cover_ids(conn, [pid], scan_embeddings=False).get(pid)
            cur = auto if auto in ids else ids[0]
        nxt = ids[(ids.index(cur) + 1) % len(ids)]
        conn.execute("UPDATE people SET cover_face_id = ? WHERE id = ?", (nxt, pid))
        conn.commit()
    finally:
        conn.close()
    return get_person(person_id)


def _fallback_cover_ids(conn, person_ids: list[int] | None = None) -> dict[int, int]:
    """Any named crop when the colour/occlusion picker has no ok face."""
    extra = ""
    params: list[Any] = []
    if person_ids:
        extra = "AND f.person_id IN (" + ",".join("?" * len(person_ids)) + ")"
        params = [int(pid) for pid in person_ids]
    rows = conn.execute(
        f"""
        SELECT person_id, id FROM (
            SELECT f.person_id, f.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.person_id
                       ORDER BY CASE f.quality WHEN 'ok' THEN 0 ELSE 1 END,
                                f.det_score DESC, f.id
                   ) AS rn
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE {_named_visible_sql()}
              {extra}
        ) ranked
        WHERE rn = 1
        """,
        params,
    ).fetchall()
    return {int(row["person_id"]): int(row["id"]) for row in rows}


def normalize_category(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in PERSON_CATEGORIES else ""


_BURST_GAP_SEC = 8.0
_LOOKALIKE_COPY_SIM = 0.85


def _photo_area(face: dict[str, Any]) -> int:
    return max(0, int(face.get("width") or 0)) * max(0, int(face.get("height") or 0))


def _face_vec(face: dict[str, Any]) -> np.ndarray | None:
    blob = face.get("embedding")
    if blob is None:
        return None
    try:
        vec = bytes_to_embedding(blob)
    except Exception:
        return None
    if vec is None or vec.size == 0:
        return None
    return l2_normalize(vec)


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
    return _collapse_lookalike_copies(shown)


def _collapse_lookalike_copies(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the larger scan when the same print was indexed twice under different files."""
    kept: list[dict[str, Any]] = []
    vecs: list[np.ndarray | None] = []
    for face in faces:
        vec = _face_vec(face)
        replaced = False
        if vec is not None:
            for i, other in enumerate(vecs):
                if other is None:
                    continue
                if float(np.dot(vec, other)) < _LOOKALIKE_COPY_SIM:
                    continue
                if _photo_area(face) > _photo_area(kept[i]):
                    kept[i] = face
                    vecs[i] = vec
                replaced = True
                break
        if not replaced:
            kept.append(face)
            vecs.append(vec)
    return kept


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


def normalize_name_part(value: str | None) -> str:
    """One name field: collapsed whitespace, no surrounding commas."""
    return " ".join(str(value or "").split()).strip(" ,")[:120]


def given_names(name: str | None) -> list[str]:
    """Every word of the displayed name except the surname."""
    shown = normalize_name_part(name)
    if not shown or is_unknown_name(shown):
        return []
    words = shown.split()
    return words[:-1] if len(words) > 1 else words


def birth_full_name(name: str | None, birth_surname: str | None) -> str:
    """Given names with the birth surname, for search and tree matching."""
    birth = normalize_name_part(birth_surname)
    if not birth:
        return ""
    return " ".join([*given_names(name), birth]).strip()


def name_variants(name: str | None, birth_surname: str | None) -> list[str]:
    """Every way this person may be typed: full married name, first plus married
    surname when there are middle names, and the same two with the birth surname."""
    shown = normalize_name_part(name)
    if not shown:
        return []
    out: list[str] = [shown]
    if is_unknown_name(shown):
        return out
    words = shown.split()
    surnames = [words[-1]] if len(words) > 1 else []
    birth = normalize_name_part(birth_surname)
    if birth and birth.casefold() not in {s.casefold() for s in surnames}:
        surnames.append(birth)
    given = words[:-1] if len(words) > 1 else words
    for surname in surnames or [""]:
        for firsts in ([*given], [given[0]]) if len(given) > 1 else ([*given],):
            text = " ".join([*firsts, surname]).strip()
            if text and text.casefold() not in {o.casefold() for o in out}:
                out.append(text)
    return out


def nee_surname(name: str | None, birth_surname: str | None) -> str:
    """The birth surname to show as "née", or "" when it matches the displayed name."""
    birth = normalize_name_part(birth_surname)
    if not birth:
        return ""
    shown = normalize_name_part(name).split()
    if shown and shown[-1].casefold() == birth.casefold():
        return ""
    return birth


def _one_surname(value: str, label: str) -> str:
    text = normalize_name_part(value)
    if " " in text or "," in text:
        raise ValueError(f"{label} is one surname, without given names")
    return text


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
    birth = birth_full_name(person.get("name"), person.get("birth_surname")).casefold()
    hay = f"{name} {nick} {birth}".strip()
    variants = [v.casefold() for v in name_variants(person.get("name"), person.get("birth_surname"))]
    if any(wanted == v or wanted in v for v in variants):
        return True
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
        # Identity match: the full given names with the birth surname. A shorter
        # "first last" form is a search hit only, since a middle name may mean a
        # different person (see the Jonathan Reid Cole cluster test).
        born = conn.execute(
            """
            SELECT p.*, COUNT(f.id) AS named_faces
            FROM people p
            LEFT JOIN faces f ON f.person_id = p.id
            WHERE IFNULL(p.birth_surname, '') != ''
            GROUP BY p.id
            ORDER BY named_faces DESC, p.id
            """
        ).fetchall()
        for item in born:
            person = dict(item)
            if birth_full_name(person.get("name"), person.get("birth_surname")).casefold() == needle:
                return person
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
        if " " not in needle:
            hits = []
            for item in conn.execute("SELECT * FROM people").fetchall():
                person = dict(item)
                if is_unknown_name(person.get("name") or ""):
                    continue
                first = str(person.get("name") or "").strip().split()
                if first and first[0].casefold() == needle:
                    hits.append(person)
            if len(hits) == 1:
                return hits[0]
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


def list_people(folder: str | None = None, *, lite: bool = False, names: bool = False) -> list[dict[str, Any]]:
    wanted = (folder or "").strip() or None
    if names and not wanted:
        return _list_people_names()
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


def _list_people_names() -> list[dict[str, Any]]:
    """Name typeahead: every catalog person, no face or cover scan."""
    conn = connect()
    init_db(conn)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()]
    finally:
        conn.close()


def _list_people_covers_lite() -> list[dict[str, Any]]:
    """Picker lists: name, category, and a clear colour crop. No embedding scan."""
    conn = connect()
    init_db(conn)
    try:
        people = [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()]
        stats = _named_people_stats(conn)
        best_cover = _best_cover_ids(conn, scan_embeddings=False)
        fallback = _fallback_cover_ids(conn)
        out: list[dict[str, Any]] = []
        for person in people:
            pid = int(person["id"])
            info = stats.get(pid)
            if not info:
                continue
            person["face_count"] = int(info["face_count"] or 0)
            out.append(person)
        _apply_cover_choice(conn, out, best_cover, fallback)
        return out
    finally:
        conn.close()


def _list_people_covers() -> list[dict[str, Any]]:
    """Named people plus one cover crop. Skips the full face scan used by Faces in DB View."""
    conn = connect()
    init_db(conn)
    try:
        people = [dict(r) for r in conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()]
        stats = _named_people_stats(conn)
        best_cover = _best_cover_ids(conn)
        fallback = _fallback_cover_ids(conn)
        out: list[dict[str, Any]] = []
        for person in people:
            pid = int(person["id"])
            info = stats.get(pid)
            if not info:
                continue
            person["face_count"] = int(info["face_count"] or 0)
            person["first_seen"] = info["first_seen"]
            person["last_seen"] = info["last_seen"]
            person["age_min"] = info["age_min"]
            person["age_max"] = info["age_max"]
            out.append(person)
        _apply_cover_choice(conn, out, best_cover, fallback)
        return out
    finally:
        conn.close()


def _folder_people_stats(wanted: str) -> dict[int, dict[str, Any]]:
    from .originals import is_preview_path

    conn = connect()
    init_db(conn)
    neighbors: dict[int, list[Any]] = {}
    pinned_rows: list[Any] = []
    try:
        rows = conn.execute(
            f"""
            SELECT f.person_id, f.id, f.photo_id, f.det_score, f.x1, f.y1, f.x2, f.y2,
                   f.age_est, f.sex_est, f.quality, ph.path, ph.taken_at, ph.sha256
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE {_named_visible_sql()}
            """
        ).fetchall()
        neighbors = _faces_on_photos(conn, list({int(r["photo_id"]) for r in rows}))
        pinned_rows = conn.execute(
            "SELECT id, cover_face_id FROM people WHERE cover_face_id IS NOT NULL"
        ).fetchall()
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
                "cover_face_id": int(row["id"]),
                "first_seen": row["taken_at"],
                "last_seen": row["taken_at"],
                "age_min": row["age_est"],
                "age_max": row["age_est"],
            }
            stats[pid] = item
        covers.setdefault(pid, []).append(dict(row))
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
        item["face_count"] = len(display_faces(cands)) if cands else 0
        if not cands:
            continue
        ok = [row for row in cands if str(row["quality"] or "") == "ok"]
        pool_src = ok or list(cands)
        want = _majority_sex(pool_src)
        pool = [row for row in pool_src if _row_sex(row) == want] if want else list(pool_src)
        if not pool:
            pool = list(pool_src)
        top_det = sorted(
            pool, key=lambda row: (float(row["det_score"] or 0), _face_area(row)), reverse=True
        )[:_COVER_CANDIDATES]
        top_large = sorted(pool, key=_face_area, reverse=True)[:_COVER_LARGE]
        pool = list({int(row["id"]): row for row in [*top_det, *top_large]}.values())
        item["cover_face_id"] = int(
            max(
                pool,
                key=lambda row: _cover_rank(row, neighbors.get(int(row["photo_id"])), want),
            )["id"]
        )
    pinned = {
        int(row["id"]): _int_or_none(row["cover_face_id"])
        for row in pinned_rows
    }
    pinned = {pid: fid for pid, fid in pinned.items() if fid and pid in stats}
    if pinned:
        conn = connect()
        try:
            valid = _valid_pinned_covers(conn, pinned)
        finally:
            conn.close()
        for pid, fid in valid.items():
            if any(int(row["id"]) == fid for row in (covers.get(pid) or [])):
                stats[pid]["cover_face_id"] = fid
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
                   f.created_at, f.embedding, ph.path, ph.taken_at, ph.width, ph.height,
                   ph.sha256, ph.rotation
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id = ?
              AND IFNULL(f.assigned_how, '') != 'junk'
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
        for shot in person["shots"]:
            shot.pop("embedding", None)
        for face in person["faces"]:
            face.pop("embedding", None)
        person["face_count"] = len(person["shots"])
        covers = _best_cover_ids(conn, [int(person_id)], scan_embeddings=False)
        fallback = _fallback_cover_ids(conn, [int(person_id)])
        _apply_cover_choice(conn, [person], covers, fallback)
        return person
    finally:
        conn.close()


def person_zip_filename(name: str, *, labels: bool = False) -> str:
    text = re.sub(r"[^\w]+", "-", (name or "").strip(), flags=re.UNICODE)
    text = text.strip("-") or "person"
    suffix = "-photos-labeled.zip" if labels else "-photos.zip"
    return f"{text[:60]}{suffix}"


_JPEG_ZIP_SUFFIXES = {".jpg", ".jpeg", ".jpe"}


def _local_photo_file(photo_id: int) -> Path | None:
    """Preview kept on this Mac when the original album is unmounted."""
    from . import config as config_mod

    pid = int(photo_id)
    for folder in (config_mod.VIEW_DIR, config_mod.THUMB_DIR):
        local = folder / f"{pid}.jpg"
        try:
            if local.is_file() and local.stat().st_size > 0:
                return local
        except OSError:
            continue
    return None


def _download_file_for_shot(shot: dict[str, Any]) -> Path | None:
    original = Path(str(shot.get("path") or ""))
    try:
        if original.is_file():
            return original
    except OSError:
        pass
    pid = shot.get("photo_id")
    if pid is None:
        return None
    return _local_photo_file(int(pid))


def unique_zip_name(path: Path, photo_id: int, used: set[str]) -> str:
    raw = path.name or f"photo-{int(photo_id)}"
    name = re.sub(r"[\\/]+", "_", raw).lstrip(".")
    if not name or name in {".", ".."}:
        name = f"photo-{int(photo_id)}"
    if name not in used:
        used.add(name)
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    alt = f"{stem}-{int(photo_id)}{suffix}"
    used.add(alt)
    return alt


def list_person_download_entries(person_id: int) -> dict[str, Any] | None:
    """Original files for the pictures shown on this person page, one per photo."""
    conn = connect()
    init_db(conn)
    try:
        row = conn.execute("SELECT id, name FROM people WHERE id = ?", (int(person_id),)).fetchone()
        if not row:
            return None
        faces = conn.execute(
            """
            SELECT f.id, f.photo_id, f.det_score, ph.path, ph.taken_at, ph.sha256,
                   ph.width, ph.height, ph.rotation
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id = ?
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
            ORDER BY ph.taken_at IS NULL, ph.taken_at, f.age_est IS NULL, f.age_est
            """,
            (int(person_id),),
        ).fetchall()
        shots = display_faces(drop_preview_rows([dict(f) for f in faces]))
        used: set[str] = set()
        entries: list[dict[str, Any]] = []
        missing = 0
        for shot in shots:
            original = Path(str(shot.get("path") or ""))
            src = _download_file_for_shot(shot)
            if src is None:
                missing += 1
                continue
            name_src = original if original.name else src
            if src != original and name_src.suffix.lower() not in _JPEG_ZIP_SUFFIXES:
                name_src = name_src.with_suffix(".jpg")
            entries.append(
                {
                    "src": src,
                    "arcname": unique_zip_name(name_src, int(shot["photo_id"]), used),
                    "photo_id": int(shot["photo_id"]),
                    "width": int(shot.get("width") or 0),
                    "height": int(shot.get("height") or 0),
                    "rotation": int(shot.get("rotation") or 0),
                }
            )
        return {
            "name": row["name"],
            "filename": person_zip_filename(row["name"]),
            "entries": entries,
            "missing": missing,
            "total": len(shots),
        }
    finally:
        conn.close()


def _label_faces_on_photos(photo_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    ids = [int(pid) for pid in photo_ids if pid]
    if not ids:
        return {}
    conn = connect()
    init_db(conn)
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT f.id, f.photo_id, f.x1, f.y1, f.x2, f.y2, f.person_id, f.assigned_how,
                   f.tag_x, f.tag_y, p.name AS person_name
            FROM faces f
            LEFT JOIN people p ON p.id = f.person_id
            WHERE f.photo_id IN ({marks})
              AND IFNULL(f.assigned_how, '') != 'junk'
            """,
            ids,
        ).fetchall()
        by_photo: dict[int, list[dict[str, Any]]] = {pid: [] for pid in ids}
        for row in rows:
            by_photo.setdefault(int(row["photo_id"]), []).append(dict(row))
        return by_photo
    finally:
        conn.close()


def write_person_photo_zip(person_id: int, dest: Path, *, labels: bool = False) -> dict[str, Any] | None:
    """Copy named originals into dest, or JPEG copies with name tags. Album files are only read."""
    from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

    listing = list_person_download_entries(person_id)
    if listing is None:
        return None
    listing["filename"] = person_zip_filename(listing["name"], labels=labels)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not labels:
        with ZipFile(dest, "w", compression=ZIP_STORED, allowZip64=True) as zf:
            for item in listing["entries"]:
                zf.write(item["src"], item["arcname"])
        listing["path"] = dest
        return listing
    from .label_draw import labeled_jpeg_bytes

    faces_by_photo = _label_faces_on_photos([int(item["photo_id"]) for item in listing["entries"]])
    used: set[str] = set()
    with ZipFile(dest, "w", compression=ZIP_DEFLATED, allowZip64=True) as zf:
        for item in listing["entries"]:
            stem = Path(item["arcname"]).stem
            arcname = unique_zip_name(Path(f"{stem}-labeled.jpg"), int(item["photo_id"]), used)
            try:
                data = labeled_jpeg_bytes(
                    item["src"],
                    faces_by_photo.get(int(item["photo_id"])) or [],
                    photo_id=int(item["photo_id"]),
                    photo_w=int(item.get("width") or 0),
                    photo_h=int(item.get("height") or 0),
                    rotation=int(item.get("rotation") or 0),
                )
            except Exception:
                continue
            zf.writestr(arcname, data)
    listing["path"] = dest
    return listing


def update_person(person_id: int, *, sync_sidecars: bool = True, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "nickname", "notes", "birth_year", "category", "birth_surname"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    if "name" in payload:
        payload["name"] = str(payload["name"]).strip()
        if not payload["name"]:
            raise ValueError("Name is required")
    if "birth_surname" in payload:
        payload["birth_surname"] = _one_surname(payload["birth_surname"], "Birth surname")
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


def junk_unnamed_on_photo(photo_id: int, *, sync_sidecars: bool = True) -> int:
    """Hide every unnamed face on this picture. Named people stay."""
    conn = connect()
    init_db(conn)
    try:
        ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM faces
                WHERE photo_id = ?
                  AND person_id IS NULL
                  AND IFNULL(assigned_how, '') != 'junk'
                """,
                (int(photo_id),),
            )
        ]
    finally:
        conn.close()
    n = junk_faces(ids, sync_sidecars=False)
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
              AND IFNULL(f.assigned_how, '') != 'junk'
              AND IFNULL(ph.hidden, 0) = 0
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
    """Unnamed faces, and To name groups (two or more unnamed faces)."""
    preview = preview_path_sql("ph.path")
    groups_sql = to_name_cluster_sql()
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
            SELECT c.id AS id, COUNT(f.id) AS n
            {groups_sql}
            ORDER BY n DESC, c.id ASC
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


_name_folders_cache: tuple[int, list[dict[str, Any]]] | None = None


def _path_parent(path: str) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    cut = text.rfind("/")
    return text[:cut] if cut > 0 else text


def _path_name(path: str) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    cut = text.rfind("/")
    return text[cut + 1 :] if cut >= 0 else text


def list_name_folders() -> list[dict[str, Any]]:
    """Indexed folders with how many named faces they still have."""
    global _name_folders_cache
    conn = connect()
    init_db(conn)
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        cached = _name_folders_cache
        if cached and cached[0] == n:
            return cached[1]
        photos = conn.execute(
            f"""
            SELECT id, path FROM photos
            WHERE IFNULL(hidden, 0) = 0
              AND {preview_path_sql("path")}
            """
        ).fetchall()
        named = {
            int(row["photo_id"]): int(row["n"])
            for row in conn.execute(
                """
                SELECT photo_id, COUNT(*) AS n
                FROM faces
                WHERE person_id IS NOT NULL
                GROUP BY photo_id
                """
            ).fetchall()
        }
        counts: dict[str, dict[str, Any]] = {}
        for row in photos:
            parent = _path_parent(row["path"])
            bucket = counts.get(parent)
            if bucket is None:
                bucket = {
                    "folder": _path_name(parent) or folder_of(row["path"]),
                    "path": parent,
                    "photos": 0,
                    "named_faces": 0,
                }
                counts[parent] = bucket
            bucket["photos"] += 1
            bucket["named_faces"] += named.get(int(row["id"]), 0)
        items = sorted(counts.values(), key=lambda r: (r["folder"].lower(), r["path"].lower()))
        _name_folders_cache = (n, items)
        return items
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


def _folder_has_images(path: Path) -> bool | None:
    """True when the folder has at least one full-size photo. None if it is not mounted."""
    from .originals import is_preview_dir_name, is_preview_path, skip_dir

    if not path.is_dir():
        return None
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            current = Path(dirpath)
            if skip_dir(current) and current != path:
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if not skip_dir(current / name) and not is_preview_dir_name(name)
            ]
            for name in filenames:
                if name.startswith(".") or name.startswith("._"):
                    continue
                if Path(name).suffix.lower() not in IMAGE_EXTS:
                    continue
                child = current / name
                if is_preview_path(child):
                    continue
                return True
        return False
    except OSError:
        return None


def _albums_from_indexed(indexed: list[dict[str, Any]], roots: list[Path]) -> list[dict[str, Any]]:
    """Indexed albums under roots, with group_path from the file path. No disk walk."""
    prefixes = sorted((str(root).rstrip("/") for root in roots), key=len, reverse=True)
    out: list[dict[str, Any]] = []
    for item in indexed:
        path = str(item.get("path") or "").replace("\\", "/").rstrip("/")
        if not path:
            continue
        matched = next((prefix for prefix in prefixes if path == prefix or path.startswith(f"{prefix}/")), None)
        if matched is None:
            continue
        row = dict(item)
        rest = path[len(matched) :].lstrip("/")
        parts = [part for part in rest.split("/") if part]
        if len(parts) >= 2:
            row["group"] = parts[0]
            row["group_path"] = f"{matched}/{parts[0]}"
        else:
            row["group"] = ""
            row["group_path"] = ""
        out.append(row)
    return out


def list_albums_under(folders: list[str] | None = None, *, disk: bool = True) -> list[dict[str, Any]]:
    """Albums under the selected folders, including ones not imported yet.

    A folder that itself contains albums is expanded so those nested albums
    show up. Photo counts include files in nested subfolders of that album.
    Preview-size copies (1024 x 768) are skipped.
    disk=False skips walking the NAS so a deep album link can open from the catalog.
    """
    from .originals import is_preview_path

    roots = [Path(item).expanduser() for item in (folders or []) if str(item or "").strip()]
    if not roots:
        return list_name_folders()
    if not disk:
        return sorted(
            _albums_from_indexed(list_name_folders(), roots),
            key=lambda r: ((r.get("group") or r["folder"]).lower(), r["path"].lower()),
        )

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
    for row in items:
        if row["photos"] > 0:
            row["has_images"] = True
        else:
            row["has_images"] = _folder_has_images(Path(row["path"]))
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
            SELECT f.id, f.photo_id, f.person_id, ph.path, ph.taken_at, ph.rotation,
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
                SELECT id, photo_id, person_id, path, taken_at, rotation, photo_key
                FROM ({ranked}) t
                WHERE t.photo_rn = 1
                ORDER BY t.person_id, t.id
            """
            face_params: list[Any] = params
        elif after_id is not None:
            lim = max(1, int(limit))
            face_sql = f"""
                SELECT id, photo_id, person_id, path, taken_at, rotation, photo_key
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
                SELECT id, photo_id, person_id, path, taken_at, rotation, photo_key FROM (
                    SELECT id, photo_id, person_id, path, taken_at, rotation, photo_key,
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
        n = int(cur.rowcount)
    finally:
        conn.close()
    if n:
        _sync_sidecars_for_faces(ids)
    return n


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
