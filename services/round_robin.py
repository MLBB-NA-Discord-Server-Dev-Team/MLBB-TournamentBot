"""
services/round_robin.py — Round-robin schedule generation

Given a list of team IDs, a season window (play_start → play_end), and
scheduling constraints, produces a list of (date, home_team, away_team)
tuples ready for sp_event creation.

Constraints
-----------
- Matches only on Thu / Fri / Sat / Sun
- Max 4 matches per day
- No team plays more than once per day
- At least 1 match per Thu-Sun weekend block
- Hard cap: 16 teams per league
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Tuple

logger = logging.getLogger(__name__)

MAX_TEAMS = 16
MAX_MATCHES_PER_DAY = 4
MATCH_DAYS = {3, 4, 5, 6}  # Mon=0 … Sun=6 → Thu=3, Fri=4, Sat=5, Sun=6

# Default time slots for simultaneous matches on the same day (PST evening)
TIME_SLOTS = ["19:00:00", "19:30:00", "20:00:00", "20:30:00"]


# ── Round generation (circle / polygon method) ────────────────────────────

def generate_rounds(team_ids: List[int]) -> List[List[Tuple[int, int]]]:
    """
    Circle method: fix team[0], rotate the rest.
    Returns list of rounds, each round = list of (home, away) pairs.
    Every team plays exactly once per round (or has a bye if N is odd).
    """
    teams = list(team_ids)
    n = len(teams)
    if n < 2:
        return []

    # Pad with a sentinel for bye if odd
    bye = -1
    if n % 2 == 1:
        teams.append(bye)
        n += 1

    pivot = teams[0]
    rotating = teams[1:]
    rounds: List[List[Tuple[int, int]]] = []

    for r in range(n - 1):
        pairs: List[Tuple[int, int]] = []

        # Pivot vs first in rotating list
        home, away = pivot, rotating[0]
        # Alternate home/away for balance
        if r % 2 == 1:
            home, away = away, home
        if home != bye and away != bye:
            pairs.append((home, away))

        # Fold the remaining list: pair rotating[1] with rotating[-1],
        # rotating[2] with rotating[-2], etc.
        half = (n - 1) // 2  # number of additional pairs beyond pivot match
        for i in range(half):
            home = rotating[1 + i]
            away = rotating[-(1 + i)]
            if i % 2 == 0:
                home, away = away, home
            if home != bye and away != bye:
                pairs.append((home, away))

        rounds.append(pairs)

        # Rotate: last element moves to front
        rotating = [rotating[-1]] + rotating[:-1]

    return rounds


# ── Calendar helpers ──────────────────────────────────────────────────────

def _get_match_days(play_start: date, play_end: date) -> List[date]:
    """Return all Thu/Fri/Sat/Sun dates in [play_start, play_end]."""
    days = []
    d = play_start
    while d <= play_end:
        if d.weekday() in MATCH_DAYS:
            days.append(d)
        d += timedelta(days=1)
    return days


def _group_weekends(match_days: List[date]) -> List[List[date]]:
    """Group match days into Thu-Sun blocks (a new block starts each Thu)."""
    if not match_days:
        return []
    weekends: List[List[date]] = []
    current: List[date] = []
    for d in match_days:
        if d.weekday() == 3 and current:  # Thursday = start of new block
            weekends.append(current)
            current = []
        current.append(d)
    if current:
        weekends.append(current)
    return weekends


# ── Main scheduler ────────────────────────────────────────────────────────

class ScheduleError(Exception):
    """Raised when constraints cannot be satisfied."""


def generate_schedule(
    team_ids: List[int],
    play_start: date,
    play_end: date,
) -> List[dict]:
    """
    Generate a full round-robin schedule.

    Returns list of dicts:
        {
            "date": date,
            "time": "19:00:00",
            "home_team_id": int,
            "away_team_id": int,
            "round": int,        # 1-based round number
        }

    Raises ScheduleError if constraints cannot be met.
    """
    n = len(team_ids)
    if n < 2:
        raise ScheduleError("Need at least 2 teams to generate a schedule.")
    if n > MAX_TEAMS:
        raise ScheduleError(f"Maximum {MAX_TEAMS} teams per league (got {n}).")

    # Phase 1: abstract rounds
    rounds = generate_rounds(team_ids)
    total_matches = sum(len(r) for r in rounds)
    logger.info(
        "Round-robin: %d teams → %d rounds, %d total matches",
        n, len(rounds), total_matches,
    )

    # Phase 2: calendar slots
    match_days = _get_match_days(play_start, play_end)
    weekends = _group_weekends(match_days)

    if not match_days:
        raise ScheduleError(
            f"No valid match days (Thu-Sun) between {play_start} and {play_end}."
        )

    # Effective max per day: limited by team count (each match uses 2 teams)
    max_per_day = min(MAX_MATCHES_PER_DAY, n // 2)

    # How many days does one round need?
    matches_per_round = len(rounds[0]) if rounds else 0
    days_per_round = -(-matches_per_round // max_per_day)  # ceil division

    total_days_needed = len(rounds) * days_per_round
    if total_days_needed > len(match_days):
        raise ScheduleError(
            f"Season window has {len(match_days)} match days but "
            f"{total_days_needed} are needed for {n} teams "
            f"({len(rounds)} rounds × {days_per_round} days/round)."
        )

    # Phase 3: assign rounds to calendar days
    # Each round's matches are conflict-free (every team appears at most once),
    # so we can safely pack up to max_per_day from ONE round on a single day.
    # We spread rounds across weekends as evenly as possible and ensure
    # every active weekend has at least 1 match.

    schedule: List[dict] = []

    # Build a flat list of chunks (each chunk = matches for one day-slot)
    all_chunks: List[Tuple[int, List[Tuple[int, int]]]] = []  # (round_num, chunk)
    for round_num, rnd in enumerate(rounds, 1):
        for chunk in _chunk(rnd, max_per_day):
            all_chunks.append((round_num, chunk))

    # Distribute chunks across weekends, then across days within each weekend.
    # Target: spread as evenly as possible.
    num_weekends = len(weekends)
    num_chunks = len(all_chunks)

    # Assign chunks to weekends proportionally
    # base = floor(num_chunks / num_weekends), remainder gets +1
    base_per_wk = num_chunks // num_weekends if num_weekends else num_chunks
    extra = num_chunks % num_weekends if num_weekends else 0

    chunk_idx = 0
    for wk_idx, wk_days in enumerate(weekends):
        # How many chunks this weekend gets
        wk_count = base_per_wk + (1 if wk_idx < extra else 0)
        if wk_count == 0:
            continue

        # Assign chunks to days within this weekend
        day_cursor = 0
        for _ in range(wk_count):
            if chunk_idx >= num_chunks:
                break
            if day_cursor >= len(wk_days):
                day_cursor = 0  # wrap around within the weekend
            round_num, chunk = all_chunks[chunk_idx]
            _add_chunk(schedule, wk_days[day_cursor], chunk, round_num)
            day_cursor += 1
            chunk_idx += 1

    # Phase 4: validate
    _validate(schedule, team_ids, match_days, weekends, play_start, play_end)

    logger.info(
        "Schedule generated: %d matches across %d days (%s → %s)",
        len(schedule),
        len({e["date"] for e in schedule}),
        schedule[0]["date"] if schedule else "?",
        schedule[-1]["date"] if schedule else "?",
    )
    return schedule


def _chunk(lst, size):
    """Split list into chunks of at most `size`."""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def _add_chunk(schedule, day, chunk, round_num):
    for slot_idx, (home, away) in enumerate(chunk):
        schedule.append({
            "date": day,
            "time": TIME_SLOTS[slot_idx] if slot_idx < len(TIME_SLOTS) else TIME_SLOTS[-1],
            "home_team_id": home,
            "away_team_id": away,
            "round": round_num,
        })


def _validate(schedule, team_ids, match_days, weekends, play_start, play_end):
    """Assert all constraints hold. Raises ScheduleError on violation."""
    n = len(team_ids)
    expected_total = n * (n - 1) // 2

    # Total matches
    if len(schedule) != expected_total:
        raise ScheduleError(
            f"Expected {expected_total} matches, generated {len(schedule)}."
        )

    # All dates within window
    for e in schedule:
        if e["date"] < play_start or e["date"] > play_end:
            raise ScheduleError(
                f"Match on {e['date']} outside season window "
                f"[{play_start}, {play_end}]."
            )

    # Max per day
    from collections import Counter
    day_counts = Counter(e["date"] for e in schedule)
    for d, cnt in day_counts.items():
        if cnt > MAX_MATCHES_PER_DAY:
            raise ScheduleError(f"{cnt} matches on {d} exceeds max {MAX_MATCHES_PER_DAY}.")

    # No team twice in one day
    day_teams: dict[date, set] = {}
    for e in schedule:
        dt = e["date"]
        if dt not in day_teams:
            day_teams[dt] = set()
        for tid in (e["home_team_id"], e["away_team_id"]):
            if tid in day_teams[dt]:
                raise ScheduleError(f"Team {tid} plays twice on {dt}.")
            day_teams[dt].add(tid)

    # Every pairing appears exactly once
    seen_pairs = set()
    for e in schedule:
        pair = tuple(sorted([e["home_team_id"], e["away_team_id"]]))
        if pair in seen_pairs:
            raise ScheduleError(f"Duplicate pairing: {pair}.")
        seen_pairs.add(pair)

    # Weekend minimum (only for weekends that overlap the scheduled range)
    first_day = min(e["date"] for e in schedule)
    last_day = max(e["date"] for e in schedule)
    scheduled_dates = set(e["date"] for e in schedule)
    for wk in weekends:
        if wk[-1] < first_day or wk[0] > last_day:
            continue  # weekend outside active schedule range
        if not any(d in scheduled_dates for d in wk):
            raise ScheduleError(
                f"Weekend {wk[0]}–{wk[-1]} has no matches."
            )
