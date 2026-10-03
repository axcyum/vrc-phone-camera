param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$taskRuntime = Join-Path $PSScriptRoot '..\..\work\camera-spout-venv'
$taskPython = Join-Path $taskRuntime 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskBootstrap = $env:VRC_CAMERA_PYTHON
    $taskBundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not $taskBootstrap -and (Test-Path -LiteralPath $taskBundled)) { $taskBootstrap = $taskBundled }
    if (-not $taskBootstrap -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($taskVersion in @('3.13', '3.12')) {
            $taskFound = & py "-$taskVersion" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $taskBootstrap = $taskFound; break }
        }
    }
    if (-not $taskBootstrap) { throw 'SpoutGLにはPython 3.12/3.13が必要です。インストール後、VRC_CAMERA_PYTHONにpython.exeのパスを指定してください。' }
    & $taskBootstrap -m venv $taskRuntime
    if ($LASTEXITCODE -ne 0) { throw 'Python 3のインストールを確認してください。' }
}
& $taskPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依存ライブラリのインストールに失敗しました。' }
if ($Demo) { & $taskPython app.py --demo } else { & $taskPython app.py }
