# Aelvoxim Desktop Gateway — Post-build script
# Copies config and install script into the dist folder

$distDir = "C:\Aelvoxim\aelvoxim-gateway\dist\AEL Gateway"

Write-Host "=== Copying deployment files ===" -ForegroundColor Green

# Copy config.yaml
Copy-Item "C:\Aelvoxim\aelvoxim-gateway\config.yaml" "$distDir\config.yaml" -Force
Write-Host "  config.yaml -> $distDir\config.yaml" -ForegroundColor Cyan

# Copy install_service.bat
Copy-Item "C:\Aelvoxim\aelvoxim-gateway\install_service.bat" "$distDir\install_service.bat" -Force
Write-Host "  install_service.bat -> $distDir\install_service.bat" -ForegroundColor Cyan

# Total size
$totalSize = (Get-ChildItem $distDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "Total dist size: $([math]::Round($totalSize,1)) MB" -ForegroundColor Yellow

Write-Host "`nFiles:" -ForegroundColor Yellow
Get-ChildItem $distDir -Name

Write-Host "`n=== Done ===" -ForegroundColor Green
