$ASCII_ART = @"
 ____  ____  ____ _____    ____ ____  ___ ____  
|  _ \|___ \|  _ \_   _|  / ___|  _ \|_ _|  _ \ 
| | | | __) | |_) || |   | |  _| |_) || || | | |
| |_| |/ __/|  __/ | |   | |_| |  _ < | || |_| |
|____/|_____|_|    |_|    \____|_| \_\___|____/ 
"@

$ITEMBUILDS_URL = "https://github.com/Egezenn/OpenDotaGuides/releases/latest/download/itembuilds.zip"
$REPO = "ElRizeru/d2ptparsegrid"
$GRID_URL_TEMPLATE = "https://raw.githubusercontent.com/$REPO/main/hero_grids/{0}/hero_grid_config.json"
$CONFIG_FILE = "config.json"

$CATEGORIES = @{
    "1" = @("most_played", "Most Played")
    "2" = @("high_winrate", "Most Picked Heroes (>50% Winrate)")
    "3" = @("d2pt_rating", "D2PT Rating")
}

function Get-SteamPath {
    try {
        $path = Get-ItemProperty -Path "HKCU:\Software\Valve\Steam" -Name "SteamPath" -ErrorAction Stop
        return $path.SteamPath
    } catch {
        return "C:\Program Files (x86)\Steam"
    }
}

function Get-DotaPaths($steamBase) {
    $paths = New-Object System.Collections.Generic.List[string]
    $standard = Join-Path $steamBase "steamapps\common\dota 2 beta"
    if (Test-Path $standard) { $paths.Add($standard) }

    $vdf = Join-Path $steamBase "steamapps\libraryfolders.vdf"
    if (Test-Path $vdf) {
        $content = Get-Content $vdf -Raw
        $matches = [regex]::Matches($content, '"path"\s+"([^"]+)"')
        foreach ($m in $matches) {
            $p = $m.Groups[1].Value -replace "\\\\", "\"
            $dp = Join-Path $p "steamapps\common\dota 2 beta"
            if (Test-Path $dp) { if (-not $paths.Contains($dp)) { $paths.Add($dp) } }
        }
    }
    return $paths
}

function Update-Itembuilds($dotaPaths) {
    $targetDir = $null
    foreach ($p in $dotaPaths) {
        $ib = Join-Path $p "game\dota\itembuilds"
        if (Test-Path $ib) { $targetDir = $ib; break }
    }

    if (-not $targetDir) {
        Write-Host "Itembuilds directory not found. Skipping." -ForegroundColor Yellow
        return
    }

    Write-Host "Downloading itembuilds..." -ForegroundColor Cyan
    $tempZip = "guides_temp.zip"
    Invoke-WebRequest -Uri $ITEMBUILDS_URL -OutFile $tempZip
    
    $extractPath = "extract_temp"
    if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath }
    Expand-Archive -Path $tempZip -DestinationPath $extractPath
    
    Copy-Item -Path "$extractPath\*" -Destination $targetDir -Force
    Remove-Item $tempZip
    Remove-Item -Recurse $extractPath
    Write-Host "Itembuilds updated successfully." -ForegroundColor Green
}

function Update-HeroGrids($categoryKey, $steamBase) {
    $cat = $CATEGORIES[$categoryKey]
    if (-not $cat) { $cat = $CATEGORIES["2"] }
    
    $folder = $cat[0]
    $name = $cat[1]
    $url = $GRID_URL_TEMPLATE -f $folder
    
    Write-Host "Updating grids: $name" -ForegroundColor Cyan
    $userData = Join-Path $steamBase "userdata"
    
    if (-not (Test-Path $userData)) {
        Write-Host "Steam userdata not found." -ForegroundColor Red
        return
    }

    $count = 0
    foreach ($id in Get-ChildItem $userData -Directory) {
        if ($id.Name -match "^\d+$") {
            $gridDir = Join-Path $id.FullName "570\remote\cfg"
            if (Test-Path $gridDir) {
                $target = Join-Path $gridDir "hero_grid_config.json"
                if (Test-Path $target) { Copy-Item $target "$target.bak" -Force }
                Invoke-WebRequest -Uri $url -OutFile $target
                Write-Host "Updated grid for $($id.Name)" -ForegroundColor Green
                $count++
            }
        }
    }
    if ($count -eq 0) { Write-Host "No Steam profiles found." -ForegroundColor Yellow }
}

Clear-Host
Write-Host $ASCII_ART -ForegroundColor Cyan
Write-Host ("-" * 50)

$config = @{}
if (Test-Path $CONFIG_FILE) { $config = Get-Content $CONFIG_FILE | ConvertFrom-Json }

$catChoice = $config.category
$guidesChoice = $config.install_guides

if ($catChoice -and $null -ne $guidesChoice) {
    $currentCatName = $CATEGORIES[$catChoice][1]
    Write-Host "Loaded saved settings:"
    Write-Host "Category: $currentCatName"
    Write-Host "Install Guides: $(if($guidesChoice){'Yes'}else{'No'})"
    $useSaved = Read-Host "Use these settings? (y/n, default y)"
    if ($useSaved -eq "n") { $catChoice = $null; $guidesChoice = $null }
}

if (-not $catChoice) {
    Write-Host "`nSelect Hero Grid Category:"
    Write-Host "1. Most Played"
    Write-Host "2. Most Picked Heroes (>50% Winrate)"
    Write-Host "3. D2PT Rating"
    $catChoice = Read-Host "Choice (default 2)"
    if (-not $catChoice) { $catChoice = "2" }
}

if ($null -eq $guidesChoice) {
    $gInput = Read-Host "`nInstall Item Guides? (y/n, default y)"
    $guidesChoice = ($gInput -ne "n")
}

if ($config.category -ne $catChoice -or $config.install_guides -ne $guidesChoice) {
    $save = Read-Host "`nSave settings? (y/n, default n)"
    if ($save -eq "y") {
        $config.category = $catChoice
        $config.install_guides = $guidesChoice
        $config | ConvertTo-Json | Set-Content $CONFIG_FILE
    }
}

Write-Host "`nStarting update..." -ForegroundColor Cyan
$steam = Get-SteamPath
$dota = Get-DotaPaths $steam

Update-HeroGrids $catChoice $steam
if ($guidesChoice) { Update-Itembuilds $dota }

Write-Host "`nUpdate complete. Press Enter to exit."
[void][System.Console]::ReadLine()
