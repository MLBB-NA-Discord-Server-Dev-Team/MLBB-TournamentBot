import sys, os
sys.path.insert(0, '/root/MLBB-TournamentBot')
os.chdir('/root/MLBB-TournamentBot')
from dotenv import load_dotenv; load_dotenv()

from services.db_helpers import get_play_end_for_period
print("db_helpers ok")

from services.scheduler import Scheduler
print("scheduler ok")

from scripts.season_init import compute_league_cycles, LEAGUE_MONTH_SLOT, ALL_LEAGUE_IDS
print("season_init ok\n")

print("Staggered schedule:")
for lid in sorted(LEAGUE_MONTH_SLOT, key=LEAGUE_MONTH_SLOT.get):
    cycles = compute_league_cycles(lid)
    if cycles:
        c = cycles[0]
        print(f"  [{LEAGUE_MONTH_SLOT[lid]:>2}] lid={lid}  play={c['play_start']}  reg={c['opens_at']}→{c['closes_at']}")
