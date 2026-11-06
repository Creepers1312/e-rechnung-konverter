import { type ReactNode, useEffect, useRef, useState } from "react";

/** Small hand-made SVG charts for the start page (no chart library).
 *  Colours come from CSS tokens (--series-1..3), text uses text tokens only. */

export interface Series {
  key: string;
  label: string;
  color: string; // CSS colour, e.g. "var(--series-1)"
}

export type Row = { label: string; tip?: string } & Record<string, number | string | undefined>;

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Clean axis ticks 0 .. >= max. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.999; v += step) ticks.push(Math.round(v * 100) / 100);
  return ticks;
}

export function compactEur(v: number): string {
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mio. €`;
  if (Math.abs(v) >= 1e4) return `${Math.round(v / 1e3).toLocaleString("de-DE")} Tsd. €`;
  return `${Math.round(v).toLocaleString("de-DE")} €`;
}

export function Legend({ series, values }: { series: Series[]; values?: Record<string, string> }) {
  return (
    <ul className="legend">
      {series.map((s) => (
        <li key={s.key}>
          <span className="swatch" style={{ background: s.color }} aria-hidden />
          {s.label}
          {values?.[s.key] && <strong>{values[s.key]}</strong>}
        </li>
      ))}
    </ul>
  );
}

/** Card with title, optional legend and a "Tabelle" toggle (the accessible data view). */
export function ChartCard({
  title,
  subtitle,
  legend,
  table,
  children,
}: {
  title: string;
  subtitle?: string;
  legend?: ReactNode;
  table: { head: string[]; rows: (string | number)[][] };
  children: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="chart-card">
      <div className="chart-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <small className="muted">{subtitle}</small>}
        </div>
        <button className="link small" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>
          {asTable ? "Diagramm" : "Tabelle"}
        </button>
      </div>
      {legend}
      {asTable ? (
        <div className="chart-table">
          <table className="data">
            <thead>
              <tr>
                {table.head.map((h, i) => (
                  <th key={i} className={i ? "num" : ""}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j} className={j ? "num" : ""}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </section>
  );
}

/** Vertical (optionally stacked) columns with one y-axis and a hover tooltip per column. */
export function ColumnChart({
  rows,
  series,
  height = 200,
  format = (v: number) => v.toLocaleString("de-DE"),
  labelEvery = 1,
}: {
  rows: Row[];
  series: Series[];
  height?: number;
  format?: (v: number) => string;
  labelEvery?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const totals = rows.map((r) => series.reduce((sum, s) => sum + Number(r[s.key] ?? 0), 0));
  const ticks = niceTicks(Math.max(...totals, 0));
  const top = ticks[ticks.length - 1];
  const axisW = Math.max(...ticks.map((t) => format(t).length)) * 7 + 10;
  const pad = { top: 10, bottom: 22 };
  const plotW = Math.max(width - axisW, 10);
  const plotH = height - pad.top - pad.bottom;
  const band = plotW / Math.max(rows.length, 1);
  const barW = Math.min(24, Math.max(band - 4, 2));
  // thin out x labels so they never collide (about 34px per label)
  const every = Math.max(labelEvery, Math.ceil(34 / Math.max(band, 1)));
  const y = (v: number) => pad.top + plotH - (v / top) * plotH;

  return (
    <div className="chart" ref={ref} onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="Säulendiagramm">
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={axisW} x2={width} y1={y(t)} y2={y(t)} />
              <text className="axis" x={axisW - 8} y={y(t) + 4} textAnchor="end">
                {format(t)}
              </text>
            </g>
          ))}
          {rows.map((r, i) => {
            const cx = axisW + band * i + band / 2;
            let acc = 0;
            const segs = series
              .map((s) => ({ s, v: Number(r[s.key] ?? 0) }))
              .filter((x) => x.v > 0)
              .map((x, j, all) => {
                const y0 = y(acc);
                acc += x.v;
                const y1 = y(acc);
                const last = j === all.length - 1;
                const gap = j > 0 ? 2 : 0; // surface gap between stacked segments
                const h = Math.max(y0 - y1 - gap, 1);
                return last ? (
                  <path
                    key={x.s.key}
                    fill={x.s.color}
                    d={roundedTop(cx - barW / 2, y1, barW, h, Math.min(4, h, barW / 2))}
                  />
                ) : (
                  <rect key={x.s.key} fill={x.s.color} x={cx - barW / 2} y={y1 + 0} width={barW} height={h} />
                );
              });
            return (
              <g key={i} className={hover === i ? "col hover" : "col"}>
                {segs}
                {(rows.length - 1 - i) % every === 0 && (
                  <text className="axis" x={cx} y={height - 6} textAnchor="middle">
                    {r.label}
                  </text>
                )}
                <rect
                  className="hit"
                  x={axisW + band * i}
                  y={pad.top}
                  width={band}
                  height={plotH}
                  onMouseEnter={() => setHover(i)}
                />
              </g>
            );
          })}
          <line className="baseline" x1={axisW} x2={width} y1={y(0)} y2={y(0)} />
        </svg>
      )}
      {hover !== null && rows[hover] && (
        <div
          className="tooltip"
          style={{
            left: Math.min(axisW + band * hover + band / 2, width - 90),
            top: Math.max(y(totals[hover]) - 8, 0),
          }}
        >
          <strong>{rows[hover].tip ?? rows[hover].label}</strong>
          {series.map((s) => (
            <span key={s.key}>
              <i className="swatch" style={{ background: s.color }} />
              {s.label}: {format(Number(rows[hover][s.key] ?? 0))}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** One whole split into parts (formats, sources): thin segmented bar, values in the legend. */
export function SegmentedBar({ parts }: { parts: { series: Series; value: number }[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return <p className="muted small">Noch keine Daten.</p>;
  return (
    <>
      <div className="segbar" role="img" aria-label={parts.map((p) => `${p.series.label} ${p.value}`).join(", ")}>
        {parts
          .filter((p) => p.value > 0)
          .map((p) => (
            <span
              key={p.series.key}
              style={{ flexGrow: p.value, background: p.series.color }}
              title={`${p.series.label}: ${p.value} (${Math.round((p.value / total) * 100)} %)`}
            />
          ))}
      </div>
      <Legend
        series={parts.map((p) => p.series)}
        values={Object.fromEntries(
          parts.map((p) => [p.series.key, `${p.value} · ${Math.round((p.value / total) * 100)} %`]),
        )}
      />
    </>
  );
}

/** Ranked horizontal bars with the value at the tip. */
export function BarList({ items, format }: { items: { label: string; value: number; note?: string }[]; format: (v: number) => string }) {
  const max = Math.max(...items.map((i) => i.value), 0);
  if (!items.length) return <p className="muted small">Noch keine Daten.</p>;
  return (
    <ul className="barlist">
      {items.map((it) => (
        <li key={it.label} title={it.note}>
          <span className="name">{it.label}</span>
          <span className="track">
            <span className="bar" style={{ width: `calc((100% - 104px) * ${max ? it.value / max : 0})` }} />
            <span className="val">{format(it.value)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
