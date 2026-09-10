import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, Save, Sliders, CheckCircle2, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import { useToast } from '../hooks/useToast';
import Badge from '../components/Badge';
import Button from '../components/Button';

function ConfigValue({ val, isSensitive }) {
  const [show, setShow] = useState(!isSensitive);
  const displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);

  if (!isSensitive) return <span className="font-bold text-primary">{displayVal}</span>;

  return (
    <div className="flex items-center gap-2">
      <span className="font-bold text-primary">{show ? displayVal : '••••••••••••••••'}</span>
      <button onClick={() => setShow(!show)} className="text-secondary hover:text-primary">
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

export default function Config() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: config, isLoading } = useQuery({ queryKey: ['config'], queryFn: api.config });

  const [formData, setFormData] = useState({
    routing_policy: 'AUTO',
    sandbox_ttl_seconds: 300,
    sandbox_max_concurrent: 5,
  });
  const [errors, setErrors] = useState({});

  useEffect(() => {
    if (config) {
      setFormData(prev => ({
        routing_policy: config.routing_policy || prev.routing_policy || 'AUTO',
        sandbox_ttl_seconds: config.sandbox_ttl_seconds ?? prev.sandbox_ttl_seconds ?? 300,
        sandbox_max_concurrent: config.sandbox_max_concurrent ?? prev.sandbox_max_concurrent ?? 5,
      }));
    }
  }, [config]);

  const updateMutation = useMutation({
    mutationFn: (data) => api.updateConfig(data),
    onSuccess: (res) => {
      if (res.status === 'ok') {
        toast.success('Configuration updated successfully');
        setErrors({});
        queryClient.invalidateQueries(['config']);
      } else if (res.status === 'partial') {
        toast.warning('Configuration updated with partial warnings');
        if (res.errors) setErrors(res.errors);
        queryClient.invalidateQueries(['config']);
      } else {
        if (res.errors && Object.keys(res.errors).length > 0) {
          toast.error('Configuration validation failed');
          setErrors(res.errors);
        } else {
          toast.info('No configuration changes applied');
        }
      }
    },
    onError: (err) => {
      if (err.message !== 'Rate limit exceeded') {
        toast.error(err.message || 'Failed to update configuration');
      }
    },
  });

  const handleSave = (e) => {
    if (e) e.preventDefault();
    setErrors({});
    updateMutation.mutate({
      routing_policy: formData.routing_policy,
      sandbox_ttl_seconds: Number(formData.sandbox_ttl_seconds),
      sandbox_max_concurrent: Number(formData.sandbox_max_concurrent),
    });
  };

  if (isLoading) return <div className="text-secondary">Loading configuration...</div>;
  if (!config) return null;

  // Group config keys
  const groups = {
    'AI Providers': {},
    'Platform': {},
    'Performance': {},
    'System': {},
    'Other': {},
  };

  Object.entries(config).forEach(([k, v]) => {
    const kLower = k.toLowerCase();
    if (kLower.includes('openai') || kLower.includes('anthropic') || kLower.includes('gemini') || kLower.includes('groq') || kLower.includes('ollama') || kLower.includes('openrouter')) {
      groups['AI Providers'][k] = v;
    } else if (kLower.includes('discord') || kLower.includes('telegram') || kLower.includes('slack')) {
      groups['Platform'][k] = v;
    } else if (kLower.includes('limit') || kLower.includes('timeout') || kLower.includes('threshold') || kLower.includes('max')) {
      groups['Performance'][k] = v;
    } else if (kLower.includes('db') || kLower.includes('python') || kLower.includes('version') || kLower.includes('path') || kLower.includes('host') || kLower.includes('port')) {
      groups['System'][k] = v;
    } else {
      groups['Other'][k] = v;
    }
  });

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-bold text-primary tracking-tight">Configuration</h2>
          <Badge variant="success">Hot-Reload Active</Badge>
        </div>
      </div>

      {/* Hot-reload Config Form */}
      <div className="bg-surface border border-border rounded-xl p-6 shadow-sm">
        <div className="flex items-center gap-2 mb-4 pb-3 border-b border-border">
          <Sliders className="w-5 h-5 text-indigo-400" />
          <h3 className="font-semibold text-primary">Runtime Controls (Hot-Reload)</h3>
          <span className="text-xs text-secondary ml-auto">Updates take effect immediately without restart</span>
        </div>

        <form onSubmit={handleSave} className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {/* Routing Policy */}
            <div>
              <label htmlFor="routing_policy" className="block text-xs font-semibold uppercase tracking-wider text-secondary mb-2">
                Routing Policy
              </label>
              <select
                id="routing_policy"
                name="routing_policy"
                value={formData.routing_policy}
                onChange={(e) => setFormData({ ...formData, routing_policy: e.target.value })}
                className="w-full bg-[#08090E] border border-border rounded-lg px-3 py-2.5 text-primary text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              >
                <option value="AUTO">AUTO — Dynamic Pareto balancing</option>
                <option value="ECO">ECO — Lowest cost tier</option>
                <option value="SPEED">SPEED — Lowest latency</option>
                <option value="QUALITY">QUALITY — Highest intelligence</option>
                <option value="OFFLINE">OFFLINE — Local models only</option>
              </select>
              {errors.routing_policy && (
                <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" /> {errors.routing_policy}
                </p>
              )}
              <p className="text-xs text-secondary/70 mt-1.5">Governs fallback & provider tier routing</p>
            </div>

            {/* Sandbox TTL */}
            <div>
              <label htmlFor="sandbox_ttl_seconds" className="block text-xs font-semibold uppercase tracking-wider text-secondary mb-2">
                Sandbox TTL (seconds)
              </label>
              <input
                type="number"
                id="sandbox_ttl_seconds"
                name="sandbox_ttl_seconds"
                min="60"
                max="3600"
                value={formData.sandbox_ttl_seconds}
                onChange={(e) => setFormData({ ...formData, sandbox_ttl_seconds: e.target.value })}
                className="w-full bg-[#08090E] border border-border rounded-lg px-3 py-2.5 text-primary text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
              {errors.sandbox_ttl_seconds && (
                <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" /> {errors.sandbox_ttl_seconds}
                </p>
              )}
              <p className="text-xs text-secondary/70 mt-1.5">Execution container lifespan (60–3600s)</p>
            </div>

            {/* Sandbox Max Concurrent */}
            <div>
              <label htmlFor="sandbox_max_concurrent" className="block text-xs font-semibold uppercase tracking-wider text-secondary mb-2">
                Sandbox Max Concurrent
              </label>
              <input
                type="number"
                id="sandbox_max_concurrent"
                name="sandbox_max_concurrent"
                min="1"
                max="50"
                value={formData.sandbox_max_concurrent}
                onChange={(e) => setFormData({ ...formData, sandbox_max_concurrent: e.target.value })}
                className="w-full bg-[#08090E] border border-border rounded-lg px-3 py-2.5 text-primary text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
              {errors.sandbox_max_concurrent && (
                <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" /> {errors.sandbox_max_concurrent}
                </p>
              )}
              <p className="text-xs text-secondary/70 mt-1.5">Max simultaneous sandboxes (1–50)</p>
            </div>
          </div>

          <div className="flex items-center justify-between pt-4 border-t border-border">
            <div className="flex items-center gap-2 text-xs text-secondary">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span>Hot-reload applies directly without dropping active conversations</span>
            </div>
            <Button
              type="submit"
              icon={Save}
              loading={updateMutation.isPending}
              className="px-5 py-2.5"
            >
              Save Changes
            </Button>
          </div>
        </form>
      </div>

      {/* Read-Only Parameters Section */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <h3 className="text-base font-semibold text-primary">System Parameters</h3>
          <Badge variant="neutral">Environment / Static</Badge>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {Object.entries(groups).filter(([_, keys]) => Object.keys(keys).length > 0).map(([groupName, keys]) => (
            <div key={groupName} className="bg-surface border border-border rounded-lg overflow-hidden">
              <div className="px-5 py-3 border-b border-border bg-subtle/50">
                <h3 className="font-medium text-primary">{groupName}</h3>
              </div>
              <div className="divide-y divide-border">
                {Object.entries(keys).map(([k, v]) => {
                  const isSensitive = /key|secret|token|password/i.test(k);
                  return (
                    <div key={k} className="px-5 py-3 flex items-center justify-between hover:bg-subtle/30">
                      <span className="font-mono text-sm text-secondary">{k}</span>
                      <ConfigValue val={v} isSensitive={isSensitive} />
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
