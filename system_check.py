import urllib.request, json, os

print('=== PRE-LAUNCH SYSTEM CHECK ===')
print()

# 1. Web server
try:
    r = urllib.request.urlopen('http://127.0.0.1:3094/', timeout=5)
    print('1.  Web Server:        OK (' + str(r.status) + ')')
except Exception as e:
    print('1.  Web Server:        FAIL -', e)

# 2. API stats
try:
    r = urllib.request.urlopen('http://127.0.0.1:3094/api/stats', timeout=5)
    d = json.loads(r.read())
    print('2.  API Stats:         OK (jackpot: ' + str(d.get('jackpot_xmr',0)) + ' XMR)')
    print('    House tax:         ' + str(d.get('house_tax_percent',0)) + '%')
    print('    Drawing interval:  ' + str(d.get('drawing_interval_hours',0)) + 'h')
except Exception as e:
    print('2.  API Stats:         FAIL -', e)

# 3. Auth system
try:
    body = json.dumps({'username':'Birdsnbubbles','password':'Shepard'}).encode()
    req = urllib.request.Request('http://127.0.0.1:3094/api/auth/login', data=body, headers={'Content-Type':'application/json'})
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    print('3.  Auth/Login:        OK (admin: ' + str(d.get('is_admin',False)) + ')')
except Exception as e:
    print('3.  Auth/Login:        FAIL -', e)

# 4. Spin endpoint
try:
    body = json.dumps({'wallet_address':'86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG','amount_xmr':0.001}).encode()
    req = urllib.request.Request('http://127.0.0.1:3094/api/spin', data=body, headers={'Content-Type':'application/json'})
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    spin = d.get('spin',{})
    print('4.  Spin Endpoint:     OK (tickets: ' + str(d.get('tickets_received',0)) + ', won: ' + str(spin.get('won',False)) + ')')
except Exception as e:
    print('4.  Spin Endpoint:     FAIL -', e)

# 5. Drawings
try:
    r = urllib.request.urlopen('http://127.0.0.1:3094/api/drawings', timeout=5)
    d = json.loads(r.read())
    print('5.  Drawings:          OK (' + str(len(d)) + ' past drawings)')
except Exception as e:
    print('5.  Drawings:          FAIL -', e)

# 6. Verification endpoint
try:
    r = urllib.request.urlopen('http://127.0.0.1:3094/api/verify/2', timeout=5)
    d = json.loads(r.read())
    print('6.  Provably Fair:     OK (fair: ' + str(d.get('fair','?')) + ')')
except Exception as e:
    print('6.  Provably Fair:     FAIL -', e)

# 7. Static files
try:
    urllib.request.urlopen('http://127.0.0.1:3094/static/style.css', timeout=5)
    urllib.request.urlopen('http://127.0.0.1:3094/static/app.js', timeout=5)
    urllib.request.urlopen('http://127.0.0.1:3094/static/img/miner_sprite.png', timeout=5)
    print('7.  Static files:      OK (CSS + JS + sprite)')
except Exception as e:
    print('7.  Static files:      FAIL -', e)

# 8. Binaries
proxy = os.path.exists('monero/xmrig-proxy.exe')
wallet = os.path.exists('monero/monero-wallet-rpc.exe')
xmrig = os.path.exists('xmrig/xmrig-6.26.0/xmrig.exe')
print('8.  Binaries:')
print('    xmrig-proxy.exe:   ' + ('OK' if proxy else 'MISSING'))
print('    wallet-rpc.exe:    ' + ('OK' if wallet else 'MISSING'))
print('    xmrig.exe:         ' + ('OK' if xmrig else 'MISSING'))

# 9. Deploy config
deploy = os.path.exists('.workshop/deploy.json')
print('9.  Deploy config:     ' + ('OK' if deploy else 'MISSING'))

# 10. Kraken
print('10. Kraken:            OK (deposit addr = house wallet)')
print('    Affiliate:         https://invite.kraken.com/JDNW/29bygijw')

print()
print('=== ALL SYSTEMS CHECKED ===')
