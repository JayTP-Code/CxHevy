"""Pull workouts from Hevy and build the summary the Life Dashboard shows.

Writes data/workouts_all.json (raw) and data/gym_summary.json (what the
dashboard's `gym/summary` document holds).

    python scripts/hevy_sync.py
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import requests
from zoneinfo import ZoneInfo

BASE = "https://api.hevyapp.com/v1"
ROOT = os.path.join(os.path.dirname(__file__), "..")
WEEKS = 12
TOP_LIFTS = 4
# Workouts are dated in your local time zone, not UTC.
TZ = ZoneInfo(os.environ.get("HEVY_TZ", "Australia/Melbourne"))


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


def fetch_all(key, path, field, page_size=10):
    items, page = [], 1
    while True:
        r = requests.get(
            f"{BASE}{path}",
            headers={"api-key": key},
            params={"page": page, "pageSize": page_size},
        )
        if r.status_code in (401, 403):
            raise SystemExit(f"AUTH_FAILED status={r.status_code}")
        if r.status_code == 404 and page > 1:
            break
        r.raise_for_status()
        body = r.json()
        items.extend(body.get(field, []))
        if page >= body.get("page_count", 1):
            break
        page += 1
    return items


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def e1rm(weight, reps):
    """Epley estimated one-rep max; only meaningful for 1-12 reps."""
    if not weight or not reps or reps > 12:
        return 0.0
    return weight if reps == 1 else weight * (1 + reps / 30)


# Template ids of assisted exercises (e.g. Pull Up (Assisted)), where the logged
# weight is help from the machine: less weight is progress. Filled in main().
ASSISTED = set()


def is_assisted(exercise):
    return exercise.get("exercise_template_id") in ASSISTED


def working_sets(exercise):
    return [s for s in exercise.get("sets", []) if s.get("type") != "warmup"]


def week_start(d):
    return d - timedelta(days=d.weekday())


def summarize(workouts):
    workouts = sorted(workouts, key=lambda w: w["start_time"])
    today = datetime.now(TZ).date()
    this_week = week_start(today)

    weekly = defaultdict(lambda: {"workouts": 0, "volume_kg": 0.0})
    lift_freq = Counter()
    lift_best = defaultdict(dict)  # name -> {date: (e1rm, label)}

    for w in workouts:
        d = parse_time(w["start_time"]).astimezone(TZ).date()
        wk = weekly[week_start(d)]
        wk["workouts"] += 1
        for ex in w.get("exercises", []):
            if is_assisted(ex):
                continue
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
        loaded = [s for ex in w.get("exercises", []) if not is_assisted(ex) for s in working_sets(ex)]
        recent.append({
            "title": w.get("title") or "Workout",
            "date": start.astimezone(TZ).date().isoformat(),
            "duration_min": round((end - start).total_seconds() / 60),
            "exercises": len(w.get("exercises", [])),
            "sets": len(sets),
            "volume_kg": round(sum((s.get("weight_kg") or 0) * (s.get("reps") or 0) for s in loaded)),
        })

    return {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_workouts": len(workouts),
        "streak_weeks": streak,
        "weeks": weeks,
        "lifts": lifts,
        "recent": recent,
    }


def est(weight, reps):
    """Epley estimate without the rep cap, for comparing one exercise to itself."""
    if not weight or not reps:
        return 0.0
    return weight if reps == 1 else weight * (1 + reps / 30)


def set_label(s, assisted=False):
    kg, reps, secs = s.get("weight_kg"), s.get("reps"), s.get("duration_seconds")
    if assisted and reps:
        return f"−{round(kg, 1):g} × {reps}" if kg else f"BW × {reps}"
    if secs and not reps:
        t = f"{secs // 60}:{secs % 60:02d} min" if secs >= 120 else f"{secs} s"
        return f"{round(kg, 1):g} kg · {t}" if kg else t
    if kg and reps:
        return f"{round(kg, 1):g} × {reps}"
    if reps:
        return f"BW × {reps}"
    return "–"


def top_set(sets, assisted=False):
    if assisted:
        # Least assistance wins; more reps breaks ties. Score is negative assistance.
        done = [s for s in sets if s.get("reps")]
        best = min(done, key=lambda s: ((s.get("weight_kg") or 0), -(s.get("reps") or 0)), default=None)
        return best, -(best.get("weight_kg") or 0) if best else 0.0
    best = max(sets, key=lambda s: (est(s.get("weight_kg"), s.get("reps")), s.get("duration_seconds") or 0), default=None)
    return best, est(best.get("weight_kg"), best.get("reps")) if best else 0.0


def increment(kg):
    return 2.5 if kg >= 20 else 1


def session_view(workouts, routines, folders):
    """Detail for the dashboard's session card: the last workout as a recap,
    and the next routine in the current program with targets."""
    if not workouts:
        return {}
    workouts = sorted(workouts, key=lambda w: w["start_time"])
    by_id = {r["id"]: r for r in routines}
    folder_names = {f["id"]: f["title"].strip() for f in folders}

    history = defaultdict(list)  # template id -> [(start_time, exercise)]
    for w in workouts:
        for ex in w.get("exercises", []):
            history[ex["exercise_template_id"]].append((w["start_time"], ex))

    def previous(tid, before):
        return [ex for t, ex in history[tid] if t < before]

    # ---- Recap of the most recent workout ----
    last = workouts[-1]
    start, end = parse_time(last["start_time"]), parse_time(last["end_time"])
    rec_ex, prs = [], 0
    for ex in last.get("exercises", []):
        a = is_assisted(ex)
        sets = working_sets(ex)
        top, e = top_set(sets, a)
        prev = previous(ex["exercise_template_id"], last["start_time"])
        prev_sets = [working_sets(p) for p in prev if top_set(working_sets(p), a)[0]]
        if a:
            item = {"e1rm": None, "delta": None, "pr": False}
            if top and prev_sets:
                prev_e = top_set(prev_sets[-1], True)[1]
                best_before = max(top_set(ps, True)[1] for ps in prev_sets)
                d = round(e - prev_e, 1)  # positive = less assistance than last time
                item = {"e1rm": None, "delta": d, "pr": e > best_before + 0.05,
                        "delta_text": "±0 kg assist" if abs(d) < 0.5 else f"{'−' if d > 0 else '+'}{abs(d):g} kg assist"}
        else:
            prev_e = top_set(prev_sets[-1])[1] if prev_sets else 0.0
            best_before = max((top_set(ps)[1] for ps in prev_sets), default=0.0)
            item = {"e1rm": round(e, 1),
                    "delta": round(e - prev_e, 1) if prev_sets and e and prev_e else None,
                    "pr": bool(prev_sets) and e > best_before + 0.05}
        prs += item["pr"]
        rec_ex.append({
            "name": ex["title"],
            "sets": [set_label(s, a) for s in sets],
            "top": set_label(top, a) if top else "–",
            **item,
        })

    def volume(w):
        return sum((s.get("weight_kg") or 0) * (s.get("reps") or 0)
                   for ex in w.get("exercises", []) if not is_assisted(ex) for s in working_sets(ex))
    vol = volume(last)
    same = [w for w in workouts[:-1] if last.get("routine_id") and w.get("routine_id") == last["routine_id"]]
    prev_vol = None
    if same:
        prev_vol = round(volume(same[-1]))
    last_session = {
        "title": last.get("title") or "Workout",
        "start_time": last["start_time"],
        "duration_min": round((end - start).total_seconds() / 60),
        "sets": sum(len(working_sets(ex)) for ex in last.get("exercises", [])),
        "volume_kg": round(vol),
        "prev_volume_kg": prev_vol,
        "prs": prs,
        "exercises": rec_ex,
    }

    # ---- Next routine in the current program ----
    routine = by_id.get(last.get("routine_id"))
    if not routine:
        return {"last_session": last_session}
    folder_id = routine.get("folder_id")
    phase = re.match(r"\s*(Wk\s*[\d\-–]+)", routine["title"])
    phase = phase.group(1) if phase else None
    # Match phase routines by name prefix ("Wk1-3 ..."), since a routine can
    # end up outside the program's folder in Hevy.
    pool = [r for r in routines if r["title"].strip().startswith(phase)] if phase \
        else [r for r in routines if r.get("folder_id") == folder_id]
    pool_ids = {r["id"] for r in pool}

    # Rotation = order the routines were first done in this phase.
    rotation = []
    for w in workouts:
        rid = w.get("routine_id")
        if rid in pool_ids and rid not in rotation:
            rotation.append(rid)
    rotation += [r["id"] for r in pool if r["id"] not in rotation]
    i = rotation.index(routine["id"]) if routine["id"] in rotation else -1
    nxt = by_id[rotation[(i + 1) % len(rotation)]]

    block_start = next((w["start_time"] for w in workouts
                        if by_id.get(w.get("routine_id"), {}).get("folder_id") == folder_id), last["start_time"])
    block_week = (datetime.now(timezone.utc) - parse_time(block_start)).days // 7 + 1
    total = re.search(r"(\d+)\s*wk", folder_names.get(folder_id, ""), re.I)

    plan = []
    for ex in nxt.get("exercises", []):
        a = ex.get("exercise_template_id") in ASSISTED
        planned = [s for s in ex.get("sets", []) if s.get("type") != "warmup"]
        prev = history.get(ex["exercise_template_id"], [])
        last_ex = prev[-1] if prev else None
        last_sets = working_sets(last_ex[1]) if last_ex else []
        best = 0.0 if a else max((top_set(working_sets(e))[1] for _, e in prev), default=0.0)

        rr = next((s.get("rep_range") for s in planned if s.get("rep_range")), None) or {}
        lo, hi = rr.get("start"), rr.get("end") or rr.get("start")
        p_kg = next((s.get("weight_kg") for s in planned if s.get("weight_kg")), None)
        p_secs = next((s.get("duration_seconds") for s in planned if s.get("duration_seconds")), None)
        if p_secs:
            planned_label = f"{len(planned)} × {p_secs} s"
        else:
            reps = f"{lo}–{hi}" if lo and hi and lo != hi else (str(lo) if lo else "?")
            planned_label = f"{len(planned)} × {reps}" + (f" @ {round(p_kg, 1):g} kg{' assist' if a else ''}" if p_kg else "")

        target = None
        if a and last_sets and hi:
            done = [s for s in last_sets if s.get("reps")]
            l_kg = min((s.get("weight_kg") or 0) for s in done) if done else 0
            reps = [s.get("reps") or 0 for s in done if (s.get("weight_kg") or 0) == l_kg]
            if done and all(r >= hi for r in reps):
                down = max(0, round(l_kg - increment(l_kg), 1))
                target = {"kind": "up", "text": f"Hit {hi} reps at {l_kg:g} kg assistance. Drop the assistance to {down:g} kg."}
            elif done:
                got = ", ".join(str(r) for r in reps)
                target = {"kind": "reps", "text": f"Stay at {l_kg:g} kg assistance and get every set to {hi} reps (last time: {got})."}
        elif last_sets and hi:
            l_kg = max((s.get("weight_kg") or 0) for s in last_sets)
            top_sets = [s for s in last_sets if (s.get("weight_kg") or 0) == l_kg]
            reps = [s.get("reps") or 0 for s in top_sets]
            if l_kg and all(r >= hi for r in reps):
                up = round(l_kg + increment(l_kg), 1)
                if p_kg and p_kg > l_kg:
                    up = p_kg
                target = {"kind": "up", "text": f"Hit {hi} reps on every set last time. Go up to {up:g} kg."}
            elif l_kg:
                got = ", ".join(str(r) for r in reps)
                target = {"kind": "reps", "text": f"Stay at {round(l_kg, 1):g} kg and get every set to {hi} reps (last time: {got})."}
        elif not last_sets:
            target = {"kind": "new", "text": "First time logging this one. Start at the planned weight."}

        plan.append({
            "name": ex["title"],
            "planned": planned_label,
            "last": {"date": last_ex[0], "sets": [set_label(s, a) for s in last_sets]} if last_ex else None,
            "best_e1rm": round(best, 1) if best else None,
            "target": target,
            "notes": (ex.get("notes") or "").strip()[:160],
        })

    return {
        "last_session": last_session,
        "next_session": {
            "title": nxt["title"].strip(),
            "program": folder_names.get(folder_id),
            "block_week": block_week,
            "block_weeks": int(total.group(1)) if total else None,
            "exercises": plan,
        },
    }



def save(name, data):
    out_dir = os.path.join(ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def main():
    key = load_key()
    workouts = fetch_all(key, "/workouts", "workouts")
    routines = fetch_all(key, "/routines", "routines")
    folders = fetch_all(key, "/routine_folders", "routine_folders")
    templates = fetch_all(key, "/exercise_templates", "exercise_templates", 100)
    ASSISTED.update(t["id"] for t in templates if t.get("type") == "bodyweight_assisted")
    save("workouts_all.json", workouts)
    save("routines_all.json", routines)
    summary = summarize(workouts)
    summary.update(session_view(workouts, routines, folders))
    path = save("gym_summary.json", summary)
    print(f"{len(workouts)} workouts, {len(routines)} routines -> {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
