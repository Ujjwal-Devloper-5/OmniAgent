import React, { useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Cpu } from 'lucide-react';
import { getToken, setToken as setApiToken, api } from './lib/api';
import { ToastProvider } from './components/ToastProvider';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import Overview from './pages/Overview';
import Models from './pages/Models';
import Users from './pages/Users';
import Sessions from './pages/Sessions';
import Logs from './pages/Logs';
import Config from './pages/Config';
import Button from './components/Button';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 15000 } },
});

function LoginScreen({ onLogin }) {
  const [token, setTokenInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      setApiToken(token.trim());
      await api.status();
      onLogin();
    } catch {
      setError('Invalid token or Admin API is not reachable.');
      setTokenInput('');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#08090E] p-4">
      <div className="w-full max-w-md bg-surface border border-border rounded-2xl shadow-2xl p-8">
        <div className="flex flex-col items-center mb-8">
          <div className="flex items-center gap-2 text-indigo-400 font-bold text-3xl tracking-tight mb-2">
            <Cpu className="w-8 h-8" />
            <span>OmniAgent</span>
          </div>
          <p className="text-secondary text-center text-sm">Sign in to manage your AI agent</p>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input 
              type="password" 
              placeholder="Enter Admin Token" 
              value={token}
              onChange={e => setTokenInput(e.target.value)}
              className="w-full bg-[#08090E] border border-border rounded-lg px-4 py-3 text-primary focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none transition-all"
              required
            />
          </div>
          {error && <p className="text-red-500 text-sm">{error}</p>}
          <Button type="submit" className="w-full" size="lg" loading={loading}>Sign In</Button>
        </form>
      </div>
    </div>
  );
}

function PageTitle() {
  const location = useLocation();
  const titles = {
    '/': 'Overview',
    '/models': 'Model Registry',
    '/users': 'Users',
    '/sessions': 'Sessions',
    '/logs': 'Live Logs',
    '/config': 'Configuration'
  };
  return <TopBar title={titles[location.pathname] || 'Dashboard'} />;
}

function DashboardLayout({ children }) {
  return (
    <div className="flex min-h-screen bg-[#08090E]">
      <Sidebar />
      <div className="flex-1 ml-[240px] flex flex-col h-screen overflow-hidden">
        <PageTitle />
        <main className="flex-1 overflow-y-auto p-6">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/models" element={<Models />} />
            <Route path="/users" element={<Users />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/config" element={<Config />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  
  if (!authed) return <LoginScreen onLogin={() => setAuthed(true)} />;
  
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <DashboardLayout />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
