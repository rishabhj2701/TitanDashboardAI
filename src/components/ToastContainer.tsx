import type { Toast } from '../hooks/useGlobalToast';

interface ToastContainerProps {
  toasts: Toast[];
  onRemove: (id: number) => void;
}

const ICONS: Record<string, string> = {
  success: '\u2713',
  info: '\u2139',
  warning: '\u26A0',
  error: '\u2715',
};

function ToastContainer({ toasts, onRemove }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="global-toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`global-toast global-toast-${t.type}`} onClick={() => onRemove(t.id)}>
          <span className="global-toast-icon">{ICONS[t.type]}</span>
          <span className="global-toast-msg">{t.message}</span>
        </div>
      ))}
    </div>
  );
}

export default ToastContainer;
