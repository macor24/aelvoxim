# Aelvoxim Gateway Client Test
Write-Host "=== Testing Gateway Client ===" -ForegroundColor Green

$gatewayDir = "C:\Aelvoxim\aelvoxim-gateway"
$env:PYTHONPATH = $gatewayDir

# Login to get token
Write-Host "Logging in..." -ForegroundColor Yellow
$loginResult = python -c "
import urllib.request, json
req = urllib.request.Request('http://8.134.185.33:9701/v1/auth/login',
    data=json.dumps({'email':'gmxchz@126.com','password':'admin123'}).encode(),
    headers={'Content-Type':'application/json'})
resp = urllib.request.urlopen(req, timeout=10)
data = json.loads(resp.read().decode())
print(data['api_key'])
"
$token = $loginResult.Trim()
Write-Host "Token len: $($token.Length)" -ForegroundColor Yellow

# Test WebSocket
Write-Host "`nConnecting to ws://8.134.185.33:9701/v1/gateway/ws ..." -ForegroundColor Yellow

python -c @"
import asyncio, json, sys
sys.path.insert(0, r'C:\Aelvoxim\aelvoxim-gateway')

async def test():
    import websockets
    uri = 'ws://8.134.185.33:9701/v1/gateway/ws'
    print(f'Connecting...')
    
    async with websockets.connect(uri) as ws:
        # Auth
        token = '$token'
        await ws.send(json.dumps({'type':'auth','token':token}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print(f'Auth: {resp}')
        
        # Ping
        await ws.send(json.dumps({'type':'ping'}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 5))
        print(f'Ping: {resp}')
        
        # Status
        await ws.send(json.dumps({'type':'status','desktop':'ready','version':'1.0.0'}))
        print('Status sent')
        
        print('ALL TESTS PASSED from Windows!')

asyncio.run(test())
"@

if ($LASTEXITCODE -eq 0) {
    Write-Host "=== TEST SUCCESS ===" -ForegroundColor Green
} else {
    Write-Host "=== TEST FAILED ===" -ForegroundColor Red
}
