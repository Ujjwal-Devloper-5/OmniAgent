import React from 'react';

export default function ProviderDot({ healthy, configured, size = 'sm', name }) {
  const sizes = { sm: 'w-2 h-2', md: 'w-3 h-3' };
  const sz = sizes[size] || sizes.sm;
  
  let color = 'bg-gray-500';
  let pulse = false;
  let statusText = 'Not configured';
  
  if (configured) {
    if (healthy) {
      color = 'bg-emerald-500';
      pulse = true;
      statusText = 'Healthy';
    } else {
      color = 'bg-red-500';
      statusText = 'Failing';
    }
  }

  return (
    <div className="relative flex items-center justify-center group">
      {pulse && <div className={`absolute ${sz} ${color} rounded-full animate-ping opacity-75`}></div>}
      <div className={`relative ${sz} ${color} rounded-full`}></div>
      <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1 px-2 py-1 bg-overlay border border-border rounded text-xs text-primary opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-10">
        {name ? `${name}: ${statusText}` : statusText}
      </div>
    </div>
  );
}
