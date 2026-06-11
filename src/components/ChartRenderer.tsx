import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  type ActiveElement,
  type ChartEvent,
} from 'chart.js';
import { Bar, Pie, Doughnut } from 'react-chartjs-2';
import type {
  ChartBarClickContext,
  GeneratedChartPayload,
  DualAxisChartPayload,
} from '../types/charts';

ChartJS.register(CategoryScale, LinearScale, BarElement, ArcElement, Title, Tooltip, Legend);

export const normalizeLabelValue = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value.toString();
  if (value === null || typeof value === 'undefined') return '';
  return String(value);
};

export const normalizeNumericValue = (value: unknown): number => {
  if (value === null || typeof value === 'undefined') return 0;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const cleaned = value.replace(/,/g, '').trim();
    if (!cleaned) return 0;
    const parsed = Number(cleaned);
    if (!Number.isNaN(parsed)) return parsed;
  }
  if (typeof value === 'object' && value !== null) {
    if ('value' in value) {
      const parsed = normalizeNumericValue((value as { value?: unknown }).value);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
  }
  return 0;
};

const barOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    title: { display: false },
    tooltip: {
      backgroundColor: 'rgba(10, 14, 39, 0.95)',
      titleColor: '#81c784',
      bodyColor: '#fff',
      borderColor: '#81c784',
      borderWidth: 1,
      padding: 8,
      displayColors: false,
    },
  },
  scales: {
    x: {
      grid: { color: 'rgba(100, 255, 218, 0.1)' },
      ticks: { color: '#ccd6f6', font: { size: 9 } },
    },
    y: {
      grid: { color: 'rgba(100, 255, 218, 0.1)' },
      ticks: { color: '#ccd6f6', font: { size: 9 } },
      beginAtZero: true,
    },
  },
};

const pieOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      display: true,
      position: 'bottom' as const,
      labels: { color: '#ccd6f6', font: { size: 10 } },
    },
    title: { display: false },
    tooltip: {
      backgroundColor: 'rgba(10, 14, 39, 0.95)',
      titleColor: '#81c784',
      bodyColor: '#fff',
      borderColor: '#81c784',
      borderWidth: 1,
      padding: 8,
      displayColors: true,
    },
  },
};

const COLOR_PALETTE = [
  '#81c784', '#90caf9', '#ffb74d', '#ce93d8', '#ef9a9a',
  '#a5d6a7', '#9fa8da', '#ffa726', '#f48fb1', '#e57373',
];

interface ChartRendererProps {
  payload: GeneratedChartPayload;
  title: string;
  variant: 'preview' | 'fullscreen';
  onBarClick?: (context: ChartBarClickContext) => void;
}

function ChartRenderer({ payload, title, variant, onBarClick }: ChartRendererProps) {
  const titleText = title || 'Generated Chart';
  const emitBarClick = (
    labels: string[],
    datasets: Array<{ data: unknown[] }>,
    event: ChartEvent,
    elements: ActiveElement[]
  ) => {
    if (!onBarClick || !elements.length) return;
    const nativeEvent = (event as { native?: Event }).native;
    if (nativeEvent && typeof nativeEvent.stopPropagation === 'function') {
      nativeEvent.stopPropagation();
    }
    const point = elements[0];
    const categoryIndex = point.index;
    const datasetIndex = point.datasetIndex;
    const labelRaw = labels[categoryIndex];
    const valueRaw = datasets[datasetIndex]?.data?.[categoryIndex];
    const numericValue = typeof valueRaw === 'number' && Number.isFinite(valueRaw) ? valueRaw : null;
    onBarClick({
      payload,
      categoryIndex,
      datasetIndex,
      label: normalizeLabelValue(labelRaw),
      value: numericValue,
      target: payload.categoryTargets?.[categoryIndex],
    });
  };

  if (payload.type === 'image') {
    return (
      <img
        src={payload.imageUrl}
        alt={titleText}
        style={{ width: '100%', borderRadius: 8 }}
      />
    );
  }

  if (payload.type === 'dual_axis') {
    const dualPayload: DualAxisChartPayload = payload;
    const labels = dualPayload.xValues.map(normalizeLabelValue);
    const datasets = dualPayload.series.map((series, index) => {
      const seriesColor = COLOR_PALETTE[index % COLOR_PALETTE.length];
      return {
        label: series.label,
        data: series.values.map(normalizeNumericValue),
        yAxisID: series.axis === 'right' ? 'y1' : 'y',
        backgroundColor: seriesColor,
        borderColor: seriesColor,
        borderWidth: 1,
      };
    });

    return (
      <Bar
        data={{ labels, datasets }}
        options={{
          ...barOptions,
          plugins: {
            ...barOptions.plugins,
            title: {
              display: variant === 'fullscreen',
              text: titleText,
              color: '#81c784',
              font: { size: 18, weight: 'bold' as const },
            },
          },
          scales: {
            ...barOptions.scales,
            y: {
              ...barOptions.scales.y,
              position: 'left',
              title: { display: true, text: dualPayload.leftLabel, color: '#ccd6f6' },
            },
            y1: {
              ...barOptions.scales.y,
              position: 'right',
              title: { display: true, text: dualPayload.rightLabel, color: '#ccd6f6' },
              grid: { drawOnChartArea: false },
            },
            x: {
              ...barOptions.scales.x,
              title: { display: true, text: dualPayload.xLabel, color: '#ccd6f6' },
            },
          },
          onClick: (event, elements) => emitBarClick(labels, datasets, event, elements),
        }}
      />
    );
  }

  if (payload.type !== 'bar' && payload.type !== 'pie' && payload.type !== 'doughnut') {
    return null;
  }

  const rawLabels =
    'categoryValues' in payload && payload.categoryValues && payload.categoryValues.length > 0
      ? payload.categoryValues
      : payload.xValues ?? [];
  const labels = rawLabels.map(normalizeLabelValue);
  const resolvedSeries =
    payload.series && payload.series.length > 0
      ? payload.series
      : [{ label: titleText, values: payload.xValues ?? [] }];

  const datasets = resolvedSeries.map((series, index) => {
    const values = labels.map((_, valueIdx) => normalizeNumericValue(series.values?.[valueIdx]));
    const isPieType = payload.type === 'pie' || payload.type === 'doughnut';
    const seriesColor = COLOR_PALETTE[index % COLOR_PALETTE.length];

    return {
      label: series.label || `Series ${index + 1}`,
      data: values,
      backgroundColor: isPieType ? COLOR_PALETTE.slice(0, values.length) : seriesColor,
      borderColor: isPieType ? '#0a0e27' : seriesColor,
      borderWidth: isPieType ? 2 : 1,
    };
  });

  const chartData = { labels, datasets };
  const isAreaTopUniqueSegmentsChart =
    payload.type === 'bar' && payload.meta?.chartRole === 'area_top_unique_segments';

  if (payload.type === 'pie') {
    return (
      <Pie
        data={chartData}
        options={{
          ...pieOptions,
          plugins: {
            ...pieOptions.plugins,
            title: {
              display: variant === 'fullscreen',
              text: titleText,
              color: '#81c784',
              font: { size: 18, weight: 'bold' as const },
            },
          },
        }}
      />
    );
  }

  if (payload.type === 'doughnut') {
    return (
      <Doughnut
        data={chartData}
        options={{
          ...pieOptions,
          plugins: {
            ...pieOptions.plugins,
            title: {
              display: variant === 'fullscreen',
              text: titleText,
              color: '#81c784',
              font: { size: 18, weight: 'bold' as const },
            },
          },
        }}
      />
    );
  }

  return (
    <Bar
      data={chartData}
      options={{
        ...barOptions,
        plugins: {
          ...barOptions.plugins,
          tooltip: {
            ...barOptions.plugins.tooltip,
            callbacks: isAreaTopUniqueSegmentsChart
              ? {
                afterBody: () => ['Click to snap to segment'],
              }
              : undefined,
          },
          title: {
            display: variant === 'fullscreen',
            text: titleText,
            color: '#81c784',
            font: { size: 18, weight: 'bold' as const },
          },
        },
        indexAxis: payload.orientation === 'horizontal' ? 'y' : 'x',
        scales: {
          ...barOptions.scales,
          x: {
            ...barOptions.scales.x,
            title: { display: true, text: payload.xLabel, color: '#ccd6f6' },
          },
          y: {
            ...barOptions.scales.y,
            title: { display: true, text: payload.yLabel, color: '#ccd6f6' },
          },
        },
        onHover: (event, elements) => {
          const nativeEvent = (event as { native?: Event }).native;
          const target = nativeEvent?.target as HTMLCanvasElement | null;
          if (!target) return;
          target.style.cursor =
            isAreaTopUniqueSegmentsChart && elements.length > 0 ? 'pointer' : 'default';
        },
        onClick: (event, elements) => emitBarClick(labels, datasets, event, elements),
      }}
    />
  );
}

export default ChartRenderer;
