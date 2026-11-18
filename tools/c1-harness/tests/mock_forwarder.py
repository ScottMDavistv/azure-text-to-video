#!/usr/bin/env python3
"""Offline stand-in for GitHub Codespaces port forwarding (regression tests only).

Usage: mock_forwarder.py LISTEN_PORT UPSTREAM_PORT CERT KEY STATE_DIR

STATE_DIR/mode (re-read on every request) selects the behaviour:
  private      unauthenticated -> 302 to sign-in; X-Github-Token == STATE_DIR/token -> proxied
  public       every request proxied
  interstitial unauthenticated -> 200 sign-in page (no upstream content); token -> proxied
  down         502 for everything (port not forwarded)
  wrongport    token -> a fixed unrelated page (forwarding to the wrong port)
  ambiguous    unauthenticated -> 503, authenticated -> proxied (unsafe to assume private)
  slow         like private, but each response is delayed 2 s
  Ambiguous unauthenticated outcomes (M-2a; authenticated requests are proxied):
  flaky        unauthenticated -> connection closed without a response (reset)
  unauth5xx    unauthenticated -> 503
  unauth404    unauthenticated -> 404
  hang         unauthenticated -> no response for 6 s (probe timeout)
  badredirect  unauthenticated -> 302 to https://evil.example/login
  lookalike    unauthenticated -> 302 to https://github.com.evil.example/login
  userinfo     unauthenticated -> 302 to https://github.com@evil.example/login
  httpredirect unauthenticated -> 302 to http://github.com/login (not HTTPS)
  noloc        unauthenticated -> 302 without a Location header
  Positive denials:
  unauth401    unauthenticated -> 401
  unauth403    unauthenticated -> 403
  subdomain    unauthenticated -> 307 to https://auth.github.com/login
GET /repos/OWNER/REPO with "Authorization: token <token>" returns
{"owner": {"type": <STATE_DIR/owner>}} (GitHub API stand-in).
"""
import http.server, json, os, ssl, sys, time, urllib.request

LISTEN, UPSTREAM, CERT, KEY, STATE = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]


def state(name, default=''):
    try:
        with open(os.path.join(STATE, name)) as f:
            return f.read().strip()
    except OSError:
        return default


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body=b'', headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy(self):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (UPSTREAM, self.path), timeout=5) as r:
                self.reply(r.status, r.read())
        except Exception:
            self.reply(502, b'upstream unavailable')

    def do_GET(self):
        mode, token = state('mode', 'private'), state('token')
        if mode == 'slow':
            time.sleep(2)
        if self.path.startswith('/repos/'):
            if self.headers.get('Authorization') == 'token ' + token:
                return self.reply(200, json.dumps({'owner': {'type': state('owner', 'User')}}).encode())
            return self.reply(401, b'{}')
        authed = bool(token) and self.headers.get('X-Github-Token') == token
        if mode == 'down':
            return self.reply(502, b'not forwarded')
        if mode == 'public':
            return self.proxy()
        if mode == 'ambiguous':
            return self.proxy() if authed else self.reply(503, b'forwarder error')
        if mode == 'wrongport':
            return self.reply(200, b'some other service') if authed else self.reply(302, b'', {'Location': 'https://github.com/login'})
        if authed:
            return self.proxy()
        if mode == 'flaky':
            self.close_connection = True
            return
        if mode == 'hang':
            time.sleep(6)
            self.close_connection = True
            return
        simple = {'unauth5xx': 503, 'unauth404': 404, 'unauth401': 401, 'unauth403': 403}
        if mode in simple:
            return self.reply(simple[mode], b'denied')
        redirects = {
            'badredirect': (302, 'https://evil.example/login'),
            'lookalike': (302, 'https://github.com.evil.example/login'),
            'userinfo': (302, 'https://github.com@evil.example/login'),
            'httpredirect': (302, 'http://github.com/login'),
            'subdomain': (307, 'https://auth.github.com/login?return_to=x'),
        }
        if mode in redirects:
            code, loc = redirects[mode]
            return self.reply(code, b'', {'Location': loc})
        if mode == 'noloc':
            return self.reply(302, b'')
        if mode == 'interstitial':
            return self.reply(200, b'<html>Sign in to GitHub to continue</html>')
        return self.reply(302, b'', {'Location': 'https://github.com/login'})


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv = http.server.ThreadingHTTPServer(('127.0.0.1', LISTEN), H)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
