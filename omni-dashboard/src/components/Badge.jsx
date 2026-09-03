import React from 'react';

export default function Badge({ variant = 'neutral', children }) {
  const colors = {
    success: 'bg-emerald-500/15 text-emerald-500',
    warning: 'bg-amber-500/15 text-amber-500',
    danger: 'bg-red-500/15 text-red-500',
    info: 'bg-blue-500/15 text-blue-500',
    neutral: 'bg-gray-500/15 text-gray-400',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[variant] || colors.neutral}`}>
      {children}
    </span>
  );
}
