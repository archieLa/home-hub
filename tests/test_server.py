import concurrent.futures
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class HomeHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.HomeHubServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.port = cls.http.server_port

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        server.POOL.shutdown()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old = server.DATA
        server.DATA = Path(self.directory.name) / 'entries.json'
        self.entry = dict(name='Media', description='Films', icon='🎬', category='Home', kind='local', url='http://media.local:8096', healthUrl='')

    def tearDown(self):
        server.DATA = self.old
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, None if body is None else json.dumps(body), headers if headers is not None else {'Content-Type': 'application/json', 'X-HomeHub': '1'})
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    def test_crud_and_persistence(self):
        self.assertEqual(self.request('POST', '/api/entries', self.entry)[0], 200)
        entries = self.request('GET', '/api/entries')[1]
        self.assertEqual(json.loads(server.DATA.read_text()), entries)
        identifier = entries[0]['id']
        self.entry['name'] = 'Updated'
        self.assertEqual(self.request('PUT', '/api/entries/' + identifier, self.entry)[0], 200)
        self.assertEqual(self.request('GET', '/api/entries')[1][0]['name'], 'Updated')
        self.assertEqual(self.request('DELETE', '/api/entries/' + identifier)[0], 200)
        self.assertEqual(self.request('GET', '/api/entries')[1], [])
        self.assertEqual(self.request('DELETE', '/api/entries/' + identifier)[0], 404)

    def test_destination_validation(self):
        for value in ('javascript:alert(1)', 'file:///etc/passwd', 'http://localhost:2', 'http://127.0.0.1', 'http://[::1]', 'http://0.0.0.0', 'http://169.254.169.254', 'https://user:pass@host.local', 'http://host:99999', 'http://host:0', 'http://host/a\n'):
            with self.subTest(value=value):
                self.assertEqual(self.request('POST', '/api/entries', dict(self.entry, url=value))[0], 400)
        self.assertEqual(self.request('POST', '/api/entries', [1])[0], 400)
        self.assertEqual(self.request('POST', '/api/entries', dict(self.entry, name=''))[0], 400)

    def test_cross_origin_protection(self):
        self.assertEqual(self.request('POST', '/api/entries', self.entry, {})[0], 403)
        for extra in ({'Origin': 'http://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('POST', '/api/entries', self.entry, dict({'Content-Type': 'application/json', 'X-HomeHub': '1'}, **extra))[0], 403)
        self.assertEqual(self.request('GET', '/api/entries')[1], [])

    def test_health_target_restrictions(self):
        for address in ('8.8.8.8', '169.254.169.254', '0.0.0.0', '224.0.0.1', '::ffff:127.0.0.1', 'fe80::1', '::'):
            with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', (address, 80))]):
                with self.assertRaises(ValueError):
                    server.health_target('http://test.local')
        with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('192.168.1.2', 80)), (0, 0, 0, '', ('8.8.8.8', 80))]):
            with self.assertRaises(ValueError):
                server.health_target('http://test.local')
        for address in ('127.0.0.1', '192.168.1.2', '10.1.2.3', '172.16.1.1', '::1', 'fd00::1'):
            with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', (address, 80))]):
                self.assertEqual(server.health_target('http://test.local')[1], address)

    def test_health_response_and_no_redirect_follow(self):
        from http.server import BaseHTTPRequestHandler
        class Endpoint(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302 if self.path == '/redirect' else 503 if self.path == '/error' else 200)
                self.send_header('Location', 'http://169.254.169.254/')
                self.end_headers()
            def log_message(self, *args):
                pass
        endpoint = server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=endpoint.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{endpoint.server_port}'
            self.assertEqual(server.probe(base), 'Healthy')
            self.assertEqual(server.probe(base + '/redirect'), 'Healthy')
            self.assertEqual(server.probe(base + '/error'), 'Unreachable')
        finally:
            endpoint.shutdown()
            endpoint.server_close()
        self.assertEqual(server.probe(base), 'Unreachable')

    def test_unknown_and_external_not_monitored(self):
        self.assertEqual(server.statuses([dict(self.entry, id='one')]), {'one': 'Unknown'})
        external = server.validate(dict(self.entry, kind='external', healthUrl='http://127.0.0.1'))
        self.assertEqual(external['healthUrl'], '')
        with patch.object(server.POOL, 'submit') as submit:
            self.assertEqual(server.statuses([dict(external, id='two')]), {'two': 'Unknown'})
            submit.assert_not_called()

    def test_unknown_then_cached_health(self):
        future = concurrent.futures.Future()
        entry = dict(self.entry, id='one', healthUrl='http://127.0.0.1:12345')
        server.CACHE.clear()
        with patch.object(server.POOL, 'submit', return_value=future) as submit:
            self.assertEqual(server.statuses([entry]), {'one': 'Unknown'})
            future.set_result('Healthy')
            self.assertEqual(server.statuses([entry]), {'one': 'Healthy'})
            self.assertEqual(submit.call_count, 1)

    def test_uploaded_png_persistence_and_removal(self):
        import base64, struct, zlib
        def chunk(kind, payload):
            return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x40\x90\x80\xff')) + chunk(b'IEND', b'')
        image = 'data:image/png;base64,' + base64.b64encode(png).decode()
        self.assertEqual(self.request('POST', '/api/entries', dict(self.entry, iconImage=image))[0], 200)
        saved = self.request('GET', '/api/entries')[1][0]
        self.assertEqual(saved['iconImage'], image)
        self.assertEqual(json.loads(server.DATA.read_text())[0]['iconImage'], image)
        self.assertEqual(self.request('PUT', '/api/entries/' + saved['id'], dict(saved, name='Renamed'))[0], 200)
        self.assertEqual(self.request('GET', '/api/entries')[1][0]['iconImage'], image)
        self.assertEqual(self.request('PUT', '/api/entries/' + saved['id'], dict(saved, iconImage=''))[0], 200)
        self.assertEqual(self.request('GET', '/api/entries')[1][0]['iconImage'], '')

    def test_rejects_unsafe_or_invalid_icons(self):
        import base64, struct, zlib
        for value in (None, 123, 'https://example.com/icon.png', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,!!!', 'data:image/png;base64,' + base64.b64encode(b'not a png').decode(), 'x' * 175001):
            with self.subTest(value=str(value)[:40]):
                self.assertEqual(self.request('POST', '/api/entries', dict(self.entry, iconImage=value))[0], 400)
        payload = struct.pack('>IIBBBBB', 1025, 1, 8, 6, 0, 0, 0)
        header = b'IHDR' + payload
        png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + header + struct.pack('>I', zlib.crc32(header))
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            server.validate_icon('data:image/png;base64,' + base64.b64encode(png).decode())

    def test_parallel_saves(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.request('POST', '/api/entries', dict(self.entry, name=str(i)))[0], range(16)))
        self.assertTrue(all(status == 200 for status in results))
        self.assertEqual(len(self.request('GET', '/api/entries')[1]), 16)

if __name__ == '__main__':
    unittest.main()
