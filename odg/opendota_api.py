import csv
import json
import logging
import os
import re

import requests

from . import utils

opendota_api_url = "https://api.opendota.com/api"
request_timeout = 60

logger = logging.getLogger(__name__)


def _get_json(endpoint: str, params=None, timeout=request_timeout, allow_not_found=0):
    response = requests.get(f"{opendota_api_url}{endpoint}", params=params, timeout=timeout)
    if allow_not_found and response.status_code == 404:
        logger.info("OpenDota endpoint %s was not found.", endpoint)
        return None
    if response.status_code in (429, 504):
        logger.info("OpenDota endpoint %s returned %s.", endpoint, response.status_code)
    response.raise_for_status()
    return response.json()


def _hero_data_path(hero_id: str) -> str:
    return os.path.join(utils.data_directory, f"{hero_id}.json")


def _write_hero_data(hero_id: str, hero_data: dict):
    with open(_hero_data_path(hero_id), "w") as json_file:
        json.dump(hero_data, json_file, indent=2)


def _normalise_item_name(item_name) -> str:
    if item_name is None:
        return ""
    item_name = str(item_name).strip()
    if item_name.startswith("item_"):
        item_name = item_name[5:]
    return item_name


def _parse_neutral_tier(tier) -> int | None:
    if tier is None:
        return None
    if isinstance(tier, int):
        return tier if 1 <= tier <= 5 else None

    match = re.search(r"[1-5]", str(tier))
    if match:
        return int(match.group(0))
    return None


def _tier_from_key(key) -> int | None:
    if key is None:
        return None
    key = str(key).lower()
    if "tier" not in key:
        return None
    return _parse_neutral_tier(key)


def _register_neutral_item_tier(neutral_item_tiers: dict, item_name, tier):
    tier = _parse_neutral_tier(tier)
    item_name = _normalise_item_name(item_name)
    if not item_name or tier is None:
        return
    neutral_item_tiers[item_name] = tier


def _looks_like_item_attrs(value) -> bool:
    if not isinstance(value, dict):
        return False
    return any(item_key in value for item_key in ("id", "name", "item", "item_name", "item_neutral", "key"))


def _register_neutral_item_attrs(neutral_item_tiers: dict, item_attrs, tier, item_name=None):
    _register_neutral_item_tier(neutral_item_tiers, item_name, tier)
    if isinstance(item_attrs, dict):
        _register_neutral_item_tier(neutral_item_tiers, item_attrs.get("id"), tier)
        for item_name_key in ("name", "item", "item_name", "item_neutral", "key"):
            _register_neutral_item_tier(neutral_item_tiers, item_attrs.get(item_name_key), tier)
    else:
        _register_neutral_item_tier(neutral_item_tiers, item_attrs, tier)


def _register_neutral_item_collection(neutral_item_tiers: dict, neutral_items, tier):
    if isinstance(neutral_items, list):
        for neutral_item in neutral_items:
            _register_neutral_item_collection(neutral_item_tiers, neutral_item, tier)
    elif isinstance(neutral_items, dict):
        if _looks_like_item_attrs(neutral_items):
            _register_neutral_item_attrs(neutral_item_tiers, neutral_items, tier)
            return
        for item_name, item_attrs in neutral_items.items():
            _register_neutral_item_attrs(neutral_item_tiers, item_attrs, tier, item_name=item_name)
    else:
        _register_neutral_item_tier(neutral_item_tiers, neutral_items, tier)


def _extract_neutral_item_tiers(constants_data) -> dict:
    neutral_item_tiers = {}

    if isinstance(constants_data, list):
        for neutral_item in constants_data:
            if isinstance(neutral_item, dict):
                tier = neutral_item.get("tier") or neutral_item.get("neutral_tier") or neutral_item.get("item_tier")
                if tier is not None:
                    _register_neutral_item_attrs(neutral_item_tiers, neutral_item, tier)
            else:
                logger.debug("Skipping neutral item constant without tier data: %s", neutral_item)
        return neutral_item_tiers

    if not isinstance(constants_data, dict):
        return neutral_item_tiers

    for item_name, item_attrs in constants_data.items():
        if isinstance(item_attrs, dict):
            tier = item_attrs.get("tier") or item_attrs.get("neutral_tier") or item_attrs.get("item_tier")
            if tier is not None:
                _register_neutral_item_attrs(neutral_item_tiers, item_attrs, tier, item_name=item_name)

        tier = _tier_from_key(item_name)
        if tier is not None:
            _register_neutral_item_collection(neutral_item_tiers, item_attrs, tier)

    return neutral_item_tiers


def get_neutral_item_tiers():
    """Gets a mapping of neutral item names/ids to their neutral tier."""
    constants_data = _get_json("/constants/items")
    neutral_item_tiers = _extract_neutral_item_tiers(constants_data)

    if not neutral_item_tiers:
        raise ValueError("Could not find neutral item tier data in OpenDota constants.")

    logger.debug("Loaded tier data for %s neutral item keys.", len(neutral_item_tiers))
    return neutral_item_tiers


def _neutral_item_query(hero_id: str) -> str:
    hero_id = int(hero_id)
    # OpenDota Explorer currently exposes neutral_item_history as json[].
    return f"""
SELECT
  neutral_item,
  COUNT(*) as pick_count
FROM (
  SELECT
    jsonb_array_elements(to_jsonb(pm.neutral_item_history))->>'item_neutral' AS neutral_item
  FROM (
    SELECT match_id, neutral_item_history
    FROM player_matches
    WHERE hero_id = {hero_id}
    ORDER BY match_id DESC
    LIMIT 2000
  ) pm
  JOIN matches m ON pm.match_id = m.match_id
  WHERE m.start_time > extract(epoch from now() - interval '30 days')
) hero_neutral_items
WHERE neutral_item IS NOT NULL
GROUP BY neutral_item
ORDER BY pick_count DESC;
"""


def _get_explorer_rows(sql: str):
    response = requests.get(f"{opendota_api_url}/explorer", params={"sql": sql}, timeout=request_timeout)
    if response.status_code == 400:
        logger.error(f"Explorer query returned 400: {response.text}")
    response.raise_for_status()
    response_json = response.json()
    if response_json.get("err"):
        raise ValueError(response_json["err"])
    return response_json.get("rows", [])


def _item_guide_name(item_name: str, items_map: dict | None = None) -> str:
    item_name = _normalise_item_name(item_name)
    if items_map:
        for item in items_map.values():
            if item.get("name") == item_name:
                return f"item_{item_name}"
    return f"item_{item_name}"


def get_hero_neutral_item_guide(hero_id: str, neutral_item_tiers: dict | None = None):
    """Gets top 3 neutral item recommendations per tier for the specified hero id."""
    if neutral_item_tiers is None:
        neutral_item_tiers = get_neutral_item_tiers()

    neutral_items = {f"tier_{tier}": [] for tier in range(1, 6)}
    seen_items = {f"tier_{tier}": set() for tier in range(1, 6)}
    rows = _get_explorer_rows(_neutral_item_query(hero_id))

    for row in rows:
        item_name = _normalise_item_name(row.get("neutral_item"))
        if not item_name:
            continue

        tier = neutral_item_tiers.get(item_name) or neutral_item_tiers.get(str(row.get("neutral_item")))
        if tier is None:
            logger.debug("Skipping neutral item %s without tier data.", item_name)
            continue

        tier_key = f"tier_{tier}"
        if len(neutral_items[tier_key]) >= 3:
            continue

        guide_name = _item_guide_name(item_name, None)
        if guide_name in seen_items[tier_key]:
            continue

        neutral_items[tier_key].append(guide_name)
        seen_items[tier_key].add(guide_name)

    return neutral_items


def append_hero_neutral_item_guide(hero_id: str, neutral_item_tiers: dict | None = None):
    """Fetches and appends neutral item recommendations into the hero's data json."""
    hero_path = _hero_data_path(hero_id)
    if os.path.exists(hero_path):
        with open(hero_path) as json_file:
            hero_data = json.load(json_file)
    else:
        hero_data = {}

    hero_data[utils.neutral_items_key] = get_hero_neutral_item_guide(hero_id, neutral_item_tiers=neutral_item_tiers)
    _write_hero_data(hero_id, hero_data)


def _ability_query(hero_id: str) -> str:
    hero_id = int(hero_id)
    return f"""
SELECT ability_upgrades_arr, COUNT(*) as count
FROM (
  SELECT pm.ability_upgrades_arr
  FROM (
    SELECT match_id, ability_upgrades_arr
    FROM player_matches
    WHERE hero_id = {hero_id}
    ORDER BY match_id DESC
    LIMIT 2000
  ) pm
  JOIN matches m ON pm.match_id = m.match_id
  WHERE m.start_time > extract(epoch from now() - interval '30 days')
) recent_matches
WHERE array_length(ability_upgrades_arr, 1) >= 15
GROUP BY ability_upgrades_arr
ORDER BY count DESC
LIMIT 1;
"""


def get_hero_ability_guide(hero_id: str, ability_ids_map: dict | None = None, hero_abilities_map: dict | None = None, heroes_map: dict | None = None):
    """Gets the most popular ability upgrade order for the hero."""
    if ability_ids_map is None:
        ability_ids_map = _get_json("/constants/ability_ids")
    if hero_abilities_map is None:
        hero_abilities_map = _get_json("/constants/hero_abilities")
        
    rows = _get_explorer_rows(_ability_query(hero_id))
    if not rows:
        return []
    
    ability_ids_arr = rows[0].get("ability_upgrades_arr", [])
    
    # Get the valid abilities for this hero to prevent parser-breaking legacy/invalid talents
    hero_name = None
    if hero_id in heroes_map:
        hero_name = heroes_map[hero_id]["name"]
        
    valid_abilities = set()
    if hero_name and hero_name in hero_abilities_map:
        ha = hero_abilities_map[hero_name]
        if "abilities" in ha:
            for ability in ha["abilities"]:
                if isinstance(ability, list):
                    valid_abilities.update(ability)
                else:
                    valid_abilities.add(ability)
        if "talents" in ha:
            for t in ha["talents"]:
                valid_abilities.add(t["name"])
    
    ability_guide = []
    for ability_id in ability_ids_arr:
        ability_name = ability_ids_map.get(str(ability_id))
        if ability_name:
            if ability_name == "special_bonus_attributes":
                ability_guide.append("") # Keep the index empty for stats
            elif not valid_abilities or ability_name in valid_abilities:
                ability_guide.append(ability_name)
            else:
                ability_guide.append("") # Skip invalid abilities to maintain level alignment
    
    return ability_guide


def append_hero_ability_guide(hero_id: str, ability_ids_map: dict | None = None, hero_abilities_map: dict | None = None, heroes_map: dict | None = None):
    """Fetches and appends ability upgrade recommendations into the hero's data json."""
    hero_path = _hero_data_path(hero_id)
    if os.path.exists(hero_path):
        with open(hero_path) as json_file:
            hero_data = json.load(json_file)
    else:
        hero_data = {}

    hero_data[utils.ability_upgrades_key] = get_hero_ability_guide(hero_id, ability_ids_map=ability_ids_map, hero_abilities_map=hero_abilities_map, heroes_map=heroes_map)
    _write_hero_data(hero_id, hero_data)


def get_hero_popularity_guide(hero_id: str, items_map: dict):
    """Gets the item popularies for the specified hero id."""
    guide = _get_json(f"/heroes/{hero_id}/itemPopularity")

    stages = {}
    for key, value in guide.items():
        stage = []
        for inner_keys in value.keys():
            if str(inner_keys) in items_map:
                item_name = items_map[str(inner_keys)].get("name")
                if item_name:
                    stage.append(f"item_{item_name}")
            else:
                logger.debug("Skipping unknown item id %s for hero %s.", inner_keys, hero_id)
        stages[key] = stage

    hero_path = _hero_data_path(hero_id)
    if os.path.exists(hero_path):
        with open(hero_path) as json_file:
            hero_data = json.load(json_file)
    else:
        hero_data = {}

    hero_data.update(stages)
    _write_hero_data(hero_id, hero_data)

def get_heroes_map() -> dict:
    heroes = _get_json("/heroes")
    heroes_map = {}
    for hero in heroes:
        heroes_map[str(hero["id"])] = {
            "name": hero["name"],
            "localized_name": hero["localized_name"],
            "guide_name": f"default_{hero['name'][14:]}"
        }
    return heroes_map

def get_items_map() -> dict:
    items = _get_json("/constants/items")
    items_map = {}
    for item_key, item_attrs in items.items():
        if "id" in item_attrs:
            cost = item_attrs.get("cost")
            if cost is None:
                cost = 0
            
            flag = utils.ITEM_FLAGS_OVERRIDES.get(item_key, "")
            # Apply heuristics ONLY if there is no explicit override
            if flag == "":
                if "recipe" in item_key.lower():
                    flag = "component"
                elif cost > 0 and cost <= 250:
                    flag = "consumable"
                
            items_map[str(item_attrs["id"])] = {
                "name": item_key,
                "dname": item_attrs.get("dname", ""),
                "cost": cost,
                "flag": flag
            }
    return items_map


def get_min_match_id(max_id_offset=40000000) -> int:
    """Gets the current max match_id and estimates the 30-day minimum match_id using offset."""
    rows = _get_explorer_rows("SELECT MAX(match_id) as max_id FROM matches")
    if not rows or not rows[0].get("max_id"):
        # Fallback value if query fails or returns empty
        return 8770000000
    return int(rows[0]["max_id"]) - max_id_offset


def fetch_neutral_items_batch(hero_ids: list, min_match_id: int, neutral_item_tiers: dict) -> dict:
    """Fetches neutral item popularity for a batch of heroes and parses them into guides."""
    neutral_queries = []
    for hid in hero_ids:
        q = f"""
      SELECT
        {hid} as hero_id,
        neutral_item,
        COUNT(*) as pick_count
      FROM (
        SELECT
          jsonb_array_elements(to_jsonb(pm.neutral_item_history))->>'item_neutral' AS neutral_item
        FROM player_matches pm
        WHERE hero_id = {hid} AND match_id > {min_match_id}
        ORDER BY match_id DESC
        LIMIT 2000
      ) hero_neutral_items
      WHERE neutral_item IS NOT NULL
      GROUP BY neutral_item
    """
        neutral_queries.append(q)

    union_sql = "\nUNION ALL\n".join(neutral_queries)
    rows = _get_explorer_rows(union_sql)

    hero_rows = {str(hid): [] for hid in hero_ids}
    for row in rows:
        hid = str(row.get("hero_id"))
        if hid in hero_rows:
            hero_rows[hid].append(row)

    batch_guides = {}
    for hid, rows_list in hero_rows.items():
        neutral_items = {f"tier_{tier}": [] for tier in range(1, 6)}
        seen_items = {f"tier_{tier}": set() for tier in range(1, 6)}

        for row in rows_list:
            item_name = _normalise_item_name(row.get("neutral_item"))
            if not item_name:
                continue

            tier = neutral_item_tiers.get(item_name) or neutral_item_tiers.get(str(row.get("neutral_item")))
            if tier is None:
                continue

            tier_key = f"tier_{tier}"
            if len(neutral_items[tier_key]) >= 3:
                continue

            guide_name = _item_guide_name(item_name, None)
            if guide_name in seen_items[tier_key]:
                continue

            neutral_items[tier_key].append(guide_name)
            seen_items[tier_key].add(guide_name)

        batch_guides[hid] = neutral_items

    return batch_guides


def fetch_ability_upgrades_batch(hero_ids: list, min_match_id: int, ability_ids_map: dict, hero_abilities_map: dict, heroes_map: dict) -> dict:
    """Fetches ability upgrades for a batch of heroes and parses them into upgrade sequences."""
    ability_queries = []
    for hid in hero_ids:
        q = f"""
      (SELECT 
        {hid} as hero_id,
        ability_upgrades_arr, 
        COUNT(*) as count
      FROM (
        SELECT ability_upgrades_arr
        FROM player_matches
        WHERE hero_id = {hid} AND match_id > {min_match_id}
        ORDER BY match_id DESC
        LIMIT 2000
      ) pm
      WHERE array_length(ability_upgrades_arr, 1) >= 15
      GROUP BY ability_upgrades_arr
      ORDER BY count DESC
      LIMIT 1)
    """
        ability_queries.append(q)

    union_sql = "\nUNION ALL\n".join(ability_queries)
    rows = _get_explorer_rows(union_sql)

    batch_guides = {str(hid): [] for hid in hero_ids}

    for row in rows:
        hid = str(row.get("hero_id"))
        ability_ids_arr = row.get("ability_upgrades_arr", [])
        
        hero_name = None
        if hid in heroes_map:
            hero_name = heroes_map[hid]["name"]
            
        valid_abilities = set()
        if hero_name and hero_name in hero_abilities_map:
            ha = hero_abilities_map[hero_name]
            if "abilities" in ha:
                for ability in ha["abilities"]:
                    if isinstance(ability, list):
                        valid_abilities.update(ability)
                    else:
                        valid_abilities.add(ability)
            if "talents" in ha:
                for t in ha["talents"]:
                    valid_abilities.add(t["name"])
        
        ability_guide = []
        for ability_id in ability_ids_arr:
            ability_name = ability_ids_map.get(str(ability_id))
            if ability_name:
                if ability_name == "special_bonus_attributes":
                    ability_guide.append("")
                elif not valid_abilities or ability_name in valid_abilities:
                    ability_guide.append(ability_name)
                else:
                    ability_guide.append("")
        
        batch_guides[hid] = ability_guide

    return batch_guides


def save_hero_neutral_guide(hero_id: str, neutral_guide: dict):
    """Saves neutral item guide for a hero."""
    hero_path = _hero_data_path(hero_id)
    if os.path.exists(hero_path):
        with open(hero_path) as json_file:
            hero_data = json.load(json_file)
    else:
        hero_data = {}
    hero_data[utils.neutral_items_key] = neutral_guide
    _write_hero_data(hero_id, hero_data)


def save_hero_ability_guide(hero_id: str, ability_guide: list):
    """Saves ability upgrade guide for a hero."""
    hero_path = _hero_data_path(hero_id)
    if os.path.exists(hero_path):
        with open(hero_path) as json_file:
            hero_data = json.load(json_file)
    else:
        hero_data = {}
    hero_data[utils.ability_upgrades_key] = ability_guide
    _write_hero_data(hero_id, hero_data)

