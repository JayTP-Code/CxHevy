"""Pull workouts from Hevy and build the summary the Life Dashboard shows.

Writes data/workouts_all.json (raw) and data/gym_summary.json (what the
dashboard's `gym/summary` document holds).

    python scripts/hevy_sync.py
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

BASE = "https://api.hevyapp.com/v1"
ROOT = os.path.join(os.path.dirname(__file__), "..")
WEEKS = 12
TOP_LIFTS = 4


def load_key():
    if os.environ.get("HEVY_API_KEY"):
        return os.environ["HEVY_API_KEY"].strip()
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("HEVY_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("HEVY_API_KEY not set (environment or .env)")


def fetch_workouts(key):
    workouts, page = [], 1
    while True:
        r = requests.get(
            f"{BASE}/workouts",
            headers={"api-key": key},
            params={"page": page, "pageSize": 10},
        )
        if r.status_code in (401, 403):
            raise SystemExit(f"AUTH_FAILED status={r.status_code}")
        if r.status_code == 404 and page > 1:
            break
        r.raise_for_status()
        body = r.json()
        workouts.extend(body.get("workouts", []))
        if page >= body.get("page_count", 1):
            break
        page += 1
    return workouts


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def e1rm(weight, reps):
    """Epley estimated one-rep max; only meaningful for 1-12 reps."""
    if not weight or not reps or reps > 12:
        return 0.0
    return weight if reps == 1 else weight * (1 + reps / 30)


def working_sets(exercise):
    return [s for s in exercise.get("sets", []) if s.get("type") != "warmup"]


def week_start(d):
    return d - timedelta(days=d.weekday())


def summarize(workouts):
    workouts = sorted(workouts, key=lambda w: w["start_time"])
    today = datetime.now(timezone.utc).date()
    this_week = week_start(today)

    weekly = defaultdict(lambda: {"workouts": 0, "volume_kg": 0.0})
    lift_freq = Counter()
    lift_best = defaultdict(dict)  # name -> {date: (e1rm, label)}

    for w in workouts:
        d = parse_time(w["start_time"]).date()
        wk = weekly[week_start(d)]
        wk["workouts"] += 1
        for ex in w.get("exercises", []):
            sets = working_sets(ex)
            name = ex.get("title", "Unknown")
            best = (0.0, "")
            for s in sets:
                kg, reps = s.get("weight_kg") or 0, s.get("reps") or 0
                wk["volume_kg"] += kg * reps
                est = e1rm(kg, reps)
                if est > best[0]:
                    best = (est, f"{round(kg, 1):g} kg × {reps}")
            if best[0]:
                lift_freq[name] += 1
                prev = lift_best[name].get(d.isoformat())
                if not prev or best[0] > prev[0]:
                    lift_best[name][d.isoformat()] = best

    weeks = []
    for i in range(WEEKS - 1, -1, -1):
        ws = this_week - timedelta(weeks=i)
        wk = weekly.get(ws, {"workouts": 0, "volume_kg": 0.0})
        weeks.append({
            "week_start": ws.isoformat(),
            "workouts": wk["workouts"],
            "volume_kg": round(wk["volume_kg"]),
        })

    # Streak: consecutive weeks with a workout, not breaking on the current week.
    streak, ws = 0, this_week
    if weekly.get(ws, {}).get("workouts", 0) == 0:
        ws -= timedelta(weeks=1)
    while weekly.get(ws, {}).get("workouts", 0) > 0:
        streak += 1
        ws -= timedelta(weeks=1)

    cutoff = (today - timedelta(days=180)).isoformat()
    lifts = []
    for name, _ in lift_freq.most_common(TOP_LIFTS):
        hist = [
            {"date": d, "e1rm": round(v[0], 1), "best_set": v[1]}
            for d, v in sorted(lift_best[name].items())
            if d >= cutoff
        ]
        if not hist:
            continue
        all_time = max(v[0] for v in lift_best[name].values())
        lifts.append({
            "name": name,
            "history": hist,
            "current_e1rm": hist[-1]["e1rm"],
            "best_e1rm": round(all_time, 1),
            "start_e1rm": hist[0]["e1rm"],
        })

    recent = []
    for w in reversed(workouts[-5:]):
        start, end = parse_time(w["start_time"]), parse_time(w["end_time"])
        sets = [s for ex in w.get("exercises", []) for s in working_sets(ex)]
        recent.append({
            "title": w.get("title") or "Workout",
            "date": start.date().isoformat(),
            "duration_min": round((end - start).total_seconds() / 60),
            "exercises": len(w.get("exercises", [])),
            "sets": len(sets),
            "volume_kg": round(sum((s.get("weight_kg") or 0) * (s.get("reps") or 0) for s in sets)),
        })

    return {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_workouts": len(workouts),
        "streak_weeks": streak,
        "weeks": weeks,
        "lifts": lifts,
        "recent": recent,
    }


def save(name, data):
    out_dir = os.path.join(ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def main():
    workouts = fetch_workouts(load_key())
    save("workouts_all.json", workouts)
    path = save("gym_summary.json", summarize(workouts))
    print(f"{len(workouts)} workouts -> {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
