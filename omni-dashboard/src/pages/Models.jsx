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
  const { data: models, isLoading } = useQuery({ queryKey: ['models'], queryFn: api.models });
  const [modalOpen, setModalOpen] = useState(false);
  const [editingModel, setEditingModel] = useState(null);

  const deleteMut = useMutation({
    mutationFn: api.deleteModel,
    onSuccess: () => {
      toast.success("Model deleted");
      queryClient.invalidateQueries(['models']);
    },
    onError: (e) => toast.error(e.message)
  });

  const handleDelete = (id) => {
    if (confirm(`Delete model ${id}?`)) {
      deleteMut.mutate(id);
    }
  };

  const columns = [
    { key: 'id', header: 'ID', render: (row) => <span className="font-mono" title={row.id}>{row.id.length > 20 ? row.id.substring(0,20)+'...' : row.id}</span> },
    { key: 'provider', header: 'Provider', render: (row) => <ProviderBadge provider={row.provider} /> },
    { key: 'intel', header: 'Intel', render: (row) => <StatusBar value={row.intelligence} /> },
    { key: 'speed', header: 'Speed', render: (row) => <StatusBar value={row.speed} /> },
    { key: 'tools', header: 'Tools', render: (row) => <StatusBar value={row.tool_reliability} /> },
    { key: 'vision', header: 'Vision', render: (row) => row.vision ? <Check className="w-4 h-4 text-emerald-500" /> : <XIcon className="w-4 h-4 text-muted" /> },
    { key: 'score', header: 'Score', render: (row) => <span className="font-bold">{(row.intelligence * 3 + row.speed + row.tool_reliability * 2).toFixed(1)}</span> },
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
        </div>
        <Button icon={Plus} onClick={() => { setEditingModel(null); setModalOpen(true); }}>Add Model</Button>
      </div>

      <DataTable columns={columns} data={models} loading={isLoading} emptyMessage="No models found." />

      {modalOpen && (
        <ModelFormModal 
          model={editingModel} 
          onClose={() => setModalOpen(false)} 
          onSuccess={() => { setModalOpen(false); queryClient.invalidateQueries(['models']); }}
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
