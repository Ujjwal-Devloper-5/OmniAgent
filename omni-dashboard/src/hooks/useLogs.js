import { useState, useEffect } from 'react';
import { api } from '../lib/api';

export function useLogs(token) {
  const [lines, setLines] = useState([]);
  const [connected, setConnected] = useState(false);
  
  useEffect(() => {
    if (!token) return;
    
    let isMounted = true;
    
    // Initial fetch of historical logs
    api.logs().then(data => {
      if (isMounted && data.logs) {
        setLines(data.logs);
      }
    }).catch(err => console.error("Failed to fetch initial logs", err));

    const es = new EventSource(`/api/logs/stream?token=${token}`);
    es.onopen = () => {
        if (isMounted) setConnected(true);
    };
    es.onmessage = (e) => {
        if (isMounted) {
            setLines(prev => [...prev.slice(-999), e.data]);
        }
    };
    es.onerror = () => {
        if (isMounted) setConnected(false);
    };
    return () => {
        isMounted = false;
        es.close();
    };
  }, [token]);
  
  return { lines, connected };
}
