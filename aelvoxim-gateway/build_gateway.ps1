$projDir = "C:\Aelvoxim\aelvoxim-gateway"
$distDir = "$projDir\dist"

Set-Location $projDir

# Clean old dist
if (Test-Path "$distDir\AEL Gateway") {
    Remove-Item "$distDir\AEL Gateway" -Recurse -Force
    Write-Host "Cleaned old dist" -ForegroundColor Yellow
}

Write-Host "=== Starting PyInstaller build ===" -ForegroundColor Green
pyinstaller --clean AEL_Gateway.spec 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "=== Build SUCCESS ===" -ForegroundColor Green
    $exePath = "$distDir\AEL Gateway\AEL Gateway.exe"
    if (Test-Path $exePath) {
        $size = (Get-Item $exePath).Length / 1MB
        Write-Host "Output: $exePath ($([math]::Round($size,1)) MB)" -ForegroundColor Cyan
    }
    # Copy config and install script
    Copy-Item "$projDir\config.yaml" "$distDir\AEL Gateway\config.yaml" -Force
    Copy-Item "$projDir\install_service.bat" "$distDir\AEL Gateway\install_service.bat" -Force
    $totalSize = (Get-ChildItem "$distDir\AEL Gateway" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Host "Total dist size: $([math]::Round($totalSize,1)) MB" -ForegroundColor Cyan
} else {
    Write-Host "=== Build FAILED ===" -ForegroundColor Red
}
