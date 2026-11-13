#!/usr/bin/env python3
"""HomeHub: a dependency-free dashboard for trusted home networks."""
import concurrent.futures
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
DATA = Path(os.environ.get('HOMEHUB_DATA', ROOT / 'data' / 'entries.json'))
CACHE = {}
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def url(value, destination=False):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 or c == '\\' for c in value):
        raise ValueError('Enter a valid HTTP or HTTPS URL without spaces.')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid URL or port.') from None
    if parsed.scheme not in ('http', 'https') or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError('Use an HTTP or HTTPS URL without embedded credentials.')
    if port == 0:
        raise ValueError('Port must be between 1 and 65535.')
    if destination:
        if host.lower().rstrip('.') == 'localhost' or host.lower().rstrip('.').endswith('.localhost'):
            raise ValueError('Destination must use a LAN hostname or IP, not localhost.')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            try:
                address = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
            except OSError:
                address = None
        if address and (address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast):
            raise ValueError('Destination must use a reachable LAN address.')
    return parsed


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('Entry must be an object.')
    result = {}
    for key, limit, default in [('name', 80, ''), ('description', 240, ''), ('icon', 12, '↗'), ('category', 40, 'General')]:
        value = raw.get(key, default)
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError(f'{key.capitalize()} must be text, at most {limit} characters.')
        result[key] = value.strip() or default
    if not result['name']:
        raise ValueError('Name is required.')
    result['kind'] = raw.get('kind', 'local')
    if result['kind'] not in ('local', 'external'):
        raise ValueError('Choose Local app or Website.')
    result['url'] = raw.get('url', '')
    url(result['url'], destination=True)
    result['healthUrl'] = raw.get('healthUrl', '')
    if not isinstance(result['healthUrl'], str):
        raise ValueError('Health endpoint must be text.')
    if result['kind'] == 'external':
        result['healthUrl'] = ''
    elif result['healthUrl']:
        url(result['healthUrl'])
    return result


def read_entries():
    if not DATA.exists():
        return []
    return json.loads(DATA.read_text())


def write_entries(entries):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=DATA.parent, prefix='.entries-')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(entries, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, DATA)
    finally:
        if os.path.exists(name):
            os.unlink(name)


NETWORKS = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '::1/128', 'fc00::/7'))


def health_target(value):
    parsed = url(value)
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or any(not any(ip in network for network in NETWORKS if ip.version == network.version) for ip in ips):
        raise ValueError('Health checks are restricted to private LAN and loopback addresses.')
    return parsed, str(ips[0])


def probe(value):
    try:
        parsed, address = health_target(value)
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=3)
        # Pin the validated IP while keeping the hostname for TLS verification and Host.
        connection._create_connection = lambda addr, timeout, source_address=None: socket.create_connection((address, addr[1]), timeout, source_address)
        try:
            connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), headers={'User-Agent': 'HomeHub/1', 'Connection': 'close'})
            response = connection.getresponse()
            return 'Healthy' if 200 <= response.status < 400 else 'Unreachable'
        finally:
            connection.close()
    except (OSError, ValueError, http.client.HTTPException):
        return 'Unreachable'


def statuses(entries):
    now = time.monotonic()
    result = {}
    with LOCK:
        active = {e['healthUrl'] for e in entries if e['kind'] == 'local' and e['healthUrl']}
        for stale in set(CACHE) - active:
            del CACHE[stale]
        for entry in entries:
            target = entry['healthUrl'] if entry['kind'] == 'local' else ''
            if not target:
                result[entry['id']] = 'Unknown'
                continue
            cached = CACHE.get(target)
            if cached and cached[1].done():
                result[entry['id']] = cached[1].result()
            else:
                result[entry['id']] = 'Unknown'
            if not cached or (cached[1].done() and now - cached[0] > 30):
                CACHE[target] = (now, POOL.submit(probe, target))
    return result


class HomeHubServer(ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send(self, status, body, content_type='application/json'):
        if content_type == 'application/json':
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/api/entries', '/api/health'):
            with LOCK:
                entries = read_entries()
            self.send(200, entries if path.endswith('entries') else statuses(entries))
        elif path in ('/', '/app.js', '/style.css'):
            name, mime = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}[path]
            self.send(200, (ROOT / 'static' / name).read_bytes(), mime)
        else:
            self.send(404, {'error': 'Not found.'})

    def mutate(self):
        # Custom header plus JSON prevents cross-origin forms and simple requests.
        origin = self.headers.get('Origin')
        if self.headers.get('X-HomeHub') != '1' or self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
            return self.send(403, {'error': 'Use HomeHub from its own page.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 16384:
                raise ValueError('Request too large.')
            if self.command != 'DELETE' and self.headers.get('Content-Type') != 'application/json':
                raise ValueError('Send application/json.')
            path = urlsplit(self.path).path
            identifier = path.removeprefix('/api/entries/')
            with LOCK:
                entries = read_entries()
                if self.command == 'POST' and path == '/api/entries':
                    if len(entries) >= 100:
                        raise ValueError('Maximum 100 entries.')
                    entry = validate(json.loads(self.rfile.read(length)))
                    entry['id'] = str(uuid.uuid4())
                    entries.append(entry)
                elif self.command in ('PUT', 'DELETE') and path.startswith('/api/entries/'):
                    index = next((i for i, e in enumerate(entries) if e['id'] == identifier), None)
                    if index is None:
                        return self.send(404, {'error': 'Entry not found.'})
                    if self.command == 'DELETE':
                        entries.pop(index)
                    else:
                        entries[index] = dict(validate(json.loads(self.rfile.read(length))), id=identifier)
                else:
                    return self.send(404, {'error': 'Not found.'})
                write_entries(entries)
            self.send(200, {'ok': True})
        except (ValueError, UnicodeError) as error:
            self.send(400, {'error': str(error)})
        except OSError:
            self.send(500, {'error': 'Could not save configuration. Check server storage permissions.'})

    do_POST = mutate
    do_PUT = mutate
    do_DELETE = mutate


if __name__ == '__main__':
    host = os.environ.get('HOMEHUB_HOST', '0.0.0.0')
    port = int(os.environ.get('HOMEHUB_PORT', '8080'))
    server = HomeHubServer((host, port), Handler)
    server.daemon_threads = True
    print(f'HomeHub listening on {host}:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        POOL.shutdown(wait=False, cancel_futures=True)
