# Builds synthetic "filming session" clips to test the Edit Factory pipeline.
# Uses edge-tts (network) for speech. Creates in test_clips/:
#   1. B0CKWGQY9Z_kettle.mp4  - vertical, ASIN in filename
#   2. session_part2.mp4      - vertical, two products via spoken slates
#   3. random_notes.mp4       - horizontal, no speech -> should land in UNSORTED
$ErrorActionPreference = "Stop"
$dir = Join-Path $PSScriptRoot "test_clips"
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$wavDir = Join-Path $dir "wavs"
New-Item -ItemType Directory -Force -Path $wavDir | Out-Null

$py = "C:\Users\scott\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$edge = Join-Path (Split-Path $py) "Scripts\edge-tts.exe"
if (-not (Test-Path $edge)) { $edge = "edge-tts" }

$sentences = @(
    @{ n = "s01"; t = "This stainless steel kettle boils water in about ninety seconds." },
    @{ n = "s02"; t = "The handle stays cool, which is really nice." },
    @{ n = "s03"; t = "Overall, I give it five stars." },
    @{ n = "s04"; t = "Okay, next product: the yoga mat." },
    @{ n = "s05"; t = "It is six millimeters thick and very grippy." },
    @{ n = "s06"; t = "Next product: the phone stand." },
    @{ n = "s07"; t = "It folds flat and holds my phone perfectly on my desk." }
)

foreach ($s in $sentences) {
    $mp3 = Join-Path $wavDir "$($s.n).mp3"
    $ok = $false
    for ($try = 1; $try -le 5 -and -not $ok; $try++) {
        & $edge --voice en-US-AndrewNeural --text $s.t --write-media $mp3 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Path $mp3) -and (Get-Item $mp3).Length -gt 1000) { $ok = $true }
        else { Start-Sleep -Seconds (3 * $try); Remove-Item $mp3 -ErrorAction SilentlyContinue }
    }
    if (-not $ok) { throw "edge-tts failed for $($s.n) after 5 tries" }
    Start-Sleep -Milliseconds 400
    $wav = Join-Path $wavDir "$($s.n).wav"
    ffmpeg -y -hide_banner -nostats -i $mp3 -ar 22050 -ac 1 $wav 2>$null
    if ($LASTEXITCODE -ne 0) { throw "wav convert failed for $($s.n)" }
}

function Silence-Wav([double]$sec, [string]$name) {
    $p = Join-Path $wavDir $name
    ffmpeg -y -hide_banner -nostats -f lavfi -i "anullsrc=r=22050:cl=mono" -t $sec -c:a pcm_s16le $p 2>$null
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg silence failed" }
}
Silence-Wav 3.2 "sil32.wav"
Silence-Wav 2.6 "sil26.wav"
Silence-Wav 1.8 "sil18.wav"
Silence-Wav 2.1 "sil21.wav"

function Build-Track([string[]]$order, [string]$out) {
    $list = Join-Path $wavDir "list.txt"
    ($order | ForEach-Object { "file '$_.wav'" }) -join "`n" | Set-Content -Path $list -Encoding ascii
    Push-Location $dir
    ffmpeg -y -hide_banner -nostats -f concat -safe 0 -i "wavs\list.txt" -c:a pcm_s16le $out 2>$null
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "concat failed for $out" }
    Pop-Location
}

Build-Track @("sil32","s01","sil26","s02","sil32","s03","sil18") "track1.wav"
Build-Track @("sil21","s04","sil26","s05","sil32","s06","sil26","s07","sil21") "track2.wav"

Push-Location $dir
ffmpeg -y -hide_banner -nostats -i "track1.wav" -f lavfi -i "color=c=steelblue:s=720x1280:r=30" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k "B0CKWGQY9Z_kettle.mp4" 2>$null
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "mux1 failed" }
ffmpeg -y -hide_banner -nostats -i "track2.wav" -f lavfi -i "color=c=indianred:s=720x1280:r=30" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k "session_part2.mp4" 2>$null
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "mux2 failed" }
Pop-Location

Push-Location $dir
ffmpeg -y -hide_banner -nostats -f lavfi -i "sine=frequency=200:sample_rate=22050" -f lavfi -i "color=c=olive:s=1280x720:r=30" -shortest -af "volume=-38dB" -t 8 -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 96k "random_notes.mp4" 2>$null
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "mux3 failed" }
Pop-Location

Get-ChildItem $dir -Filter *.mp4 | Select-Object Name, Length
Write-Host "`nTest clips ready in $dir"
