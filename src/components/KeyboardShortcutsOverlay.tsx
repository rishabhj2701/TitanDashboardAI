interface KeyboardShortcutsOverlayProps {
  open: boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { key: 'Esc', description: 'Clear area draw / close popups' },
  { key: 'Ctrl+S', description: 'Take map screenshot' },
  { key: 'R', description: 'Reset view to default' },
  { key: '+', description: 'Zoom in' },
  { key: '-', description: 'Zoom out' },
  { key: 'F', description: 'Fit to data bounds' },
  { key: '?', description: 'Toggle this shortcuts panel' },
];

function KeyboardShortcutsOverlay({ open, onClose }: KeyboardShortcutsOverlayProps) {
  if (!open) return null;

  return (
    <div className="shortcuts-overlay" onClick={onClose}>
      <div className="shortcuts-card" onClick={(e) => e.stopPropagation()}>
        <div className="shortcuts-header">
          <span className="shortcuts-title">Keyboard Shortcuts</span>
          <button className="shortcuts-close" onClick={onClose}>&#x2715;</button>
        </div>
        <div className="shortcuts-list">
          {SHORTCUTS.map((s) => (
            <div key={s.key} className="shortcuts-row">
              <kbd className="shortcuts-key">{s.key}</kbd>
              <span className="shortcuts-desc">{s.description}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default KeyboardShortcutsOverlay;
