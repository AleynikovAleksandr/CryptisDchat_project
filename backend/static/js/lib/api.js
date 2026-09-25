/* REST-клиент FastAPI: JWT в заголовке, прозрачное обновление по refresh-токену (ТЗ 4.2). */
(function (root) {
  'use strict';

  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.detail) || 'HTTP ' + status);
      this.status = status;
      this.code = (body && body.error) || 'http_' + status;
      this.body = body || {};
    }
  }

  const session = { access: null, refresh: null, onRefresh: null, onExpired: null };
  let refreshing = null;

  async function doRefresh() {
    if (!session.refresh) throw new ApiError(401, { error: 'no_session' });
    if (!refreshing) {
      refreshing = (async () => {
        const r = await fetch('/api/auth/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: session.refresh }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) {
          session.access = null;
          if (session.onExpired) session.onExpired(body.error || 'refresh_failed');
          throw new ApiError(r.status, body);
        }
        session.access = body.access_token;
        session.refresh = body.refresh_token;  // ротация: старый refresh больше недействителен
        if (session.onRefresh) await session.onRefresh(body);
        return body;
      })().finally(() => { refreshing = null; });
    }
    return refreshing;
  }

  async function request(method, path, body, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    let payload = body;
    if (body !== undefined && !(body instanceof FormData) && !(body instanceof Uint8Array)) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      if (session.access) headers.Authorization = 'Bearer ' + session.access;
      const r = await fetch(path, { method, headers, body: payload });
      if (r.status === 401 && attempt === 0 && session.refresh) {
        const err = await r.json().catch(() => ({}));
        if (['token_invalid', 'unauthorized'].includes(err.error)) { await doRefresh(); continue; }
        if (err.error === 'token_revoked' && session.onExpired) session.onExpired('token_revoked');
        throw new ApiError(401, err);
      }
      if (opts.raw) {
        if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => ({})));
        return new Uint8Array(await r.arrayBuffer());
      }
      if (r.status === 204) return null;
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new ApiError(r.status, data);
      return data;
    }
    throw new ApiError(401, { error: 'unauthorized' });
  }

  root.CX = root.CX || {};
  root.CX.api = {
    ApiError, session, request, refresh: doRefresh,
    get: (p, o) => request('GET', p, undefined, o),
    post: (p, b, o) => request('POST', p, b, o),
    patch: (p, b) => request('PATCH', p, b),
    del: (p) => request('DELETE', p),
    bytes: (p) => request('GET', p, undefined, { raw: true }),
  };
})(typeof window !== 'undefined' ? window : globalThis);
