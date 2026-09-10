import React, { createContext, useState, useCallback, useEffect } from 'react';
import Toast from './Toast';

export const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((type, message) => {
    const id = Date.now().toString() + Math.random().toString(36).substring(2, 7);
    setToasts(prev => [...prev.slice(-3), { id, type, message }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 5000);
  }, []);

  const toast = {
    success: (msg) => addToast('success', msg),
    error: (msg) => addToast('error', msg),
    warning: (msg) => addToast('warning', msg),
    info: (msg) => addToast('info', msg),
  };

  useEffect(() => {
    const handleRateLimit = (e) => {
      const detail = e?.detail?.detail || 'Rate limit exceeded. Please wait before retrying.';
      const retryAfter = e?.detail?.retryAfter;
      const message = retryAfter ? `${detail} (Retry after ${retryAfter}s)` : detail;
      addToast('warning', message);
    };

    window.addEventListener('api:rate-limited', handleRateLimit);
    return () => window.removeEventListener('api:rate-limited', handleRateLimit);
  }, [addToast]);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[9999] pointer-events-none flex flex-col items-end">
        {toasts.map(t => (
          <Toast key={t.id} {...t} onClose={removeToast} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
