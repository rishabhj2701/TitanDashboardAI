import { useState, useRef, useEffect, useCallback } from 'react';
import type { CvRunSummary } from '../../api/cvRunsClient';
import type { RoadColorMode } from '../../features/map/roadColorMode';

const SHOW_INFO_BUTTON = false;

type CvRunOption = CvRunSummary & {
  optionLabel: string;
};

type AppTopBarProps = {
  activeCvRunId: string;
  cvRunLoading: boolean;
  cvRunSwitching: boolean;
  cvRunError: string | null;
  cvRunOptions: CvRunOption[];
  canUndo: boolean;
  areaPolygonReady: boolean;
  areaDrawActive: boolean;
  areaAnalysisLoading: boolean;
  activeView: 'map' | 'dashboard';
  roadCount?: number;
  roadColorMode: RoadColorMode;
  onRoadColorModeChange: (mode: RoadColorMode) => void;
  onViewChange: (view: 'map' | 'dashboard') => void;
  onCvRunChange: (nextRunId: string) => void | Promise<void>;
  onResetMap: () => void | Promise<void>;
  onUndoMap: () => void;
  onStartAreaDraw: () => void;
  onConfirmArea: () => void;
  onClearAreaDraw: () => void;
  onOpenIngestion: () => void;
  onOpenInfo: () => void;
  onStartTour: () => void;
  onScreenshot: () => void;
  onToggleShortcuts: () => void;
  userName?: string;
  userEmail?: string;
  avatarUrl?: string;
  userProvider?: string;
  userCreatedAt?: string;
  onLogout?: () => void;
};

function AppTopBar({
  activeCvRunId,
  cvRunLoading,
  cvRunSwitching,
  cvRunError,
  cvRunOptions,
  canUndo,
  areaPolygonReady,
  areaDrawActive,
  areaAnalysisLoading,
  activeView,
  roadCount,
  roadColorMode,
  onRoadColorModeChange,
  onViewChange,
  onCvRunChange,
  onResetMap,
  onUndoMap,
  onStartAreaDraw,
  onConfirmArea,
  onClearAreaDraw,
  onOpenIngestion,
  onOpenInfo,
  onStartTour,
  onScreenshot,
  onToggleShortcuts,
  userName,
  userEmail,
  avatarUrl,
  userProvider,
  userCreatedAt,
  onLogout,
}: AppTopBarProps) {
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  const polygonStatus = areaAnalysisLoading
    ? { label: 'Analyzing polygon...', detail: 'Sending polygon to analysis.' }
    : areaDrawActive
      ? { label: 'Drawing polygon', detail: 'Click to add points. Double-click to finish.' }
      : areaPolygonReady
        ? { label: 'Polygon ready', detail: 'Polygon locked. Confirm to analyze, or Clear to redraw.' }
        : null;

  const closeProfile = useCallback(() => setProfileOpen(false), []);

  // Close on click outside
  useEffect(() => {
    if (!profileOpen) return;
    const handler = (e: MouseEvent) => {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        closeProfile();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [profileOpen, closeProfile]);

  // Close on Escape
  useEffect(() => {
    if (!profileOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeProfile();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [profileOpen, closeProfile]);
  return (
    <div className="top-bar">
      <div className="top-bar-left">
        <div className="brand">Traffic AI System</div>
        <div className="top-bar-tabs">
          <span
            className={`tab ${activeView === 'map' ? 'active' : ''}`}
            onClick={() => onViewChange('map')}
          >
            Traffic Map
          </span>
          <span
            className={`tab ${activeView === 'dashboard' ? 'active' : ''}`}
            onClick={() => onViewChange('dashboard')}
          >
            Dashboard
          </span>
        </div>
      </div>
      <div className="top-bar-right">
        <div className="top-bar-scroll">
          {activeView === 'map' && (
          <div className="topbar-actions">
          <div className="topbar-group">
            <span className="topbar-group-label">CV Run</span>
            <div className="topbar-group-buttons">
              <select
                className="topbar-select"
                value={activeCvRunId}
                onChange={(event) => {
                  void onCvRunChange(event.target.value);
                }}
                disabled={cvRunLoading || cvRunSwitching || cvRunOptions.length === 0}
                title={activeCvRunId}
              >
                {!cvRunOptions.length ? (
                  <option value="">
                    {cvRunLoading ? 'Loading runs…' : 'No runs'}
                  </option>
                ) : null}
                {activeCvRunId && !cvRunOptions.some((run) => run.run_id === activeCvRunId) ? (
                  <option value={activeCvRunId}>{activeCvRunId}</option>
                ) : null}
                {cvRunOptions.map((run) => (
                  <option key={run.run_id} value={run.run_id} title={`${run.run_id} | ${run.schema_name}`}>
                    {run.optionLabel}
                  </option>
                ))}
              </select>
              {cvRunSwitching ? <span className="topbar-pill">Switching…</span> : null}
            </div>
          </div>
          <div className="topbar-group">
            <span className="topbar-group-label">Map</span>
            <div className="topbar-group-buttons">
              <button className="topbar-btn" onClick={onResetMap}>
                Reset CV
              </button>
              <button
                className="topbar-btn secondary"
                onClick={onUndoMap}
                disabled={!canUndo}
              >
                Undo
              </button>
            </div>
          </div>
          <div className="topbar-group">
            <span className="topbar-group-label">Polygon</span>
            <div className="topbar-group-buttons">
              <button
                className="topbar-btn"
                onClick={onStartAreaDraw}
                disabled={areaPolygonReady || areaDrawActive || areaAnalysisLoading}
              >
                Draw Area
              </button>
              <button
                className="topbar-btn"
                onClick={onConfirmArea}
                disabled={!areaPolygonReady || areaAnalysisLoading}
              >
                {areaAnalysisLoading ? 'Analyzing…' : 'Confirm'}
              </button>
              <button className="topbar-btn secondary" onClick={onClearAreaDraw}>
                Clear
              </button>
              {polygonStatus && (
                <span className="topbar-pill topbar-pill-status" title={polygonStatus.detail}>
                  {polygonStatus.label}
                </span>
              )}
            </div>
          </div>
          <div className="topbar-group">
            <span className="topbar-group-label">Tools</span>
            <div className="topbar-group-buttons">
              <button className="topbar-btn" onClick={onScreenshot} title="Screenshot (Ctrl+S)">
                Screenshot
              </button>
              <button className="topbar-btn" onClick={onToggleShortcuts} title="Keyboard shortcuts (?)">
                ?
              </button>
            </div>
          </div>
          <div className="topbar-group">
            <span className="topbar-group-label">Color</span>
            <div className="topbar-group-buttons">
              <button
                className={`topbar-btn${roadColorMode === 'speed-compliance' ? ' active' : ''}`}
                onClick={() => onRoadColorModeChange('speed-compliance')}
                title="Color roads by speed compliance"
              >
                Speed
              </button>
              <button
                className={`topbar-btn${roadColorMode === 'vehicle-count' ? ' active' : ''}`}
                onClick={() => onRoadColorModeChange('vehicle-count')}
                title="Color roads by unique vehicle count"
              >
                Vehicles
              </button>
            </div>
          </div>
          </div>
          )}
          {cvRunError ? <span className="topbar-pill">{cvRunError}</span> : null}
          {activeView === 'map' && roadCount != null && roadCount > 0 && (
            <span className="topbar-stats-badge" title="Road segments loaded">
              {roadCount.toLocaleString()} roads
            </span>
          )}
          <button className="ingestion-button" onClick={onOpenIngestion} aria-label="Open ingestion inspector">
            Ingestion
          </button>
          <button className="tour-button" onClick={onStartTour} aria-label="Start guided tour">
            Tour
          </button>
          {SHOW_INFO_BUTTON && (
            <button className="info-button" onClick={onOpenInfo} aria-label="Open app info">
              <span className="info-dot">i</span>
              Info
            </button>
          )}
        </div>
        {userName && (
          <div className="topbar-profile" ref={profileRef}>
            <button
              className="topbar-profile-trigger"
              onClick={() => setProfileOpen((p) => !p)}
              aria-label="User menu"
            >
              {avatarUrl ? (
                <img src={avatarUrl} alt="" className="topbar-avatar" />
              ) : (
                <span className="topbar-avatar-fallback">
                  {userName.charAt(0).toUpperCase()}
                </span>
              )}
            </button>

            {profileOpen && (
              <div className="topbar-dropdown">
                <div className="topbar-dropdown-header">
                  {avatarUrl ? (
                    <img src={avatarUrl} alt="" className="topbar-dropdown-avatar" />
                  ) : (
                    <span className="topbar-dropdown-avatar-fallback">
                      {userName.charAt(0).toUpperCase()}
                    </span>
                  )}
                  <div className="topbar-dropdown-info">
                    <span className="topbar-dropdown-name">{userName}</span>
                    {userEmail && <span className="topbar-dropdown-email">{userEmail}</span>}
                    {userProvider && (
                      <span className="topbar-dropdown-provider">
                        {userProvider === 'google' ? (
                          <svg width="12" height="12" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
                        ) : (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.02 10.02 0 0 0 22 12.017C22 6.484 17.522 2 12 2z"/></svg>
                        )}
                        Signed in with {userProvider.charAt(0).toUpperCase() + userProvider.slice(1)}
                      </span>
                    )}
                  </div>
                </div>
                {userCreatedAt && (
                  <div className="topbar-dropdown-meta">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" />
                    </svg>
                    Member since {new Date(userCreatedAt).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}
                  </div>
                )}
                <div className="topbar-dropdown-divider" />
                {onLogout && (
                  <button className="topbar-dropdown-item topbar-dropdown-logout" onClick={() => { onLogout(); closeProfile(); }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                      <polyline points="16 17 21 12 16 7" />
                      <line x1="21" y1="12" x2="9" y2="12" />
                    </svg>
                    Sign Out
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default AppTopBar;
