import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, Check, X as XIcon } from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from '../hooks/useToast';
import DataTable from '../components/DataTable';
import Button from '../components/Button';
import Badge from '../components/Badge';
import Modal from '../components/Modal';

function ProviderBadge({ provider }) {
  const colors = {
    openrouter: 'violet', ollama: 'blue', openai: 'success', gemini: 'warning', anthropic: 'danger', groq: 'info'
  };
  return <Badge variant={colors[provider] || 'neutral'}>{provider}</Badge>;
}

function StatusBar({ value }) {
  const v = Math.max(0, Math.min(10, value));
  return (
    <div className="w-16 h-1.5 bg-subtle rounded-full overflow-hidden">
      <div className="h-full bg-indigo-600 rounded-full" style={{ width: `${v * 10}%` }}></div>
    </div>
  );
}

export default function Models() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: rawModels, isLoading: isModelsLoading } = useQuery({ queryKey: ['models'], queryFn: api.models });
  const { data: statusData, isLoading: isStatusLoading } = useQuery({
    queryKey: ['modelsStatus'],
    queryFn: api.modelsStatus,
    retry: 1,
    refetchInterval: 30000,
  });

  const [modalOpen, setModalOpen] = useState(false);
  const [editingModel, setEditingModel] = useState(null);

  const statusModels = statusData?.models;
  const hasStatusError = !!statusData?.error;

  // Build Pareto status lookup
  const statusMap = new Map();
  if (Array.isArray(statusModels) && statusModels.length > 0) {
    statusModels.forEach((m, idx) => {
      statusMap.set(m.id, { ...m, rank: idx + 1 });
    });
  }

  // If statusData is available with models, but rawModels is empty/loading, or enrich rawModels with status
  const baseList = rawModels && rawModels.length > 0
    ? rawModels
    : (Array.isArray(statusModels) && statusModels.length > 0 ? statusModels : []);

  const models = baseList.map(m => {
    const status = statusMap.get(m.id);
    const score = status?.score !== undefined
      ? status.score
      : Number(((m.intelligence || 5) * 3 + (m.speed || 5) + (m.tool_reliability || 5) * 2).toFixed(1));
    const rank = status?.rank;
    const available = status?.available !== undefined ? status.available : (m.available !== false);
    return {
      ...m,
      score,
      rank,
      available,
    };
  }).sort((a, b) => {
    if (a.rank && b.rank) return a.rank - b.rank;
    return (b.score || 0) - (a.score || 0);
  });

  const isLoading = isModelsLoading && (!models || models.length === 0);

  const deleteMut = useMutation({
    mutationFn: api.deleteModel,
    onSuccess: () => {
      toast.success("Model deleted");
      queryClient.invalidateQueries(['models']);
      queryClient.invalidateQueries(['modelsStatus']);
    },
    onError: (e) => toast.error(e.message)
  });

  const handleDelete = (id) => {
    if (confirm(`Delete model ${id}?`)) {
      deleteMut.mutate(id);
    }
  };

  const columns = [
    { key: 'rank', header: 'Rank', render: (row) => row.rank ? <Badge variant={row.rank <= 3 ? 'success' : 'neutral'}>#{row.rank}</Badge> : <span className="text-muted font-mono">-</span> },
    { key: 'id', header: 'ID', render: (row) => <span className="font-mono" title={row.id}>{row.id.length > 20 ? row.id.substring(0,20)+'...' : row.id}</span> },
    { key: 'provider', header: 'Provider', render: (row) => <ProviderBadge provider={row.provider} /> },
    { key: 'intel', header: 'Intel', render: (row) => <StatusBar value={row.intelligence} /> },
    { key: 'speed', header: 'Speed', render: (row) => <StatusBar value={row.speed} /> },
    { key: 'tools', header: 'Tools', render: (row) => <StatusBar value={row.tool_reliability} /> },
    { key: 'vision', header: 'Vision', render: (row) => row.vision ? <Check className="w-4 h-4 text-emerald-500" /> : <XIcon className="w-4 h-4 text-muted" /> },
    { key: 'score', header: 'Pareto Score', render: (row) => <span className="font-bold text-primary">{row.score}</span> },
    { key: 'status', header: 'Status', render: (row) => <Badge variant={row.available ? 'success' : 'danger'}>{row.available ? 'Available' : 'Unavailable'}</Badge> },
    { key: 'actions', header: '', render: (row) => (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" icon={Edit2} onClick={() => { setEditingModel(row); setModalOpen(true); }} />
          <Button variant="ghost" size="sm" icon={Trash2} className="text-red-500 hover:text-red-400" onClick={() => handleDelete(row.id)} />
        </div>
      )
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-primary">Model Registry</h2>
          <Badge variant="info">{models?.length || 0} Total</Badge>
          {statusMap.size > 0 ? (
            <Badge variant="success">Live Pareto Active</Badge>
          ) : hasStatusError ? (
            <Badge variant="warning">Fallback Scoring</Badge>
          ) : null}
        </div>
        <Button icon={Plus} onClick={() => { setEditingModel(null); setModalOpen(true); }}>Add Model</Button>
      </div>

      <DataTable columns={columns} data={models} loading={isLoading} emptyMessage="No models found." />

      {modalOpen && (
        <ModelFormModal 
          model={editingModel} 
          onClose={() => setModalOpen(false)} 
          onSuccess={() => { setModalOpen(false); queryClient.invalidateQueries(['models']); queryClient.invalidateQueries(['modelsStatus']); }}
        />
      )}
    </div>
  );
}

function ModelFormModal({ model, onClose, onSuccess }) {
  const { toast } = useToast();
  const [formData, setFormData] = useState(model || {
    id: '', provider: 'openrouter', intelligence: 5, speed: 5, tool_reliability: 5, vision: false, context_window: 8192, tags: []
  });

  const saveMut = useMutation({
    mutationFn: (d) => model ? api.updateModel(model.id, d) : api.addModel(d),
    onSuccess: () => {
      toast.success(`Model ${model ? 'updated' : 'added'} successfully`);
      onSuccess();
    },
    onError: (e) => toast.error(e.message)
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    const data = { ...formData, tags: Array.isArray(formData.tags) ? formData.tags : formData.tags.split(',').map(s=>s.trim()).filter(Boolean) };
    saveMut.mutate(data);
  };

  return (
    <Modal open={true} onClose={onClose} title={model ? "Edit Model" : "Add Model"} 
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button loading={saveMut.isPending} onClick={handleSubmit}>Save Model</Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-xs font-medium uppercase tracking-wider text-secondary mb-1">Model ID</label>
          <input type="text" value={formData.id} onChange={e => setFormData({...formData, id: e.target.value})} disabled={!!model}
            className="w-full bg-surface border border-border rounded-md px-3 py-2 text-primary focus:border-indigo-500 focus:outline-none" required />
        </div>
        <div>
          <label className="block text-xs font-medium uppercase tracking-wider text-secondary mb-1">Provider</label>
          <select value={formData.provider} onChange={e => setFormData({...formData, provider: e.target.value})}
            className="w-full bg-surface border border-border rounded-md px-3 py-2 text-primary focus:border-indigo-500 focus:outline-none">
            {['openrouter', 'ollama', 'openai', 'gemini', 'anthropic', 'groq'].map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-4">
          {['intelligence', 'speed', 'tool_reliability'].map(field => (
            <div key={field}>
              <label className="block text-xs font-medium uppercase tracking-wider text-secondary mb-1 capitalize">{field.replace('_', ' ')}: {formData[field]}</label>
              <input type="range" min="1" max="10" value={formData[field]} onChange={e => setFormData({...formData, [field]: parseInt(e.target.value)})}
                className="w-full accent-indigo-600" />
            </div>
          ))}
          <div>
             <label className="block text-xs font-medium uppercase tracking-wider text-secondary mb-1">Context Window</label>
             <input type="number" value={formData.context_window} onChange={e => setFormData({...formData, context_window: parseInt(e.target.value)})}
                className="w-full bg-surface border border-border rounded-md px-3 py-2 text-primary focus:border-indigo-500 focus:outline-none" required />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input type="checkbox" id="vision" checked={formData.vision} onChange={e => setFormData({...formData, vision: e.target.checked})} className="accent-indigo-600 w-4 h-4" />
          <label htmlFor="vision" className="text-sm text-primary">Supports Vision</label>
        </div>
        <div>
          <label className="block text-xs font-medium uppercase tracking-wider text-secondary mb-1">Tags (comma separated)</label>
          <input type="text" value={Array.isArray(formData.tags) ? formData.tags.join(', ') : formData.tags} onChange={e => setFormData({...formData, tags: e.target.value})}
            className="w-full bg-surface border border-border rounded-md px-3 py-2 text-primary focus:border-indigo-500 focus:outline-none" />
        </div>
      </form>
    </Modal>
  );
}
