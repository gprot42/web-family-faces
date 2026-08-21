from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from .config import CHILD_AGE, MERGE_SIM
from .db import connect, init_db
from .people import age_band, all_person_centroids, is_unknown_name
from .util import cosine


def merge_suggestions(limit: int = 12) -> list[dict[str, Any]]:
    """Suggest person pairs that may be the same identity across ages.

    Never auto-applied. Same-photo co-occurrence is a veto (two people in one
    frame cannot be one person). Complementary dates/ages and companion overlap
    raise the score.
    """
    conn = connect()
    init_db(conn)
    try:
        people = [dict(r) for r in conn.execute("SELECT id, name FROM people").fetchall()]
        if len(people) < 2:
            return []

        faces = conn.execute(
            """
            SELECT f.id, f.person_id, f.age_est, f.photo_id, ph.taken_at
            FROM faces f
            JOIN photos ph ON ph.id = f.photo_id
            WHERE f.person_id IS NOT NULL AND f.quality = 'ok'
            """
        ).fetchall()

        photo_people: dict[int, set[int]] = defaultdict(set)
        person_photos: dict[int, set[int]] = defaultdict(set)
        person_ages: dict[int, list[float]] = defaultdict(list)
        person_years: dict[int, list[int]] = defaultdict(list)
        for face in faces:
            pid = int(face["person_id"])
            photo_people[int(face["photo_id"])].add(pid)
            person_photos[pid].add(int(face["photo_id"]))
            if face["age_est"] is not None:
                person_ages[pid].append(float(face["age_est"]))
            if face["taken_at"]:
                try:
                    person_years[pid].append(int(str(face["taken_at"])[:4]))
                except ValueError:
                    pass

        # If A and B share a photo they are different people.
        veto: set[tuple[int, int]] = set()
        companions: dict[int, set[int]] = defaultdict(set)
        for members in photo_people.values():
            ids = sorted(members)
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    veto.add((a, b))
                    companions[a].add(b)
                    companions[b].add(a)

        centroids = all_person_centroids(conn)
        names = {p["id"]: p["name"] for p in people}

        scored: list[dict[str, Any]] = []
        ids = [p["id"] for p in people]
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pair = (min(a, b), max(a, b))
                if pair in veto:
                    continue
                bands_a = centroids.get(a) or {}
                bands_b = centroids.get(b) or {}
                best = -1.0
                best_pair = ("all", "all")
                for ba, va in bands_a.items():
                    if ba == "all":
                        continue
                    for bb, vb in bands_b.items():
                        if bb == "all":
                            continue
                        sim = cosine(va, vb)
                        if sim > best:
                            best = sim
                            best_pair = (ba, bb)
                if best < 0 and "all" in bands_a and "all" in bands_b:
                    best = cosine(bands_a["all"], bands_b["all"])
                    best_pair = ("all", "all")

                ca, cb = companions[a], companions[b]
                union = ca | cb
                companion_jaccard = (len(ca & cb) / len(union)) if union else 0.0

                ages_a = person_ages[a]
                ages_b = person_ages[b]
                mean_a = float(np.mean(ages_a)) if ages_a else None
                mean_b = float(np.mean(ages_b)) if ages_b else None
                complementary_age = False
                if mean_a is not None and mean_b is not None:
                    complementary_age = abs(mean_a - mean_b) >= 15

                years_a = person_years[a]
                years_b = person_years[b]
                complementary_year = False
                if years_a and years_b:
                    complementary_year = abs(float(np.mean(years_a)) - float(np.mean(years_b))) >= 12

                # Child vs adult similarity is not trusted as a positive.
                child_adult = False
                if mean_a is not None and mean_b is not None:
                    child_adult = (mean_a < CHILD_AGE) != (mean_b < CHILD_AGE)

                score = 0.0
                reasons: list[str] = []
                if not child_adult and best >= MERGE_SIM:
                    score += best
                    reasons.append(f"embedding {best:.2f} ({best_pair[0]}↔{best_pair[1]})")
                elif child_adult:
                    reasons.append("child↔adult: embedding ignored")
                if companion_jaccard >= 0.15:
                    score += 0.25 * companion_jaccard
                    reasons.append(f"shared companions {companion_jaccard:.0%}")
                if complementary_age:
                    score += 0.08
                    reasons.append("age ranges look sequential")
                if complementary_year:
                    score += 0.08
                    reasons.append("photo years look sequential")

                if score <= 0:
                    continue
                named_a = not is_unknown_name(names[a])
                named_b = not is_unknown_name(names[b])
                # Two real names stay split unless the faces themselves match.
                if named_a and named_b and best < MERGE_SIM:
                    continue
                # Age/year alone is not enough to put a question on People.
                if companion_jaccard < 0.15 and best < MERGE_SIM:
                    continue
                scored.append(
                    {
                        "person_a": {"id": a, "name": names[a], "age_mean": mean_a, "band": age_band(mean_a)},
                        "person_b": {"id": b, "name": names[b], "age_mean": mean_b, "band": age_band(mean_b)},
                        "similarity": best,
                        "score": score,
                        "reasons": reasons,
                        "auto_safe": False,
                    }
                )

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:limit]
    finally:
        conn.close()
