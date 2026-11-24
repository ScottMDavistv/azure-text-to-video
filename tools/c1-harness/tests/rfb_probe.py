#!/usr/bin/env python3
"""RFB (VNC) authentication probe for the C1 harness regression tests.

Usage: rfb_probe.py unix SOCKET_PATH PASSWORD
       rfb_probe.py ws   NOVNC_PORT  PASSWORD

Asserts, over the given transport, that the server offers ONLY VNC
authentication (type 2), refuses "None" authentication, rejects a wrong
password and accepts the right one. Reading the offered types alone is not
enough: x11vnc sends them before its host check may refuse the client.
Standard library only (DES and a minimal WebSocket client are inline).
"""
import base64, os, socket, struct, sys

PC1 = [57,49,41,33,25,17,9,1,58,50,42,34,26,18,10,2,59,51,43,35,27,19,11,3,60,52,44,36,63,55,47,39,31,23,15,7,62,54,46,38,30,22,14,6,61,53,45,37,29,21,13,5,28,20,12,4]
PC2 = [14,17,11,24,1,5,3,28,15,6,21,10,23,19,12,4,26,8,16,7,27,20,13,2,41,52,31,37,47,55,30,40,51,45,33,48,44,49,39,56,34,53,46,42,50,36,29,32]
IP = [58,50,42,34,26,18,10,2,60,52,44,36,28,20,12,4,62,54,46,38,30,22,14,6,64,56,48,40,32,24,16,8,57,49,41,33,25,17,9,1,59,51,43,35,27,19,11,3,61,53,45,37,29,21,13,5,63,55,47,39,31,23,15,7]
FP = [IP.index(i) + 1 for i in range(1, 65)]
E = [32,1,2,3,4,5,4,5,6,7,8,9,8,9,10,11,12,13,12,13,14,15,16,17,16,17,18,19,20,21,20,21,22,23,24,25,24,25,26,27,28,29,28,29,30,31,32,1]
P = [16,7,20,21,29,12,28,17,1,15,23,26,5,18,31,10,2,8,24,14,32,27,3,9,19,13,30,6,22,11,4,25]
SH = [1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1]
SB = [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7,0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8,4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0,15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13],
      [15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10,3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5,0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15,13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9],
      [10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8,13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1,13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7,1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12],
      [7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15,13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9,10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4,3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14],
      [2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9,14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6,4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14,11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3],
      [12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11,10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8,9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6,4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13],
      [4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1,13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6,1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2,6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12],
      [13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7,1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2,7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8,2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]]


def _perm(v, tab, n):
    return sum(((v >> (n - t)) & 1) << (len(tab) - 1 - i) for i, t in enumerate(tab))


def des_encrypt(key, block):
    k = _perm(int.from_bytes(key, 'big'), PC1, 64)
    c, d, ks = k >> 28, k & 0xFFFFFFF, []
    for s in SH:
        c = ((c << s) | (c >> (28 - s))) & 0xFFFFFFF
        d = ((d << s) | (d >> (28 - s))) & 0xFFFFFFF
        ks.append(_perm((c << 28) | d, PC2, 56))
    b = _perm(int.from_bytes(block, 'big'), IP, 64)
    l, r = b >> 32, b & 0xFFFFFFFF
    for k in ks:
        x, o = _perm(r, E, 32) ^ k, 0
        for i in range(8):
            ch = (x >> (42 - 6 * i)) & 63
            o = (o << 4) | SB[i][(((ch & 32) >> 4) | (ch & 1)) * 16 + ((ch >> 1) & 15)]
        l, r = r, l ^ _perm(o, P, 32)
    return _perm((r << 32) | l, FP, 64).to_bytes(8, 'big')


def vnc_key(password):
    return bytes(int(f'{b:08b}'[::-1], 2) for b in (password.encode() + b'\0' * 8)[:8])


class UnixTransport:
    def __init__(self, path):
        self.c = socket.socket(socket.AF_UNIX)
        self.c.settimeout(5)
        self.c.connect(path)

    def recv(self, n):
        b = b''
        while len(b) < n:
            d = self.c.recv(n - len(b))
            if not d:
                raise ConnectionError('closed')
            b += d
        return b

    def send(self, b):
        self.c.sendall(b)

    def close(self):
        self.c.close()


class WebSocketTransport:
    """Minimal RFC 6455 client: binary subprotocol, masked client frames."""

    def __init__(self, port):
        self.c = socket.create_connection(('127.0.0.1', int(port)), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.c.sendall(('GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:%s\r\nUpgrade: websocket\r\n'
                        'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n'
                        'Sec-WebSocket-Protocol: binary\r\n\r\n' % (port, key)).encode())
        head = b''
        while b'\r\n\r\n' not in head:
            d = self.c.recv(1)
            if not d:
                raise ConnectionError('closed during upgrade')
            head += d
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise ConnectionError('websocket upgrade refused')
        self.buf = b''

    def _raw(self, n):
        b = b''
        while len(b) < n:
            d = self.c.recv(n - len(b))
            if not d:
                raise ConnectionError('closed')
            b += d
        return b

    def recv(self, n):
        while len(self.buf) < n:
            h = self._raw(2)
            op, ln = h[0] & 0x0F, h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack('>H', self._raw(2))[0]
            elif ln == 127:
                ln = struct.unpack('>Q', self._raw(8))[0]
            mask = self._raw(4) if h[1] & 0x80 else None
            data = self._raw(ln)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 8:
                raise ConnectionError('websocket closed')
            if op == 1:
                data = base64.b64decode(data)
            if op in (0, 1, 2):
                self.buf += data
        r, self.buf = self.buf[:n], self.buf[n:]
        return r

    def send(self, b):
        mask = os.urandom(4)
        hdr = bytes([0x82])
        n = len(b)
        hdr += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n)
        self.c.sendall(hdr + mask + bytes(x ^ mask[i % 4] for i, x in enumerate(b)))

    def close(self):
        self.c.close()


def attempt(make, sectype, password=None):
    t = make()
    try:
        if not t.recv(12).startswith(b'RFB '):
            raise AssertionError('not an RFB server')
        t.send(b'RFB 003.008\n')
        n = t.recv(1)[0]
        types = list(t.recv(n))
        assert types == [2], ('security types offered', types)
        t.send(bytes([sectype]))
        if sectype == 2:
            ch = t.recv(16)
            k = vnc_key(password)
            t.send(des_encrypt(k, ch[:8]) + des_encrypt(k, ch[8:]))
        return struct.unpack('>I', t.recv(4))[0] == 0
    except (ConnectionError, OSError):
        return False
    finally:
        t.close()


def main():
    mode, target, password = sys.argv[1:4]
    make = (lambda: UnixTransport(target)) if mode == 'unix' else (lambda: WebSocketTransport(target))
    wrong = 'WRONGPW1' if password != 'WRONGPW1' else 'WRONGPW2'
    assert attempt(make, 1) is False, mode + ': None authentication must be refused'
    assert attempt(make, 2, wrong) is False, mode + ': a wrong VNC password must be rejected'
    assert attempt(make, 2, password) is True, mode + ': the correct VNC password must authenticate'
    print(mode + ': only VNC auth offered; none-auth refused; wrong password rejected; correct password accepted')


if __name__ == '__main__':
    main()
