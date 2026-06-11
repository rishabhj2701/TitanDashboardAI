import { useState, useCallback, useEffect } from 'react';

interface TourStep {
  target: string; // CSS selector
  title: string;
  description: string;
  position: 'top' | 'bottom' | 'left' | 'right';
}

const TOUR_STEPS: TourStep[] = [
  {
    target: '.brand',
    title: 'Welcome to Traffic AI System',
    description: 'This is your connected-vehicle speed analytics platform. Let\'s take a quick tour of the key features.',
    position: 'bottom',
  },
  {
    target: '.topbar-select',
    title: 'CV Run Selector',
    description: 'Switch between different connected-vehicle data runs to analyze different time periods or datasets.',
    position: 'bottom',
  },
  {
    target: '.map-container',
    title: 'Interactive Map',
    description: 'The map shows road segments colored by speed compliance. Red = over limit, green = within limit. Click any road for details.',
    position: 'top',
  },
  {
    target: '.map-search-wrapper',
    title: 'Location Search',
    description: 'Search for any location to quickly navigate the map. Uses Mapbox geocoding.',
    position: 'right',
  },
  {
    target: '.map-legend',
    title: 'Legend & Bookmarks',
    description: 'View the speed color legend and save map bookmarks to quickly return to locations of interest.',
    position: 'left',
  },
  {
    target: '.chat-trigger',
    title: 'AI Chat Assistant',
    description: 'Ask natural language questions about the data. "Show top 5 roads by speed" or "Summarize crash data". Charts can be pinned to your dashboard.',
    position: 'left',
  },
  {
    target: '.top-bar-tabs',
    title: 'Map & Dashboard Views',
    description: 'Switch between the Map view for spatial analysis and the Dashboard view for charts, analytics widgets, and saved visualizations.',
    position: 'bottom',
  },
  {
    target: '.insight-toggle-btn',
    title: 'Network Intelligence',
    description: 'View AI-generated insights about network health, speeding hotspots, and get an executive summary of your data.',
    position: 'left',
  },
];

interface GuidedTourProps {
  onComplete: () => void;
}

function GuidedTour({ onComplete }: GuidedTourProps) {
  const [step, setStep] = useState(0);
  const [pos, setPos] = useState<{ top: number; left: number; width: number; height: number } | null>(null);

  const updatePosition = useCallback(() => {
    const target = document.querySelector(TOUR_STEPS[step].target);
    if (target) {
      const rect = target.getBoundingClientRect();
      setPos({ top: rect.top, left: rect.left, width: rect.width, height: rect.height });
    } else {
      setPos(null);
    }
  }, [step]);

  useEffect(() => {
    updatePosition();
    window.addEventListener('resize', updatePosition);
    return () => window.removeEventListener('resize', updatePosition);
  }, [updatePosition]);

  const handleNext = useCallback(() => {
    if (step < TOUR_STEPS.length - 1) {
      setStep(step + 1);
    } else {
      localStorage.setItem('titan_tour_done_v2', '1');
      onComplete();
    }
  }, [step, onComplete]);

  const handleSkip = useCallback(() => {
    localStorage.setItem('titan_tour_done_v2', '1');
    onComplete();
  }, [onComplete]);

  const current = TOUR_STEPS[step];
  const TOOLTIP_W = 360;
  const TOOLTIP_H = 200; // approximate height
  const tooltipStyle: React.CSSProperties = {};
  if (pos) {
    let top = 0;
    let left = 0;
    if (current.position === 'bottom') {
      top = pos.top + pos.height + 12;
      left = pos.left + pos.width / 2 - TOOLTIP_W / 2;
    } else if (current.position === 'top') {
      top = pos.top - TOOLTIP_H - 12;
      left = pos.left + pos.width / 2 - TOOLTIP_W / 2;
    } else if (current.position === 'left') {
      top = pos.top + pos.height / 2 - TOOLTIP_H / 2;
      left = pos.left - TOOLTIP_W - 12;
    } else {
      top = pos.top + pos.height / 2 - TOOLTIP_H / 2;
      left = pos.left + pos.width + 12;
    }
    // Clamp within viewport
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    top = Math.max(12, Math.min(top, vh - TOOLTIP_H - 12));
    left = Math.max(12, Math.min(left, vw - TOOLTIP_W - 12));
    tooltipStyle.top = top;
    tooltipStyle.left = left;
  } else {
    tooltipStyle.top = '50%';
    tooltipStyle.left = '50%';
    tooltipStyle.transform = 'translate(-50%, -50%)';
  }

  return (
    <div className="guided-tour-overlay">
      {/* Spotlight cutout */}
      {pos && (
        <div
          className="guided-tour-spotlight"
          style={{
            top: pos.top - 6,
            left: pos.left - 6,
            width: pos.width + 12,
            height: pos.height + 12,
          }}
        />
      )}

      {/* Tooltip */}
      <div className="guided-tour-tooltip" style={tooltipStyle}>
        <div className="guided-tour-step-count">Step {step + 1} of {TOUR_STEPS.length}</div>
        <div className="guided-tour-title">{current.title}</div>
        <div className="guided-tour-desc">{current.description}</div>
        <div className="guided-tour-actions">
          <button className="guided-tour-skip" onClick={handleSkip}>Skip Tour</button>
          <button className="guided-tour-next" onClick={handleNext}>
            {step < TOUR_STEPS.length - 1 ? 'Next' : 'Finish'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default GuidedTour;
