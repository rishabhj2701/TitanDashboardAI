import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { CvRoadAggregateProperties } from '../services/dataLoader';
import { buildCvRoadPopupHtml } from '../features/map/popupHtml';
import type { RoadColorMode } from '../features/map/roadColorMode';
import {
  computeAvgUniqueVehiclesPerHourFromHourlyJson,
  parseHourlyUniqueVehicles,
} from '../utils/hourlyVehicles';

type RoadPanelElement = HTMLElement & {
  _resizeObserver?: ResizeObserver | null;
};

type UseCvRoadPinnedPanelParams = {
  hasPanelStats: boolean;
  showStatsPanel: boolean;
  dockFloatingPanels: (roadPanelEl?: HTMLElement | null) => void;
  setCvRoadPanelOpen: Dispatch<SetStateAction<boolean>>;
  roadColorMode: RoadColorMode;
};

type UseCvRoadPinnedPanelResult = {
  showCvRoadPinnedPanel: (props: CvRoadAggregateProperties) => void;
  closeCvRoadPinnedPanel: () => void;
};

const getPanel = (): RoadPanelElement | null => {
  return document.getElementById('cv-road-detail-panel') as RoadPanelElement | null;
};

const placeRoadPanelDefault = (panel: HTMLElement) => {
  const mapContainerEl = document.querySelector('.map-container') as HTMLElement | null;
  const chatPanel = document.querySelector('.minimal-chat.expanded') as HTMLElement | null;
  const mapRect = mapContainerEl?.getBoundingClientRect();
  const viewportLeft = mapRect ? mapRect.left + 10 : 10;
  let viewportRight = mapRect ? mapRect.right - 10 : window.innerWidth - 10;
  if (chatPanel) {
    const chatRect = chatPanel.getBoundingClientRect();
    viewportRight = Math.min(viewportRight, chatRect.left - 12);
  }
  const availableWidth = Math.max(240, viewportRight - viewportLeft);
  const panelW = panel.offsetWidth || 460;
  const panelH = panel.offsetHeight || 210;
  const left = viewportLeft + Math.max(0, (availableWidth - panelW) / 2);
  const top = Math.max(64, window.innerHeight - panelH - 24);
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
  panel.style.bottom = '';
  panel.style.transform = '';
  panel.dataset.userMoved = '0';
};

const wireChartTooltips = (panel: HTMLElement) => {
  const bars = Array.from(panel.querySelectorAll<SVGGraphicsElement>('.road-detail-chart-bar[data-tooltip]'));
  if (!bars.length) return;

  const tooltip = document.createElement('div');
  tooltip.className = 'road-detail-chart-tooltip';
  panel.appendChild(tooltip);

  const placeTooltip = (event: MouseEvent) => {
    if (!tooltip.textContent) return;
    const panelRect = panel.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    let left = event.clientX - panelRect.left + 10;
    let top = event.clientY - panelRect.top - tooltipRect.height - 10;
    if (left + tooltipRect.width > panel.clientWidth - 6) {
      left = panel.clientWidth - tooltipRect.width - 6;
    }
    if (left < 6) left = 6;
    if (top < 6) {
      top = event.clientY - panelRect.top + 12;
    }
    if (top + tooltipRect.height > panel.clientHeight - 6) {
      top = panel.clientHeight - tooltipRect.height - 6;
    }
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  };

  const showTooltip = (event: MouseEvent, label: string) => {
    tooltip.textContent = label;
    tooltip.style.opacity = '1';
    placeTooltip(event);
  };

  const hideTooltip = () => {
    tooltip.style.opacity = '0';
  };

  bars.forEach((bar) => {
    const label = String(bar.getAttribute('data-tooltip') || '').trim();
    if (!label) return;
    bar.addEventListener('mouseenter', (event) => showTooltip(event as MouseEvent, label));
    bar.addEventListener('mousemove', (event) => placeTooltip(event as MouseEvent));
    bar.addEventListener('mouseleave', hideTooltip);
  });
  panel.addEventListener('mouseleave', hideTooltip);
};

export const useCvRoadPinnedPanel = ({
  hasPanelStats,
  showStatsPanel,
  dockFloatingPanels,
  setCvRoadPanelOpen,
  roadColorMode,
}: UseCvRoadPinnedPanelParams): UseCvRoadPinnedPanelResult => {
  const roadColorModeRef = useRef<RoadColorMode>(roadColorMode);

  useEffect(() => {
    roadColorModeRef.current = roadColorMode;
  }, [roadColorMode]);

  const closeCvRoadPinnedPanel = useCallback(() => {
    const panel = getPanel();
    if (panel?._resizeObserver) {
      panel._resizeObserver.disconnect();
      panel._resizeObserver = null;
    }
    panel?.remove();
    setCvRoadPanelOpen(false);
    window.dispatchEvent(new CustomEvent('map-hover-clear'));
  }, [setCvRoadPanelOpen]);

  const showCvRoadPinnedPanel = useCallback((props: CvRoadAggregateProperties) => {
    const html = buildCvRoadPopupHtml(props, roadColorModeRef.current);
    let panel = getPanel();
    if (!panel) {
      panel = document.createElement('div') as RoadPanelElement;
      panel.id = 'cv-road-detail-panel';
      panel.className = 'road-detail-panel-shell';
      panel.style.cssText = `
        position: fixed;
        z-index: 9999;
        max-width: 500px;
        width: min(460px, 86vw);
        max-height: 56vh;
        overflow: auto;
        pointer-events: auto;
        min-width: 320px;
        min-height: 170px;
        resize: both;
        touch-action: none;
      `;
      document.body.appendChild(panel);
    } else {
      panel.classList.add('road-detail-panel-shell');
    }

    setCvRoadPanelOpen(true);
    panel.innerHTML = html;

    const inner = panel.firstElementChild as HTMLElement | null;
    if (inner) {
      inner.style.position = 'relative';
      const actions = inner.querySelector('.road-detail-header-actions') as HTMLElement | null;
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = 'X';
      closeBtn.className = 'chat-close-btn';
      closeBtn.classList.add('road-detail-close-btn');
      closeBtn.onclick = closeCvRoadPinnedPanel;
      const pinBtn = document.createElement('button');
      pinBtn.textContent = 'Pin to Dashboard';
      pinBtn.className = 'road-detail-pin-btn';
      pinBtn.onclick = () => {
        const p = props as Record<string, unknown>;
        const toNum = (v: unknown): number | null => {
          const n = Number(v);
          return Number.isFinite(n) ? n : null;
        };
        const toCount = (v: unknown): number => {
          const n = Number(v);
          return Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0;
        };
        const hourlyEntries = parseHourlyUniqueVehicles(p['hourly_unique_vehicles_json']);
        const hourlyJson = hourlyEntries.length
          ? Object.fromEntries(hourlyEntries.map((entry) => [String(entry.hour), entry.value]))
          : null;
        const avgUniqueVehiclesPerHourFromHourly = computeAvgUniqueVehiclesPerHourFromHourlyJson(
          p['hourly_unique_vehicles_json']
        );
        const peakHour = (() => {
          if (!hourlyEntries.length) return null;
          let bestHour = -1;
          let bestVal = -1;
          for (const entry of hourlyEntries) {
            if (entry.value > bestVal) {
              bestHour = entry.hour;
              bestVal = entry.value;
            }
          }
          if (bestHour < 0 || bestVal <= 0) return null;
          const suffix = bestHour >= 12 ? 'PM' : 'AM';
          const base = bestHour % 12 === 0 ? 12 : bestHour % 12;
          return `${base} ${suffix}`;
        })();
        const areaAnalysis = p['area_analysis'] === true || p['area_analysis'] === 'true' || p['from_area_analysis'] === true;
        window.dispatchEvent(new CustomEvent('pin-road-stats', {
          detail: {
            road_name: props.road_name || props.name || (p['roadName'] as string) || 'Unknown',
            road_segment_id:
              String(
                props.road_segment_id ??
                p['segment_id'] ??
                props.way_id ??
                p['way_id'] ??
                p['roadSegmentId'] ??
                p['wayId'] ??
                ''
              ).trim(),
            avg_speed_mph: toNum(p['avg_speed_mph'] ?? p['avg_speed']),
            speed_limit_mph: toNum(p['speed_limit_mph'] ?? p['speed_limit_mode'] ?? p['speed_limit_p50_mph'] ?? p['speed_limit']),
            p50_speed_mph: toNum(p['p50_speed_mph'] ?? p['p50_speed']),
            p90_speed_mph: toNum(p['p90_speed_mph'] ?? p['p90_speed']),
            crash_count: toCount(p['crash_count'] ?? p['crashes']),
            hard_brake_count: toCount(p['hard_brake_count'] ?? p['hard_brakes']),
            area_analysis: areaAnalysis,
            point_count: typeof props.point_count === 'number' ? props.point_count : null,
            unique_vehicles_total: toNum(p['unique_vehicles_total']),
            avg_unique_vehicles_per_hour:
              avgUniqueVehiclesPerHourFromHourly ?? toNum(p['avg_unique_vehicles_per_hour']),
            peak_hour: peakHour,
            hourly_unique_vehicles_json: hourlyJson,
            pinnedAt: Date.now(),
          },
        }));
        pinBtn.textContent = 'Pinned!';
        pinBtn.disabled = true;
        setTimeout(() => { pinBtn.textContent = 'Pin to Dashboard'; pinBtn.disabled = false; }, 2000);
      };

      if (actions) {
        actions.insertBefore(pinBtn, null);
        actions.appendChild(closeBtn);
      } else {
        inner.appendChild(pinBtn);
        inner.appendChild(closeBtn);
      }

      wireChartTooltips(panel);

      if (panel._resizeObserver) {
        panel._resizeObserver.disconnect();
      }
      const resizeObserver = new ResizeObserver(([entry]) => {
        const width = entry.contentRect.width || 460;
        const scale = Math.max(0.76, Math.min(1.1, width / 460));
        panel?.style.setProperty('--road-scale', String(scale));
      });
      resizeObserver.observe(panel);
      panel._resizeObserver = resizeObserver;

      const header = inner.querySelector('.road-detail-header') as HTMLElement | null;
      if (header) {
        const dragState = { active: false, pointerId: -1, dx: 0, dy: 0 };
        header.onpointerdown = (ev: PointerEvent) => {
          if (ev.button !== 0) return;
          const target = ev.target as HTMLElement | null;
          if (target?.closest('.road-detail-close-btn') || target?.closest('.road-detail-pin-btn')) return;
          const rect = panel!.getBoundingClientRect();
          dragState.active = true;
          dragState.pointerId = ev.pointerId;
          dragState.dx = ev.clientX - rect.left;
          dragState.dy = ev.clientY - rect.top;
          header.setPointerCapture(ev.pointerId);
          panel!.style.bottom = '';
          panel!.style.transform = '';
          panel!.dataset.userMoved = '1';
        };
        header.onpointermove = (ev: PointerEvent) => {
          if (!dragState.active || dragState.pointerId !== ev.pointerId) return;
          const panelRect = panel!.getBoundingClientRect();
          const minX = 8;
          const minY = 64;
          const maxX = Math.max(minX, window.innerWidth - panelRect.width - 8);
          const maxY = Math.max(minY, window.innerHeight - panelRect.height - 8);
          const nextLeft = Math.min(Math.max(ev.clientX - dragState.dx, minX), maxX);
          const nextTop = Math.min(Math.max(ev.clientY - dragState.dy, minY), maxY);
          panel!.style.left = `${nextLeft}px`;
          panel!.style.top = `${nextTop}px`;
        };
        const stopDrag = (ev: PointerEvent) => {
          if (dragState.pointerId !== ev.pointerId) return;
          dragState.active = false;
          dragState.pointerId = -1;
          try {
            header.releasePointerCapture(ev.pointerId);
          } catch {
            // no-op
          }
        };
        header.onpointerup = stopDrag;
        header.onpointercancel = stopDrag;
      }
    }

    if (showStatsPanel && hasPanelStats) {
      dockFloatingPanels(panel);
    } else if (panel.dataset.userMoved !== '1') {
      placeRoadPanelDefault(panel);
    }
  }, [closeCvRoadPinnedPanel, dockFloatingPanels, hasPanelStats, setCvRoadPanelOpen, showStatsPanel]);

  useEffect(() => {
    return () => {
      closeCvRoadPinnedPanel();
    };
  }, [closeCvRoadPinnedPanel]);

  return {
    showCvRoadPinnedPanel,
    closeCvRoadPinnedPanel,
  };
};
