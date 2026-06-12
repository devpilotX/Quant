"use client";

/** Chart wrappers. ECharts for analytical charts, lightweight-charts for
 * candles. Both render client-side only and resize with their container. */

import * as echarts from "echarts";
import { useEffect, useRef } from "react";

const DARK = {
  textColor: "#8a93a6",
  axisLine: "#232a3b",
  split: "#1a2030",
  blue: "#4f8cff",
  green: "#2fbf71",
  red: "#e5484d",
  amber: "#f5a524",
};

export function EChart({
  option,
  height = 260,
}: {
  option: echarts.EChartsOption;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: "transparent",
        textStyle: { color: DARK.textColor, fontSize: 10 },
        grid: { left: 70, right: 16, top: 24, bottom: 28 },
        ...option,
      },
      { notMerge: true }
    );
  }, [option]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}

export function lineOption(
  series: { name: string; data: [string, number][]; color?: string; area?: boolean }[],
  fmt?: (v: number) => string
): echarts.EChartsOption {
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#161b28",
      borderColor: "#232a3b",
      textStyle: { color: "#d7dce6", fontSize: 11 },
      valueFormatter: fmt ? (v) => fmt(v as number) : undefined,
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: DARK.axisLine } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: fmt },
      splitLine: { lineStyle: { color: DARK.split } },
    },
    series: series.map((s, i) => ({
      name: s.name,
      type: "line",
      showSymbol: false,
      data: s.data,
      lineStyle: { width: 1.4, color: s.color ?? [DARK.blue, DARK.green, DARK.amber][i % 3] },
      itemStyle: { color: s.color ?? DARK.blue },
      areaStyle: s.area
        ? { color: `${s.color ?? DARK.blue}18` }
        : undefined,
    })),
  };
}

export function barOption(
  categories: string[],
  values: number[],
  fmt?: (v: number) => string
): echarts.EChartsOption {
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#161b28",
      borderColor: "#232a3b",
      textStyle: { color: "#d7dce6", fontSize: 11 },
      valueFormatter: fmt ? (v) => fmt(v as number) : undefined,
    },
    xAxis: {
      type: "category",
      data: categories,
      axisLine: { lineStyle: { color: DARK.axisLine } },
      axisLabel: { rotate: categories.length > 8 ? 30 : 0 },
    },
    yAxis: {
      type: "value",
      axisLabel: { formatter: fmt },
      splitLine: { lineStyle: { color: DARK.split } },
    },
    series: [
      {
        type: "bar",
        data: values.map((v) => ({
          value: v,
          itemStyle: { color: v >= 0 ? DARK.green : DARK.red },
        })),
        barMaxWidth: 36,
      },
    ],
  };
}

/** Stacked regime-probability strip over time. */
export function regimeOption(
  rows: { ts: string; probs: Record<string, number> }[]
): echarts.EChartsOption {
  const labels = Array.from(
    new Set(rows.flatMap((r) => Object.keys(r.probs)))
  ).sort();
  const colors: Record<string, string> = {
    calm_trend: DARK.green,
    calm_range: DARK.blue,
    turbulent: DARK.red,
  };
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#161b28",
      borderColor: "#232a3b",
      textStyle: { color: "#d7dce6", fontSize: 11 },
      valueFormatter: (v) => `${((v as number) * 100).toFixed(1)}%`,
    },
    legend: { textStyle: { color: DARK.textColor, fontSize: 10 }, top: 0 },
    xAxis: { type: "time", axisLine: { lineStyle: { color: DARK.axisLine } } },
    yAxis: { type: "value", min: 0, max: 1, splitLine: { lineStyle: { color: DARK.split } } },
    series: labels.map((label) => ({
      name: label,
      type: "line",
      stack: "p",
      showSymbol: false,
      lineStyle: { width: 0.5 },
      areaStyle: { color: `${colors[label] ?? DARK.amber}55` },
      itemStyle: { color: colors[label] ?? DARK.amber },
      data: rows.map((r) => [r.ts, r.probs[label] ?? 0]),
    })),
  };
}

/** Candles + trade markers via lightweight-charts. */
export function CandleChart({
  bars,
  markers = [],
  height = 320,
}: {
  bars: { ts: string; open: number; high: number; low: number; close: number }[];
  markers?: { ts: string; side: "BUY" | "SELL"; label: string }[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    let disposed = false;
    let chart: import("lightweight-charts").IChartApi | null = null;
    let ro: ResizeObserver | null = null;

    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !ref.current) return;
      chart = lw.createChart(ref.current, {
        height,
        layout: { background: { color: "transparent" }, textColor: DARK.textColor, fontSize: 10 },
        grid: {
          vertLines: { color: DARK.split },
          horzLines: { color: DARK.split },
        },
        timeScale: { borderColor: DARK.axisLine, timeVisible: true },
        rightPriceScale: { borderColor: DARK.axisLine },
      });
      const toUtc = (ts: string) =>
        (Math.floor(new Date(`${ts}Z`).getTime() / 1000)) as import("lightweight-charts").UTCTimestamp;
      const series = chart.addSeries(lw.CandlestickSeries, {
        upColor: DARK.green,
        downColor: DARK.red,
        borderUpColor: DARK.green,
        borderDownColor: DARK.red,
        wickUpColor: DARK.green,
        wickDownColor: DARK.red,
      });
      series.setData(
        bars.map((b) => ({
          time: toUtc(b.ts),
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        }))
      );
      if (markers.length) {
        lw.createSeriesMarkers(
          series,
          markers.map((m) => ({
            time: toUtc(m.ts),
            position: m.side === "BUY" ? "belowBar" : "aboveBar",
            color: m.side === "BUY" ? DARK.green : DARK.red,
            shape: m.side === "BUY" ? "arrowUp" : "arrowDown",
            text: m.label,
          }))
        );
      }
      chart.timeScale().fitContent();
      ro = new ResizeObserver(() => {
        if (ref.current && chart) chart.applyOptions({ width: ref.current.clientWidth });
      });
      ro.observe(ref.current);
    })();

    return () => {
      disposed = true;
      ro?.disconnect();
      chart?.remove();
    };
  }, [bars, markers, height]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
