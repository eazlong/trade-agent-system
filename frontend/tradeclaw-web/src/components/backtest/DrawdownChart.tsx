"use client";

import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler,
} from "chart.js";
import { Line } from "react-chartjs-2";
import type { DrawdownPoint } from "@/lib/api";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler
);

interface DrawdownChartProps {
  data: DrawdownPoint[];
}

function formatDate(ts: string): string {
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function DrawdownChart({ data }: DrawdownChartProps) {
  if (!data.length) {
    return <div className="h-40 bg-bg2 rounded-lg animate-pulse" />;
  }

  const labels = data.map((p) => formatDate(p.timestamp));
  const ddData = data.map((p) => p.drawdown * 100); // convert to percentage

  return (
    <div className="h-48">
      <Line
        data={{
          labels,
          datasets: [
            {
              label: "回撤 %",
              data: ddData,
              borderColor: "#ef5350",
              borderWidth: 1.5,
              pointRadius: 0,
              tension: 0.4,
              fill: true,
              backgroundColor: (ctx: any) => {
                const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 160);
                g.addColorStop(0, "rgba(239,83,80,0)");
                g.addColorStop(1, "rgba(239,83,80,0.15)");
                return g;
              },
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
                label: (ctx: any) =>
                  ` 回撤: ${Number(ctx.parsed.y).toFixed(2)}%`,
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
                  typeof v === "number" ? `${v.toFixed(2)}%` : v,
              },
            },
          },
        }}
      />
    </div>
  );
}
