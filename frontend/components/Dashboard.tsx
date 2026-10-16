"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Bot,
  Check,
  Clock3,
  FileSearch,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { DashboardData, TicketSummary } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";


const metricIcons = [Zap, Sparkles, Clock3, ShieldCheck];
const agentSteps = [
  { name: "分诊 Agent", detail: "意图、情绪与优先级", icon: Bot, tone: "purple" },
  { name: "知识检索", detail: "RAG · pgvector", icon: FileSearch, tone: "teal" },
  { name: "政策判断", detail: "风险与合规规则", icon: ShieldCheck, tone: "orange" },
  { name: "方案生成", detail: "回复与执行动作", icon: Sparkles, tone: "blue" },
];

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.dashboard(), api.tickets()])
      .then(([dashboard, ticketList]) => {
        setData(dashboard);
        setTickets(ticketList);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  if (error) return <ConnectionError message={error} />;
  if (!data) return <LoadingScreen />;
  const maxTrend = Math.max(...data.trend.map((item) => item.automated + item.manual), 1);
  const donutBackground = buildDonutGradient(data.categories);

  return (
    <div className="page-wrap">
      <PageHeader title="智能客服运营台" description="实时掌握 Agent 处理效率、服务质量与待办风险。" />

      <section className="metric-grid">
        {data.metrics.map((metric, index) => {
          const Icon = metricIcons[index];
          return (
            <article className="metric-card" key={metric.label}>
              <div className={`metric-icon metric-${index}`}><Icon size={19} /></div>
              <div className="metric-content">
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </div>
              <span className={`metric-change ${metric.tone}`}>{metric.change}</span>
            </article>
          );
        })}
      </section>

      <section className="dashboard-grid primary-grid" id="analytics">
        <article className="panel chart-panel">
          <div className="panel-heading">
            <div><h2>对话处理趋势</h2></div>
            <div className="legend"><span><i className="dot automated" />Agent 自动处理</span><span><i className="dot manual" />人工处理</span></div>
          </div>
          <div className="bar-chart">
            {data.trend.map((item) => (
              <div className="bar-column" key={item.day}>
                <div className="bar-stack" title={`${item.day}：${item.automated + item.manual} 单`}>
                  <span className={`bar automated ${item.automated === 0 ? "empty" : ""}`} style={{ height: `${(item.automated / maxTrend) * 185}px` }} />
                  <span className={`bar manual ${item.manual === 0 ? "empty" : ""}`} style={{ height: `${(item.manual / maxTrend) * 185}px` }} />
                </div>
                <span>{item.day}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel category-panel">
          <div className="panel-heading"><div><h2>问题分布</h2></div><button className="ghost-link">本周</button></div>
          <div className="donut-wrap">
            <div className="donut" style={{ background: donutBackground }}><div><strong>{tickets.length}</strong><span>总对话</span></div></div>
            <div className="category-list">
              {data.categories.map((item) => <div key={item.name}><span><i style={{ background: item.color }} />{item.name}</span><strong>{item.value}%</strong></div>)}
            </div>
          </div>
        </article>
      </section>

      <section className="dashboard-grid secondary-grid">
        <article className="panel" id="agents">
          <div className="panel-heading">
            <div><h2>多 Agent 协作链路</h2></div>
            <span className="live-pill"><i />运行正常</span>
          </div>
          <div className="agent-flow">
            {agentSteps.map((agent, index) => {
              const Icon = agent.icon;
              return <div className="agent-step-wrap" key={agent.name}>
                <div className="agent-step"><div className={`agent-icon ${agent.tone}`}><Icon size={18} /></div><div><strong>{agent.name}</strong><span>{agent.detail}</span></div><Check size={15} className="step-check" /></div>
                {index < agentSteps.length - 1 && <div className="flow-line"><span /></div>}
              </div>;
            })}
          </div>
          <div className="flow-footer"><Workflow size={17} /><span>LangGraph 状态编排</span><b>·</b><span>并行检索与上下文查询</span><b>·</b><span>人工审批门控</span></div>
        </article>

        <article className="panel approval-highlight">
          <div className="approval-orb"><ShieldCheck size={26} /></div>
          <h2>{data.pending_approvals > 0 ? <>{data.pending_approvals} 个高风险动作<br />等待您的确认</> : <>当前没有高风险动作<br />等待人工确认</>}</h2>
          <p>退款、补偿等资金操作必须经过人工审批，所有决策都写入审计轨迹。</p>
          <Link className="primary-button light" href="/chats?status=pending_approval"><span>查看待人工对话</span><ArrowUpRight size={17} /></Link>
        </article>
      </section>

      <section className="panel ticket-preview">
        <div className="panel-heading"><div><h2>最近对话</h2></div><Link className="ghost-link" href="/chats">查看全部 <ArrowUpRight size={15} /></Link></div>
        <div className="ticket-table">
          <div className="ticket-row ticket-head"><span>工单</span><span>客户</span><span>优先级</span><span>状态</span><span>Agent 判断</span><span /></div>
          {tickets.slice(0, 4).map((ticket) => <div className="ticket-row" key={ticket.id}>
            <div><strong>{ticket.ticket_no}</strong><span>{ticket.subject}</span></div>
            <span>{ticket.customer_name}</span>
            <span><i className={`priority-dot ${ticket.priority}`} />{priorityLabel(ticket.priority)}</span>
            <span className={`status-badge ${ticket.status}`}>{statusLabel(ticket.status)}</span>
            <span>{ticket.category ? `${categoryLabel(ticket.category)} · ${Math.round((ticket.confidence ?? 0) * 100)}%` : "等待分析"}</span>
            <Link href={`/${ticket.case_type === "after_sales" ? "tickets" : "chats"}/${ticket.id}`} aria-label={`查看${ticket.ticket_no}`}><ArrowUpRight size={17} /></Link>
          </div>)}
        </div>
      </section>
    </div>
  );
}

function LoadingScreen() {
  return <div className="center-state"><LoaderCircle className="spin" size={26} /><strong>正在连接 Agent 工作台</strong><span>加载工单与运营数据…</span></div>;
}

function ConnectionError({ message }: { message: string }) {
  return <div className="center-state error-state"><Zap size={26} /><strong>后端服务尚未连接</strong><span>{message}</span><code>conda activate resolveai && uvicorn app.main:app --reload</code></div>;
}

export const statusLabel = (value: string) => ({
  new: "待处理",
  processing: "处理中",
  in_progress: "处理中",
  pending_approval: "处理中",
  awaiting_customer: "处理中",
  needs_review: "处理中",
  resolved: "已解决",
}[value] ?? value);
export const priorityLabel = (value: string) => ({ urgent: "紧急", high: "高", medium: "中", low: "低" }[value] ?? value);
export const categoryLabel = (value: string) => ({ refund: "退款", logistics: "物流", payment: "支付", account: "账号", product: "商品", other: "其他" }[value] ?? value);

function buildDonutGradient(categories: DashboardData["categories"]) {
  if (categories.length === 0) return "#eceef2";
  let start = 0;
  const stops = categories.map((item) => {
    const end = start + item.value;
    const stop = `${item.color} ${start}% ${end}%`;
    start = end;
    return stop;
  });
  if (start < 100) stops.push(`#eceef2 ${start}% 100%`);
  return `conic-gradient(${stops.join(", ")})`;
}
