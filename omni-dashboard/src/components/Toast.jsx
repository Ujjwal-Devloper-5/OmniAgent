import React from 'react';
import { CheckCircle, XCircle, AlertTriangle, Info, X } from 'lucide-react';

export default function Toast({ id, type, message, onClose }) {
  const icons = {
    success: <CheckCircle className="w-5 h-5 text-emerald-500" />,
    error: <XCircle className="w-5 h-5 text-red-500" />,
    warning: <AlertTriangle className="w-5 h-5 text-amber-500" />,
    info: <Info className="w-5 h-5 text-blue-500" />,
  };
  
  const borders = {
    success: 'border-emerald-500/20',
    error: 'border-red-500/20',
    warning: 'border-amber-500/20',
    info: 'border-blue-500/20',
  };

  return (
    <div className={`toast-enter flex items-start gap-3 p-4 mb-3 w-80 bg-overlay border ${borders[type]} rounded-lg shadow-xl pointer-events-auto`}>
      <div className="shrink-0 mt-0.5">{icons[type]}</div>
      <div className="flex-1 text-sm text-primary">{message}</div>
      <button onClick={() => onClose(id)} className="shrink-0 text-secondary hover:text-primary">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
