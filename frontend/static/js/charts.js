// Chart.js helpers with consistent instrument styling and a SIMULATED watermark plugin.
const Chart = window.Chart;

if (Chart) {
  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = "#687B91";
  Chart.defaults.borderColor = "#EAF0F6";
  Chart.register({
    id: "simWatermark",
    afterDraw(chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      const { ctx, chartArea: a } = chart;
      ctx.save();
      ctx.fillStyle = "rgba(154, 100, 16, 0.85)";
      ctx.font = '600 10.5px "IBM Plex Sans", sans-serif';
      const label = "SIMULATED";
      const w = ctx.measureText(label).width + 12;
      ctx.fillStyle = "#FDF0D5";
      ctx.fillRect(a.left + 6, a.top + 6, w, 18);
      ctx.strokeStyle = "#EFD39C";
      ctx.strokeRect(a.left + 6.5, a.top + 6.5, w - 1, 17);
      ctx.fillStyle = "#9A6410";
      ctx.fillText(label, a.left + 12, a.top + 19);
      ctx.restore();
    },
  });
}

export const COLORS = { current: "#168BFF", voltage: "#00B8D9", power: "#1769E8", ref: "#687B91", band: "rgba(22,139,255,0.10)",
  pass: "#16A36A", warn: "#E9A23B", fail: "#DC4545" };

export function lineChart(canvas, { datasets, xTitle = "", yTitle = "", sim = false, xTicks, legend = false, yBeginAtZero = false,
  points = false }) {
  if (!Chart) return null;
  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: datasets.map((d) => ({
        label: d.label, data: d.data || [], borderColor: d.color, backgroundColor: d.fill || d.color,
        borderWidth: d.width || 1.8, pointRadius: d.points ?? (points ? 2.5 : 0), pointHoverRadius: 3, tension: 0,
        borderDash: d.dashed ? [5, 4] : undefined, fill: d.fillTo !== undefined ? d.fillTo : false, spanGaps: false,
        order: d.order ?? 0,
      })),
    },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", intersect: false, axis: "x" },
      scales: {
        x: { type: "linear", title: { display: !!xTitle, text: xTitle }, grid: { color: "#EEF2F7" },
          ticks: { maxTicksLimit: 8, callback: xTicks || ((v) => v) } },
        y: { title: { display: !!yTitle, text: yTitle }, grid: { color: "#EEF2F7" }, beginAtZero: yBeginAtZero,
          ticks: { maxTicksLimit: 6 } },
      },
      plugins: {
        legend: { display: legend, position: "top", align: "end", labels: { boxWidth: 14, boxHeight: 2 } },
        tooltip: { backgroundColor: "#172B45", titleFont: { weight: "500" }, bodyFont: { family: '"IBM Plex Mono", monospace' } },
        simWatermark: { enabled: sim },
      },
    },
  });
}

export function setSim(chart, sim) {
  if (!chart) return;
  chart.options.plugins.simWatermark.enabled = !!sim;
}
