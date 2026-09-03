import React, { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Cpu, Users, MessageSquare, Terminal, Settings } from 'lucide-react';
import { api } from '../lib/api';

export default function Sidebar() {
  const [isHealthy, setIsHealthy] = useState(true);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        await api.health();
        setIsHealthy(true);
      } catch {
        setIsHealthy(false);
      }
    };
    checkHealth();
    const int = setInterval(checkHealth, 15000);
    return () => clearInterval(int);
  }, []);

  const navItems = [
    { to: '/', icon: LayoutDashboard, label: 'Overview' },
    { to: '/models', icon: Cpu, label: 'Models' },
    { to: '/users', icon: Users, label: 'Users' },
    { to: '/sessions', icon: MessageSquare, label: 'Sessions' },
    { to: '/logs', icon: Terminal, label: 'Logs' },
    { to: '/config', icon: Settings, label: 'Config' },
  ];

  return (
    <div className="fixed left-0 top-0 bottom-0 w-[240px] bg-surface border-r border-border flex flex-col">
      <div className="p-6">
        <div className="flex items-center gap-2 text-indigo-400 font-bold text-xl tracking-tight">
          <Cpu className="w-6 h-6" />
          <span>OmniAgent</span>
        </div>
        <div className="mt-2 inline-flex px-2 py-0.5 rounded-full bg-subtle text-xs font-medium text-secondary border border-border">
          v2.0.0
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => 
              `flex items-center gap-3 px-3 py-2 rounded-md transition-colors text-sm font-medium ${
                isActive 
                  ? 'bg-indigo-600/10 text-indigo-400 border-l-2 border-indigo-600 pl-[10px]' 
                  : 'text-secondary hover:text-primary hover:bg-subtle border-l-2 border-transparent pl-[10px]'
              }`
            }
          >
            <item.icon className="w-5 h-5" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-border flex items-center gap-3">
        <div className={`w-2 h-2 rounded-full ${isHealthy ? 'bg-emerald-500' : 'bg-red-500'}`}></div>
        <span className="text-sm text-secondary font-medium">
          {isHealthy ? 'API Connected' : 'API Disconnected'}
        </span>
      </div>
    </div>
  );
}
