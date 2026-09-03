import { useContext } from 'react';
import { ToastContext } from '../components/ToastProvider';

export function useToast() {
  return useContext(ToastContext);
}
