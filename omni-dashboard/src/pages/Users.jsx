import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Edit2, Users as UsersIcon } from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from '../hooks/useToast';
import DataTable from '../components/DataTable';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Modal from '../components/Modal';

function formatRelTime(ts) {
  if (!ts) return 'Unknown';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

export default function Users() {
  const queryClient = useQueryClient();
  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: api.users });
  const [editingUser, setEditingUser] = useState(null);

  const columns = [
    { key: 'user_id', header: 'User ID', render: (row) => <span className="font-mono text-xs">{row.user_id}</span> },
    { key: 'platform', header: 'Platform', render: (row) => {
      const colors = { discord: 'indigo', telegram: 'blue', slack: 'violet' };
      return <Badge variant={colors[row.platform] || 'neutral'}>{row.platform}</Badge>;
    }},
    { key: 'system_prompt', header: 'System Prompt', render: (row) => (
      row.system_prompt ? 
        <span className="text-secondary italic text-xs max-w-[200px] truncate block">{row.system_prompt.substring(0, 60)}{row.system_prompt.length>60?'...':''}</span> : 
        <span className="text-muted text-xs">None</span>
    )},
    { key: 'preferred_model', header: 'Pref. Model', render: (row) => <span className="font-mono text-xs">{row.preferred_model || 'auto'}</span> },
    { key: 'updated_at', header: 'Last Active', render: (row) => <span className="text-xs">{formatRelTime(row.updated_at)}</span> },
    { key: 'actions', header: '', render: (row) => (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" icon={Edit2} onClick={() => setEditingUser(row)} />
        </div>
      )
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-primary">Users</h2>
        <Badge variant="info">{users?.length || 0} Total</Badge>
      </div>

      {!isLoading && users?.length === 0 ? (
        <div className="border border-border rounded-lg bg-surface">
          <div className="flex flex-col items-center justify-center p-12 text-center">
            <div className="flex items-center justify-center w-16 h-16 rounded-full bg-subtle mb-4 text-secondary">
              <UsersIcon className="w-8 h-8" />
            </div>
            <h3 className="text-lg font-medium text-primary mb-2">No users yet</h3>
            <p className="text-secondary max-w-sm">Users appear here once they interact with the bot.</p>
          </div>
        </div>
      ) : (
        <DataTable columns={columns} data={users} loading={isLoading} />
      )}

      {editingUser && (
        <PromptModal user={editingUser} onClose={() => setEditingUser(null)} onSuccess={() => { setEditingUser(null); queryClient.invalidateQueries(['users']); }} />
      )}
    </div>
  );
}

function PromptModal({ user, onClose, onSuccess }) {
  const { toast } = useToast();
  const [prompt, setPrompt] = useState(user.system_prompt || '');

  const saveMut = useMutation({
    mutationFn: () => api.setPrompt(user.user_id, prompt),
    onSuccess: () => { toast.success("Prompt saved"); onSuccess(); },
    onError: (e) => toast.error(e.message)
  });

  const clearMut = useMutation({
    mutationFn: () => api.clearPrompt(user.user_id),
    onSuccess: () => { toast.success("Prompt cleared"); onSuccess(); },
    onError: (e) => toast.error(e.message)
  });

  return (
    <Modal open={true} onClose={onClose} title="Custom System Prompt" 
      footer={
        <div className="flex justify-between w-full">
          <Button variant="danger" onClick={() => clearMut.mutate()} loading={clearMut.isPending}>Clear Prompt</Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={() => saveMut.mutate()} loading={saveMut.isPending}>Save</Button>
          </div>
        </div>
      }
    >
      <div className="space-y-2">
        <p className="text-sm text-secondary">User: <span className="font-mono text-primary">{user.user_id}</span></p>
        <textarea 
          className="w-full bg-surface border border-border rounded-md px-3 py-2 text-primary focus:border-indigo-500 focus:outline-none resize-none font-mono text-sm" 
          rows="8" 
          placeholder="Enter a custom system prompt for this user. This will be injected into every AI response for this user..."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        ></textarea>
        <p className="text-xs text-secondary text-right">{prompt.length} chars</p>
      </div>
    </Modal>
  );
}
