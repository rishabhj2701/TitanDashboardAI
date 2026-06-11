import { useState, useCallback } from 'react';

type MapStyle = 'dark' | 'light' | 'satellite' | 'streets';

const STYLES: Record<MapStyle, { url: string; label: string; icon: string }> = {
  dark: { url: 'mapbox://styles/mapbox/dark-v11', label: 'Dark', icon: '\u{1F319}' },
  light: { url: 'mapbox://styles/mapbox/light-v11', label: 'Light', icon: '\u2600' },
  satellite: { url: 'mapbox://styles/mapbox/satellite-streets-v12', label: 'Satellite', icon: '\u{1F6F0}' },
  streets: { url: 'mapbox://styles/mapbox/streets-v12', label: 'Streets', icon: '\u{1F5FA}' },
};

interface ThemeSwitcherProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
}

function ThemeSwitcher({ mapRef }: ThemeSwitcherProps) {
  const [current, setCurrent] = useState<MapStyle>('dark');
  const [open, setOpen] = useState(false);

  const handleSwitch = useCallback((style: MapStyle) => {
    if (style === current) { setOpen(false); return; }
    const map = mapRef.current;
    if (!map) return;
    setCurrent(style);
    setOpen(false);
    map.setStyle(STYLES[style].url);
  }, [current, mapRef]);

  return (
    <div className="theme-switcher">
      <button
        className="theme-switcher-btn"
        onClick={() => setOpen(!open)}
        title="Map Style"
      >
        {STYLES[current].icon}
      </button>
      {open && (
        <div className="theme-switcher-menu">
          {(Object.keys(STYLES) as MapStyle[]).map(s => (
            <button
              key={s}
              className={`theme-switcher-option${s === current ? ' active' : ''}`}
              onClick={() => handleSwitch(s)}
            >
              <span>{STYLES[s].icon}</span>
              <span>{STYLES[s].label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default ThemeSwitcher;
