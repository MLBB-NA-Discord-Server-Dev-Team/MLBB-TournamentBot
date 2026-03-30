"""
scripts/league_pages.py
Creates/updates WP pages and sp_league taxonomy terms for all league formats.

League structure:
  - 6 formats × 3 primary lore leagues = 18 leagues
  - Draft Pick BO3 also includes Cadia Riverlands (legacy, 4th league)

Run this script to bootstrap all league pages, then re-run season_init.py
to generate sp_table posts for the new league terms.
Idempotent — safe to re-run.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import subprocess
import json
from dotenv import load_dotenv

load_dotenv()

WP_URL  = os.getenv("WP_PLAY_MLBB_URL", "https://play.mlbb.site").strip().strip('"').rstrip("/")
WP_USER = os.getenv("WP_PLAY_MLBB_USER", "admin").strip().strip('"')
WP_PASS = os.getenv("WP_PLAY_MLBB", "").strip().strip('"')
AUTH    = (WP_USER, WP_PASS)
HEADERS = {"User-Agent": "MLBB-TournamentBot/1.0"}
WP_PATH = "/var/www/sites/play.mlbb.site"   # WP-CLI --path

# ── Lore faction data ─────────────────────────────────────────────────────────

LORE = {
    "Moniyan": {
        "display": "Moniyan",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/3/32/Moniyan_Empire.jpg/revision/latest?cb=20200411133754",
        "image_name": "moniyan-empire.jpg",
        "media_id":   450,   # already uploaded
        "lore_desc":  (
            "The Moniyan Empire is a holy empire and bastion of light, built by those who believe in the "
            "Lord of Light and centered around the prosperous capital city of Lumina City. Humanity united "
            "under the Moniyan banner, establishing the Church of Light and the Imperial Knights to defend "
            "the realm against demonic threats."
        ),
        "lore_html": (
            "<p>The <strong>Moniyan Empire</strong> is a holy empire and bastion of light, built by those "
            "who believe in the Lord of Light and centered around the prosperous capital city of Lumina City. "
            "Humanity united under the Moniyan banner, establishing the Church of Light and the Imperial "
            "Knights to defend the realm against demonic threats.</p>"
            "<p>The Empire is ruled by Princess Silvanna and her family, where the Lightborn Heroes live to "
            "protect and serve the light against the darkness of the Abyss.</p>"
        ),
    },
    "Abyss": {
        "display": "Abyss",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/8/85/Prince_of_the_Abyss.jpg/revision/latest?cb=20211222023348",
        "image_name": "the-abyss.jpg",
        "media_id":   456,
        "lore_desc":  (
            "The Abyss is a hideous scar carved into the Land of Dawn where innumerable demons lurk, "
            "plotting to devour the light and plunge the world into darkness. Sealed at its bottom is the "
            "most terrible demon in the Land of Dawn — the Abyss Dominator — whose will grows stronger as "
            "ancient seals fade."
        ),
        "lore_html": (
            "<p>The <strong>Abyss</strong> is a hideous scar carved into the Land of Dawn where innumerable "
            "demons lurk, plotting to devour the light and plunge the world into darkness. Deep within the "
            "southern mountains lies this bottomless realm where darkness reigns supreme, with the only light "
            "being crimson lava flowing through the crevices.</p>"
            "<p>Sealed at its bottom is the most terrible demon in the Land of Dawn — the Abyss Dominator — "
            "whose will grows stronger as ancient seals gradually fade.</p>"
        ),
    },
    "Northern Vale": {
        "display": "Northern Vale",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/e/e0/Northern_Vale_-_Ship_1.jpg/revision/latest?cb=20200408100818",
        "image_name": "northern-vale.jpg",
        "media_id":   462,
        "lore_desc":  (
            "Northern Vale is the coldest spot in the Land of Dawn, a continent of ice and snow surrounded "
            "by the vast Frozen Sea. This harsh region is home to the tenacious Northern Valers, whose "
            "ancestors once built a splendid civilization there. According to legend, dying on the battlefield "
            "is a unique honor — heroes' souls reunite in the Sacred Palace of their ancestors."
        ),
        "lore_html": (
            "<p><strong>Northern Vale</strong> is the coldest spot in the Land of Dawn, a continent of ice "
            "and snow surrounded by the vast Frozen Sea. This harsh, icy region is home to the tenacious "
            "and brave Northern Valers, whose ancestors — the Iceland Golems — once built a splendid "
            "civilization there.</p>"
            "<p>According to Northern Valer legends, dying on the battlefield is a unique honor, as heroes' "
            "souls are believed to be reunited in the Sacred Palace built by their ancestors.</p>"
        ),
    },
    "Cadia Riverlands": {
        "display": "Cadia Riverlands",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/2/23/MLBB_Project_NEXT_Ling%2C_Wanwan_and_Yu_Zhong_in_Cadia_Riverlands_Entrance_Background.png/revision/latest?cb=20200831044304",
        "image_name": "cadia-riverlands.png",
        "media_id":   470,
        "lore_desc":  (
            "Cadia Riverlands is an isolated ancient land at the easternmost tip of the Land of Dawn, where "
            "harmony among all things guides all inhabitants. Different city-states are scattered across the "
            "region, each possessing unique cultural heritage. The Great Dragon and his disciples protect "
            "this land, fostering coexistence between diverse races and cultures."
        ),
        "lore_html": (
            "<p><strong>Cadia Riverlands</strong> is an isolated ancient land at the easternmost tip of the "
            "Land of Dawn, where harmony among all things has always been the central philosophy guiding all "
            "inhabitants. Different city-states are scattered across this region like pieces on a chessboard, "
            "each possessing unique cultural heritage based on Eastern traditions.</p>"
            "<p>The Great Dragon and his disciples protect this land, fostering peaceful coexistence between "
            "diverse races and cultures in this abundant, naturally harmonious realm.</p>"
        ),
    },
}

# ── Page content builders ─────────────────────────────────────────────────────

def cover_block(image_url: str, media_id: int, title: str) -> str:
    """Gutenberg cover block using the faction's featured image."""
    return (
        f'<!-- wp:cover {{"url":{json.dumps(image_url)},"id":{media_id},"dimRatio":40,"minHeight":320,"minHeightUnit":"px"}} -->'
        f'<div class="wp-block-cover" style="min-height:320px">'
        f'<span aria-hidden="true" class="wp-block-cover__background has-background-dim-40 has-background-dim"></span>'
        f'<img class="wp-block-cover__image-background wp-image-{media_id}" alt="" src="{image_url}" data-object-fit="cover"/>'
        f'<div class="wp-block-cover__inner-container">'
        f'<!-- wp:heading {{"textAlign":"center","level":1,"style":{{"color":{{"text":"#ffffff"}}}}}} -->'
        f'<h1 class="wp-block-heading has-text-align-center has-text-color" style="color:#ffffff">{title}</h1>'
        f'<!-- /wp:heading -->'
        f'</div></div>'
        f'<!-- /wp:cover -->'
    )


def rules_block(games: int, mode: str) -> str:
    fmt_label = f"Best of {games}" if games > 1 else "Single Game"
    mode_str = "5v5 Custom Room — Draft Pick" if mode == "Draft Pick" else "5v5 Custom Room — Brawl"
    rows = (
        f"<tr><th>Format</th><td>{fmt_label}</td></tr>"
        f"<tr><th>Mode</th><td>{mode_str}</td></tr>"
        "<tr><th>Scheduling</th><td>Ad-Hoc | Fixed | Event</td></tr>"
    )
    return (
        "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
        "<!-- wp:heading --><h2 class=\"wp-block-heading\">League Rules</h2><!-- /wp:heading -->"
        f'<!-- wp:html --><table class="league-rules"><tbody>{rows}</tbody></table><!-- /wp:html -->'
        "<!-- wp:paragraph -->"
        '<p>See the <a href="/general-rules/">General Rules</a> page for sportsmanship guidelines, '
        "disconnect policies, and no-show procedures.</p>"
        "<!-- /wp:paragraph -->"
    )


# ── Format definitions ────────────────────────────────────────────────────────
#
# Each format entry defines:
#   key          internal identifier
#   games        number of games in the series
#   mode         "Draft Pick" or "Brawl"
#   slug_suffix  appended to lore slug for the WP page URL
#   term_suffix  appended to lore name for sp_league term (blank = lore name as-is)
#   lore_names   which lore factions compete in this format
#   existing_ids pre-created sp_league term IDs (skip creation if present)

FORMATS = [
    {
        "key":         "dp3",
        "games":       3,
        "mode":        "Draft Pick",
        "slug_suffix": "5v5-draftpick",
        "term_suffix": "",              # "Moniyan League" (original, unchanged)
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale", "Cadia Riverlands"],
        "existing_ids": {"Moniyan": 25, "Abyss": 26, "Northern Vale": 27, "Cadia Riverlands": 28},
    },
    {
        "key":         "dp5",
        "games":       5,
        "mode":        "Draft Pick",
        "slug_suffix": "5g-5v5-draftpick",
        "term_suffix": " — DP BO5",
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale"],
        "existing_ids": {},
    },
    {
        "key":         "dp1",
        "games":       1,
        "mode":        "Draft Pick",
        "slug_suffix": "1g-5v5-draftpick",
        "term_suffix": " — DP BO1",
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale"],
        "existing_ids": {},
    },
    {
        "key":         "brawl1",
        "games":       1,
        "mode":        "Brawl",
        "slug_suffix": "1g-5v5-brawl",
        "term_suffix": " — Brawl BO1",
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale"],
        "existing_ids": {},
    },
    {
        "key":         "brawl3",
        "games":       3,
        "mode":        "Brawl",
        "slug_suffix": "3g-5v5-brawl",
        "term_suffix": " — Brawl BO3",
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale"],
        "existing_ids": {},
    },
    {
        "key":         "brawl5",
        "games":       5,
        "mode":        "Brawl",
        "slug_suffix": "5g-5v5-brawl",
        "term_suffix": " — Brawl BO5",
        "lore_names":  ["Moniyan", "Abyss", "Northern Vale"],
        "existing_ids": {},
    },
]


# ── SP REST helpers ───────────────────────────────────────────────────────────

def sp_get(endpoint):
    r = requests.get(f"{WP_URL}/wp-json/sportspress/v2/{endpoint}",
                     auth=AUTH, headers=HEADERS, params={"per_page": 100})
    r.raise_for_status()
    return r.json()


def get_or_create_league_term(name: str, slug: str, description: str, sp_id: int = None) -> int:
    if sp_id:
        # Update existing term description
        requests.post(f"{WP_URL}/wp-json/sportspress/v2/leagues/{sp_id}",
                      auth=AUTH, headers=HEADERS, json={"description": description})
        print(f"  UPDATED [league term]: {name} (id={sp_id})")
        return sp_id
    existing = sp_get("leagues")
    for t in existing:
        if t["slug"] == slug or t["name"].lower() == name.lower():
            print(f"  EXISTS [league term]: {name} (id={t['id']})")
            return t["id"]
    r = requests.post(f"{WP_URL}/wp-json/sportspress/v2/leagues",
                      auth=AUTH, headers=HEADERS,
                      json={"name": name, "slug": slug, "description": description})
    r.raise_for_status()
    tid = r.json()["id"]
    print(f"  CREATED [league term]: {name} (id={tid})")
    return tid


def wpcli(*args) -> str:
    """Run a WP-CLI command and return stdout. Skips Jetpack to avoid rate-limit hooks."""
    cmd = ["wp", "--allow-root", f"--path={WP_PATH}", "--skip-plugins", "--skip-themes"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"WP-CLI error: {result.stderr.strip()}")
    return result.stdout.strip()


_CONTENT_TMP = "/tmp/mlbb_league_page_content.html"


def get_or_update_page(slug: str, title: str, content: str, media_id: int) -> int:
    # Write content to temp file — avoids all shell/CLI escaping issues
    with open(_CONTENT_TMP, "w") as f:
        f.write(content)

    # Check existence via REST (reliable slug lookup, read-only — no Jetpack issues)
    r = requests.get(f"{WP_URL}/wp-json/wp/v2/pages",
                     auth=AUTH, headers=HEADERS,
                     params={"slug": slug, "per_page": 1})
    existing = r.json() if r.ok else []

    thumb_php = f"update_post_meta($id,'_thumbnail_id',{media_id});" if media_id else ""

    if existing:
        page_id = existing[0]["id"]
        thumb_update = f"update_post_meta({page_id},'_thumbnail_id',{media_id});" if media_id else ""
        php = (
            f"$c=file_get_contents('{_CONTENT_TMP}');"
            f"wp_update_post(['ID'=>{page_id},'post_title'=>{json.dumps(title)},"
            f"'post_content'=>$c,'post_name'=>{json.dumps(slug)},'post_status'=>'publish']);"
            f"{thumb_update}"
        )
        wpcli("eval", php)
        print(f"  UPDATED [page]: /{slug}/ (id={page_id})")
        return page_id

    php = (
        f"$c=file_get_contents('{_CONTENT_TMP}');"
        f"$id=wp_insert_post(['post_type'=>'page','post_title'=>{json.dumps(title)},"
        f"'post_content'=>$c,'post_name'=>{json.dumps(slug)},'post_status'=>'publish']);"
        f"{thumb_php}"
        f"echo $id;"
    )
    page_id = int(wpcli("eval", php))
    print(f"  CREATED [page]: /{slug}/ (id={page_id})")
    return page_id


def build_page_content(lore: dict, games: int, mode: str, page_title: str) -> str:
    return cover_block(lore["image_url"], lore["media_id"], page_title) + rules_block(games, mode)


GENERAL_RULES_SLUG = "general-rules"
GENERAL_RULES_TITLE = "General Rules"
GENERAL_RULES_CONTENT = (
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">Sportsmanship</h2><!-- /wp:heading -->"
    "<!-- wp:list --><ul class=\"wp-block-list\">"
    "<li>All players are expected to conduct themselves with respect toward opponents, teammates, and staff.</li>"
    "<li>Harassment, hate speech, or unsportsmanlike behavior in any form will result in disciplinary action up to and including permanent ban.</li>"
    "<li>Disputes must be raised through official channels — do not argue in match chat or public channels.</li>"
    "</ul><!-- /wp:list -->"
    "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">Disconnects</h2><!-- /wp:heading -->"
    "<!-- wp:paragraph --><p>If a player disconnects during a game, pause the match and allow them time to reconnect. "
    "A full game replay is only granted if the disconnect occurs within the first 90 seconds of the game.</p><!-- /wp:paragraph -->"
    "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">No-Shows &amp; Scheduled Events</h2><!-- /wp:heading -->"
    "<!-- wp:paragraph --><p>For Fixed and Event scheduling, teams must be ready to play at the scheduled time. "
    "A grace period of 10 minutes is allowed. After the grace period, the absent team forfeits the match. "
    "Repeated no-shows may result in removal from the league.</p><!-- /wp:paragraph -->"
    "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">Scheduling Modes</h2><!-- /wp:heading -->"
    "<!-- wp:list --><ul class=\"wp-block-list\">"
    "<li><strong>Ad-Hoc</strong> — Teams coordinate and play matches at any mutually agreed time within the season window.</li>"
    "<li><strong>Fixed</strong> — Matches are assigned a specific date and time by league administration.</li>"
    "<li><strong>Event</strong> — All matches in a round are played during a single scheduled event session.</li>"
    "</ul><!-- /wp:list -->"
)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── General Rules page ────────────────────────────────────────────────────
    print("\n=== General Rules Page ===")
    get_or_update_page(GENERAL_RULES_SLUG, GENERAL_RULES_TITLE, GENERAL_RULES_CONTENT, 0)

    # ── League pages ──────────────────────────────────────────────────────────
    all_league_ids = []

    for fmt in FORMATS:
        print(f"\n{'='*60}")
        print(f"Format: {fmt['games']}-Game 5v5 {fmt['mode']}")
        print('='*60)

        for lore_key in fmt["lore_names"]:
            lore = LORE[lore_key]
            term_name = f"{lore_key} League{fmt['term_suffix']}"
            term_slug = f"{lore_key.lower().replace(' ', '-')}-league{fmt['term_suffix'].lower().replace(' ', '-').replace('—', '').replace('  ', '-')}"
            page_slug = f"{lore_key.lower().replace(' ', '-')}-league-{fmt['slug_suffix']}"
            page_title = f"{lore_key} League — {fmt['games']}-Game 5v5 {fmt['mode']}"

            print(f"\n  [{lore_key}]")

            sp_id = fmt["existing_ids"].get(lore_key)
            league_id = get_or_create_league_term(
                name=term_name,
                slug=term_slug,
                description=lore["lore_desc"],
                sp_id=sp_id,
            )
            all_league_ids.append(league_id)

            content = build_page_content(lore, fmt["games"], fmt["mode"], page_title)
            get_or_update_page(page_slug, page_title, content, lore["media_id"])

    print(f"\n✓ Done. {len(all_league_ids)} league terms processed.")
    print(f"\nsp_league IDs for season_init:\n{all_league_ids}")


if __name__ == "__main__":
    main()
