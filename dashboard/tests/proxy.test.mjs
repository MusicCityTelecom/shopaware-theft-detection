import test from 'node:test';
import assert from 'node:assert/strict';
import { GET, POST } from '../src/app/api/[...path]/route.ts';

function request(method = 'GET', options = {}) {
  const req = new Request('http://localhost/api/health?check=1', { method, ...options });
  req.nextUrl = new URL(req.url);
  return req;
}
const context = { params: Promise.resolve({ path: ['health'] }) };

test('proxy retains auth, CSRF, origin, byte ranges and streams responses', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.match(url, /\/health\?check=1$/);
    for (const [name, value] of Object.entries({ cookie: 'session=test', origin: 'https://shopaware.example',
      'x-csrf-token': 'token', range: 'bytes=0-9' })) assert.equal(options.headers.get(name), value);
    assert.equal(options.headers.get('x-hop'), null);
    assert.equal(options.headers.get('host'), null);
    return new Response('0123456789', { status: 206, headers: {
      'content-range': 'bytes 0-9/100', 'set-cookie': 'session=new; HttpOnly',
      connection: 'x-private-hop', 'x-private-hop': 'remove' } });
  });
  const response = await GET(request('GET', { headers: { cookie: 'session=test',
    origin: 'https://shopaware.example', 'x-csrf-token': 'token', range: 'bytes=0-9',
    connection: 'x-hop', 'x-hop': 'remove', host: 'attacker' } }), context);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('content-range'), 'bytes 0-9/100');
  assert.equal(response.headers.get('set-cookie'), 'session=new; HttpOnly');
  assert.equal(response.headers.get('x-private-hop'), null);
  assert.equal(await response.text(), '0123456789');
});

test('rejects oversized requests including missing or dishonest length headers', async (t) => {
  t.mock.method(globalThis, 'fetch', () => { throw Error('must not forward'); });
  for (const headers of [{}, { 'content-length': '1' }, { 'content-length': '1048577' }]) {
    const response = await POST(request('POST', { headers, body: 'x'.repeat(1048577) }), context);
    assert.equal(response.status, 413);
  }
  assert.equal(globalThis.fetch.mock.calls.length, 0);
});

test('backend failure never leaks its address or exception', async (t) => {
  t.mock.method(globalThis, 'fetch', () => { throw Error('secret at private.internal:8000'); });
  const response = await GET(request(), context);
  assert.equal(response.status, 502);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await response.json(), { detail: 'ShopAware backend is unavailable' });
});

test('forwards a bounded mutation body exactly', async (t) => {
  t.mock.method(globalThis, 'fetch', async (_, options) => {
    assert.equal(new TextDecoder().decode(options.body), '{"reviewed":true}');
    return new Response(null, { status: 204 });
  });
  assert.equal((await POST(request('POST', { body: '{"reviewed":true}' }), context)).status, 204);
});
