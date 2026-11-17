import { ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type SeriesMarker, type Time } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "./theme";
import { money } from "./ui";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function baseOptions() {
  return {
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: token("--muted"), fontSize: 11,
              fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
    grid: { vertLines: { visible: false }, horzLines: { color: token("--grid"), style: LineStyle.Solid } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderColor: token("--axis"), fixLeftEdge: true, fixRightEdge: true },
    crosshair: { mode: CrosshairMode.Magnet, vertLine: { color: token("--axis"), labelVisible: true, style: LineStyle.Solid, width: 1 as const },
                 horzLine: { visible: false, labelVisible: false } },
    handleScale: false,
    handleScroll: false,
    autoSize: true,
  };
}

export interface LineSpec {
  name: string;
  color: string; // CSS 변수 이름. 예: --series-1
  data: { time: string; value: number }[];
  format?: (v: number) => string;
  area?: boolean;
}

/** 여러 시계열을 한 축에 그리는 선 차트. 범례가 호버 위치의 값을 함께 보여 준다. */
export function LineChart({ series, height = 260 }: { series: LineSpec[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const [hover, setHover] = useState<{ time: string; values: (number | undefined)[] } | null>(null);
  const key = JSON.stringify(series.map((s) => [s.name, s.data.length, s.data[s.data.length - 1]]));

  useEffect(() => {
    if (!el.current) return;
    const chart: IChartApi = createChart(el.current, { ...baseOptions(), height });
    const handles = series.map((s) => {
      const color = token(s.color);
      const opts = { color, lineColor: color, lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: false,
                     crosshairMarkerRadius: 4, crosshairMarkerBorderColor: token("--surface"), crosshairMarkerBorderWidth: 2,
                     priceFormat: { type: "custom" as const, formatter: s.format ?? ((v: number) => money(v)), minMove: 0.0001 } };
      const h = s.area
        ? chart.addAreaSeries({ ...opts, topColor: color + "33", bottomColor: color + "05" })
        : chart.addLineSeries(opts);
      h.setData(s.data.map((d) => ({ time: d.time as Time, value: d.value })));
      return h;
    });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      if (!p.time || !p.point) return setHover(null);
      setHover({ time: String(p.time), values: handles.map((h) => (p.seriesData.get(h) as { value?: number } | undefined)?.value) });
    });
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, theme, height]);

  return (
    <div>
      <div className="legend">
        {series.map((s, i) => {
          const last = s.data[s.data.length - 1]?.value;
          const v = hover ? hover.values[i] : last;
          return (
            <span key={s.name}>
              <i className="key" style={{ background: `var(${s.color})` }} />
              {s.name}
              <b className="num">{v == null ? "–" : (s.format ?? ((x: number) => money(x)))(v)}</b>
            </span>
          );
        })}
        <span className="when num">{hover ? hover.time : series[0]?.data.length ? `${series[0].data[series[0].data.length - 1].time} 기준` : ""}</span>
      </div>
      <div className="chart" ref={el} style={{ height }} />
    </div>
  );
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Mark {
  time: string;
  side: "buy" | "sell";
  text: string;
}

/** 일봉 캔들 차트 + 매수·매도 지점 표시. 상승 빨강, 하락 파랑. */
export function CandleChart({ bars, marks, lines, height = 380 }: { bars: Candle[]; marks: Mark[]; lines?: { price: number; title: string }[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const [hover, setHover] = useState<Candle | null>(null);
  const key = JSON.stringify([bars.length, bars[bars.length - 1], marks.length, lines]);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, { ...baseOptions(), height, handleScale: true, handleScroll: true,
                                            timeScale: { borderColor: token("--axis") } });
    const up = token("--up"), down = token("--down");
    const candles = chart.addCandlestickSeries({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down,
                                                 borderVisible: false, priceLineVisible: false });
    candles.setData(bars.map((b) => ({ ...b, time: b.time as Time })));
    const byTime = new Map(bars.map((b) => [b.time, b]));
    const m: SeriesMarker<Time>[] = marks
      .filter((x) => byTime.has(x.time))
      .sort((a, b) => a.time.localeCompare(b.time))
      .map((x) => ({ time: x.time as Time, position: x.side === "buy" ? "belowBar" : "aboveBar",
                     shape: x.side === "buy" ? "arrowUp" : "arrowDown", color: token("--ink"), text: x.text }));
    candles.setMarkers(m);
    for (const l of lines ?? [])
      candles.createPriceLine({ price: l.price, color: token("--ink-2"), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title });
    const volume = chart.addHistogramSeries({ priceScaleId: "", priceFormat: { type: "volume" }, color: token("--axis"),
                                              priceLineVisible: false, lastValueVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volume.setData(bars.map((b) => ({ time: b.time as Time, value: b.volume })));
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 120), to: bars.length + 2 });
    chart.subscribeCrosshairMove((p) => setHover(p.time ? byTime.get(String(p.time)) ?? null : null));
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, theme, height]);

  const b = hover ?? bars[bars.length - 1];
  return (
    <div>
      <div className="legend num">
        {b && (
          <>
            <span>시 <b>{b.open.toFixed(2)}</b></span>
            <span>고 <b>{b.high.toFixed(2)}</b></span>
            <span>저 <b>{b.low.toFixed(2)}</b></span>
            <span>종 <b>{b.close.toFixed(2)}</b></span>
            <span>거래량 <b>{Math.round(b.volume).toLocaleString()}</b></span>
            <span className="when">{b.time}</span>
          </>
        )}
      </div>
      <div className="chart" ref={el} style={{ height }} />
    </div>
  );
}
