import { useState, useCallback, useEffect } from 'react';
import { getTopSpeedingRoads } from '../api/dataQualityClient';

interface AlertRule {
  id: string;
  name: string;
  threshold: number; // mph over limit
  enabled: boolean;
}

const STORAGE_KEY = 'titan_alert_rules';

function loadRules(): AlertRule[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [
      { id: 'default-10', name: 'High Speed', threshold: 10, enabled: true },
      { id: 'default-20', name: 'Critical Speed', threshold: 20, enabled: true },
    ];
  } catch { return []; }
}

function saveRules(rules: AlertRule[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(rules));
}

interface AlertMatch {
  roadName: string;
  speedOver: number;
  ruleName: string;
}

function AlertRulesPanel() {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<AlertRule[]>(loadRules);
  const [matches, setMatches] = useState<AlertMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [newName, setNewName] = useState('');
  const [newThreshold, setNewThreshold] = useState(15);

  const scanAlerts = useCallback(async (currentRules: AlertRule[]) => {
    setLoading(true);
    try {
      const data = await getTopSpeedingRoads(50);
      if (!data?.roads) { setLoading(false); return; }
      const results: AlertMatch[] = [];
      for (const road of data.roads) {
        for (const rule of currentRules) {
          if (rule.enabled && road.speed_over_limit >= rule.threshold) {
            results.push({
              roadName: road.road_name || 'Unknown',
              speedOver: road.speed_over_limit,
              ruleName: rule.name,
            });
          }
        }
      }
      setMatches(results);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (open && matches.length === 0) {
      scanAlerts(rules);
    }
  }, [open]);

  const addRule = useCallback(() => {
    if (!newName.trim()) return;
    const rule: AlertRule = {
      id: `rule-${Date.now()}`,
      name: newName.trim(),
      threshold: newThreshold,
      enabled: true,
    };
    const updated = [...rules, rule];
    setRules(updated);
    saveRules(updated);
    setNewName('');
    setNewThreshold(15);
    scanAlerts(updated);
  }, [newName, newThreshold, rules, scanAlerts]);

  const toggleRule = useCallback((id: string) => {
    const updated = rules.map(r => r.id === id ? { ...r, enabled: !r.enabled } : r);
    setRules(updated);
    saveRules(updated);
    scanAlerts(updated);
  }, [rules, scanAlerts]);

  const deleteRule = useCallback((id: string) => {
    const updated = rules.filter(r => r.id !== id);
    setRules(updated);
    saveRules(updated);
    scanAlerts(updated);
  }, [rules, scanAlerts]);

  const alertCount = matches.length;

  return (
    <>
      <button
        className={`alert-rules-toggle${alertCount > 0 ? ' has-alerts' : ''}`}
        onClick={() => setOpen(!open)}
        title="Speed Alert Rules"
      >
        {'\u{1F514}'}
        {alertCount > 0 && <span className="alert-badge">{alertCount}</span>}
      </button>
      {open && (
        <div className="alert-rules-panel">
          <div className="alert-rules-header">
            <span className="alert-rules-title">Alert Rules</span>
            <button className="alert-rules-close" onClick={() => setOpen(false)}>{'\u2715'}</button>
          </div>

          {/* Rules list */}
          <div className="alert-rules-list">
            {rules.map(rule => (
              <div key={rule.id} className={`alert-rule-item${rule.enabled ? '' : ' disabled'}`}>
                <button className="alert-rule-toggle" onClick={() => toggleRule(rule.id)}>
                  {rule.enabled ? '\u2611' : '\u2610'}
                </button>
                <span className="alert-rule-name">{rule.name}</span>
                <span className="alert-rule-threshold">{'\u2265'}{rule.threshold} mph over</span>
                <button className="alert-rule-delete" onClick={() => deleteRule(rule.id)}>{'\u2715'}</button>
              </div>
            ))}
          </div>

          {/* Add new rule */}
          <div className="alert-rule-add">
            <input
              className="alert-rule-input"
              placeholder="Rule name"
              value={newName}
              onChange={e => setNewName(e.target.value)}
            />
            <input
              className="alert-rule-num"
              type="number"
              min={1}
              max={100}
              value={newThreshold}
              onChange={e => setNewThreshold(parseInt(e.target.value) || 0)}
            />
            <button className="alert-rule-add-btn" onClick={addRule}>Add</button>
          </div>

          {/* Matches */}
          {loading && <div className="alert-loading">Scanning roads...</div>}
          {!loading && matches.length > 0 && (
            <div className="alert-matches">
              <div className="alert-matches-title">{matches.length} alerts triggered</div>
              {matches.slice(0, 20).map((m, i) => (
                <div key={i} className="alert-match-item">
                  <span className="alert-match-icon">{'\u26A0'}</span>
                  <span className="alert-match-road">{m.roadName}</span>
                  <span className="alert-match-over">+{m.speedOver.toFixed(1)} mph</span>
                  <span className="alert-match-rule">{m.ruleName}</span>
                </div>
              ))}
              {matches.length > 20 && (
                <div className="alert-more">...and {matches.length - 20} more</div>
              )}
            </div>
          )}
          {!loading && matches.length === 0 && (
            <div className="alert-no-matches">No alerts triggered</div>
          )}
        </div>
      )}
    </>
  );
}

export default AlertRulesPanel;
