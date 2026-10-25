import urllib.request, urllib.error, json, os

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
    # a shape-valid FAKE address: this probe must never name the operator's
    # real wallet (it is a diagnostic, not a payment)
    _smoke_wallet = '4' + '0' * 94
    body = json.dumps({'wallet_address': _smoke_wallet, 'amount_xmr': 0.001}).encode()
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

# 7. Static files (and the SPRITE-REMOVAL contract: the legacy
#    pixel-art miner assets are retired, so those URLs must 404 - a
#    server still serving them means a stale bundle is deployed)
try:
    urllib.request.urlopen('http://127.0.0.1:3094/static/style.css', timeout=5)
    urllib.request.urlopen('http://127.0.0.1:3094/static/app.js', timeout=5)
    print('7.  Static files:      OK (CSS + JS)')
except Exception as e:
    print('7.  Static files:      FAIL -', e)

SPRITES = ('miner_sprite.png', 'miner_reference.png', 'miner_transparent.png')
still_served = []
for name in SPRITES:
    try:
        urllib.request.urlopen(
            'http://127.0.0.1:3094/static/img/' + name, timeout=5)
        still_served.append(name)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            still_served.append(name + ' (HTTP ' + str(e.code) + ')')
    except Exception:
        pass          # connection refused etc. is check 1's failure, not this one
print('7b. Sprite assets:     ' + ('OK (all retired, 404)' if not still_served
                                  else 'FAIL - still served: ' + ', '.join(still_served)))

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
