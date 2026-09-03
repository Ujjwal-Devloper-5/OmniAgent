import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Users, Cpu, CheckCircle, MessageSquare } from 'lucide-react';
import StatCard from '../components/StatCard';
import ProviderDot from '../components/ProviderDot';
import Badge from '../components/Badge';
import { api } from '../lib/api';

export default function Overview() {
  const { data: statusData } = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 30000 });
  const { data: sessionsData } = useQuery({ queryKey: ['sessions'], queryFn: api.sessions, refetchInterval: 30000 });
  const { data: modelsData } = useQuery({ queryKey: ['models'], queryFn: api.models, refetchInterval: 30000 });

  const activeSessions = sessionsData?.length || 0;
  const totalModels = modelsData?.length || 0;
  
  let healthyProviders = 0;
  let providers = [];
  if (statusData?.providers) {
    providers = Object.entries(statusData.providers).map(([name, p]) => ({name, ...p}));
    healthyProviders = providers.filter(p => p.configured && p.healthy).length;
  }
  
  const totalMessages = sessionsData?.reduce((acc, s) => acc + (s.total_turns || 0), 0) || 0;

  const recentSessions = (sessionsData || []).sort((a, b) => b.last_ts - a.last_ts).slice(0, 5);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Active Sessions" value={activeSessions} subtitle="In memory" icon={Users} color="indigo" />
        <StatCard title="Total Models" value={totalModels} subtitle="Available in registry" icon={Cpu} color="violet" />
        <StatCard title="Healthy Providers" value={healthyProviders} subtitle={`Out of ${providers.length} total`} icon={CheckCircle} color="emerald" />
        <StatCard title="Total Messages" value={totalMessages} subtitle="Across all sessions" icon={MessageSquare} color="blue" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div className="lg:col-span-3 bg-surface border border-border rounded-lg overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <h3 className="text-base font-semibold text-primary">Provider Health</h3>
          </div>
          <table className="w-full text-left text-sm">
            <thead className="bg-subtle border-b border-border">
              <tr>
                <th className="px-5 py-3 font-medium text-secondary">Provider</th>
                <th className="px-5 py-3 font-medium text-secondary">Status</th>
                <th className="px-5 py-3 font-medium text-secondary">Capabilities</th>
                <th className="px-5 py-3 font-medium text-secondary">Failures</th>
              </tr>
            </thead>
            <tbody>
              {providers.length > 0 ? providers.map((p) => (
                <tr key={p.name} className="border-b border-border last:border-0 hover:bg-subtle">
                  <td className="px-5 py-3 font-medium capitalize">{p.name}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <ProviderDot healthy={p.healthy} configured={p.configured} />
                      <span className="text-secondary">{!p.configured ? 'Not Configured' : p.healthy ? 'Healthy' : 'Failing'}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 flex gap-1 flex-wrap">
                    {p.capabilities?.map(c => <Badge key={c} variant="info">{c}</Badge>) || <span className="text-muted">-</span>}
                  </td>
                  <td className="px-5 py-3">
                    {p.consecutive_failures > 0 ? (
                      <span className="text-red-500 font-medium">{p.consecutive_failures}</span>
                    ) : (
                      <span className="text-secondary">0</span>
                    )}
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="4" className="px-5 py-8 text-center text-secondary">Loading providers...</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="lg:col-span-2 bg-surface border border-border rounded-lg overflow-hidden flex flex-col">
          <div className="px-5 py-4 border-b border-border">
            <h3 className="text-base font-semibold text-primary">Recent Sessions</h3>
          </div>
          <div className="flex-1">
            {recentSessions.length > 0 ? (
              <div className="divide-y divide-border">
                {recentSessions.map(s => {
                  const platform = s.session_id.split('-')[0] || 'unknown';
                  return (
                    <div key={s.session_id} className="p-4 hover:bg-subtle transition-colors flex items-center justify-between">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm text-primary max-w-[150px] truncate" title={s.session_id}>
                            {s.session_id.substring(0, 16)}...
                          </span>
                          <Badge variant="neutral">{platform}</Badge>
                        </div>
                        <span className="text-xs text-secondary">{s.total_turns} turns</span>
                      </div>
                      <span className="text-xs text-secondary">
                        {new Date(s.last_ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                      </span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="p-8 text-center text-secondary">No recent sessions</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
