import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Eye, EyeOff } from 'lucide-react';
import { api } from '../lib/api';
import Badge from '../components/Badge';

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
  const { data: config, isLoading } = useQuery({ queryKey: ['config'], queryFn: api.config });

  if (isLoading) return <div className="text-secondary">Loading configuration...</div>;
  if (!config) return null;

  // Group config keys
  const groups = {
    'AI Providers': {},
    'Platform': {},
    'Performance': {},
    'System': {},
    'Other': {}
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
    <div className="space-y-6 pb-12">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-primary">Configuration</h2>
        <Badge variant="warning">Read-only</Badge>
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
  );
}
