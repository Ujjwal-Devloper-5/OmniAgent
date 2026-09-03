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
    window.location.reload();
    throw new Error('Unauthorized');
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
  reboot:  ()            => request('/reboot', { method: 'POST' }),
};
