import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import './ChartOverlay.css';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

interface ChartOverlayProps {
  data: any[];
  type: 'speed' | 'road' | 'violations';
  onClose: () => void;
}

const ChartOverlay: React.FC<ChartOverlayProps> = ({ data, type, onClose }) => {
  const generateChartData = () => {
    if (type === 'speed') {
      const bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
      const counts = new Array(bins.length - 1).fill(0);
      const labels = bins.slice(0, -1).map((b, i) => `${b}-${bins[i + 1]}`);

      data.forEach(v => {
        for (let i = 0; i < bins.length - 1; i++) {
          if (v.speed >= bins[i] && v.speed < bins[i + 1]) {
            counts[i]++;
            break;
          }
        }
      });

      return {
        labels,
        datasets: [{
          label: 'Vehicles',
          data: counts,
          backgroundColor: counts.map((_, i) => {
            const colors = ['#64ffda', '#88ff00', '#ffff00', '#ffa500', '#ff6b6b', '#ff4444', '#ff0000', '#cc0000', '#990000', '#660000'];
            return colors[i];
          }),
          borderColor: '#64ffda',
          borderWidth: 1,
        }]
      };
    }

    return { labels: [], datasets: [] };
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: false,
      },
      title: {
        display: true,
        text: type === 'speed' ? 'Speed Distribution (MPH)' : 'Data Analysis',
        color: '#64ffda',
        font: { size: 18, weight: 'bold' as const },
      },
      tooltip: {
        backgroundColor: 'rgba(10, 14, 39, 0.95)',
        titleColor: '#64ffda',
        bodyColor: '#fff',
        borderColor: '#64ffda',
        borderWidth: 1,
        padding: 12,
        displayColors: false,
      }
    },
    scales: {
      x: {
        grid: { color: 'rgba(100, 255, 218, 0.1)' },
        ticks: { color: '#ccd6f6', font: { size: 11 } },
      },
      y: {
        grid: { color: 'rgba(100, 255, 218, 0.1)' },
        ticks: { color: '#ccd6f6', font: { size: 11 } },
        beginAtZero: true,
      }
    }
  };

  const chartData = generateChartData();

  return (
    <div className="chart-overlay" onClick={onClose}>
      <div className="chart-container" onClick={(e) => e.stopPropagation()}>
        <button className="chart-close" onClick={onClose}>
          ✕
        </button>
        <div className="chart-content">
          <Bar data={chartData} options={{
            ...options,
            plugins: {
              ...options.plugins,
              title: {
                display: true,
                text: `Speed Distribution - ${data.length} vehicles`,
                color: '#64ffda',
                font: { size: 18, weight: 'bold' as const },
              }
            }
          }} />
        </div>
      </div>
    </div>
  );
};

export default ChartOverlay;
