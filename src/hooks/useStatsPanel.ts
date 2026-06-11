import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, PointerEvent as ReactPointerEvent, RefObject, SetStateAction } from 'react';

type PanelPosition = { x: number; y: number };

type UseStatsPanelParams = {
  hasPanelStats: boolean;
  cvRoadPanelOpen: boolean;
};

type UseStatsPanelResult = {
  showStatsPanel: boolean;
  setShowStatsPanel: Dispatch<SetStateAction<boolean>>;
  statsPanelPos: PanelPosition | null;
  statsPanelScale: number;
  statsPanelRef: RefObject<HTMLDivElement | null>;
  handleStatsDragStart: (e: ReactPointerEvent<HTMLDivElement>) => void;
  handleStatsDragMove: (e: ReactPointerEvent<HTMLDivElement>) => void;
  handleStatsDragEnd: (e: ReactPointerEvent<HTMLDivElement>) => void;
  dockFloatingPanels: (roadPanelEl?: HTMLElement | null) => void;
};

export const useStatsPanel = ({ hasPanelStats, cvRoadPanelOpen }: UseStatsPanelParams): UseStatsPanelResult => {
  const [showStatsPanel, setShowStatsPanel] = useState(true);
  const [statsPanelPos, setStatsPanelPos] = useState<PanelPosition | null>(null);
  const [statsPanelScale, setStatsPanelScale] = useState(1);
  const statsPanelRef = useRef<HTMLDivElement | null>(null);
  const statsDragRef = useRef({
    active: false,
    pointerId: -1,
    offsetX: 0,
    offsetY: 0,
  });

  const clampStatsPanelPosition = useCallback((x: number, y: number) => {
    const panelWidth = statsPanelRef.current?.offsetWidth ?? Math.min(360, Math.floor(window.innerWidth * 0.92));
    const panelHeight = statsPanelRef.current?.offsetHeight ?? 280;
    const minX = 8;
    const minY = 64;
    const maxX = Math.max(minX, window.innerWidth - panelWidth - 8);
    const maxY = Math.max(minY, window.innerHeight - panelHeight - 8);
    return {
      x: Math.min(Math.max(x, minX), maxX),
      y: Math.min(Math.max(y, minY), maxY),
    };
  }, []);

  useEffect(() => {
    if (!hasPanelStats || statsPanelPos) return;
    const initial = clampStatsPanelPosition(
      Math.round((window.innerWidth - Math.min(360, Math.floor(window.innerWidth * 0.92))) / 2),
      Math.round(window.innerHeight - 320)
    );
    setStatsPanelPos(initial);
  }, [hasPanelStats, statsPanelPos, clampStatsPanelPosition]);

  useEffect(() => {
    const handleResize = () => {
      setStatsPanelPos((prev) => {
        if (!prev) return prev;
        return clampStatsPanelPosition(prev.x, prev.y);
      });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [clampStatsPanelPosition]);

  useEffect(() => {
    const el = statsPanelRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width || 360;
      const scale = Math.max(0.62, Math.min(1.3, width / 360));
      setStatsPanelScale(scale);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [showStatsPanel, hasPanelStats]);

  const handleStatsDragStart = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const panel = statsPanelRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    statsDragRef.current = {
      active: true,
      pointerId: e.pointerId,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const handleStatsDragMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = statsDragRef.current;
    if (!drag.active || drag.pointerId !== e.pointerId) return;
    const next = clampStatsPanelPosition(
      e.clientX - drag.offsetX,
      e.clientY - drag.offsetY
    );
    setStatsPanelPos(next);
  }, [clampStatsPanelPosition]);

  const handleStatsDragEnd = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = statsDragRef.current;
    if (drag.pointerId !== e.pointerId) return;
    statsDragRef.current.active = false;
    statsDragRef.current.pointerId = -1;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // no-op
    }
  }, []);

  const dockFloatingPanels = useCallback((roadPanelEl?: HTMLElement | null) => {
    const statsEl = statsPanelRef.current;
    const roadEl = roadPanelEl ?? (document.getElementById('cv-road-detail-panel') as HTMLElement | null);
    if (!statsEl || !roadEl) return;
    if (!showStatsPanel || !hasPanelStats) return;

    const mapContainerEl = document.querySelector('.map-container') as HTMLElement | null;
    const chatPanel = document.querySelector('.minimal-chat.expanded') as HTMLElement | null;
    const mapRect = mapContainerEl?.getBoundingClientRect();
    const viewportLeft = mapRect ? mapRect.left + 10 : 10;
    let viewportRight = mapRect ? mapRect.right - 10 : window.innerWidth - 10;
    if (chatPanel) {
      const chatRect = chatPanel.getBoundingClientRect();
      viewportRight = Math.min(viewportRight, chatRect.left - 12);
    }
    const availableWidth = Math.max(260, viewportRight - viewportLeft);
    const gap = 12;
    const statsW = statsEl.offsetWidth || 320;
    const statsH = statsEl.offsetHeight || 240;
    const roadW = roadEl.offsetWidth || 320;
    const roadH = roadEl.offsetHeight || 240;

    const bottomY = Math.max(64, window.innerHeight - 24);
    const totalW = statsW + roadW + gap;

    if (totalW <= availableWidth) {
      const startX = viewportLeft + Math.max(0, (availableWidth - totalW) / 2);
      const statsPos = clampStatsPanelPosition(startX, bottomY - statsH);
      setStatsPanelPos(statsPos);

      const roadLeft = startX + statsW + gap;
      roadEl.style.left = `${roadLeft}px`;
      roadEl.style.top = `${Math.max(64, bottomY - roadH)}px`;
      roadEl.style.bottom = '';
      roadEl.style.transform = '';
      roadEl.dataset.userMoved = '0';
      return;
    }

    const centerX = viewportLeft + availableWidth / 2;
    const stackedRoadLeft = Math.max(viewportLeft, Math.min(viewportRight - roadW, centerX - roadW / 2));
    roadEl.style.left = `${stackedRoadLeft}px`;
    roadEl.style.top = `${Math.max(64, bottomY - roadH)}px`;
    roadEl.style.bottom = '';
    roadEl.style.transform = '';
    roadEl.dataset.userMoved = '0';

    const statsPos = clampStatsPanelPosition(
      Math.max(viewportLeft, Math.min(viewportRight - statsW, centerX - statsW / 2)),
      Math.max(64, bottomY - roadH - gap - statsH)
    );
    setStatsPanelPos(statsPos);
  }, [showStatsPanel, hasPanelStats, clampStatsPanelPosition]);

  useEffect(() => {
    if (!cvRoadPanelOpen || !showStatsPanel || !hasPanelStats) return;
    const roadEl = document.getElementById('cv-road-detail-panel') as HTMLElement | null;
    if (roadEl) {
      dockFloatingPanels(roadEl);
    }
  }, [cvRoadPanelOpen, showStatsPanel, hasPanelStats, dockFloatingPanels]);

  return {
    showStatsPanel,
    setShowStatsPanel,
    statsPanelPos,
    statsPanelScale,
    statsPanelRef,
    handleStatsDragStart,
    handleStatsDragMove,
    handleStatsDragEnd,
    dockFloatingPanels,
  };
};
