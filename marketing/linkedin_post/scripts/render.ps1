<#
render.ps1 — Step 4a: render a deck's deck.html -> deck.pdf via headless Edge.

Same pipeline as the pitch deck: msedge --headless=new --print-to-pdf.
The @page size in rayhan_brand.css (11.25in x 14.0625in) fixes the 1080x1350
portrait geometry — do NOT pass a paper-size flag here.

Usage:
    pwsh scripts/render.ps1 <short>              # renders decks/<short>/deck.html
    pwsh scripts/render.ps1 <short> -Html hook.html -Pdf hook.pdf

<short> is the deck folder name (first 8 chars of the token), e.g. ec47e5cc.
A unique --user-data-dir avoids profile-lock contention when rendering several
decks in parallel.
#>
param(
    [Parameter(Mandatory = $true)][string]$Short,
    [string]$Html = "deck.html",
    [string]$Pdf  = "deck.pdf"
)

$ErrorActionPreference = "Stop"
$root    = Split-Path $PSScriptRoot -Parent            # marketing/linkedin_post/
$deckDir = Join-Path $root "decks/$Short"
$htmlPath = Join-Path $deckDir $Html
$pdfPath  = Join-Path $deckDir $Pdf

if (-not (Test-Path $htmlPath)) { throw "no such deck html: $htmlPath" }

# locate Edge (64-bit or 32-bit install)
$edge = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "msedge.exe not found in Program Files" }

$uri = "file:///" + ($htmlPath -replace '\\', '/')
$udd = Join-Path $env:TEMP ("edge_deck_" + $Short)

& $edge --headless=new --disable-gpu --no-pdf-header-footer `
    --user-data-dir="$udd" --print-to-pdf="$pdfPath" $uri | Out-Null
Start-Sleep -Seconds 4

if (Test-Path $pdfPath) {
    $kb = [math]::Round((Get-Item $pdfPath).Length / 1KB)
    Write-Host "ok: $Short -> $pdfPath  (${kb} KB)"
} else {
    throw "render produced no PDF: $pdfPath"
}
