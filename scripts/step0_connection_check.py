import json
import os
import sys

import requests

BASE = "https://api.hevyapp.com/v1"


def load_key():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("HEVY_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("HEVY_API_KEY not found in .env")


def get(path, key, params=None):
    r = requests.get(f"{BASE}{path}", headers={"api-key": key}, params=params)
    return r


def save(name, data):
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w") as f:
        json.dump(data, f, indent=2)


def main():
    key = load_key()

    r = get("/user/info", key)
    if r.status_code in (401, 403):
        print(f"AUTH_FAILED status={r.status_code} body={r.text[:500]}")
        sys.exit(1)
    r.raise_for_status()
    user = r.json()
    save("user_info.json", user)
    print("USER_INFO:", json.dumps(user, indent=2))

    r = get("/workouts/count", key)
    r.raise_for_status()
    count = r.json()
    save("workouts_count.json", count)
    print("WORKOUTS_COUNT:", json.dumps(count, indent=2))

    r = get("/routines", key, params={"page": 1, "pageSize": 10})
    r.raise_for_status()
    routines = r.json()
    save("routines_existing.json", routines)
    print("ROUTINES:", json.dumps(routines, indent=2)[:2000])

    r = get("/routine_folders", key, params={"page": 1, "pageSize": 10})
    r.raise_for_status()
    folders = r.json()
    save("routine_folders_existing.json", folders)
    print("ROUTINE_FOLDERS:", json.dumps(folders, indent=2)[:2000])


if __name__ == "__main__":
    main()
