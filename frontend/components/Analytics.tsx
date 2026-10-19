"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, Bot, CheckCircle2, FlaskConical, Gauge, LoaderCircle, ShieldCheck, Sparkles, Zap } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { api, watchBackgroundTask } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import type { BackgroundTask, DashboardData, PerformanceMetrics, TicketSummary } from "@/lib/types";
import { categoryLabel, statusLabel } from "@/components/Dashboard";


export function Analytics() {
  const { user } = useAuth();
  const [data, setData] = useState<DashboardData | null>(null);
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [performance, setPerformance] = useState<PerformanceMetrics | null>(null);
  const [error, setError] = useState("");
  const [evaluationTask, setEvaluationTask] = useState<BackgroundTask | null>(null);

  async function runEvaluation() {
    setError("");
    try {
      const queued = await api.startEvaluation();
      setEvaluationTask(queued);
      await watchBackgroundTask(queued.id, setEvaluationTask);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  useEffect(() => {
    Promise.all([api.dashboard(), api.tickets(), user?.role === "admin" ? api.performanceMetrics() : Promise.resolve(null)])
      .then(([dashboard, ticketList, performanceData]) => { setData(dashboard); setTickets(ticketList); setPerformance(performanceData); })
      .catch((reason: Error) => setError(reason.message));
  }, [user?.role]);

  const analyzed = tickets.filter((ticket) => ticket.category);
  const resolved = tickets.filter((ticket) => ticket.status === "resolved");
  const averageConfidence = analyzed.length
    ? Math.round(analyzed.reduce((total, ticket) => total + (ticket.confidence ?? 0), 0) / analyzed.length * 100)
    : 0;
  const statusDistribution = useMemo(() => {
    const processingStatuses = new Set(["in_progress", "pending_approval", "awaiting_customer", "needs_review"]);
    return [
      { status: "new", count: tickets.filter((ticket) => ticket.status === "new").length },
      { status: "processing", count: tickets.filter((ticket) => processingStatuses.has(ticket.status)).length },
      { status: "resolved", count: tickets.filter((ticket) => ticket.status === "resolved").length },
    ].filter((item) => item.count > 0);
  }, [tickets]);

  if (error) return <div className="center-state error-state"><Zap size={26} /><strong>无法读取效果数据</strong><span>{error}</span></div>;
  if (!data) return <div className="center-state"><LoaderCircle className="spin" size={26} /><strong>正在计算运营指标</strong></div>;

  const maxTrend = Math.max(...data.trend.map((item) => item.automated + item.manual), 1);
  const coverage = tickets.length ? Math.round(analyzed.length / tickets.length * 100) : 0;
  const resolutionRate = tickets.length ? Math.round(resolved.length / tickets.length * 100) : 0;

  return (
    <div className="page-wrap feature-page">
      <PageHeader title="效果分析" description="从效率、质量和风险三个维度评估 Agent 的业务表现。" />

      <section className="panel async-evaluation-panel"><div><span><FlaskConical size={17} /></span><div><h2>Agent 自动化评测</h2><p>{evaluationTask?.message ?? "离线规则回归：120 条用例，覆盖客服、商城、FAQ、库存、多轮与安全边界；不调用 DeepSeek。真实模型评测请使用专用命令。"}</p></div></div><div className="async-evaluation-action">{evaluationTask && !["finished", "failed"].includes(evaluationTask.status) && <strong>{evaluationTask.progress}%</strong>}{evaluationTask?.status === "finished" && <strong className="success">{String(evaluationTask.result?.passed_cases ?? "—")}/{String(evaluationTask.result?.evaluated_cases ?? "—")} 通过</strong>}<button type="button" className="background-task-button" disabled={user?.role !== "admin" || Boolean(evaluationTask && !["finished", "failed"].includes(evaluationTask.status))} onClick={runEvaluation}>{evaluationTask && !["finished", "failed"].includes(evaluationTask.status) ? <LoaderCircle className="spin" size={16} /> : <FlaskConical size={16} />}运行离线规则评测</button></div>{evaluationTask && !["finished", "failed"].includes(evaluationTask.status) && <div className="async-evaluation-progress"><i style={{ width: `${evaluationTask.progress}%` }} /></div>}</section>

      <section className="feature-metrics">
        <AnalyticsMetric icon={Bot} label="Agent 分析覆盖率" value={`${coverage}%`} detail={`${analyzed.length}/${tickets.length} 张工单已分析`} tone="purple" />
        <AnalyticsMetric icon={CheckCircle2} label="当前解决率" value={`${resolutionRate}%`} detail={`${resolved.length} 张工单已解决`} tone="teal" />
        <AnalyticsMetric icon={Gauge} label="平均置信度" value={`${averageConfidence}%`} detail="基于已分析工单" tone="blue" />
        <AnalyticsMetric icon={ShieldCheck} label="等待人工审批" value={String(data.pending_approvals)} detail="高风险动作受门控" tone="orange" />
      </section>

      {performance && <section className="feature-metrics">
        <AnalyticsMetric icon={Gauge} label="API 响应时间" value={`${performance.http.p50_ms} ms`} detail={`P95 ${performance.http.p95_ms} ms · ${performance.http.samples} 个样本`} tone="blue" />
        <AnalyticsMetric icon={Zap} label="检索缓存命中率" value={`${Math.round(performance.retrieval_cache.hit_rate * 100)}%`} detail={`${performance.retrieval_cache.hits} 次命中 / ${performance.retrieval_cache.misses} 次未命中`} tone="teal" />
        <AnalyticsMetric icon={Bot} label="记录平均 Token" value={String(Math.round(performance.models.average_tokens_per_call))} detail={`基于 ${performance.models.ledger_calls} 次有台账调用`} tone="purple" />
        <AnalyticsMetric icon={Activity} label="平均估算成本（非账单）" value={performance.models.recorded_average_cost_per_call_usd == null ? "暂无完整费用记录" : `$${performance.models.recorded_average_cost_per_call_usd.toFixed(6)}`} detail={`基于 ${performance.models.complete_cost_calls ?? 0} 次完整记录 · 合计 $${performance.models.known_estimated_cost_usd.toFixed(6)}`} tone="orange" className="recorded-cost-metric" />
        <AnalyticsMetric icon={Gauge} label="Agent 端到端耗时" value={`${(performance.workflow.p50_ms / 1000).toFixed(2)} 秒`} detail={`含排队 · P95 ${(performance.workflow.p95_ms / 1000).toFixed(2)} 秒 · ${performance.workflow.failed_runs} 次失败 · 不含人工等待`} tone="blue" />
        <AnalyticsMetric icon={CheckCircle2} label="客户确认解决周期" value={performance.workflow.customer_resolution_cycle.samples ? `${(performance.workflow.customer_resolution_cycle.p50_ms / 60000).toFixed(1)} 分钟` : "暂无客户确认样本"} detail={`从提交到客户确认 · 含人工等待 · ${performance.workflow.customer_resolution_cycle.samples} 个已确认样本`} tone="teal" />
      </section>}

      <section className="analytics-grid">
        <article className="panel feature-panel analytics-trend">
          <div className="feature-panel-heading compact"><div><h2>Agent 与人工处理趋势</h2></div><div className="legend"><span><i className="dot automated" />Agent</span><span><i className="dot manual" />人工</span></div></div>
          <div className="analytics-bars">
            {data.trend.map((item) => <div className="analytics-column" key={item.day}>
              <div className="analytics-values"><span>{item.automated}</span><small>{item.manual}</small></div>
              <div className="analytics-bar-stack"><i className={`agent ${item.automated === 0 ? "empty" : ""}`} style={{ height: `${item.automated / maxTrend * 190}px` }} /><i className={`human ${item.manual === 0 ? "empty" : ""}`} style={{ height: `${item.manual / maxTrend * 190}px` }} /></div>
              <strong>{item.day}</strong>
            </div>)}
          </div>
        </article>

        <article className="panel feature-panel status-analysis">
          <div className="feature-panel-heading compact"><div><h2>工单状态分布</h2></div></div>
          <div className="status-distribution">
            {statusDistribution.map((item) => {
              const percentage = tickets.length ? Math.round(item.count / tickets.length * 100) : 0;
              return <div className="status-progress" key={item.status}><div><span>{statusLabel(item.status)}</span><strong>{item.count} 单 · {percentage}%</strong></div><div><i className={item.status} style={{ width: `${percentage}%` }} /></div></div>;
            })}
          </div>
        </article>
      </section>

      <section className="analytics-grid lower">
        <article className="panel feature-panel category-analysis">
          <div className="feature-panel-heading compact"><div><h2>问题类型分布</h2></div></div>
          <div className="analytics-category-list">
            {data.categories.map((item) => <div key={item.name}><span><i style={{ background: item.color }} />{item.name}</span><strong>{item.value}%</strong><div><i style={{ width: `${item.value}%`, background: item.color }} /></div></div>)}
          </div>
        </article>

        <article className="panel feature-panel confidence-panel">
          <div className="feature-panel-heading compact"><div><h2>最近工单置信度</h2></div><Sparkles size={19} /></div>
          <div className="confidence-list">
            {analyzed.slice(0, 5).map((ticket) => <div key={ticket.id}><span><strong>{ticket.ticket_no}</strong><small>{categoryLabel(ticket.category ?? "other")}</small></span><div><i style={{ width: `${Math.round((ticket.confidence ?? 0) * 100)}%` }} /></div><strong>{Math.round((ticket.confidence ?? 0) * 100)}%</strong></div>)}
            {analyzed.length === 0 && <div className="feature-empty"><Activity size={23} /><strong>暂无已分析工单</strong><span>运行 Agent 后即可看到质量指标。</span></div>}
          </div>
        </article>
      </section>
    </div>
  );
}

function AnalyticsMetric({ icon: Icon, label, value, detail, tone, className = "" }: { icon: typeof Activity; label: string; value: string; detail: string; tone: string; className?: string }) {
  return <article className={`feature-metric panel ${className}`}><div className={`feature-metric-icon ${tone}`}><Icon size={19} /></div><div><span>{label}</span><strong title={value}>{value}</strong><small title={detail}>{detail}</small></div></article>;
}
