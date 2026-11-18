import type { EquityPoint } from "./types";

interface Props {
  points: EquityPoint[];
  baseline: number;
}

const W = 640;
const H = 180;
const PAD = { l: 8, r: 8, t: 12, b: 20 };

export default function EquityChart({ points, baseline }: Props) {
  if (points.length < 2) {
    return <p className="empty">La curva aparece cuando haya al menos dos velas procesadas.</p>;
  }
  const ys = points.map((p) => p.equity).concat(baseline);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - min) / span) * (H - PAD.t - PAD.b);
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const last = points[points.length - 1].equity;
  const up = last >= baseline;
  const date = (t: number) => new Date(t).toISOString().slice(0, 10);

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Patrimonio del símbolo: de ${points[0].equity.toFixed(0)} a ${last.toFixed(0)} USDT`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={y(baseline)} y2={y(baseline)} className="chart-base" />
        <polyline points={line} className={up ? "chart-line up" : "chart-line down"} fill="none" />
        <circle cx={x(points.length - 1)} cy={y(last)} r="4" className={up ? "chart-dot up" : "chart-dot down"} />
        <text x={PAD.l} y={H - 4} className="chart-axis">{date(points[0].candle_time)}</text>
        <text x={W - PAD.r} y={H - 4} textAnchor="end" className="chart-axis">{date(points[points.length - 1].candle_time)}</text>
      </svg>
      <figcaption>La línea punteada es el capital inicial de este símbolo.</figcaption>
    </figure>
  );
}
