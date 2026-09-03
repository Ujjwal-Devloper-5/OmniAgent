import React, { useState, useRef, useEffect } from 'react';
import { Download, Pause, Play, Trash2 } from 'lucide-react';
import { useLogs } from '../hooks/useLogs';
import { getToken } from '../lib/api';
import Badge from '../components/Badge';
import Button from '../components/Button';

export default function Logs() {
  const token = getToken();
  const { lines, connected } = useLogs(token);
  const [filter, setFilter] = useState('ALL');
  const [search, setSearch] = useState('');
  const [paused, setPaused] = useState(false);
  const [displayLines, setDisplayLines] = useState([]);
  const logsEndRef = useRef(null);
  const containerRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (paused) return;
    let filtered = lines;
    if (filter !== 'ALL') filtered = filtered.filter(l => l.includes(`[${filter}]`));
    if (search) filtered = filtered.filter(l => l.toLowerCase().includes(search.toLowerCase()));
    setDisplayLines(filtered);
  }, [lines, filter, search, paused]);

  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'auto' });
    }
  }, [displayLines, autoScroll]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setAutoScroll(isAtBottom);
  };

  const clearLines = () => {
    setDisplayLines([]);
  };

  const downloadLogs = () => {
    const blob = new Blob([displayLines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `omniagent_logs_${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const levels = ['ALL', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

  const renderLine = (line, idx) => {
    let color = 'text-[#8892A4]';
    let bold = false;
    if (line.includes('[WARNING]')) color = 'text-[#F59E0B]';
    else if (line.includes('[ERROR]')) color = 'text-[#EF4444]';
    else if (line.includes('[CRITICAL]')) { color = 'text-[#EF4444]'; bold = true; }
    else if (line.includes('started') || line.includes('ready') || line.includes('OK')) color = 'text-[#10B981]';
    
    return (
      <div key={idx} className={`${color} ${bold ? 'font-bold' : ''} whitespace-pre-wrap break-words leading-[1.6]`}>
        {line}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-primary">Live Logs</h2>
          <Badge variant={connected ? 'success' : 'danger'}>{connected ? 'Connected' : 'Disconnected'}</Badge>
        </div>
        <div className="flex items-center gap-2">
          <input 
            type="text" 
            placeholder="Search logs..." 
            value={search} 
            onChange={e => setSearch(e.target.value)}
            className="bg-surface border border-border rounded-md px-3 py-1.5 text-sm text-primary focus:border-indigo-500 focus:outline-none w-48"
          />
          <div className="flex bg-surface rounded-md border border-border overflow-hidden">
            {levels.map(l => (
              <button 
                key={l} 
                onClick={() => setFilter(l)}
                className={`px-3 py-1.5 text-xs font-medium transition-colors ${filter === l ? 'bg-indigo-600 text-white' : 'text-secondary hover:bg-subtle'}`}
              >
                {l}
              </button>
            ))}
          </div>
          <Button variant="ghost" size="sm" icon={Trash2} onClick={clearLines} />
          <Button variant="ghost" size="sm" icon={paused ? Play : Pause} onClick={() => setPaused(!paused)} />
          <Button variant="ghost" size="sm" icon={Download} onClick={downloadLogs} />
        </div>
      </div>

      <div 
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 bg-[#08090E] border border-border rounded-lg p-4 font-mono text-[12px] overflow-y-auto min-h-[500px]"
      >
        {displayLines.map((l, i) => renderLine(l, i))}
        <div ref={logsEndRef} />
      </div>
    </div>
  );
}
