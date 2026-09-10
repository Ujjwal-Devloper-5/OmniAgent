const BASE = '/api';

export function getToken() {
  return localStorage.getItem('omni_token');
}

export function setToken(token) {
  localStorage.setItem('omni_token', token);
}

export function clearToken() {
  localStorage.removeItem('omni_token');
}

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (res.status === 401 || res.status === 403) {
    clearToken();
    window.dispatchEvent(new CustomEvent('auth:unauthorized', { detail: { status: res.status } }));
    throw new Error('Unauthorized');
  }
  if (res.status === 429) {
    const retryAfter = res.headers.get('Retry-After');
    const err = await res.json().catch(() => ({ detail: 'Too many requests. Please wait before retrying.' }));
    window.dispatchEvent(
      new CustomEvent('api:rate-limited', {
        detail: { detail: err.detail || 'Rate limit exceeded', retryAfter: retryAfter || undefined },
      })
    );
    throw new Error('Rate limit exceeded');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  health:  ()            => request('/health'),
  status:  ()            => request('/status'),
  models:  ()            => request('/models').then(res => res.models),
  modelsStatus: ()       => request('/models/status'),
  addModel:    (data)    => request('/models', { method: 'POST', body: JSON.stringify(data) }),
  updateModel: (id, d)   => request(`/models/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(d) }),
  deleteModel: (id)      => request(`/models/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  users:   ()            => request('/users'),
  user:    (id)          => request(`/users/${encodeURIComponent(id)}`),
  setPrompt:  (id, p)    => request(`/users/${encodeURIComponent(id)}/system_prompt`, { method: 'POST', body: JSON.stringify({ prompt: p }) }),
  clearPrompt:(id)       => request(`/users/${encodeURIComponent(id)}/system_prompt`, { method: 'DELETE' }),
  sessions:()            => request('/sessions'),
  clearSession:(id)      => request(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  logs:    ()            => request('/logs'),
  config:  ()            => request('/config'),
  updateConfig: (data)   => request('/config/update', { method: 'POST', body: JSON.stringify(data) }),
  mcpStatus: ()          => request('/mcp/status'),
  reboot:  ()            => request('/reboot', { method: 'POST' }),
};
