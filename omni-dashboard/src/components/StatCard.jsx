import React from 'react';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

export default function StatCard({ title, value, subtitle, icon: Icon, trend, color = 'indigo' }) {
  const colorMap = {
    indigo: 'bg-indigo-500/10 text-indigo-500',
    violet: 'bg-violet-500/10 text-violet-500',
    emerald: 'bg-emerald-500/10 text-emerald-500',
    blue: 'bg-blue-500/10 text-blue-500',
  };
  const iconBg = colorMap[color] || colorMap.indigo;

  return (
    <div className="bg-surface border border-border rounded-lg p-5 hover:border-border-strong hover:shadow-lg transition-all">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-secondary">{title}</p>
          <h3 className="text-2xl font-bold text-primary mt-1">{value}</h3>
        </div>
        <div className={`p-2 rounded-full ${iconBg}`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2 text-sm">
        {trend && (
          <span className={`flex items-center font-medium ${trend > 0 ? 'text-emerald-500' : 'text-red-500'}`}>
            {trend > 0 ? <ArrowUpRight className="w-4 h-4 mr-1" /> : <ArrowDownRight className="w-4 h-4 mr-1" />}
            {Math.abs(trend)}%
          </span>
        )}
        <span className="text-secondary">{subtitle}</span>
      </div>
    </div>
  );
}
