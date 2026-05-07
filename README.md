# Dota 2 Pro Tracker Hero Grids Auto-Parser

## Structure
`hero_grids/{role}/{category}/hero_grid_config.json`

## Setup
1. Fork repository.
2. **Settings** -> **Actions** -> **General** -> **Workflow permissions** -> **Read and write permissions**.
3. **Actions** -> **Update Hero Grids** -> **Run workflow**.

## Local
```bash
pip install -r requirements.txt
playwright install chromium
python parser.py
```
