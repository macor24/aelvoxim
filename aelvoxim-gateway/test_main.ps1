# 测试 main.py 启动逻辑
Write-Host "=== Testing Gateway main.py ===" -ForegroundColor Green

$gatewayDir = "C:\Aelvoxim\aelvoxim-gateway"
$env:PYTHONPATH = $gatewayDir

# 清理旧 token（如果有）
if (Test-Path "$gatewayDir\gateway_token.json") {
    Remove-Item "$gatewayDir\gateway_token.json" -Force
    Write-Host "Cleaned old token" -ForegroundColor Yellow
}

# 测试1：首次启动（应提示打开配置页面）
Write-Host "`n=== Test 1: First-time startup (expect setup prompt) ===" -ForegroundColor Yellow
$process = Start-Process -NoNewWindow -PassThru -FilePath "python" -ArgumentList "main.py --setup" -RedirectStandardOutput "$env:TEMP\gateway_test1.log" -RedirectStandardError "$env:TEMP\gateway_test1.err"
Start-Sleep -Seconds 3
$process.Kill()
$output = Get-Content "$env:TEMP\gateway_test1.log" -Raw
Write-Host $output

# 测试2：正常启动（带 token）
Write-Host "`n=== Test 2: Full startup with WebSocket ===" -ForegroundColor Yellow
Write-Host "Starting Gateway with HTTP + WebSocket (will run for 5s)..." -ForegroundColor Cyan
$process2 = Start-Process -NoNewWindow -PassThru -FilePath "python" -ArgumentList "main.py" -RedirectStandardOutput "$env:TEMP\gateway_test2.log" -RedirectStandardError "$env:TEMP\gateway_test2.err"
Start-Sleep -Seconds 2

# 检查 HTTP 端口是否在监听
$testHttp = python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9705/api/status', timeout=3).status)"
if ($testHttp -eq 200) {
    Write-Host "✅ HTTP 9705 OK" -ForegroundColor Green
} else {
    Write-Host "❌ HTTP 9705 failed: $testHttp" -ForegroundColor Red
}

# 检查进程输出
$output2 = Get-Content "$env:TEMP\gateway_test2.log" -Raw
Write-Host "Output: $output2" -ForegroundColor Gray

Start-Sleep -Seconds 3
$process2.Kill()

Write-Host "`n=== Test Complete ===" -ForegroundColor Green
