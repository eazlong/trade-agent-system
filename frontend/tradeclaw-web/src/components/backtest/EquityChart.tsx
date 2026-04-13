"use client";

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
import type { EquityPoint } from "@/lib/api";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler
);

interface EquityChartProps {
  data: EquityPoint[];
  showBenchmark?: boolean;
}

function formatDate(ts: string): string {
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function EquityChart({ data, showBenchmark = false }: EquityChartProps) {
  if (!data.length) {
    return <div className="h-40 bg-bg2 rounded-lg animate-pulse" />;
  }

  const labels = data.map((p) => formatDate(p.timestamp));
  const equityData = data.map((p) => p.equity);

  const datasets: any[] = [
    {
      label: "净值",
      data: equityData,
      borderColor: "#00e676",
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.4,
      fill: true,
      backgroundColor: (ctx: any) => {
        const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 160);
        g.addColorStop(0, "rgba(0,230,118,0.15)");
        g.addColorStop(1, "rgba(0,230,118,0)");
        return g;
      },
    },
  ];

  if (showBenchmark && data.length > 0) {
    const start = equityData[0];
    const benchmark = equityData.map((_, i) =>
      parseFloat((start + (equityData[equityData.length - 1] - start) * (i / (equityData.length - 1))).toFixed(4))
    );
    datasets.push({
      label: "基准",
      data: benchmark,
      borderColor: "rgba(255,255,255,0.15)",
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.4,
      fill: false,
    });
  }

  return (
    <div className="h-48">
      <Line
        data={{ labels, datasets }}
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
                label: (ctx: any) =>
                  ` ${ctx.dataset.label}: ${Number(ctx.parsed.y).toFixed(2)}`,
              },
            },
          },
          scales: {
            x: {
              grid: { color: "rgba(255,255,255,0.03)" },
              ticks: {
                color: "#4d5766",
                font: { size: 10, family: "IBM Plex Mono" },
                maxTicksLimit: 10,
              },
            },
            y: {
              grid: { color: "rgba(255,255,255,0.04)" },
              ticks: {
                color: "#4d5766",
                font: { size: 10, family: "IBM Plex Mono" },
                callback: (v: string | number) =>
                  typeof v === "number" ? v.toFixed(2) : v,
              },
            },
          },
        }}
      />
    </div>
  );
}
