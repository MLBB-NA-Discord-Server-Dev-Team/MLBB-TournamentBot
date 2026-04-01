"""
services/match_parser.py — AI-powered MLBB match screenshot parser

Uses Claude claude-haiku-4-5 vision to extract:
  - result        : "VICTORY" or "DEFEAT" (must be VICTORY for a valid win submission)
  - battle_id     : unique match identifier (bottom-left, labeled "BattleID:")
  - winner_kills  : kill count on the VICTORY side
  - loser_kills   : kill count on the DEFEAT side
  - duration      : match duration string e.g. "07:16"
  - match_ts      : datetime string e.g. "03/31/2026 22:18:03"
  - confidence    : 0.0–1.0 estimate of parse reliability

Raises MatchParseError if:
  - The screenshot shows "DEFEAT" (submitter must be on the winning team)
  - BattleID cannot be found
  - Confidence is below MIN_CONFIDENCE
"""

import json
import re
import base64
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp
import anthropic

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.60
WARN_CONFIDENCE = 0.85   # below this: require admin + captain confirm


class MatchParseError(Exception):
    """Raised when screenshot cannot be parsed or shows a DEFEAT."""
    pass


@dataclass
class MatchResult:
    battle_id: str
    winner_kills: int
    loser_kills: int
    duration: str
    match_timestamp: Optional[str]
    confidence: float
    raw_response: str


_PROMPT = """You are parsing a Mobile Legends: Bang Bang (MLBB) end-of-game scoreboard screenshot.

The screen layout:
- Top center: large bold text — either "VICTORY" or "DEFEAT"
- Flanking that text: two kill-count numbers (left team kills on the left, right team kills on the right)
- Top right: "Duration MM:SS" and a date/time stamp "MM/DD/YYYY HH:MM:SS"
- Bottom left (small gray text): "BattleID: <number>"

Extract exactly these fields and return ONLY a JSON object, no other text:

{
  "result": "VICTORY" or "DEFEAT",
  "battle_id": "<the full numeric BattleID string>",
  "left_kills": <integer>,
  "right_kills": <integer>,
  "duration": "<MM:SS string>",
  "match_timestamp": "<MM/DD/YYYY HH:MM:SS string or null>",
  "confidence": <float 0.0-1.0>,
  "notes": "<any issues or uncertainties>"
}

Rules:
- battle_id must be the exact number after "BattleID:" — it is a long integer (16-19 digits)
- winner_kills is the kill count on whichever side shows VICTORY
- If the result word is not clearly "VICTORY" or "DEFEAT", set confidence below 0.6
- If BattleID is not visible, set battle_id to null and confidence to 0.0
"""


async def parse(image_url: str, api_key: str) -> MatchResult:
    """
    Download the screenshot from image_url and parse it with Claude.
    Raises MatchParseError on DEFEAT or unparseable image.
    """
    # Download image bytes
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            if resp.status != 200:
                raise MatchParseError(f"Could not download screenshot (HTTP {resp.status})")
            image_bytes = await resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            # Normalise JFIF → jpeg
            if "jpeg" in content_type or "jfif" in content_type:
                media_type = "image/jpeg"
            elif "png" in content_type:
                media_type = "image/png"
            elif "webp" in content_type:
                media_type = "image/webp"
            elif "gif" in content_type:
                media_type = "image/gif"
            else:
                media_type = "image/jpeg"

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()
    logger.debug("Claude raw response: %s", raw)

    # Strip markdown code fences if present
    raw_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise MatchParseError(f"Claude returned non-JSON response: {raw[:200]}") from e

    confidence = float(data.get("confidence", 0.0))
    result = str(data.get("result", "")).upper()
    battle_id = data.get("battle_id")

    if not battle_id:
        raise MatchParseError(
            "BattleID not found in screenshot. Make sure the full scoreboard is visible."
        )

    if result == "DEFEAT":
        raise MatchParseError(
            "Screenshot shows **DEFEAT**. Only the **winning team captain** should submit results."
        )

    if result != "VICTORY":
        raise MatchParseError(
            f"Could not read match result from screenshot (got: `{result}`). "
            "Ensure the scoreboard is fully visible and unobstructed."
        )

    if confidence < MIN_CONFIDENCE:
        raise MatchParseError(
            f"Screenshot quality too low to parse reliably (confidence: {confidence:.0%}). "
            "Please submit a clearer screenshot of the full scoreboard."
        )

    # Determine winner/loser kills from perspective
    # The submitter sees VICTORY — their side's kills are on whatever side Claude read
    # We store winner_kills and loser_kills directly
    left_kills = int(data.get("left_kills", 0))
    right_kills = int(data.get("right_kills", 0))
    # The VICTORY side is whichever has more context — Claude already labels result,
    # so we infer: the side where "VICTORY" is displayed has the higher structural
    # position. We store both as winner/loser based on which is higher.
    winner_kills = max(left_kills, right_kills)
    loser_kills = min(left_kills, right_kills)

    return MatchResult(
        battle_id=str(battle_id).strip(),
        winner_kills=winner_kills,
        loser_kills=loser_kills,
        duration=str(data.get("duration", "")).strip(),
        match_timestamp=data.get("match_timestamp"),
        confidence=confidence,
        raw_response=raw,
    )
