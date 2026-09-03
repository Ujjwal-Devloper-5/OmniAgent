import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from '../hooks/useToast';
import DataTable from '../components/DataTable';
import Badge from '../components/Badge';
import Button from '../components/Button';

function formatRelTime(ts) {
  if (!ts) return 'Unknown';
  const diff = (Date.now() - new Date(ts * 1000).getTime()) / 1000;
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

export default function Sessions() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: sessions, isLoading } = useQuery({ queryKey: ['sessions'], queryFn: api.sessions });

  const clearMut = useMutation({
    mutationFn: api.clearSession,
    onSuccess: () => { toast.success("Session cleared"); queryClient.invalidateQueries(['sessions']); },
    onError: (e) => toast.error(e.message)
  });

  const columns = [
    { key: 'session_id', header: 'Session ID', render: (row) => {
        const platform = row.session_id.split('-')[0] || '';
        return (
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs">{row.session_id}</span>
          </div>
        );
      }
    },
    { key: 'platform', header: 'Platform', render: (row) => <Badge variant="neutral">{row.session_id.split('-')[0] || 'unknown'}</Badge> },
    { key: 'messages', header: 'Messages', render: (row) => <span className="text-right block w-10">{row.total_turns}</span> },
    { key: 'started', header: 'Started', render: (row) => <span className="text-xs">{new Date(row.first_ts * 1000).toLocaleString()}</span> },
    { key: 'last_active', header: 'Last Active', render: (row) => <span className="text-xs" title={new Date(row.last_ts * 1000).toLocaleString()}>{formatRelTime(row.last_ts)}</span> },
    { key: 'actions', header: '', render: (row) => (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" icon={Trash2} className="text-red-500 hover:text-red-400" onClick={() => {
            if (confirm("Clear memory for this session?")) clearMut.mutate(row.session_id);
          }} />
        </div>
      )
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-primary">Sessions</h2>
          <Badge variant="info">{sessions?.length || 0} Active</Badge>
        </div>
        {/* Bulk clear not implemented as per API, but keeping structure clean */}
      </div>
      <DataTable columns={columns} data={sessions} loading={isLoading} emptyMessage="No active sessions." />
    </div>
  );
}
