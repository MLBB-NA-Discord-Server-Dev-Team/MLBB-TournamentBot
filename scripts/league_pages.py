"""
scripts/league_pages.py
Creates/updates WP pages and descriptions for each lore league.
- Uploads fandom images to WP media library
- Sets description on sp_league taxonomy terms
- Creates (or updates) a WP page per league with lore content + featured image
Idempotent — safe to re-run.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import mimetypes
import time
from dotenv import load_dotenv

load_dotenv()

WP_URL  = os.getenv("WP_PLAY_MLBB_URL", "https://play.mlbb.site").strip().strip('"').rstrip("/")
WP_USER = os.getenv("WP_PLAY_MLBB_USER", "admin").strip().strip('"')
WP_PASS = os.getenv("WP_PLAY_MLBB", "").strip().strip('"')
AUTH    = (WP_USER, WP_PASS)
HEADERS = {"User-Agent": "MLBB-TournamentBot/1.0"}

LEAGUES = [
    {
        "name":        "Moniyan League",
        "slug":        "moniyan-league",
        "sp_id":       25,
        "image_url":   "https://static.wikia.nocookie.net/mobile-legends/images/3/32/Moniyan_Empire.jpg/revision/latest?cb=20200411133754",
        "image_name":  "moniyan-empire.jpg",
        "description": (
            "The Moniyan Empire is a holy empire and bastion of light, built by those who believe in the "
            "Lord of Light and centered around the prosperous capital city of Lumina City. Humanity united "
            "under the Moniyan banner, establishing the Church of Light and the Imperial Knights to defend "
            "the realm against demonic threats. The Empire is ruled by Princess Silvanna and her family, "
            "where the Lightborn Heroes live to protect and serve the light against the darkness of the Abyss."
        ),
        "page_content": (
            "<p>The <strong>Moniyan Empire</strong> is a holy empire and bastion of light, built by those "
            "who believe in the Lord of Light and centered around the prosperous capital city of Lumina City. "
            "Humanity united under the Moniyan banner, establishing the Church of Light and the Imperial "
            "Knights to defend the realm against demonic threats.</p>"
            "<p>The Empire is ruled by Princess Silvanna and her family, where the Lightborn Heroes live to "
            "protect and serve the light against the darkness of the Abyss.</p>"
            "<p>Teams competing in the <strong>Moniyan League</strong> carry the banner of the Empire's "
            "honor into battle each season.</p>"
        ),
    },
    {
        "name":        "Abyss League",
        "slug":        "abyss-league",
        "sp_id":       26,
        "image_url":   "https://static.wikia.nocookie.net/mobile-legends/images/8/85/Prince_of_the_Abyss.jpg/revision/latest?cb=20211222023348",
        "image_name":  "the-abyss.jpg",
        "description": (
            "The Abyss is a hideous scar carved into the Land of Dawn where innumerable demons lurk, "
            "plotting to devour the light and plunge the world into darkness. Deep within the southern "
            "mountains lies this bottomless realm where darkness reigns supreme, with the only light being "
            "crimson lava flowing through the crevices. Sealed at its bottom is the most terrible demon "
            "in the Land of Dawn — the Abyss Dominator — whose will grows stronger as ancient seals fade."
        ),
        "page_content": (
            "<p>The <strong>Abyss</strong> is a hideous scar carved into the Land of Dawn where innumerable "
            "demons lurk, plotting to devour the light and plunge the world into darkness. Deep within the "
            "southern mountains lies this bottomless realm where darkness reigns supreme, with the only light "
            "being crimson lava flowing through the crevices.</p>"
            "<p>Sealed at its bottom is the most terrible demon in the Land of Dawn — the Abyss Dominator — "
            "whose will grows stronger as ancient seals gradually fade.</p>"
            "<p>Teams competing in the <strong>Abyss League</strong> embrace the raw power of darkness and "
            "fight with relentless aggression each season.</p>"
        ),
    },
    {
        "name":        "Northern Vale League",
        "slug":        "northern-vale-league",
        "sp_id":       27,
        "image_url":   "https://static.wikia.nocookie.net/mobile-legends/images/e/e0/Northern_Vale_-_Ship_1.jpg/revision/latest?cb=20200408100818",
        "image_name":  "northern-vale.jpg",
        "description": (
            "Northern Vale is the coldest spot in the Land of Dawn, a continent of ice and snow surrounded "
            "by the vast Frozen Sea. This harsh region is home to the tenacious and brave Northern Valers, "
            "whose ancestors — the Iceland Golems — once built a splendid civilization there. According to "
            "Northern Valer legends, dying on the battlefield is a unique honor, as heroes' souls are "
            "believed to be reunited in the Sacred Palace built by their ancestors."
        ),
        "page_content": (
            "<p><strong>Northern Vale</strong> is the coldest spot in the Land of Dawn, a continent of ice "
            "and snow surrounded by the vast Frozen Sea. This harsh, icy region is home to the tenacious "
            "and brave Northern Valers, whose ancestors — the Iceland Golems — once built a splendid "
            "civilization there.</p>"
            "<p>According to Northern Valer legends, dying on the battlefield is a unique honor, as heroes' "
            "souls are believed to be reunited in the Sacred Palace built by their ancestors.</p>"
            "<p>Teams competing in the <strong>Northern Vale League</strong> embody the unyielding spirit "
            "of the frozen north and never surrender.</p>"
        ),
    },
    {
        "name":        "Cadia Riverlands League",
        "slug":        "cadia-riverlands-league",
        "sp_id":       28,
        "image_url":   "https://static.wikia.nocookie.net/mobile-legends/images/2/23/MLBB_Project_NEXT_Ling%2C_Wanwan_and_Yu_Zhong_in_Cadia_Riverlands_Entrance_Background.png/revision/latest?cb=20200831044304",
        "image_name":  "cadia-riverlands.png",
        "description": (
            "Cadia Riverlands is an isolated ancient land at the easternmost tip of the Land of Dawn, "
            "where harmony among all things has always been the central philosophy guiding all inhabitants. "
            "Different city-states are scattered across this region like pieces on a chessboard, each "
            "possessing unique cultural heritage. The Great Dragon and his disciples protect this land, "
            "fostering peaceful coexistence between diverse races and cultures in this naturally harmonious realm."
        ),
        "page_content": (
            "<p><strong>Cadia Riverlands</strong> is an isolated ancient land at the easternmost tip of the "
            "Land of Dawn, where harmony among all things has always been the central philosophy guiding all "
            "inhabitants. Different city-states are scattered across this region like pieces on a chessboard, "
            "each possessing unique cultural heritage based on Eastern traditions.</p>"
            "<p>The Great Dragon and his disciples protect this land, fostering peaceful coexistence between "
            "diverse races and cultures in this abundant, naturally harmonious realm.</p>"
            "<p>Teams competing in the <strong>Cadia Riverlands League</strong> honor the ancient ways of "
            "discipline, strategy, and balance in every match.</p>"
        ),
    },
]


# ── WP REST helpers ───────────────────────────────────────────────────────────

def upload_image(image_url: str, filename: str) -> int:
    """Download image from URL and upload to WP media library. Returns media ID."""
    # Check if already uploaded by slug
    existing = requests.get(
        f"{WP_URL}/wp-json/wp/v2/media",
        auth=AUTH, headers=HEADERS,
        params={"search": filename.rsplit(".", 1)[0], "per_page": 5}
    ).json()
    for m in existing:
        if m.get("slug", "").startswith(filename.rsplit(".", 1)[0].replace(".", "-")):
            print(f"  EXISTS [media]: {filename} (id={m['id']})")
            return m["id"]

    print(f"  Downloading: {image_url}")
    img_resp = requests.get(image_url, headers={"User-Agent": "MLBB-TournamentBot/1.0"}, timeout=30)
    img_resp.raise_for_status()

    mime = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    upload = requests.post(
        f"{WP_URL}/wp-json/wp/v2/media",
        auth=AUTH,
        headers={**HEADERS, "Content-Disposition": f'attachment; filename="{filename}"',
                 "Content-Type": mime},
        data=img_resp.content
    )
    upload.raise_for_status()
    media_id = upload.json()["id"]
    print(f"  UPLOADED [media]: {filename} (id={media_id})")
    time.sleep(5)  # allow WP to settle after media upload
    return media_id


def set_league_description(sp_id: int, description: str, name: str):
    r = requests.post(
        f"{WP_URL}/wp-json/sportspress/v2/leagues/{sp_id}",
        auth=AUTH, headers=HEADERS,
        json={"description": description}
    )
    if r.ok:
        print(f"  OK [league desc]: {name}")
    else:
        print(f"  FAIL [league desc]: {name} {r.status_code}")


def get_or_create_page(slug: str, title: str, content: str, media_id: int) -> int:
    existing = requests.get(
        f"{WP_URL}/wp-json/wp/v2/pages",
        auth=AUTH, headers=HEADERS,
        params={"slug": slug}
    ).json()
    payload = {
        "title":          title,
        "content":        content,
        "status":         "publish",
        "slug":           slug,
        "featured_media": media_id,
    }
    if existing:
        page_id = existing[0]["id"]
        for attempt in range(3):
            r = requests.post(f"{WP_URL}/wp-json/wp/v2/pages/{page_id}",
                              auth=AUTH, headers=HEADERS, json=payload)
            if r.ok:
                break
            time.sleep(3)
        r.raise_for_status()
        print(f"  UPDATED [page]: {title} (id={page_id})")
        return page_id
    else:
        for attempt in range(3):
            r = requests.post(f"{WP_URL}/wp-json/wp/v2/pages",
                              auth=AUTH, headers=HEADERS, json=payload)
            if r.ok:
                break
            time.sleep(3)
        r.raise_for_status()
        page_id = r.json()["id"]
        print(f"  CREATED [page]: {title} (id={page_id})")
        return page_id


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    for league in LEAGUES:
        print(f"\n=== {league['name']} ===")

        media_id = upload_image(league["image_url"], league["image_name"])
        set_league_description(league["sp_id"], league["description"], league["name"])
        get_or_create_page(
            slug=league["slug"],
            title=league["name"],
            content=league["page_content"],
            media_id=media_id,
        )

    print("\n✓ League pages complete.")


if __name__ == "__main__":
    main()
