import { useEffect, useState } from "react";
import { getDashboardStats } from "./api";
import { BarList, ChartCard, ColumnChart, Legend, SegmentedBar, type Series, compactEur } from "./Charts";
import type { DashboardStats } from "./types";
import { eur } from "./util";

const ORIGIN: Series[] = [
  { key: "automatic", label: "automatisch", color: "var(--series-1)" },
  { key: "manual", label: "von Hand", color: "var(--series-2)" },
];
const PROFILES: Series[] = [
  { key: "zugferd", label: "ZUGFeRD", color: "var(--series-1)" },
  { key: "xrechnung", label: "XRechnung", color: "var(--series-2)" },
];
const SOURCES: Series[] = [
  { key: "idoc", label: "SAP-IDoc", color: "var(--series-1)" },
  { key: "pdf", label: "PDF", color: "var(--series-2)" },
  { key: "manual", label: "manuell", color: "var(--series-3)" },
];
const MONTH = new Intl.DateTimeFormat("de-DE", { month: "short" });
const MONTH_LONG = new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" });
const DAY = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" });
const WEEKDAY = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });

/** Same period of the previous month, e.g. "1.–6. Okt." */
function previousPeriod(): string {
  const now = new Date();
  const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `1.–${now.getDate()}. ${MONTH.format(prev)}`;
}

function delta(now: number, before: number): { text: string; dir: "up" | "down" | "flat" } | null {
  if (!before) return null;
  const pct = Math.round(((now - before) / before) * 100);
  return { text: `${pct > 0 ? "+" : ""}${pct} % ggü. ${previousPeriod()}`, dir: pct > 0 ? "up" : pct < 0 ? "down" : "flat" };
}

function Delta({ d }: { d: ReturnType<typeof delta> }) {
  if (!d) return <small>kein Vormonat zum Vergleich</small>;
  return (
    <small className={`delta ${d.dir}`}>
      <span aria-hidden>{d.dir === "up" ? "▲" : d.dir === "down" ? "▼" : "■"}</span> {d.text}
    </small>
  );
}

/** Key figures and charts of the start page. */
export function DashboardCharts({ refreshKey }: { refreshKey: number }) {
  const [s, setS] = useState<DashboardStats | null>(null);
  useEffect(() => {
    getDashboardStats(30).then(setS).catch(() => undefined);
  }, [refreshKey]);
  if (!s) return null;

  const thisCount = s.this_month.count;
  const thisVol = Number(s.this_month.volume);
  const daily = s.daily.map((d) => {
    const dt = new Date(`${d.date}T12:00:00`);
    return { label: DAY.format(dt), tip: WEEKDAY.format(dt), automatic: d.automatic, manual: d.manual };
  });
  const monthly = s.monthly.map((m) => {
    const dt = new Date(`${m.month}-15T12:00:00`);
    return { label: MONTH.format(dt), tip: `${MONTH_LONG.format(dt)} · ${m.count} Rechnungen`, volume: Number(m.volume) };
  });
  const total30 = s.daily.reduce((n, d) => n + d.automatic + d.manual, 0);
  const auto30 = s.daily.reduce((n, d) => n + d.automatic, 0);

  return (
    <>
      <div className="kpis figures">
        <div className="kpi static">
          <span>E-Rechnungen im {MONTH_LONG.format(new Date()).split(" ")[0]}</span>
          <strong>{thisCount.toLocaleString("de-DE")}</strong>
          <Delta d={delta(thisCount, s.last_month_to_date.count)} />
        </div>
        <div className="kpi static">
          <span>Rechnungsvolumen (brutto, EUR)</span>
          <strong>{compactEur(thisVol)}</strong>
          <Delta d={delta(thisVol, Number(s.last_month_to_date.volume))} />
        </div>
        <div className="kpi static">
          <span>Automatisierungsquote (30 Tage)</span>
          <strong>{s.automation_rate == null ? "–" : `${Math.round(s.automation_rate * 100)} %`}</strong>
          <div className="meter" aria-hidden>
            <span style={{ width: `${(s.automation_rate ?? 0) * 100}%` }} />
          </div>
          <small>
            {auto30} von {total30} ohne Klick erzeugt
          </small>
        </div>
        <div className="kpi static">
          <span>Ø Zeit bis zur Freigabe</span>
          <strong>
            {s.review.avg_hours_to_resolve == null
              ? "–"
              : s.review.avg_hours_to_resolve < 48
                ? `${s.review.avg_hours_to_resolve.toLocaleString("de-DE")} h`
                : `${Math.round(s.review.avg_hours_to_resolve / 24)} Tage`}
          </strong>
          <small>
            {s.review.resolved} geprüft · {s.review.discarded} verworfen (30 Tage)
          </small>
        </div>
      </div>

      <div className="charts">
        <ChartCard
          title="E-Rechnungen pro Tag"
          subtitle="letzte 30 Tage"
          legend={<Legend series={ORIGIN} />}
          table={{
            head: ["Tag", "automatisch", "von Hand"],
            rows: daily.map((d) => [d.tip, d.automatic, d.manual]),
          }}
        >
          <ColumnChart rows={daily} series={ORIGIN} labelEvery={5} />
        </ChartCard>

        <ChartCard
          title="Rechnungsvolumen pro Monat"
          subtitle="brutto in EUR, letzte 12 Monate"
          table={{ head: ["Monat", "Rechnungen", "Volumen"], rows: s.monthly.map((m, i) => [monthly[i].tip.split(" · ")[0], m.count, eur(m.volume)]) }}
        >
          <ColumnChart
            rows={monthly}
            series={[{ key: "volume", label: "Volumen", color: "var(--series-1)" }]}
            format={compactEur}
          />
        </ChartCard>

        <ChartCard
          title="Formate und Quellen"
          subtitle="letzte 30 Tage"
          table={{
            head: ["", "Anzahl"],
            rows: [
              ...PROFILES.map((p) => [`Format: ${p.label}`, s.by_profile[p.key] ?? 0]),
              ...SOURCES.map((p) => [`Quelle: ${p.label}`, s.by_source[p.key] ?? 0]),
            ],
          }}
        >
          <h3 className="chart-sub">Format</h3>
          <SegmentedBar parts={PROFILES.map((p) => ({ series: p, value: s.by_profile[p.key] ?? 0 }))} />
          <h3 className="chart-sub">Quelle</h3>
          <SegmentedBar parts={SOURCES.map((p) => ({ series: p, value: s.by_source[p.key] ?? 0 }))} />
        </ChartCard>

        <ChartCard
          title="Top-Käufer"
          subtitle="nach Volumen, letzte 30 Tage"
          table={{ head: ["Käufer", "Rechnungen", "Volumen"], rows: s.top_buyers.map((b) => [b.name, b.count, eur(b.volume)]) }}
        >
          <BarList
            items={s.top_buyers.map((b) => ({ label: b.name, value: Number(b.volume), note: `${b.count} Rechnungen` }))}
            format={compactEur}
          />
        </ChartCard>
      </div>
    </>
  );
}
