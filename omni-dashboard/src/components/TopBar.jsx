import React, { useState, useEffect } from 'react';
import { RefreshCw, Power } from 'lucide-react';
import Button from './Button';
import { api } from '../lib/api';
import { useToast } from '../hooks/useToast';

export default function TopBar({ title }) {
  const [time, setTime] = useState(new Date().toLocaleTimeString());
  const { toast } = useToast();

  useEffect(() => {
    const int = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(int);
  }, []);

  const handleReboot = async () => {
    if (confirm("Are you sure you want to reboot the AI agent?")) {
      try {
        await api.reboot();
        toast.success("Reboot initiated");
      } catch (err) {
        toast.error("Reboot failed: " + err.message);
      }
    }
  };

  const handleRefresh = () => {
    window.location.reload();
  };

  return (
    <div className="h-[56px] bg-[#08090E] border-b border-border flex items-center justify-between px-6 sticky top-0 z-40">
      <h1 className="text-lg font-semibold text-primary">{title}</h1>
      <div className="flex items-center gap-4">
        <span className="text-sm font-mono text-secondary">{time}</span>
        <Button variant="ghost" size="sm" icon={RefreshCw} onClick={handleRefresh} />
        <Button variant="danger" size="sm" icon={Power} onClick={handleReboot}>Reboot</Button>
      </div>
    </div>
  );
}
