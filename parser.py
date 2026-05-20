import asyncio
import os
import json
import logging
from typing import List, Tuple
from playwright.async_api import async_playwright, Page, Browser
from playwright_stealth import stealth_async
import csv
import shutil
import time
import requests
from odg import compiler
from odg import opendota_api
from odg import utils

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

retryable_status_codes = (429, 504)

def call_opendota_with_wait(description: str, callback):
    while True:
        try:
            return callback()
        except requests.exceptions.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            if status_code in retryable_status_codes:
                logger.info(f"Waiting for OpenDota while {description}; got status {status_code}..")
                time.sleep(15)
                continue
            logger.exception(f"OpenDota returned a non-retryable status while {description}.")
            raise
        except requests.exceptions.Timeout:
            logger.info(f"Waiting for OpenDota while {description}; request timed out..")
            time.sleep(15)
        except requests.exceptions.RequestException:
            logger.exception(f"Waiting for OpenDota while {description}; request failed..")
            time.sleep(15)

async def block_useless_resources(route):
    """Abort requests for images, fonts, media, and analytics scripts to speed up loading."""
    if route.request.resource_type in ["image", "media", "font"]:
        await route.abort()
    elif any(x in route.request.url for x in ["google-analytics", "googletagmanager", "amplitude", "facebook", "doubleclick"]):
        await route.abort()
    else:
        await route.continue_()


async def async_build_odg_guides():
    import concurrent.futures

    logger.info("Starting OpenDotaGuides build process...")
    
    logger.info("Refreshing the data..")
    if os.path.exists(utils.data_directory):
        shutil.rmtree(utils.data_directory)
    if os.path.exists(utils.itembuilds_directory):
        shutil.rmtree(utils.itembuilds_directory)
        
    os.makedirs(utils.data_directory, exist_ok=True)
    os.makedirs(utils.itembuilds_directory, exist_ok=True)

    heroes_map = call_opendota_with_wait("fetching heroes", opendota_api.get_heroes_map)
    items_map = call_opendota_with_wait("fetching items", opendota_api.get_items_map)
    
    # Items by name mapping for compiler
    items_by_name = {}
    for item_id, item_data in items_map.items():
        items_by_name[f"item_{item_data['name']}"] = item_data

    neutral_item_tiers = call_opendota_with_wait(
        "fetching neutral item constants", opendota_api.get_neutral_item_tiers
    )
    ability_ids_map = call_opendota_with_wait(
        "fetching ability id constants", lambda: opendota_api._get_json("/constants/ability_ids")
    )
    hero_abilities_map = call_opendota_with_wait(
        "fetching hero abilities constants", lambda: opendota_api._get_json("/constants/hero_abilities")
    )
    
    # 1. Fetch item popularity guides for all heroes concurrently using ThreadPoolExecutor
    logger.info(f"Concurrently fetching item popularity for {len(heroes_map)} heroes...")
    
    def fetch_pop(hid):
        call_opendota_with_wait(
            f"fetching shop item popularity for {heroes_map[hid]['localized_name']}",
            lambda: opendota_api.get_hero_popularity_guide(hid, items_map),
        )
        
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    loop = asyncio.get_running_loop()
    
    tasks = []
    for hero_id in heroes_map.keys():
        task = loop.run_in_executor(executor, fetch_pop, hero_id)
        tasks.append(task)
        
    await asyncio.gather(*tasks)
    logger.info("Finished concurrently fetching item popularity guides.")
    
    # 2. Get the min_match_id estimation
    min_match_id = call_opendota_with_wait("fetching max match_id boundary", opendota_api.get_min_match_id)
    logger.info(f"Using estimated min_match_id boundary: {min_match_id}")

    # Batch heroes list in groups of 20 for neutral items & ability upgrades
    hero_ids = list(heroes_map.keys())
    batch_size = 20
    batches = [hero_ids[i:i + batch_size] for i in range(0, len(hero_ids), batch_size)]

    # 3. Fetch neutral item popularity in batches
    logger.info(f"Fetching neutral item guides in {len(batches)} batches...")
    for idx, batch in enumerate(batches, start=1):
        logger.info(f"Neutral items batch {idx}/{len(batches)}")
        batch_neutrals = call_opendota_with_wait(
            f"fetching neutral items batch {idx}",
            lambda b=batch: opendota_api.fetch_neutral_items_batch(b, min_match_id, neutral_item_tiers)
        )
        for hid, neutral_guide in batch_neutrals.items():
            opendota_api.save_hero_neutral_guide(hid, neutral_guide)
        await asyncio.sleep(0.5)

    # 4. Fetch ability upgrade guides in batches
    logger.info(f"Fetching ability upgrade guides in {len(batches)} batches...")
    for idx, batch in enumerate(batches, start=1):
        logger.info(f"Ability upgrades batch {idx}/{len(batches)}")
        batch_abilities = call_opendota_with_wait(
            f"fetching ability upgrades batch {idx}",
            lambda b=batch: opendota_api.fetch_ability_upgrades_batch(b, min_match_id, ability_ids_map, hero_abilities_map, heroes_map)
        )
        for hid, ability_guide in batch_abilities.items():
            opendota_api.save_hero_ability_guide(hid, ability_guide)
        await asyncio.sleep(0.5)

    # 5. Compile files
    for i, (hero_id, hero_data) in enumerate(heroes_map.items(), start=1):
        if not os.path.exists(os.path.join(utils.data_directory, f"{hero_id}.json")):
            continue
        logger.info(
            f"Compiling file {i}/{len(heroes_map)} {hero_data['localized_name']}"
        )
        compiler.compile_scrape_to_guide_vdf(hero_id, items_by_name, heroes_map, keep_starting_items=False)

    logger.info("Zipping itembuilds...")
    shutil.make_archive("itembuilds", "zip", utils.itembuilds_directory)
    logger.info("Created itembuilds.zip successfully!")


CATEGORIES: List[Tuple[str, str]] = [
    ("Most Played", "most_played"),
    ("Most Picked Heroes (>50% Winrate)", "high_winrate"),
    ("D2PT Rating", "d2pt_rating")
]

BASE_URL = "https://dota2protracker.com/meta-hero-grids"
OUTPUT_DIR = "hero_grids"


async def validate_json(file_path: str) -> bool:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "configs" in data and isinstance(data["configs"], list):
                return True
            logger.warning(f"JSON at {file_path} is valid but missing 'configs' key")
            return False
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Validation failed for {file_path}: {e}")
        return False


async def download_grid(page: Page):
    try:
        selector = "button:has-text('Download')"
        logger.info(f"Waiting for selector: {selector}")
        await page.wait_for_selector(selector, state="attached", timeout=180000)
        
        await asyncio.sleep(2)
        
        buttons = page.locator("button", has_text="Download")
        count = await buttons.count()
        
        if count < len(CATEGORIES):
            logger.error(f"Found only {count} buttons, expected at least {len(CATEGORIES)}")
            return

        for i, (display_name, folder_name) in enumerate(CATEGORIES):
            try:
                logger.info(f"Processing category: {display_name}")
                download_button = buttons.nth(i)
                
                async with page.expect_download(timeout=180000) as download_info:
                    await download_button.click()
                
                download = await download_info.value
                target_path = os.path.join(OUTPUT_DIR, folder_name, "hero_grid_config.json")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                
                await download.save_as(target_path)
                
                if await validate_json(target_path):
                    logger.info(f"[SUCCESS] {target_path}")
                else:
                    logger.error(f"[FAILURE] {target_path} is invalid or incomplete")
                    
            except Exception as e:
                logger.error(f"Error downloading {display_name}: {e}")
                
    except Exception as e:
        logger.critical(f"Fatal error during download process: {e}")


async def main():
    async with async_playwright() as p:
        logger.info("Launching browser...")
        browser: Browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page: Page = await context.new_page()
        await stealth_async(page)
        
        # Intercept and abort useless resources to speed up page load
        await page.route("**/*", block_useless_resources)
        
        try:
            logger.info(f"Navigating to {BASE_URL}...")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=180000)
            
            logger.info("Waiting for page to load dynamic elements (2s)...")
            await asyncio.sleep(2)
            
            await download_grid(page)
        except Exception as e:
            logger.error(f"Navigation or page interaction failed: {e}")
        finally:
            await browser.close()
            logger.info("Browser closed")


async def run_all():
    await main()
    await async_build_odg_guides()


if __name__ == "__main__":
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}")
        import sys
        sys.exit(1)

