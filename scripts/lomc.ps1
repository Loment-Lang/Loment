# scripts/lomc.ps1 — build/run a .lomt on Windows WITHOUT Python (docs/157 section 3.4)
#
# Three things only: the seed `loment/build/selfhost_driver.ll` (the driver IR the reference
# implementation emitted; committed) + clang (the foundation language) + WSL (to execute the
# Linux ELF). stage1 is cached at `loment/build/stage1.elf` and rebuilt only when the seed
# changes.
#
#   powershell -File scripts/lomc.ps1 loment/examples/user_hello.lomt          # build only
#   powershell -File scripts/lomc.ps1 loment/examples/user_hello.lomt -Run     # build and run
#   powershell -File scripts/lomc.ps1 <file.lomt> -OutDir loment/build
#
# Output: <OutDir>/<name>.ll (IR) and <OutDir>/<name>.elf (x86_64 Linux static executable).
# Boundary: the target is a Linux ELF (Loment programs use Linux syscalls), so -Run uses WSL.
#
# NOTE: this file is deliberately **ASCII only**. Windows PowerShell 5.1 reads a .ps1 with no
# BOM using the ANSI code page, so non-ASCII text here turns into mojibake and then into parse
# errors. The Chinese explanation lives in docs/157 section 3.4.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$File,

    # run the result inside WSL after building (prints its output)
    [switch]$Run,

    # output directory (relative to the repo root, or absolute)
    [string]$OutDir = 'loment/build',

    # print the commands instead of running them
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Seed = Join-Path $Root 'loment/build/selfhost_driver.ll'
$Stage1 = Join-Path $Root 'loment/build/stage1.elf'

function Find-Clang {
    $c = Get-Command clang -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $fb = 'C:\Program Files\LLVM\bin\clang.exe'
    if (Test-Path $fb) { return $fb }
    throw "clang not found (install LLVM, or put clang.exe on PATH)"
}

function To-WslPath([string]$p) {
    # no shell-string building: let wslpath do the conversion (Windows -> WSL by default)
    $out = & wsl -e wslpath -a $p
    if ($LASTEXITCODE -ne 0) { throw "wslpath failed: $p" }
    return "$out".Trim()
}

$clang = Find-Clang
$HasWsl = [bool](Get-Command wsl -ErrorAction SilentlyContinue)

# ---- entry file: absolute path, then the repo-relative form (the driver eats the relative one)
$src = if ([IO.Path]::IsPathRooted($File)) { $File } else { Join-Path (Get-Location) $File }
$src = [IO.Path]::GetFullPath($src)
if (-not (Test-Path $src)) { throw "entry not found: $src" }
if (-not $src.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "the entry must live inside this repo (the driver resolves 'use' relative to it): $src"
}
$rel = $src.Substring($Root.Length + 1).Replace('\', '/')

$name = [IO.Path]::GetFileNameWithoutExtension($src)
$outDirAbs = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $Root $OutDir }
$outLl = Join-Path $outDirAbs "$name.ll"
$outElf = Join-Path $outDirAbs "$name.elf"
New-Item -ItemType Directory -Force -Path $outDirAbs | Out-Null

if (-not (Test-Path $Seed)) {
    throw "missing seed $Seed (regenerate with: python tools/loment_seed.py --emit -- that is the only step that needs Python)"
}

# ---- 1. stage1: link the seed with clang (cached; rebuilt when the seed is newer)
$needStage1 = $true
if (Test-Path $Stage1) {
    $needStage1 = (Get-Item $Stage1).LastWriteTimeUtc -lt (Get-Item $Seed).LastWriteTimeUtc
}
if ($needStage1) {
    Write-Host "[1/4] clang(seed) -> stage1"
    if ($DryRun) {
        Write-Host "      $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld -o $Stage1 $Seed"
    } else {
        & $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static `
            -fuse-ld=lld -o $Stage1 $Seed
        if ($LASTEXITCODE -ne 0) { throw "stage1 link failed" }
    }
} else {
    Write-Host "[1/4] stage1 is up to date (seed unchanged)"
}

if (-not $HasWsl) {
    throw "WSL is required to execute stage1 (it is a Linux ELF). Without WSL use: sh loment/bootstrap.sh on Linux/macOS"
}

# ---- 2. stage1 compiles the entry -> IR (copy into /tmp to execute: DrvFs cannot exec in place)
Write-Host "[2/4] stage1 compiles $rel -> $name.ll"
if (-not $DryRun) {
    & wsl -e cp (To-WslPath $Stage1) /tmp/lomc_stage1
    if ($LASTEXITCODE -ne 0) { throw "copying stage1 into WSL failed" }
    & wsl -e chmod +x /tmp/lomc_stage1
    if ($LASTEXITCODE -ne 0) { throw "chmod failed" }
    # working directory = repo root: the driver resolves 'use' itself, so pass the relative path
    Push-Location $Root
    # stage1 writes UTF-8, but Windows PowerShell decodes a native command's stdout with
    # [Console]::OutputEncoding -- GBK on a Chinese Windows. The IR carries the source's
    # comments, so GBK decoding turns them into mojibake, and a double-byte decode can
    # swallow the newline after one: the next `define` is glued onto a `;` comment and
    # `entry:` ends up at top level (clang: "expected top-level entity"). Measured
    # 2026-10-21 on loment/examples/tour.lomt -- compile "succeeded", link failed.
    $prevEnc = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $ir = & wsl -e /tmp/lomc_stage1 $rel
    } finally {
        [Console]::OutputEncoding = $prevEnc
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "stage1 failed to compile (see diagnostics above)" }
    # write with LF (PowerShell's '>' would write CRLF/UTF-16)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($outLl, (($ir -join "`n") + "`n"), $utf8)
}

# ---- 3. IR -> x86_64 Linux ELF (cross link with the Windows clang, same flags as the gates)
Write-Host "[3/4] clang link -> $name.elf"
if ($DryRun) {
    Write-Host "      $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld -o $outElf $outLl"
} else {
    & $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static `
        -fuse-ld=lld -o $outElf $outLl
    if ($LASTEXITCODE -ne 0) { throw "link failed" }
}

# ---- 4. optional: run it in WSL
if ($Run) {
    Write-Host "[4/4] running (WSL):"
    if (-not $DryRun) {
        & wsl -e cp (To-WslPath $outElf) /tmp/lomc_run
        if ($LASTEXITCODE -ne 0) { throw "copying the ELF into WSL failed" }
        & wsl -e chmod +x /tmp/lomc_run
        & wsl -e /tmp/lomc_run
        $code = $LASTEXITCODE
        Write-Host "[lomc] exit code $code"
    }
} else {
    Write-Host "[4/4] skipped running (add -Run to execute it in WSL)"
}

if (-not $DryRun) {
    $llKb = [math]::Round((Get-Item $outLl).Length / 1KB, 1)
    $elfKb = [math]::Round((Get-Item $outElf).Length / 1KB, 1)
    Write-Host "[lomc] ${name}: IR ${llKb}KB -> ELF ${elfKb}KB"
    Write-Host "[lomc] no Python involved: seed + clang + WSL (Python is only needed to regenerate the seed, docs/159)"
}
