import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, Database, HardDrive, Layers3, LineChart, RefreshCw, Server, ShieldAlert, Users, Zap } from "lucide-react";
import { monitorApi } from "./api";
import type { ErrorRow, Overview, OverviewComponent, OverviewModel, SessionRow, SystemUsageBreakdown, SystemUsageCatalog, SystemUsageDimension, SystemUsageSnapshot, UsageRow } from "./api";

function fmt(value: number | null | undefined, suffix = "") {
  return value == null ? "—" : `${value.toLocaleString()}${suffix}`;
}

function time(value?: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
}

function credits(value: number | null | undefined) {
  return value == null ? "未完整计价" : `${(value / 1_000_000).toFixed(4)} credits`;
}

const TOKEN_LABELS: Record<string, string> = {
  input_tokens: "输入 Token",
  cached_input_tokens: "缓存读取",
  cache_write_input_tokens: "缓存写入",
  output_tokens: "输出 Token",
  reasoning_output_tokens: "推理 Token",
  total_tokens: "总 Token",
  cached_tokens: "缓存 Token",
  cache_miss_tokens: "缓存未命中",
  reasoning_tokens: "推理 Token",
};

function tokenLabel(key: string) {
  return TOKEN_LABELS[key] ?? key.replaceAll("_", " ");
}

function usageTotalTokens(row: { tokens?: Record<string, number>; total_tokens?: number }) {
  return Number(row.tokens?.total_tokens ?? row.total_tokens ?? 0);
}

function Empty({ text }: { text: string }) {
  return <div className="mon-empty"><Database /><span>{text}</span></div>;
}

function PageIntro({ eyebrow, title, description, meta }: { eyebrow: string; title: string; description: string; meta?: string }) {
  return <header className="mon-page-intro"><div><span className="mon-section-kicker">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>{meta ? <span className="mon-page-intro-meta">{meta}</span> : null}</header>;
}

function shortNumber(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(value);
}

type ActivityPoint = { day: string; events: number; tokens: number };

function activityPoints(systemUsage: SystemUsageSnapshot | null, usage: UsageRow[], fallback: Overview): ActivityPoint[] {
  const points = new Map<string, ActivityPoint>();
  for (const row of systemUsage?.breakdown ?? []) {
    const point = points.get(row.day) ?? { day: row.day, events: 0, tokens: 0 };
    point.events += Number(row.events ?? 0);
    point.tokens += usageTotalTokens(row);
    points.set(row.day, point);
  }
  if (!points.size) {
    for (const row of usage) {
      const point = points.get(row.day) ?? { day: row.day, events: 0, tokens: 0 };
      point.events += row.requests;
      point.tokens += row.total_tokens;
      points.set(row.day, point);
    }
  }
  if (!points.size && fallback.requests) {
    points.set("当前周期", { day: "当前周期", events: fallback.requests, tokens: fallback.tokens?.total_tokens ?? 0 });
  }
  return [...points.values()].sort((left, right) => left.day.localeCompare(right.day)).slice(-14);
}

function ActivityChart({ points }: { points: ActivityPoint[] }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const width = 900;
  const height = 240;
  const pad = { top: 24, right: 22, bottom: 38, left: 42 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const maxEvents = Math.max(1, ...points.map((point) => point.events));
  const maxTokens = Math.max(1, ...points.map((point) => point.tokens));
  const x = (index: number) => pad.left + (points.length === 1 ? innerWidth / 2 : index * innerWidth / (points.length - 1));
  const y = (value: number, max: number) => pad.top + innerHeight - value / max * innerHeight;
  const eventLine = points.map((point, index) => `${x(index)},${y(point.events, maxEvents)}`).join(" ");
  const tokenLine = points.map((point, index) => `${x(index)},${y(point.tokens, maxTokens)}`).join(" ");
  const eventArea = `${pad.left},${pad.top + innerHeight} ${eventLine} ${x(points.length - 1)},${pad.top + innerHeight}`;
  const activePoint = activeIndex == null ? null : points[activeIndex];
  const activePointY = activePoint ? y(activePoint.events, maxEvents) : 0;
  const tooltipPlacement = activePointY <= pad.top + 22 ? "below" : "above";
  const tooltipLeft = activeIndex == null ? 50 : Math.min(90, Math.max(10, x(activeIndex) / width * 100));
  const tooltipTop = activePoint ? (activePointY + (tooltipPlacement === "below" ? 10 : -10)) / height * 100 : 30;

  if (!points.length) return <Empty text="当前周期还没有可绘制的活动数据" />;
  return <div className="mon-activity-chart"><div className="mon-chart-legend"><span><i className="request" />请求事件 <b>{fmt(points.reduce((sum, point) => sum + point.events, 0))}</b></span><span><i className="token" />Token <b>{shortNumber(points.reduce((sum, point) => sum + point.tokens, 0))}</b></span><small>按日聚合 · 悬停查看明细</small></div><div className="mon-chart-stage"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="按日请求与 Token 趋势" preserveAspectRatio="none"><defs><linearGradient id="monitor-activity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#6558d3" stopOpacity=".22" /><stop offset="1" stopColor="#6558d3" stopOpacity="0" /></linearGradient></defs>{[0, .25, .5, .75, 1].map((ratio) => <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + innerHeight * ratio} y2={pad.top + innerHeight * ratio} className="mon-chart-gridline" />)}<polygon points={eventArea} fill="url(#monitor-activity-fill)" /><polyline points={eventLine} className="mon-chart-line request-line" /><polyline points={tokenLine} className="mon-chart-line token-line" />{points.map((point, index) => {
    const label = `${point.day}：${fmt(point.events)} 次请求，${fmt(point.tokens)} Token`;
    return <g key={point.day} className="mon-chart-point-hit" tabIndex={0} role="button" aria-label={label} onMouseEnter={() => setActiveIndex(index)} onMouseLeave={() => setActiveIndex((current) => current === index ? null : current)} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex((current) => current === index ? null : current)}><circle cx={x(index)} cy={y(point.events, maxEvents)} r="14" className="mon-chart-hit-area" aria-hidden="true" /><circle cx={x(index)} cy={y(point.events, maxEvents)} r="4" className="mon-chart-point request-point" aria-hidden="true" /><circle cx={x(index)} cy={y(point.tokens, maxTokens)} r="3" className="mon-chart-point token-point" aria-hidden="true" /><text x={x(index)} y={height - 12} textAnchor="middle" className="mon-chart-label" aria-hidden="true">{point.day.slice(5)}</text></g>;
  })}</svg>{activePoint ? <div className="mon-chart-tooltip" role="status" aria-live="polite" data-placement={tooltipPlacement} style={{ left: `${tooltipLeft}%`, top: `${tooltipTop}%` }}><strong>{activePoint.day}</strong><span><b>{fmt(activePoint.events)}</b> 次请求</span><span><b>{fmt(activePoint.tokens)}</b> Token</span></div> : null}</div><div className="mon-chart-foot"><span>峰值 <strong>{fmt(Math.max(...points.map((point) => point.events)))} 次事件</strong></span><span>最近一天 <strong>{fmt(points.at(-1)?.tokens ?? 0)} tokens</strong></span></div></div>;
}

function MetricBar({ label, value, max, detail, tone = "accent" }: { label: string; value: number; max: number; detail: string; tone?: "accent" | "danger" | "success" }) {
  return <div className="mon-metric-bar"><div><span>{label}</span><strong>{detail}</strong></div><div className={`mon-metric-track ${tone}`}><i style={{ width: `${Math.min(100, Math.max(0, value / Math.max(1, max) * 100))}%` }} /></div></div>;
}

const USER_LOAD_INITIAL_COUNT = 4;
const USER_LOAD_BATCH_SIZE = 5;

function UserLoadList({ users }: { users: Overview["top_users"] }) {
  const [visibleCount, setVisibleCount] = useState(() => Math.min(USER_LOAD_INITIAL_COUNT, users.length));
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLSpanElement>(null);
  const hasMore = visibleCount < users.length;
  const loadMore = useCallback(() => {
    setVisibleCount((current) => Math.min(current + USER_LOAD_BATCH_SIZE, users.length));
  }, [users.length]);

  useEffect(() => {
    const list = listRef.current;
    const sentinel = sentinelRef.current;
    if (!list || !sentinel || !hasMore || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore();
    }, { root: list, rootMargin: "96px 0px" });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadMore, visibleCount]);

  return <div className="mon-user-load-list" ref={listRef} aria-label="用户负载列表">{users.slice(0, visibleCount).map((user) => <div key={user.user_id}><span className="mon-user-avatar">{user.user_id.slice(-1).toUpperCase()}</span><div><strong>{user.user_id}</strong><small>{user.workspaces.join(" · ") || "无工作区"}</small></div><b>{fmt(user.requests)}<em>请求</em></b><i className={user.error_rate > .05 ? "danger-text" : ""}>{(user.error_rate * 100).toFixed(1)}%</i></div>)}{hasMore ? <><button className="mon-user-load-more" type="button" onClick={loadMore}>加载更多用户 <small>已显示 {visibleCount} / {users.length}</small></button><span ref={sentinelRef} className="mon-user-load-sentinel" aria-hidden="true" /></> : <div className="mon-user-load-complete">已显示全部 {users.length} 位用户</div>}</div>;
}

export function MonitorOverviewPage({ data, usage, systemUsage }: { data: Overview; usage: UsageRow[]; systemUsage: SystemUsageSnapshot | null }) {
  const runtime = data.runtime ?? {};
  const tags = data.tags ?? { channels: [], sources: [], span_kinds: [] };
  const status = data.status_breakdown ?? [];
  const users = data.top_users ?? [];
  const points = activityPoints(systemUsage, usage, data);
  const queueSize = typeof runtime.queue_size === "number" ? runtime.queue_size : 0;
  const queueCapacity = typeof runtime.queue_capacity === "number" ? runtime.queue_capacity : 0;
  const queueRatio = queueCapacity ? queueSize / queueCapacity : 0;
  const healthGood = data.error_rate <= .05 && Number(runtime.dropped_events ?? 0) === 0;
  const dominantStatus = status[0];
  const topComponent = data.component_spans?.[0];
  const topChannel = tags.channels[0];
  const healthLabel = data.requests ? (healthGood ? "运行平稳" : "需要关注") : "等待数据";

  return <div className="mon-page mon-overview-page mon-overview-page-v2">
    <header className="mon-overview-heading"><div><span className="mon-section-kicker">SYSTEM OBSERVABILITY · ALL USERS</span><h2>系统总览</h2><p>从请求节奏、服务健康到用户负载，先看全局信号，再沿路由深入定位。</p></div><div className={`mon-overview-health-pill ${healthGood ? "good" : "warn"}`}><span>{healthGood ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}</span><strong>{healthLabel}</strong><small>最近 {data.period_days} 天 · {data.latest_trace_at ? `更新于 ${time(data.latest_trace_at)}` : "暂无 Trace"}</small></div></header>
    <section className="mon-overview-signalband" aria-label="系统核心信号"><div className="mon-signal-lead"><span>逻辑请求</span><strong>{fmt(data.requests)}</strong><small>{fmt(data.successes)} 成功 · {fmt(data.failed_requests ?? data.errors)} 失败</small></div><div><span>错误率</span><strong className={data.error_rate > .05 ? "danger-text" : "success-text"}>{(data.error_rate * 100).toFixed(2)}%</strong><small>{fmt(data.errors)} error / timeout</small></div><div><span>活跃用户</span><strong>{fmt(data.active_users)}</strong><small>{fmt(data.active_workspaces)} 个工作区</small></div><div className="mon-signal-percentiles"><span>响应耗时分位</span><div><b>P90 {fmt(data.latency_ms.p90, " ms")}</b><b>P95 {fmt(data.latency_ms.p95, " ms")}</b><b>P99 {fmt(data.latency_ms.p99, " ms")}</b></div><small>P50 {fmt(data.latency_ms.p50, " ms")} · 逻辑请求</small></div><div className="mon-signal-percentiles"><span>首 Token（TTFT）</span><div><b>P90 {fmt(data.ttft_ms.p90, " ms")}</b><b>P95 {fmt(data.ttft_ms.p95, " ms")}</b><b>P99 {fmt(data.ttft_ms.p99, " ms")}</b></div><small>P50 {fmt(data.ttft_ms.p50, " ms")} · 有效 TTFT 样本</small></div><div><span>活跃会话</span><strong>{fmt(data.active_sessions)}</strong><small>全用户合计</small></div></section>
    <div className="mon-overview-canvas"><section className="mon-panel mon-overview-activity"><header><div><span className="mon-panel-kicker">TRAFFIC RHYTHM</span><h2>请求节奏</h2><p>UsageEvent 与 Token 按日叠加，观察今天是否出现突增或异常回落。</p></div><span className="mon-panel-route-hint">用量中心 →</span></header><ActivityChart points={points} /></section><aside className="mon-overview-rail"><section className="mon-panel mon-health-panel"><header><div><span className="mon-panel-kicker">SERVICE HEALTH</span><h2>健康信号</h2></div><CheckCircle2 className={healthGood ? "health-good" : "health-warn"} /></header><div className="mon-health-body"><div className="mon-health-score"><strong>{healthGood ? "GOOD" : "WATCH"}</strong><span>{healthGood ? "无明显饱和或丢弃" : "存在需要跟进的信号"}</span></div><MetricBar label="Telemetry 队列" value={queueRatio * 100} max={100} detail={queueCapacity ? `${fmt(queueSize)} / ${fmt(queueCapacity)}` : "容量未知"} tone={queueRatio > .8 ? "danger" : "accent"} /><MetricBar label="结果状态" value={dominantStatus?.requests ?? 0} max={Math.max(1, data.requests)} detail={dominantStatus ? `${dominantStatus.value} ${dominantStatus.requests}` : "—"} tone="success" /><div className="mon-health-foot"><span>实时订阅 <b>{fmt(typeof runtime.live_subscribers === "number" ? runtime.live_subscribers : null)}</b></span><span>丢弃事件 <b className={Number(runtime.dropped_events ?? 0) > 0 ? "danger-text" : ""}>{fmt(typeof runtime.dropped_events === "number" ? runtime.dropped_events : null)}</b></span></div></div></section><section className="mon-panel mon-load-panel"><header><div><span className="mon-panel-kicker">USER LOAD</span><h2>全用户负载</h2></div><Users className="mon-panel-health-icon" /></header>{users.length ? <UserLoadList key={`${data.period_days}:${data.latest_trace_at ?? "none"}:${users.length}:${users[0]?.user_id ?? ""}`} users={users} /> : <Empty text="当前周期没有用户请求" />}</section></aside></div>
    <section className="mon-panel mon-overview-insights"><header><div><span className="mon-panel-kicker">OPERATING CONTEXT</span><h2>入口、依赖与提醒</h2><p>把总览信号压缩成几个可以立刻采取行动的判断。</p></div><span className="mon-panel-route-hint">深入分析 →</span></header><div className="mon-insight-columns"><div><div className="mon-insight-title"><Layers3 size={15} /><span>流量入口</span></div><div className="mon-insight-primary"><strong>{fmt(data.requests)}</strong><span>总入口</span></div><div className="mon-chip-row">{tags.channels.slice(0, 4).map((item) => <span key={item.value}><b>{item.value}</b><em>{fmt(item.requests)}</em></span>)}{topChannel ? <small>主入口 {topChannel.value}</small> : null}</div></div><div><div className="mon-insight-title"><Zap size={15} /><span>依赖焦点</span></div><div className="mon-insight-primary"><strong>{fmt(topComponent?.requests)}</strong><span>依赖调用</span></div><div className="mon-focus-row"><strong>{topComponent?.name ?? "暂无组件"}</strong><span>{topComponent ? `${fmt(topComponent.requests)} 次调用 · ${(topComponent.error_rate * 100).toFixed(1)}% 错误 · ${fmt(topComponent.avg_duration_ms, " ms")} 平均` : "进入组件与模型查看依赖"}</span></div></div><div><div className="mon-insight-title"><ShieldAlert size={15} /><span>行动提醒</span></div><div className="mon-insight-primary"><strong className={data.errors > 0 ? "danger-text" : "success-text"}>{fmt(data.errors)}</strong><span>错误事件</span></div><div className="mon-focus-row"><strong>{data.error_rate > .05 ? "错误率超过观察阈值" : "错误率在观察阈值内"}</strong><span>{data.event_names?.length ? `${fmt(data.event_names.length)} 类事件信号 · ${data.latest_event_at ? `最后事件 ${time(data.latest_event_at)}` : "等待事件"}` : "暂无事件标记"}</span></div></div></div></section>
  </div>;
}

export const USAGE_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const USAGE_WINDOW_MINUTES = 120;
const USAGE_BUCKET_MINUTES = 5;
const USAGE_USER_PAGE_SIZE = 12;
const USAGE_DIMENSION_LIMIT = 8;

type UsageTrendPoint = { timestamp: string; label: string; events: number; tokens: number };

function trendLabel(value: string) {
  if (!value) return "当前周期";
  const normalized = value.replace("T", " ");
  return normalized.length >= 16 ? normalized.slice(0, 16) : normalized;
}

function usageTrendPoints(breakdown: SystemUsageBreakdown[], fallback: SystemUsageBreakdown[], to?: string, windowMinutes = USAGE_WINDOW_MINUTES, bucketMinutes = USAGE_BUCKET_MINUTES) {
  const aggregate = new Map<string, UsageTrendPoint>();
  const rows = breakdown.length ? breakdown : fallback;
  for (const row of rows) {
    const timestamp = row.period_start ?? row.day;
    const point = aggregate.get(timestamp) ?? { timestamp, label: trendLabel(timestamp), events: 0, tokens: 0 };
    point.events += Number(row.events ?? 0);
    point.tokens += usageTotalTokens(row);
    aggregate.set(timestamp, point);
  }
  const hasFiveMinuteBuckets = breakdown.some((row) => row.granularity === "five_minute");
  if (hasFiveMinuteBuckets) {
    const endMs = Date.parse(to ?? "");
    const latest = Number.isFinite(endMs) ? endMs : Math.max(...[...aggregate.keys()].map((item) => Date.parse(item)).filter(Number.isFinite), Date.now());
    const alignedLatest = Math.ceil(latest / (bucketMinutes * 60 * 1000)) * (bucketMinutes * 60 * 1000);
    const start = alignedLatest - windowMinutes * 60 * 1000;
    const points: UsageTrendPoint[] = [];
    for (let cursor = start; cursor < alignedLatest; cursor += bucketMinutes * 60 * 1000) {
      const timestamp = new Date(cursor).toISOString();
      const matching = [...aggregate.values()].find((point) => Math.abs(Date.parse(point.timestamp) - cursor) < 1000);
      points.push(matching ?? { timestamp, label: trendLabel(timestamp), events: 0, tokens: 0 });
    }
    return points;
  }
  return [...aggregate.values()].sort((left, right) => left.timestamp.localeCompare(right.timestamp)).slice(-14);
}

function UsageTrendChart({ breakdown, fallback, to, windowMinutes = USAGE_WINDOW_MINUTES, bucketMinutes = USAGE_BUCKET_MINUTES }: { breakdown: SystemUsageBreakdown[]; fallback: SystemUsageBreakdown[]; to?: string; windowMinutes?: number; bucketMinutes?: number }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const points = usageTrendPoints(breakdown, fallback, to, windowMinutes, bucketMinutes);
  const width = 900;
  const height = 220;
  const pad = { top: 16, right: 20, bottom: 34, left: 20 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const maxEvents = Math.max(1, ...points.map((point) => point.events));
  const maxTokens = Math.max(1, ...points.map((point) => point.tokens));
  const x = (index: number) => pad.left + (points.length === 1 ? innerWidth / 2 : index * innerWidth / Math.max(1, points.length - 1));
  const y = (value: number, max: number) => pad.top + innerHeight - value / max * innerHeight;
  const eventLine = points.map((point, index) => `${x(index)},${y(point.events, maxEvents)}`).join(" ");
  const tokenLine = points.map((point, index) => `${x(index)},${y(point.tokens, maxTokens)}`).join(" ");
  const activePoint = activeIndex == null ? null : points[activeIndex];
  const activeY = activePoint ? y(activePoint.events, maxEvents) : 0;
  const tooltipPlacement = activeY < pad.top + 42 ? "below" : "above";
  const tooltipLeft = activeIndex == null ? 50 : Math.min(89, Math.max(11, x(activeIndex) / width * 100));
  const tooltipTop = activePoint ? activeY / height * 100 : 30;
  if (!points.length) return <Empty text="还没有 Token 用量数据" />;
  const totalTokens = points.reduce((sum, point) => sum + point.tokens, 0);
  const totalEvents = points.reduce((sum, point) => sum + point.events, 0);
  return <div className="mon-usage-trend"><div className="mon-chart-legend"><span><i className="request" />事件 <b>{fmt(totalEvents)}</b></span><span><i className="token" />Token <b>{shortNumber(totalTokens)}</b></span><small>每 5 分钟 · 最近 {windowMinutes} 分钟滑动窗口</small></div><div className="mon-chart-stage mon-usage-trend-stage"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`最近 ${windowMinutes} 分钟 Token 趋势`} preserveAspectRatio="none"><defs><linearGradient id="monitor-usage-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#6558d3" stopOpacity=".18" /><stop offset="1" stopColor="#6558d3" stopOpacity="0" /></linearGradient></defs>{[0, .25, .5, .75, 1].map((ratio) => <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + innerHeight * ratio} y2={pad.top + innerHeight * ratio} className="mon-chart-gridline" />)}<polyline points={eventLine} className="mon-chart-line request-line" /><polyline points={tokenLine} className="mon-chart-line token-line" />{points.map((point, index) => { const label = `${point.label}：${fmt(point.events)} 次事件，${fmt(point.tokens)} Token`; return <g key={point.timestamp} className="mon-chart-point-hit" tabIndex={0} role="button" aria-label={label} onMouseEnter={() => setActiveIndex(index)} onMouseLeave={() => setActiveIndex((current) => current === index ? null : current)} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex((current) => current === index ? null : current)}><circle cx={x(index)} cy={y(point.events, maxEvents)} r="13" className="mon-chart-hit-area" aria-hidden="true" /><circle cx={x(index)} cy={y(point.events, maxEvents)} r="3.5" className="mon-chart-point request-point" aria-hidden="true" /><circle cx={x(index)} cy={y(point.tokens, maxTokens)} r="3" className="mon-chart-point token-point" aria-hidden="true" />{(index === 0 || index === points.length - 1 || index % Math.max(1, Math.floor(points.length / 5)) === 0) ? <text x={x(index)} y={height - 10} textAnchor="middle" className="mon-chart-label" aria-hidden="true">{point.label.slice(5, 16)}</text> : null}</g>; })}</svg>{activePoint ? <div className="mon-chart-tooltip" role="status" aria-live="polite" data-placement={tooltipPlacement} style={{ left: `${tooltipLeft}%`, top: `${tooltipTop}%` }}><strong>{activePoint.label}</strong><span><b>{fmt(activePoint.events)}</b> 次事件</span><span><b>{fmt(activePoint.tokens)}</b> Token</span></div> : null}</div><div className="mon-chart-foot"><span>窗口事件 <strong>{fmt(totalEvents)}</strong></span><span>窗口 Token <strong>{fmt(totalTokens)}</strong></span></div></div>;
}

function catalogModel(catalog: SystemUsageCatalog | undefined, name: string) {
  return catalog?.models[name] ?? Object.values(catalog?.models ?? {}).find((model) => model.model_id === name);
}

function UsageDimensionTable({ title, description, rows, label, catalog, kind }: { title: string; description: string; rows: SystemUsageDimension[]; label: (row: SystemUsageDimension) => string; catalog?: SystemUsageCatalog; kind: "provider" | "model" }) {
  const visibleRows = rows.slice(0, USAGE_DIMENSION_LIMIT);
  return <section className="mon-panel mon-usage-dimension-panel"><header><div><span className="mon-panel-kicker">{kind === "provider" ? "PROVIDER REGISTRY" : "MODEL CATALOG"}</span><h2>{title}</h2><p>{description}</p></div><span className="mon-usage-panel-count">{rows.length} 个</span></header>{visibleRows.length ? <div className="mon-table mon-table-scroll"><table><thead><tr><th>维度</th><th>调用</th><th>计价</th><th>总 Token</th><th>Credits</th></tr></thead><tbody>{visibleRows.map((row, index) => { const name = label(row); const providerLinked = kind === "provider" ? catalog?.providers[name] : undefined; const modelLinked = kind === "model" ? catalogModel(catalog, name) : undefined; return <tr key={`${name}-${index}`}><td><strong>{name}</strong>{row.provider && kind === "model" ? <small>{row.provider}</small> : null}{providerLinked ? <small className="mon-usage-linked">{providerLinked.adapter ?? "未标注适配器"} · {providerLinked.api_key_configured ? "已配置" : "待配置"}</small> : null}{modelLinked ? <small className="mon-usage-linked">{modelLinked.model_id ?? name}{modelLinked.profile_names?.length ? ` · ${modelLinked.profile_names.join(" / ")}` : ""}</small> : null}</td><td>{fmt(row.events)}</td><td>{row.credits_complete ? `${fmt(row.priced_events)} 完整` : `${fmt(row.unpriced_events)} 待补`}</td><td>{fmt(row.tokens?.total_tokens)}</td><td>{credits(row.credits_micro)}</td></tr>; })}</tbody></table></div> : <Empty text="暂无详细用量" />}{rows.length > visibleRows.length ? <footer className="mon-usage-panel-footer">已显示前 {visibleRows.length} 个高用量项</footer> : null}</section>;
}

function PagedUsageUsers({ days, fallbackRows }: { days: number; fallbackRows: SystemUsageDimension[] }) {
  const [rows, setRows] = useState<SystemUsageDimension[]>(() => fallbackRows.slice(0, USAGE_USER_PAGE_SIZE));
  const [total, setTotal] = useState(fallbackRows.length);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(fallbackRows.length > USAGE_USER_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const loadingRef = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLSpanElement>(null);
  const loadPage = useCallback(async (pageOffset: number, append: boolean) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setLoadError("");
    try {
      const page = await monitorApi.systemUsageUsers(days, USAGE_USER_PAGE_SIZE, pageOffset);
      setRows((current) => append ? [...current, ...page.items] : page.items);
      setTotal(page.total);
      setOffset(page.offset + page.items.length);
      setHasMore(page.has_more);
    } catch (reason) {
      if (!append && fallbackRows.length) {
        setRows(fallbackRows.slice(0, USAGE_USER_PAGE_SIZE));
        setTotal(fallbackRows.length);
        setOffset(Math.min(USAGE_USER_PAGE_SIZE, fallbackRows.length));
        setHasMore(fallbackRows.length > USAGE_USER_PAGE_SIZE);
      } else {
        setLoadError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [days, fallbackRows]);

  useEffect(() => { queueMicrotask(() => void loadPage(0, false)); }, [loadPage]);
  useEffect(() => {
    const list = listRef.current;
    const sentinel = sentinelRef.current;
    if (!list || !sentinel || !hasMore || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting) && !loading) void loadPage(offset, true); }, { root: list, rootMargin: "96px 0px" });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadPage, loading, offset]);

  return <section className="mon-panel mon-usage-dimension-panel mon-usage-users-panel"><header><div><span className="mon-panel-kicker">ALL USER LEDGER</span><h2>按用户</h2><p>所有用户聚合；按页拉取，列表不会一次性挂载。</p></div><span className="mon-usage-panel-count">{fmt(total)} 位</span></header>{rows.length ? <div className="mon-table mon-table-scroll mon-usage-user-table" ref={listRef}><table><thead><tr><th>用户</th><th>调用</th><th>计价</th><th>总 Token</th><th>Credits</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.user_id ?? "unknown"}-${index}`}><td><strong>{row.user_id ?? "unknown"}</strong>{row.workspace_id ? <small>{row.workspace_id}</small> : null}<small className="mon-usage-linked">全用户统计</small></td><td>{fmt(row.events)}</td><td>{row.credits_complete ? `${fmt(row.priced_events)} 完整` : `${fmt(row.unpriced_events)} 待补`}</td><td>{fmt(row.tokens?.total_tokens)}</td><td>{credits(row.credits_micro)}</td></tr>)}</tbody></table>{hasMore ? <><button className="mon-usage-load-more" type="button" onClick={() => void loadPage(offset, true)} disabled={loading}>{loading ? <><RefreshCw className="spin" size={13} />正在加载</> : <>加载更多用户 <small>已显示 {rows.length} / {total}</small></>}</button><span ref={sentinelRef} className="mon-usage-load-sentinel" aria-hidden="true" /></> : <div className="mon-usage-load-complete">已显示 {rows.length} 位用户</div>}</div> : <Empty text={loadError || "当前周期没有用户用量"} />}{loadError && rows.length ? <footer className="mon-usage-panel-footer">下一页加载失败，可稍后重试</footer> : null}</section>;
}

export function MonitorUsagePage({ data, usage, systemUsage }: { data: Overview; usage: UsageRow[]; systemUsage: SystemUsageSnapshot | null }) {
  const [trend, setTrend] = useState<SystemUsageSnapshot | null>(null);
  const tokenValues = systemUsage && Object.keys(systemUsage.tokens).length ? systemUsage.tokens : data.tokens ?? {};
  const fallbackBreakdown = systemUsage?.breakdown?.length ? systemUsage.breakdown : usage.map((row) => ({ ...row, tokens: { total_tokens: row.total_tokens } }));
  const fallbackUsers = systemUsage?.users ?? [];
  const catalog = systemUsage?.catalog;
  const refreshTrend = useCallback(async () => {
    try { setTrend(await monitorApi.systemUsageTrend(USAGE_WINDOW_MINUTES, USAGE_BUCKET_MINUTES)); } catch { /* Keep the last successful window visible. */ }
  }, []);
  useEffect(() => {
    queueMicrotask(() => void refreshTrend());
    const timer = window.setInterval(() => void refreshTrend(), USAGE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshTrend]);
  const trendBreakdown = trend?.breakdown?.length ? trend.breakdown : fallbackBreakdown;
  return <div className="mon-page mon-usage-page">
    <PageIntro eyebrow="USAGE LEDGER · ALL USERS" title="Token 用量中心" description="按全用户的 canonical UsageEvent 账本查看 Token、Provider 与模型；明细按需加载，趋势保持最近窗口。" meta={systemUsage ? `${fmt(systemUsage.events)} 次事件 · ${systemUsage.credits_complete ? "计价完整" : "部分待补"}` : "兼容 Trace 用量"} />
    <div className="mon-usage-catalog-link"><LineChart size={14} /><span>{catalog ? "来自多模型厂商管理" : "模型目录暂未同步"}</span><small>{catalog ? `Provider 连接、真实模型 ID 与用量账本已同步 · ${Object.values(catalog.providers).map((provider) => provider.adapter).filter(Boolean).join(" / ") || "暂无 Provider"} · ${Object.values(catalog.models).map((model) => model.profile_names?.join(" / ")).filter(Boolean).join(" / ") || "模型目录已同步"}` : "当前只显示账本中的 Provider 与模型标识"}</small></div>
    <section className="mon-usage-summary" aria-label="用量中心摘要"><div className="mon-usage-summary-lead"><span>全用户总 Token</span><strong>{fmt(tokenValues.total_tokens)}</strong><small>{fmt(systemUsage?.events ?? data.requests)} 次事件 · {fmt(data.active_users)} 位活跃用户</small></div><div><span>输入</span><strong>{fmt(tokenValues.input_tokens)}</strong><small>含缓存读取 {fmt(tokenValues.cached_input_tokens)}</small></div><div><span>输出</span><strong>{fmt(tokenValues.output_tokens)}</strong><small>含推理 {fmt(tokenValues.reasoning_output_tokens)}</small></div><div><span>计价状态</span><strong className={systemUsage?.credits_complete === false ? "danger-text" : "success-text"}>{systemUsage ? (systemUsage.credits_complete ? "完整" : "部分") : "—"}</strong><small>{fmt(systemUsage?.priced_events)} 已计价 · {fmt(systemUsage?.unpriced_events)} 待补</small></div></section>
    <div className="mon-page-grid mon-usage-top-grid"><section className="mon-panel mon-fixed-panel mon-usage-token-panel"><header><div><span className="mon-panel-kicker">CANONICAL TOKEN LEDGER</span><h2>Token 与缓存</h2><p>所有用户的输入、输出、缓存与推理 Token 汇总。</p></div><span className={`mon-coverage-badge ${systemUsage?.credits_complete === false ? "warning" : ""}`}>{systemUsage ? (systemUsage.credits_complete ? "计价完整" : "部分待补") : "账本未连接"}</span></header><div className="mon-usage-token-list">{Object.entries(tokenValues).map(([key, value]) => <article key={key}><span>{tokenLabel(key)}</span><strong>{fmt(value)}</strong></article>)}{!Object.keys(tokenValues).length && <Empty text="暂无 Token 汇总" />}</div><div className="mon-usage-ledger-footer"><span>{systemUsage ? `${fmt(systemUsage.priced_events)} 次已计价 · ${fmt(systemUsage.unpriced_events)} 次待补` : "当前显示观测 Trace 的兼容 Token 汇总"}</span><strong>{credits(systemUsage?.credits_micro)}</strong></div></section><section className="mon-panel mon-fixed-panel mon-usage-trend-panel"><header><div><span className="mon-panel-kicker">USAGE WINDOW</span><h2>实时用量趋势</h2><p>同一份全用户账本按 5 分钟聚合，自动刷新并保持滑动窗口。</p></div><div className="mon-usage-refresh-state"><Activity size={15} /><span>5 min</span></div></header><UsageTrendChart breakdown={trendBreakdown} fallback={fallbackBreakdown} to={trend?.to} windowMinutes={trend?.window_minutes ?? USAGE_WINDOW_MINUTES} bucketMinutes={trend?.bucket_minutes ?? USAGE_BUCKET_MINUTES} /></section></div>
    <div className="mon-usage-dimensions"><PagedUsageUsers days={data.period_days || 30} fallbackRows={fallbackUsers} /><UsageDimensionTable title="按 Provider" description="与多模型厂商管理中的连接配置同步，展示适配器与密钥状态。" rows={systemUsage?.providers ?? []} label={(row) => row.provider ?? "unknown"} catalog={catalog} kind="provider" /><UsageDimensionTable title="按模型" description="与模型目录使用相同的 Provider 和真实模型 ID。" rows={systemUsage?.models ?? []} label={(row) => row.provider_model ?? "unknown"} catalog={catalog} kind="model" /></div>
  </div>;
}

function MetricTable({ title, description, columns, rows }: { title: string; description: string; columns: string[]; rows: Array<OverviewComponent | OverviewModel> }) {
  return <section className="mon-panel mon-fixed-panel"><header><div><h2>{title}</h2><p>{description}</p></div></header>{rows.length ? <div className="mon-table mon-table-scroll"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.slice(0, 16).map((row, index) => { const label = "name" in row ? row.name : row.provider_model; const subtitle = "label" in row ? row.label : `${row.provider} · ${row.model_profile}`; return <tr key={`${label}-${index}`}><td><strong>{label}</strong><small>{subtitle}</small></td><td>{fmt(row.requests)}</td><td className={row.error_rate > .05 ? "danger-text" : ""}>{(row.error_rate * 100).toFixed(1)}%</td><td>{fmt(row.retries)}</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td></tr>; })}</tbody></table></div> : <Empty text="当前周期没有组件数据" />}</section>;
}

export function MonitorComponentsPage({ data }: { data: Overview }) {
  const spans = data.component_spans ?? [];
  const models = data.models ?? [];
  return <div className="mon-page mon-components-page"><PageIntro eyebrow="DEPENDENCIES · MODEL / WORKER / TOOL" title="组件与模型" description="把组件调用、失败、重试和模型 Provider 分开看，定位是哪一层开始退化。" meta={`${fmt(spans.length)} 个组件 · ${fmt(models.length)} 个模型`} /><div className="mon-components-grid"><MetricTable title="组件 Span" description="按 Span attempt 统计，内部重试不会隐藏。" columns={["组件", "调用", "失败率", "重试", "平均耗时", "Token"]} rows={spans} /><MetricTable title="模型与 Provider" description="比较 Provider、模型和配置档位的健康度。" columns={["模型", "调用", "失败率", "重试", "平均耗时", "Token"]} rows={models} /></div><section className="mon-panel mon-fixed-panel"><header><div><h2>Span 类型分布</h2><p>快速确认流量是否异常集中在某一类内部工作。</p></div></header><div className="mon-component-chip-grid">{(data.span_kinds ?? []).map((item) => <div key={item.name}><span>{item.name}</span><strong>{fmt(item.requests)}</strong><small>{fmt(item.total_tokens)} tokens · {(item.error_rate * 100).toFixed(1)}% errors</small></div>)}{!data.span_kinds?.length && <Empty text="暂无 Span 类型" />}</div></section></div>;
}

export function MonitorSessionsPage({ rows }: { rows: SessionRow[] }) {
  return <div className="mon-page mon-table-page"><PageIntro eyebrow="USERS · WORKSPACES · SESSIONS" title="用户会话" description="按全用户范围查看活跃 Session、错误、响应和 Token，不再混在系统总览里。" meta={`${fmt(rows.length)} 个 Session`} /><section className="mon-panel mon-route-table-panel"><header><div><h2>活跃 Session</h2><p>最近 {rows.length ? "周期" : "暂无数据"} 的会话汇总。</p></div><Users className="mon-panel-health-icon" /></header><div className="mon-table mon-table-scroll"><table><thead><tr><th>Session</th><th>用户 / Workspace</th><th>Turn</th><th>错误</th><th>平均响应</th><th>Token</th><th>最后活跃</th></tr></thead><tbody>{rows.map((row) => <tr key={row.session_id}><td><code>{row.session_id}</code></td><td>{row.user_id}<small>{row.workspace_id} · {row.channel}</small></td><td>{row.turns}</td><td>{row.errors}</td><td>{fmt(row.avg_duration_ms, " ms")}</td><td>{fmt(row.total_tokens)}</td><td>{time(row.last_seen)}</td></tr>)}</tbody></table>{!rows.length && <Empty text="没有 Session 数据" />}</div></section></div>;
}

export function MonitorErrorsPage({ rows, onOpen }: { rows: ErrorRow[]; onOpen?: (traceId: string) => void }) {
  return <div className="mon-page mon-table-page"><PageIntro eyebrow="ERRORS · TIMEOUTS · RETRIES" title="错误分析" description="按错误类型、组件和操作名聚合，优先处理影响面最大的故障。" meta={`${fmt(rows.length)} 个错误分组`} /><section className="mon-panel mon-route-table-panel"><header><div><h2>错误、超时与重试</h2><p>点击错误卡片进入完整 Trace。</p></div><AlertTriangle className="mon-panel-health-icon" /></header><div className="mon-error-grid mon-error-grid-scroll">{rows.map((row) => <button className="mon-error-summary" type="button" key={`${row.error_kind}-${row.kind}-${row.name}`} onClick={() => row.sample_trace_id && onOpen?.(row.sample_trace_id)} disabled={!row.sample_trace_id || !onOpen}><AlertTriangle /><span><strong>{row.error_kind}</strong><small>{row.kind} · {row.name}</small></span><b>{row.count}</b><time>{time(row.last_seen)}</time></button>)}{!rows.length && <Empty text="当前周期没有错误" />}</div></section></div>;
}

export function MonitorStoragePage({ storage, retentionDays, onPrune }: { storage: Record<string, unknown>; retentionDays: number; onPrune: () => Promise<void> }) {
  const [pruning, setPruning] = useState(false);
  const prune = async () => {
    if (!confirm(`清理 ${retentionDays} 天以前的 Trace 与 Event？`)) return;
    setPruning(true);
    try {
      await onPrune();
    } finally {
      setPruning(false);
    }
  };
  const retention = storage.retention as { enabled?: boolean; trace_days?: number; event_days?: number; interval_s?: number } | undefined;
  const scheduledRetention = retention?.enabled !== false;
  const traceRetentionDays = retention?.trace_days ?? retentionDays;
  const eventRetentionDays = retention?.event_days ?? retentionDays;
  return <div className="mon-page mon-storage-page"><PageIntro eyebrow="RETENTION · TELEMETRY STORAGE" title="数据留存" description="查看 Telemetry 存储状态。监控数据按月自动清理，避免长期堆积占用存储；UsageEvent 计费账本不由此操作删除。" meta={scheduledRetention ? `自动清理 · Trace ${traceRetentionDays} 天` : "自动清理已关闭"} /><section className="mon-panel mon-storage-route-panel"><header><div><h2>Telemetry 存储状态</h2><p>{scheduledRetention ? `每月自动清理超过 ${traceRetentionDays} 天的 Trace / Span、超过 ${eventRetentionDays} 天的 Event。` : "自动清理已关闭，请由管理员手动维护留存。"}</p></div><Server className="mon-panel-health-icon" /></header><div className="mon-storage-json"><pre className="mon-json">{JSON.stringify(storage, null, 2)}</pre><button className="mon-storage-prune" type="button" onClick={() => void prune()} disabled={pruning}><HardDrive size={16} />{pruning ? "正在清理…" : `立即清理超过 ${retentionDays} 天的数据`}</button></div></section></div>;
}
