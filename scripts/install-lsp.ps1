# scripts/install-lsp.ps1 - build the Loment language server and install it for the editor
#
# NO PYTHON: seed (committed driver IR) + clang + WSL only.
#   1. clang(seed) -> stage1 (cached next to lomc.ps1)
#   2. stage1 compiles loment/tools/lsp.lomt -> IR   (self-hosted compiler, byte-identical to
#      the reference on that file -- gated by tools/loment_p8_test.py, 44/44 corpus)
#   3. clang links the IR -> x86_64 Linux ELF
#   4. copy it into the WSL filesystem (persistent, not /tmp) and chmod +x
#
#   powershell -File scripts/install-lsp.ps1                 # build + install
#   powershell -File scripts/install-lsp.ps1 -DryRun         # print the commands
#   powershell -File scripts/install-lsp.ps1 -WslDir '/home/me/.local/share/loment'
#
# Then point VS Code at it (settings):
#   "loment.serverCommand": "wsl",
#   "loment.serverArgs": ["-e", "/home/<you>/.local/share/loment/lsp"]
# (Without those two settings the extension keeps spawning tools/loment_lsp.py with Python.)
#
# NOTE: ASCII only -- Windows PowerShell 5.1 reads a .ps1 without a BOM using the ANSI code
# page, so non-ASCII text here would turn into mojibake and then into parse errors.
# Chinese explanation: docs/157 section 3.5.

[CmdletBinding()]
param(
    # WSL-side directory for the server; empty = "$HOME/.local/share/loment" (resolved below).
    # NOTE: a literal "~" is NOT expanded -- arguments to wsl.exe are passed verbatim (no shell),
    # so pass an absolute path or leave this empty.
    [string]$WslDir = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Seed = Join-Path $Root 'loment/build/selfhost_driver.ll'
$Stage1 = Join-Path $Root 'loment/build/stage1.elf'
$Entry = 'loment/tools/lsp.lomt'

function Find-Clang {
    $c = Get-Command clang -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $fb = 'C:\Program Files\LLVM\bin\clang.exe'
    if (Test-Path $fb) { return $fb }
    throw "clang not found (install LLVM, or put clang.exe on PATH)"
}

function To-WslPath([string]$p) {
    $out = & wsl -e wslpath -a $p
    if ($LASTEXITCODE -ne 0) { throw "wslpath failed: $p" }
    return "$out".Trim()
}

$clang = Find-Clang
if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    throw "WSL is required (the language server is a Linux ELF)"
}
if (-not (Test-Path $Seed)) {
    throw "missing seed $Seed (python tools/loment_seed.py --emit regenerates it; that is the only Python step in the whole chain)"
}

# resolve the WSL home **without a shell**: arguments to wsl.exe are verbatim, so a literal "~"
# would create a directory named "~" instead of the home directory.
if ($WslDir -eq '') {
    $wslHome = (& wsl -e printenv HOME).Trim()
    if (-not $wslHome) { throw "cannot resolve HOME inside WSL" }
    $WslDir = "$wslHome/.local/share/loment"
} elseif ($WslDir.StartsWith('~')) {
    throw "WslDir must be an absolute WSL path (~ is not expanded): got '$WslDir'"
}

# ---- 1. stage1 from the seed (shared with lomc.ps1; rebuild when the seed is newer)
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
    Write-Host "[1/4] stage1 is up to date"
}

$outLl = Join-Path $Root 'loment/build/lsp.ll'
$outElf = Join-Path $Root 'loment/build/lsp.elf'

# ---- 2. stage1 compiles the LSP (self-hosted compiler, no Python)
Write-Host "[2/4] stage1 compiles $Entry -> lsp.ll"
if (-not $DryRun) {
    & wsl -e rm -f /tmp/loment_install_stage1
    & wsl -e cp (To-WslPath $Stage1) /tmp/loment_install_stage1
    if ($LASTEXITCODE -ne 0) { throw "copying stage1 into WSL failed" }
    & wsl -e chmod +x /tmp/loment_install_stage1
    Push-Location $Root
    # Same trap as scripts/lomc.ps1: PowerShell decodes a native command's stdout with
    # [Console]::OutputEncoding (GBK here), while stage1 writes UTF-8. The IR carries the
    # source's comments; decoding them as GBK mangles them and can eat the following
    # newline, gluing the next `define` onto a comment.
    $prevEnc = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $ir = & wsl -e /tmp/loment_install_stage1 $Entry
    } finally {
        [Console]::OutputEncoding = $prevEnc
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "stage1 failed to compile the language server" }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($outLl, (($ir -join "`n") + "`n"), $utf8)
}

# ---- 3. IR -> x86_64 Linux ELF
Write-Host "[3/4] clang link -> lsp.elf"
if ($DryRun) {
    Write-Host "      $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld -o $outElf $outLl"
} else {
    & $clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static `
        -fuse-ld=lld -o $outElf $outLl
    if ($LASTEXITCODE -ne 0) { throw "link failed" }
}

# ---- 4. install into the WSL filesystem (persistent location)
Write-Host "[4/4] install into WSL: $WslDir/lsp"
if ($DryRun) {
    Write-Host "      wsl -e mkdir -p $WslDir"
    Write-Host "      wsl -e cp <elf> $WslDir/lsp && wsl -e chmod +x $WslDir/lsp"
} else {
    & wsl -e mkdir -p $WslDir
    if ($LASTEXITCODE -ne 0) { throw "mkdir in WSL failed" }
    & wsl -e rm -f "$WslDir/lsp"
    & wsl -e cp (To-WslPath $outElf) "$WslDir/lsp"
    if ($LASTEXITCODE -ne 0) { throw "installing the server into WSL failed" }
    & wsl -e chmod +x "$WslDir/lsp"
    # smoke test: `--check` on a clean file must exit 0 (no editor needed)
    Push-Location $Root
    try {
        & wsl -e "$WslDir/lsp" --check loment/examples/mathutil.lomt
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rc -ne 0) { throw "installed server failed its smoke test (--check, exit $rc)" }
    $size = [math]::Round((Get-Item $outElf).Length / 1KB, 1)
    Write-Host "[lsp] installed: $WslDir/lsp (${size}KB); smoke test --check -> 0"

    # Windows shim: MSYS/Cygwin vim converts a `/home/...` argument into a Windows path
    # before spawning wsl.exe, so vim -> wsl fails ("execvpe ... No such file or directory").
    # Going through cmd.exe avoids the conversion (cmd does no MSYS path rewriting).
    # NOTE: keep this file ASCII-only -- PowerShell 5.1 reads a .ps1 without BOM as ANSI,
    # and a mojibake comment can break the parsing of the following lines.
    $shim = Join-Path $Root 'loment/build/loment-lsp.cmd'
    $bat = "@echo off`r`nrem Loment language server shim (generated by scripts/install-lsp.ps1)`r`n" +
           "wsl -e $WslDir/lsp %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText($shim, $bat, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[lsp] MSYS vim shim: loment/build/loment-lsp.cmd"

    Write-Host "[lsp] point the editor at it:"
    Write-Host "      VS Code:  `"loment.serverCommand`": `"wsl`", `"loment.serverArgs`": [`"-e`", `"$WslDir/lsp`"]"
    Write-Host "      Vim:      let g:loment_lsp_cmd = 'loment/build/loment-lsp.cmd'   (MSYS/Cygwin vim)"
    Write-Host "      Vim(WSL): let g:loment_lsp_cmd = '$WslDir/lsp'                  (vim inside WSL)"
    Write-Host "[lsp] no Python involved: seed + clang + stage1 (Python only regenerates the seed)"
}
