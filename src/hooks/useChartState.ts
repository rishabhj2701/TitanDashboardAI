import { useState, useCallback } from 'react';
import type { GeneratedChartPayload as ChatChartPayload } from '../types/charts';

export interface UseChartStateResult {
  showChart: { type: 'speed' | 'road' | 'violations' } | null;
  visualizations: Array<{
    id: string;
    type: 'speed' | 'road' | 'violations';
    title: string;
    data: any[];
    timestamp: Date;
  }>;
  chartSpecs: Array<{
    id: string;
    title: string;
    payload: ChatChartPayload;
    timestamp: Date;
  }>;
  setShowChart: React.Dispatch<React.SetStateAction<{ type: 'speed' | 'road' | 'violations' } | null>>;
  setVisualizations: React.Dispatch<React.SetStateAction<Array<{
    id: string;
    type: 'speed' | 'road' | 'violations';
    title: string;
    data: any[];
    timestamp: Date;
  }>>>;
  setChartSpecs: React.Dispatch<React.SetStateAction<Array<{
    id: string;
    title: string;
    payload: ChatChartPayload;
    timestamp: Date;
  }>>>;
  handleChartPayload: (payloads: ChatChartPayload[]) => void;
  handleReplaceChart: (chartId: string, payload: ChatChartPayload, title?: string) => void;
  handleShowChart: (type: 'speed' | 'road' | 'violations', filteredData: any[], vehicleData: any[]) => void;
}

export const useChartState = (useRoadTiles: boolean): UseChartStateResult => {
  const [showChart, setShowChart] = useState<{ type: 'speed' | 'road' | 'violations' } | null>(null);
  const [visualizations, setVisualizations] = useState<Array<{
    id: string;
    type: 'speed' | 'road' | 'violations';
    title: string;
    data: any[];
    timestamp: Date;
  }>>([]);
  const [chartSpecs, setChartSpecs] = useState<Array<{
    id: string;
    title: string;
    payload: ChatChartPayload;
    timestamp: Date;
  }>>([]);

  const handleChartPayload = useCallback((payloads: ChatChartPayload[]) => {
    if (!payloads?.length) return;
    setChartSpecs(prev => [
      ...payloads.map(payload => ({
        id: `chart-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        title: payload.title || 'Generated Chart',
        payload,
        timestamp: new Date(),
      })),
      ...prev,
    ]);
  }, [useRoadTiles]);

  const handleReplaceChart = useCallback((chartId: string, payload: ChatChartPayload, title?: string) => {
    setChartSpecs(prev => prev.map(chart => (
      chart.id === chartId
        ? {
            ...chart,
            payload,
            title: title ?? chart.title,
            timestamp: new Date(),
          }
        : chart
    )));
  }, []);

  const handleShowChart = useCallback((type: 'speed' | 'road' | 'violations', filteredData: any[], vehicleData: any[]) => {
    // Get current filtered data and use it
    const dataToUse = filteredData.length > 0 ? filteredData : vehicleData;

    // Create descriptive title with data count
    let title = '';
    if (type === 'speed') {
      title = filteredData.length > 0
        ? `Speed Distribution (${dataToUse.length} filtered vehicles)`
        : `Speed Distribution (${dataToUse.length} vehicles)`;
    } else if (type === 'road') {
      title = `By Road (${dataToUse.length} vehicles)`;
    } else {
      title = `Violations (${dataToUse.length} vehicles)`;
    }

    // Add to visualizations history
    const newViz = {
      id: `viz-${Date.now()}`,
      type,
      title,
      data: dataToUse,
      timestamp: new Date(),
    };

    setVisualizations(prev => [newViz, ...prev]);
    setShowChart({ type });
  }, []);

  return {
    showChart,
    visualizations,
    chartSpecs,
    setShowChart,
    setVisualizations,
    setChartSpecs,
    handleChartPayload,
    handleReplaceChart,
    handleShowChart,
  };
};
