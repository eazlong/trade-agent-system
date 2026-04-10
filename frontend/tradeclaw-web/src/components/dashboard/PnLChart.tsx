"use client";

import { useEffect, useRef, useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler
);

export default function PnLChart() {
  const [chartReady, setChartReady] = useState(false);
  const [chartData, setChartData] = useState<{ labels: string[]; data: number[]; benchmark: number[] } | null>(null);

  useEffect(() => {
    setChartReady(true);
    const days = 30;
    const labels = Array.from({ length: days }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (days - i));
      return `${d.getMonth() + 1}/${d.getDate()}`;
    });
    let v = 1.0;
    const data = [v];
    for (let i = 1; i < days; i++) {
      v += (Math.random() - 0.45) * 0.015;
      data.push(parseFloat(v.toFixed(4)));
    }
    const benchmark = data.map((_, i) => parseFloat((1 + i * 0.001).toFixed(4)));
    setChartData({ labels, data, benchmark });
  }, []);

  if (!chartReady || !chartData) {
    return <div className="h-40 bg-bg2 rounded-lg animate-pulse" />;
  }

  return (
    <div className="h-40">
      <Line
        data={{
          labels: chartData.labels,
          datasets: [
            {
              label: "净值",
              data: chartData.data,
              borderColor: "#00e676",
              borderWidth: 1.5,
              pointRadius: 0,
              tension: 0.4,
              fill: true,
              backgroundColor: (ctx) => {
                const g = (ctx.chart as any).ctx.createLinearGradient(0, 0, 0, 160);
                g.addColorStop(0, "rgba(0,230,118,0.15)");
                g.addColorStop(1, "rgba(0,230,118,0)");
                return g;
              },
            },
            {
              label: "基准",
              data: chartData.benchmark,
              borderColor: "rgba(255,255,255,0.15)",
              borderWidth: 1,
              borderDash: [4, 4],
              pointRadius: 0,
              tension: 0.4,
              fill: false,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "rgba(14,17,23,0.95)",
              titleColor: "#e8ecf2",
              bodyColor: "#8b95a8",
              borderColor: "rgba(255,255,255,0.1)",
              borderWidth: 1,
              callbacks: {
                label: (ctx) => ` ${ctx.dataset.label}: ${(ctx.parsed.y as number).toFixed(4)}`,
              },
            },
          },
          scales: {
            x: {
              grid: { color: "rgba(255,255,255,0.03)" },
              ticks: { color: "#4d5766", font: { size: 10, family: "IBM Plex Mono" }, maxTicksLimit: 8 },
            },
            y: {
              grid: { color: "rgba(255,255,255,0.04)" },
              ticks: {
                color: "#4d5766",
                font: { size: 10, family: "IBM Plex Mono" },
                callback: (v: string | number) => typeof v === 'number' ? v.toFixed(3) : v,
              },
            },
          },
        }}
      />
    </div>
  );
}
