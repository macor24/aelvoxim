# Test WebSocket connection and check cloud
Write-Host "=== Testing Gateway WebSocket ===" -ForegroundColor Green

$gatewayDir = "C:\Aelvoxim\aelvoxim-gateway"

# Start connection in background
$job = Start-Job -ScriptBlock {
    param($dir)
    cd $dir
    python -c @"
import asyncio, json, urllib.request, sys
sys.path.insert(0, r'C:\Aelvoxim\aelvoxim-gateway')
req = urllib.request.Request('http://8.134.185.33:9701/v1/auth/login',
    data=json.dumps({'email':'gmxchz@126.com','password':'admin123'}).encode(),
    headers={'Content-Type':'application/json'})
resp = urllib.request.urlopen(req, timeout=10)
key = json.loads(resp.read().decode())['api_key']
import websockets
async def test():
    uri = 'ws://8.134.185.33:9701/v1/gateway/ws'
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({'type':'auth','token':key}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 5))
        print(f'AUTH:{resp}')
        # Wait 15 seconds to check cloud
        await asyncio.sleep(15)
        print('DONE')
asyncio.run(test())
"@
} -ArgumentList $gatewayDir

Start-Sleep -Seconds 3

# Check cloud for connection
Write-Host "Checking cloud..." -ForegroundColor Yellow
$result = ssh -o StrictHostKeyChecking=no root@8.134.185.33 "cd /opt/aelvoxim/src && AELVOXIM_DATABASE_URL='host=127.0.0.1 port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_778af6539f11998d' /opt/aelvoxim/venv/bin/python3 -c 'import sys; sys.path.insert(0,\".\"); from aelvoxim.server.gateway_ws import _gateway_connections; print(\"Connections:\", list(_gateway_connections.keys()))' 2>&1"
Write-Host "Cloud: $result" -ForegroundColor Cyan

# Check cloud journal for [GW] markers
$journal = ssh -o StrictHostKeyChecking=no root@8.134.185.33 "journalctl -u aelvoxim-api --no-pager -n 30 2>&1 | grep '\[GW\]'" 2>&1
Write-Host "Journal [GW]: $journal" -ForegroundColor Cyan

Start-Sleep -Seconds 15

# Check job output
Receive-Job -Job $job -ErrorAction SilentlyContinue | Write-Host

Write-Host "=== Test Complete ===" -ForegroundColor Green
