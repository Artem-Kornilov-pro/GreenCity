"""Сводка ступенчатого прогона Locust по ступеням: RPS и время ответа.

    python tools/loadtest/steps.py loadtest-report/run_stats_history.csv
"""

from __future__ import annotations

import collections
import csv
import sys


def _median(window: list[dict], key: str) -> int:
    return sorted(int(float(r[key] or 0)) for r in window)[len(window) // 2]


def summarize(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Name"] == "Aggregated"]
    by_users: dict[int, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_users[int(row["User Count"])].append(row)
    steps = []
    for users in sorted(u for u in by_users if u):
        # Первые секунды ступени -- разгон, их не считаем.
        window = by_users[users][3:] or by_users[users]
        rps = [float(r["Requests/s"]) for r in window]
        fails = [float(r["Failures/s"]) for r in window]
        steps.append({
            "users": users, "rps": sum(rps) / len(rps), "rps_max": max(rps),
            "p50": _median(window, "50%"), "p95": _median(window, "95%"), "p99": _median(window, "99%"), "fails": sum(fails) / len(fails),
        })
    return steps


def main() -> None:
    print("польз.  RPS(ср)  RPS(макс)   p50   p95   p99  ошибок/с")
    for s in summarize(sys.argv[1]):
        print(f"{s['users']:6} {s['rps']:8.1f} {s['rps_max']:9.1f} {s['p50']:5} {s['p95']:5} {s['p99']:5}  {s['fails']:7.2f}")


if __name__ == "__main__":
    main()
