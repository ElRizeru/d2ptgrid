import asyncio
import os
import shutil
from playwright.async_api import async_playwright

ROLE_MAPPING = {
    "0": "all_roles",
    "1": "carry",
    "2": "mid",
    "3": "offlane",
    "4": "support",
    "5": "hard_support"
}

CATEGORY_MAPPING = {
    "Most Played": "most_played",
    "High Winrate": "high_winrate",
    "D2PT Rating": "d2pt_rating"
}

BASE_URL = "https://dota2protracker.com/meta-hero-grids"
OUTPUT_DIR = "hero_grids"

async def download_grid(page, role_id, role_name):
    print(f"[PROCESS] {role_name} ({role_id})")
    await page.select_option("select#config-select", role_id)
    await page.wait_for_timeout(2000)
    
    for display_name, folder_name in CATEGORY_MAPPING.items():
        try:
            section = page.locator("div.flex.flex-col.gap-4", has_text=display_name).first
            download_button = section.locator("button", has_text="Download")
            
            async with page.expect_download() as download_info:
                await download_button.click()
            
            download = await download_info.value
            target_path = os.path.join(OUTPUT_DIR, role_name, folder_name, "hero_grid_config.json")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            await download.save_as(target_path)
            print(f"  [SUCCESS] {target_path}")
        except Exception as e:
            print(f"  [ERROR] {role_name} {display_name}: {e}")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="networkidle")
        for role_id, role_name in ROLE_MAPPING.items():
            await download_grid(page, role_id, role_name)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
