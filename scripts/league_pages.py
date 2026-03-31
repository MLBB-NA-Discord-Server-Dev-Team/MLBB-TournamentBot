"""
scripts/league_pages.py
Creates/updates WP pages and sp_league taxonomy terms for all league formats.

League structure (12 leagues across 3 formats):
  - Draft Pick BO5 (5-Game):  Moniyan, Abyss, Northern Vale, Cadia Riverlands
  - Draft Pick BO3 (3-Game):  Agelta, Los Pecados, Aberleen, Dragon Altar
  - Brawl (format per season): Megalith, Vonetis, Oasis, Swan Castle

Run this script to bootstrap all league pages, then re-run season_init.py
to generate sp_table posts for the new league terms.
Idempotent — safe to re-run.
"""
import sys, os
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

# ── Lore region data ──────────────────────────────────────────────────────────
#
# media_id: WP media attachment ID (0 = needs upload, set after first run)
# image_url: source URL from MLBB fandom wiki

LORE = {
    # ── Draft Pick BO5 ────────────────────────────────────────────────────────
    "Moniyan": {
        "display":    "Moniyan Empire",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/3/32/Moniyan_Empire.jpg/revision/latest?cb=20200411133754",
        "image_name": "moniyan-empire.jpg",
        "media_id":   450,
        "lore_desc":  (
            "The Moniyan Empire is a holy empire and bastion of light, built by those who believe in the "
            "Lord of Light and centered around the prosperous capital city of Lumina City."
        ),
    },
    "Abyss": {
        "display":    "The Abyss",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/8/85/Prince_of_the_Abyss.jpg/revision/latest?cb=20211222023348",
        "image_name": "the-abyss.jpg",
        "media_id":   456,
        "lore_desc":  (
            "The Abyss is a hideous scar carved into the Land of Dawn where innumerable demons lurk, "
            "plotting to devour the light and plunge the world into darkness."
        ),
    },
    "Northern Vale": {
        "display":    "Northern Vale",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/e/e0/Northern_Vale_-_Ship_1.jpg/revision/latest?cb=20200408100818",
        "image_name": "northern-vale.jpg",
        "media_id":   462,
        "lore_desc":  (
            "Northern Vale is the coldest spot in the Land of Dawn, a continent of ice and snow "
            "surrounded by the vast Frozen Sea, home to the tenacious Northern Valers."
        ),
    },
    "Cadia Riverlands": {
        "display":    "Cadia Riverlands",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/2/23/MLBB_Project_NEXT_Ling%2C_Wanwan_and_Yu_Zhong_in_Cadia_Riverlands_Entrance_Background.png/revision/latest?cb=20200831044304",
        "image_name": "cadia-riverlands.png",
        "media_id":   470,
        "lore_desc":  (
            "Cadia Riverlands is an isolated ancient land at the easternmost tip of the Land of Dawn, "
            "where harmony among all things guides all inhabitants."
        ),
    },
    # ── Draft Pick BO3 ────────────────────────────────────────────────────────
    "Agelta": {
        "display":    "Agelta Drylands",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/4/44/Agelta_Drylands.jpg/revision/latest?cb=20220304094414",
        "image_name": "agelta-drylands.jpg",
        "media_id":   0,
        "lore_desc":  (
            "Agelta Drylands is a vast desert in the west of the Land of Dawn, meaning "
            "'yellow sand everywhere.' The harsh terrain is home to Los Pecados and The Oasis."
        ),
    },
    "Los Pecados": {
        "display":    "Los Pecados",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/2/23/Los_Pecados_-_City_of_Sins_Full.png/revision/latest?cb=20210124052915",
        "image_name": "los-pecados.png",
        "media_id":   0,
        "lore_desc":  (
            "Los Pecados is a lawless city of sins in the Agelta Drylands, established after the "
            "Moniyan Empire's collapse by homeless soldiers. A black market thrives in its shadows."
        ),
    },
    "Aberleen": {
        "display":    "Castle Aberleen",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/3/3f/Castle_Aberleen.jpg/revision/latest?cb=20200411134009",
        "image_name": "castle-aberleen.jpg",
        "media_id":   0,
        "lore_desc":  (
            "Castle Aberleen is a mist-shrouded fortress in Avalor, ruled by House Paxley "
            "in the southern reaches of the Moniyan Empire."
        ),
    },
    "Dragon Altar": {
        "display":    "Dragon Altar",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/d/d3/Dragon_Altar_-_Full.png/revision/latest?cb=20211230063031",
        "image_name": "dragon-altar.png",
        "media_id":   0,
        "lore_desc":  (
            "The Dragon Altar is a sacred site in the Cadia Riverlands, hidden among "
            "sky-soaring mountains where the Great Dragon and his disciples reside."
        ),
    },
    # ── Brawl ─────────────────────────────────────────────────────────────────
    "Megalith": {
        "display":    "Megalith Wasteland",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/2/26/Megalith_Wasteland.jpg/revision/latest?cb=20200313120257",
        "image_name": "megalith-wasteland.jpg",
        "media_id":   0,
        "lore_desc":  (
            "The Megalith Wasteland is a rugged terrain on the border of Northern Vale and the "
            "Moniyan Empire, known for its towering rock formations and harsh conditions."
        ),
    },
    "Vonetis": {
        "display":    "Vonetis Sea",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/0/0e/Blue_Flame.jpg/revision/latest?cb=20200414080908",
        "image_name": "vonetis-sea.jpg",
        "media_id":   0,
        "lore_desc":  (
            "The Vonetis Sea is an archipelago home to the Dorik people, with islands "
            "including Perlas, Blue Flame Island, and Solari Isle."
        ),
    },
    "Oasis": {
        "display":    "The Oasis",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/e/e8/Agelta_Drylands_-_The_Oasis_Full.png/revision/latest?cb=20210123094237",
        "image_name": "the-oasis.png",
        "media_id":   0,
        "lore_desc":  (
            "The Oasis is a sanctuary created by Belerick in the Agelta Drylands, "
            "offering respite to trade caravans crossing the desert. Home to Floryn."
        ),
    },
    "Swan Castle": {
        "display":    "Swan Castle",
        "image_url":  "https://static.wikia.nocookie.net/mobile-legends/images/b/be/Azure_Lake.png/revision/latest?cb=20180913182035",
        "image_name": "azure-lake-swan-castle.png",
        "media_id":   0,
        "lore_desc":  (
            "Swan Castle is a romantic retreat near Azure Lake in the Moniyan Empire, "
            "founded by Prince Alvin I as a sanctuary apart from the throne."
        ),
    },
}

# ── Format definitions ────────────────────────────────────────────────────────
#
# existing_id: sp_league term ID to rename/update (None = create fresh)
# page_slug:   WP page slug (clean, no format suffix)
# page_title:  displayed page title

FORMATS = [
    {
        "key":      "dp5",
        "games":    5,
        "mode":     "Draft Pick",
        "label":    "5-Game 5v5 Draft Pick",
        "leagues": [
            {"name": "Moniyan",          "existing_id": 34},
            {"name": "Abyss",            "existing_id": 35},
            {"name": "Northern Vale",    "existing_id": 36},
            {"name": "Cadia Riverlands", "existing_id": 37},  # repurposed from DP BO1
        ],
    },
    {
        "key":      "dp3",
        "games":    3,
        "mode":     "Draft Pick",
        "label":    "3-Game 5v5 Draft Pick",
        "leagues": [
            {"name": "Agelta",       "existing_id": 25},   # repurposed from Moniyan BO3
            {"name": "Los Pecados",  "existing_id": 26},   # repurposed from Abyss BO3
            {"name": "Aberleen",     "existing_id": 27},   # repurposed from NV BO3
            {"name": "Dragon Altar", "existing_id": 28},   # repurposed from Cadia BO3
        ],
    },
    {
        "key":      "brawl",
        "games":    None,   # format set per tournament
        "mode":     "Brawl",
        "label":    "5v5 Brawl",
        "leagues": [
            {"name": "Megalith",    "existing_id": 40},   # repurposed from Moniyan Brawl BO1
            {"name": "Vonetis",     "existing_id": 41},   # repurposed from Abyss Brawl BO1
            {"name": "Oasis",       "existing_id": 42},   # repurposed from NV Brawl BO1
            {"name": "Swan Castle", "existing_id": 43},   # repurposed from Moniyan Brawl BO3
        ],
    },
]

# sp_league IDs that are now retired (renamed or consolidated).
# These remain in the DB but are removed from ALL_LEAGUE_IDS in season_init.
RETIRED_IDS = [38, 39, 44, 45, 46, 47, 48]


# ── Image upload ──────────────────────────────────────────────────────────────

def upload_image(image_url: str, image_name: str) -> int:
    """Download image and upload to WP media library. Returns media ID."""
    img_data = requests.get(image_url, timeout=30).content
    content_type = "image/png" if image_name.endswith(".png") else "image/jpeg"
    r = requests.post(
        f"{WP_URL}/wp-json/wp/v2/media",
        auth=AUTH,
        headers={**HEADERS, "Content-Disposition": f'attachment; filename="{image_name}"',
                 "Content-Type": content_type},
        data=img_data,
    )
    r.raise_for_status()
    mid = r.json()["id"]
    print(f"    UPLOADED image: {image_name} (media_id={mid})")
    return mid


def ensure_media_id(lore_key: str) -> int:
    """Return existing media_id or upload image and return new ID."""
    entry = LORE[lore_key]
    if entry["media_id"]:
        return entry["media_id"]
    mid = upload_image(entry["image_url"], entry["image_name"])
    entry["media_id"] = mid   # cache for this run
    return mid


# ── Page content builders ─────────────────────────────────────────────────────

def cover_block(image_url: str, media_id: int, title: str) -> str:
    """Gutenberg cover block using the region's featured image."""
    return (
        f'<!-- wp:cover {{"url":{json.dumps(image_url)},"id":{media_id},"dimRatio":40,'
        f'"minHeight":320,"minHeightUnit":"px"}} -->'
        f'<div class="wp-block-cover" style="min-height:320px">'
        f'<span aria-hidden="true" class="wp-block-cover__background has-background-dim-40 has-background-dim"></span>'
        f'<img class="wp-block-cover__image-background wp-image-{media_id}" alt="" '
        f'src="{image_url}" data-object-fit="cover"/>'
        f'<div class="wp-block-cover__inner-container">'
        f'<!-- wp:heading {{"textAlign":"center","level":1,"style":{{"color":{{"text":"#ffffff"}}}}}} -->'
        f'<h1 class="wp-block-heading has-text-align-center has-text-color" style="color:#ffffff">{title}</h1>'
        f'<!-- /wp:heading -->'
        f'</div></div>'
        f'<!-- /wp:cover -->'
    )


def rules_block(games: int | None, mode: str, format_label: str) -> str:
    fmt_label = (
        f"Best of {games}" if games and games > 1
        else "Single Game" if games == 1
        else "Set per tournament"
    )
    mode_str = "5v5 Custom Room — Draft Pick" if mode == "Draft Pick" else "5v5 Custom Room — Brawl"
    rows = (
        f"<tr><th>Format</th><td>{fmt_label}</td></tr>"
        f"<tr><th>Mode</th><td>{mode_str}</td></tr>"
        "<tr><th>Scheduling</th><td>Ad-Hoc | Fixed</td></tr>"
    )
    return (
        "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
        "<!-- wp:heading --><h2 class=\"wp-block-heading\">League Rules</h2><!-- /wp:heading -->"
        f'<!-- wp:html --><table class="league-rules"><tbody>{rows}</tbody></table><!-- /wp:html -->'
        "<!-- wp:paragraph -->"
        '<p>See the <a href="/general-rules/">General Rules</a> page for sportsmanship guidelines, '
        "disconnect policies, and scheduling definitions.</p>"
        "<!-- /wp:paragraph -->"
    )


def build_page_content(lore_key: str, games: int | None, mode: str, format_label: str,
                        page_title: str, media_id: int) -> str:
    image_url = LORE[lore_key]["image_url"]
    return cover_block(image_url, media_id, page_title) + rules_block(games, mode, format_label)


# ── General Rules page ────────────────────────────────────────────────────────

GENERAL_RULES_SLUG  = "general-rules"
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
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">Scheduling Modes</h2><!-- /wp:heading -->"
    "<!-- wp:list --><ul class=\"wp-block-list\">"
    "<li><strong>Ad-Hoc</strong> — Games can be completed at any time when all teams are available "
    "within the season window. Teams coordinate directly and report the result.</li>"
    "<li><strong>Fixed</strong> — Open competitive time slots are set by league administration "
    "(for example, every Friday, Saturday, and Sunday between 7 PM–11 PM PST). "
    "Teams play during any available slot.</li>"
    "<li><strong>Event</strong> — All matches in a round take place during a single scheduled event session. "
    "Teams that do not show up for a scheduled event match forfeit that round.</li>"
    "</ul><!-- /wp:list -->"
    "<!-- wp:separator --><hr class=\"wp-block-separator has-alpha-channel-opacity\"/><!-- /wp:separator -->"
    "<!-- wp:heading --><h2 class=\"wp-block-heading\">No-Shows</h2><!-- /wp:heading -->"
    "<!-- wp:paragraph --><p>For Fixed scheduling, a 10-minute grace period applies. "
    "After the grace period, the absent team forfeits the match. "
    "For Event scheduling, no grace period is granted — no-shows are an immediate forfeit. "
    "Repeated no-shows may result in removal from the league.</p><!-- /wp:paragraph -->"
)


# ── SP REST helpers ───────────────────────────────────────────────────────────

def sp_get(endpoint):
    r = requests.get(f"{WP_URL}/wp-json/sportspress/v2/{endpoint}",
                     auth=AUTH, headers=HEADERS, params={"per_page": 100})
    r.raise_for_status()
    return r.json()


def get_or_update_league_term(name: str, slug: str, description: str, existing_id: int | None) -> int:
    if existing_id:
        r = requests.post(
            f"{WP_URL}/wp-json/sportspress/v2/leagues/{existing_id}",
            auth=AUTH, headers=HEADERS,
            json={"name": name, "slug": slug, "description": description},
        )
        if r.ok:
            print(f"  UPDATED [league term]: {name} (id={existing_id})")
            return existing_id
        print(f"  WARN: could not update term {existing_id}: {r.status_code}")

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


# ── WP page helpers ───────────────────────────────────────────────────────────

def wpcli(*args) -> str:
    cmd = ["wp", "--allow-root", f"--path={WP_PATH}", "--skip-plugins", "--skip-themes"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"WP-CLI error: {result.stderr.strip()}")
    return result.stdout.strip()


_CONTENT_TMP = "/tmp/mlbb_league_page_content.html"


def get_or_update_page(slug: str, title: str, content: str, media_id: int) -> int:
    with open(_CONTENT_TMP, "w") as f:
        f.write(content)

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    all_league_ids = []

    # ── General Rules page ────────────────────────────────────────────────────
    print("\n=== General Rules Page ===")
    get_or_update_page(GENERAL_RULES_SLUG, GENERAL_RULES_TITLE, GENERAL_RULES_CONTENT, 0)

    # ── League pages ──────────────────────────────────────────────────────────
    for fmt in FORMATS:
        print(f"\n{'='*60}")
        print(f"Format: {fmt['label']}")
        print('='*60)

        for league in fmt["leagues"]:
            lore_key = league["name"]
            lore = LORE[lore_key]
            term_name = f"{lore_key} League"
            term_slug = lore_key.lower().replace(" ", "-") + "-league"
            page_slug  = lore_key.lower().replace(" ", "-") + "-league"
            page_title = f"{lore['display']} League"

            print(f"\n  [{lore_key}]")

            media_id = ensure_media_id(lore_key)

            league_id = get_or_update_league_term(
                name=term_name,
                slug=term_slug,
                description=lore["lore_desc"],
                existing_id=league.get("existing_id"),
            )
            all_league_ids.append(league_id)

            content = build_page_content(
                lore_key, fmt["games"], fmt["mode"], fmt["label"], page_title, media_id
            )
            get_or_update_page(page_slug, page_title, content, media_id)

    print(f"\n✓ Done. {len(all_league_ids)} league terms processed.")
    print(f"\nsp_league IDs for season_init:\n{all_league_ids}")
    print(f"\nRetired IDs (remove from season_init ALL_LEAGUE_IDS):\n{RETIRED_IDS}")


if __name__ == "__main__":
    main()
