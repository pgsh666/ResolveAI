"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  GitBranch,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
  TicketCheck,
  UsersRound,
  Zap,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";
import { serviceDisplayText } from "@/lib/presentation";
import type { AgentRun, AgentTopology, HealthData } from "@/lib/types";


const agentMeta: Record<string, { description: string; icon: typeof Bot; tone: string }> = {
  "分诊 Agent": { description: "识别意图、情绪与优先级", icon: Bot, tone: "purple" },
  "知识检索 Agent": { description: "RAG 向量召回政策依据", icon: DatabaseZap, tone: "teal" },
  "客户上下文 Agent": { description: "通过 MCP 查询订单与客户", icon: UsersRound, tone: "blue" },
  "政策判断 Agent": { description: "校验退款额度与风险规则", icon: ShieldCheck, tone: "orange" },
  "方案生成 Agent": { description: "生成动作建议与回复草稿", icon: Sparkles, tone: "purple" },
  "质检 Agent": { description: "检查引用、话术和审批规则", icon: CheckCircle2, tone: "teal" },
  "审批门控 Agent": { description: "决定自动执行或人工审批", icon: GitBranch, tone: "orange" },
};

export function AgentOperations() {
  const [topology, setTopology] = useState<AgentTopology | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.agentTopology(), api.agentRuns(), api.health()])
      .then(([graph, recentRuns, healthData]) => {
        setTopology(graph);
        setRuns(recentRuns);
        setHealth(healthData);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const agents = useMemo(() => {
    if (!topology) return [];
    return Array.from(new Set(topology.edges.flatMap((edge) => [edge.from, edge.to])))
      .filter((name) => name !== "START");
  }, [topology]);
  const uniqueTickets = new Set(runs.map((run) => run.ticket_id)).size;
  const averageDuration = runs.length
    ? Math.round(runs.reduce((total, run) => total + run.duration_ms, 0) / runs.length)
    : 0;

  if (error) return <div className="center-state error-state"><Zap size={26} /><strong>无法读取 Agent 运行数据</strong><span>{error}</span><code>请重启 FastAPI 后端以加载新增接口</code></div>;
  if (!topology || !health) return <div className="center-state"><LoaderCircle className="spin" size={26} /><strong>正在读取 Agent 运行状态</strong></div>;

  return (
    <div className="page-wrap feature-page">
      <PageHeader title="Agent 运行" description="查看多 Agent 编排拓扑、模型状态与每一步执行轨迹。" />

      <section className="feature-metrics">
        <MetricCard icon={Activity} label="运行事件" value={String(runs.length)} detail="最近 30 条审计记录" tone="purple" />
        <MetricCard icon={TicketCheck} label="覆盖工单" value={String(uniqueTickets)} detail="已有 Agent 执行轨迹" tone="teal" />
        <MetricCard icon={Clock3} label="平均节点耗时" value={`${averageDuration}ms`} detail="基于当前审计样本" tone="orange" />
        <MetricCard icon={Bot} label="当前模型" value={health.model} detail={`${health.agent_mode} · ${health.mcp_enabled ? "MCP 开启" : "MCP 关闭"}`} tone="blue" />
      </section>

      <section className="panel feature-panel">
        <div className="feature-panel-heading">
          <div><h2>多 Agent 编排拓扑</h2><p>分诊后并行获取知识和客户上下文，再汇入政策判断与审批门控。</p></div>
          <span className="live-pill"><i />工作流可用</span>
        </div>
        <div className="agent-topology-grid">
          {agents.map((name, index) => {
            const meta = agentMeta[name] ?? { description: "工作流处理节点", icon: Bot, tone: "blue" };
            const Icon = meta.icon;
            const incoming = topology.edges.filter((edge) => edge.to === name).map((edge) => edge.from).filter((source) => source !== "START");
            return (
              <article className="topology-card" key={name}>
                <div className={`topology-icon ${meta.tone}`}><Icon size={19} /></div>
                <div><span>节点 {String(index + 1).padStart(2, "0")}</span><strong>{name}</strong><p>{meta.description}</p></div>
                <small>{incoming.length ? `上游：${incoming.join("、")}` : "工作流入口"}</small>
              </article>
            );
          })}
        </div>
      </section>

      <section className="panel feature-panel run-table-panel">
        <div className="feature-panel-heading compact">
          <div><h2>最近运行记录</h2></div>
          <span className="data-source-pill">数据库实时数据</span>
        </div>
        <div className="run-table">
          <div className="run-row run-head"><span>时间 / 工单</span><span>Agent 节点</span><span>执行动作</span><span>耗时</span><span>状态</span></div>
          {runs.map((run) => (
            <Link className="run-row" href={`/tickets/${run.ticket_id}`} key={run.id}>
              <span><strong>{formatTime(run.created_at)}</strong><small>{run.ticket_no}</small></span>
              <span>{run.agent}</span>
              <span><strong>{serviceDisplayText(run.action)}</strong><small>{serviceDisplayText(run.summary)}</small></span>
              <span>{run.duration_ms}ms</span>
              <span className="run-status"><CheckCircle2 size={13} />{run.status === "completed" ? "完成" : run.status}</span>
            </Link>
          ))}
          {runs.length === 0 && <div className="feature-empty"><Activity size={23} /><strong>还没有运行记录</strong><span>进入工单中心运行一次 Agent 分析后，这里会展示完整轨迹。</span><Link href="/tickets">前往工单中心</Link></div>}
        </div>
      </section>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, detail, tone }: { icon: typeof Bot; label: string; value: string; detail: string; tone: string }) {
  return <article className="feature-metric panel"><div className={`feature-metric-icon ${tone}`}><Icon size={19} /></div><div><span>{label}</span><strong title={value}>{value}</strong><small>{detail}</small></div></article>;
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
