import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Wrench, AlertTriangle, CheckCircle2, XCircle, ShieldAlert, Cpu } from 'lucide-react';
import { api } from '../lib/api';
import Badge from './Badge';

export default function McpStatusCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['mcpStatus'],
    queryFn: api.mcpStatus,
    refetchInterval: 30000,
    retry: 1,
  });

  if (isLoading) {
    return (
      <div className="bg-surface border border-border rounded-xl p-5 shadow-sm">
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Wrench className="w-5 h-5 text-indigo-400" />
            <h3 className="font-semibold text-primary">MCP Servers & Tools</h3>
          </div>
          <span className="text-xs text-secondary">Checking...</span>
        </div>
        <div className="py-6 text-center text-sm text-secondary">Loading MCP status...</div>
      </div>
    );
  }

  // Defensive fallback if endpoint returned runtime error or unavailable
  const hasError = !!data?.error;
  const isAvailable = data?.available === true;
  const servers = data?.servers || {};
  const serverEntries = Object.entries(servers);
  const totalTools = data?.total_tools ?? serverEntries.reduce((acc, [_, s]) => acc + (s.tools_count || s.tool_names?.length || 0), 0);

  return (
    <div className="bg-surface border border-border rounded-xl p-5 shadow-sm flex flex-col justify-between">
      <div>
        <div className="flex items-center justify-between pb-3 border-b border-border mb-4">
          <div className="flex items-center gap-2">
            <Wrench className="w-5 h-5 text-indigo-400" />
            <h3 className="font-semibold text-primary">MCP Tool Servers</h3>
          </div>
          <div className="flex items-center gap-2">
            {hasError ? (
              <Badge variant="warning">Status Degraded</Badge>
            ) : isAvailable ? (
              <Badge variant="success">Active</Badge>
            ) : (
              <Badge variant="neutral">Offline</Badge>
            )}
            <span className="text-xs font-mono text-secondary">{totalTools} Tools</span>
          </div>
        </div>

        {hasError && (
          <div className="mb-4 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg flex items-start gap-2.5 text-xs text-amber-400">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">MCP Subsystem Warning</p>
              <p className="text-secondary mt-0.5 font-mono break-all">{data.error}</p>
            </div>
          </div>
        )}

        {serverEntries.length > 0 ? (
          <div className="space-y-3">
            {serverEntries.map(([name, s]) => {
              const isHealthy = s.healthy && !s.disabled;
              const isTripped = s.disabled || (s.failures && s.failures >= 3);
              return (
                <div key={name} className="p-3 bg-[#08090E] border border-border rounded-lg flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Cpu className="w-4 h-4 text-secondary" />
                      <span className="font-medium text-sm text-primary capitalize">{name}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {isTripped ? (
                        <Badge variant="danger">Circuit Breaker Tripped</Badge>
                      ) : isHealthy ? (
                        <Badge variant="success">Healthy</Badge>
                      ) : (
                        <Badge variant="warning">Degraded</Badge>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-xs text-secondary">
                    <span>Tools: <strong className="text-primary">{s.tools_count ?? s.tool_names?.length ?? 0}</strong></span>
                    <span>Failures: <strong className={s.failures > 0 ? "text-red-400" : "text-primary"}>{s.failures || 0}</strong></span>
                  </div>

                  {s.tool_names && s.tool_names.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-1 border-t border-border/50">
                      {s.tool_names.slice(0, 6).map((tool) => (
                        <span key={tool} className="px-1.5 py-0.5 bg-subtle text-[11px] font-mono rounded text-secondary">
                          {tool}
                        </span>
                      ))}
                      {s.tool_names.length > 6 && (
                        <span className="text-[11px] text-muted self-center">
                          +{s.tool_names.length - 6} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          !hasError && (
            <div className="py-6 text-center text-xs text-secondary">
              No active MCP servers registered in current session.
            </div>
          )
        )}
      </div>

      <div className="pt-3 mt-4 border-t border-border flex items-center justify-between text-xs text-secondary">
        <span>Circuit Breaker: <span className="text-primary font-medium">Auto-Isolation (3 failures)</span></span>
        <span>Polling: <span className="text-primary font-medium">30s</span></span>
      </div>
    </div>
  );
}
