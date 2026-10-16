"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Clock3, GitBranch, Headset, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { QuestionRoute } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { CommerceEmpty, CommerceStats } from "@/components/CommerceUI";
import { formatBusinessTime } from "@/lib/time";
import { serviceDisplayText } from "@/lib/presentation";

const outcomes: Record<string, string> = { processing: "处理中", replied: "已自动回复", handed_off: "已转人工", waiting_human: "等待人工回复", failed_to_human: "失败后转人工", human_replied: "人工已回复" };
export function QuestionRoutes() {
  const [rows, setRows] = useState<QuestionRoute[]>([]);
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const reload = () => api.questionRoutes(activeQuery, offset).then(data => { if (active) { setRows(data); setError(""); } }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    void reload(); const timer = setInterval(reload, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [activeQuery, offset]);
  function search(e: FormEvent) { e.preventDefault(); setLoading(true); setOffset(0); setActiveQuery(query.trim()); if (offset === 0 && query.trim() === activeQuery) setLoading(false); }
  return <div className="page-wrap feature-page question-routes-page">
    <PageHeader title="提问与路由记录" description="从一次提问，到每一步处理，让服务过程清晰可追溯。" />
    <CommerceStats loading={loading} items={[
      { label: "本页提问", value: rows.length, note: "当前查询结果，每页最多 100 条", icon: GitBranch },
      { label: "本页自动回复", value: rows.filter(r => r.outcome === "replied").length, note: "仅统计当前页面记录", icon: CheckCircle2, tone: "teal" },
      { label: "本页转人工", value: rows.filter(r => r.handed_off).length, note: "包括等待与已完成人工回复", icon: Headset, tone: "orange" },
      { label: "本页平均耗时", value: rows.length ? `${(rows.reduce((sum, r) => sum + r.total_ms, 0) / rows.length / 1000).toFixed(2)} 秒` : "—", note: "不含等待人工回复时间", icon: Clock3, tone: "blue" },
    ]} />
    <section className="panel commerce-control-panel"><div className="commerce-control-top"><div><h2>请求记录</h2><p>按问题内容或工单编号查找完整处理链路</p></div><span className="live-caption"><i />每 5 秒同步</span></div><form className="commerce-filters" onSubmit={search}><label className="commerce-search-field"><Search size={18} /><input aria-label="搜索问题或工单编号" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索问题或工单编号" /></label><button className="commerce-button" type="submit">查询<ArrowRight size={16} /></button></form></section>
    <p className="route-counting-note"><Activity size={16} />API 次数统计外部模型与 MCP HTTP 请求（含重试），不包含数据库查询、离线规则和浏览器轮询。</p>
    {error && <p className="customer-error" role="alert">{error}</p>}
    {loading && <CommerceEmpty title="正在同步路由记录" loading />}
    {!loading && rows.map(r => <article className="panel route-card" key={r.id}>
      <header><div className="route-request-identity"><span className="route-request-icon"><GitBranch size={17} /></span><Link href={`/chats/${r.ticket_id}`}>{r.ticket_no}</Link><span>{formatBusinessTime(r.request_time)}</span></div><strong className={`commerce-status ${r.handed_off ? "overdue" : r.outcome === "processing" ? "in_transit" : "signed"}`}><i />{outcomes[r.outcome] ?? r.outcome}</strong></header>
      <div className="route-question"><span>客户提问</span><h2>{r.question}</h2></div>
      <div className="route-metrics"><span>API 请求 <b>{r.api_requests}<small> 次</small></b><small>模型 {r.model_requests} · 工具 {r.tool_requests}</small></span><span>总处理耗时 <b>{(r.total_ms / 1000).toFixed(2)}<small> 秒</small></b><small>不含等待人工</small></span><span>降级处理 <b className={r.degraded ? "metric-warning" : "metric-ok"}>{r.degraded ? "已发生" : "未发生"}</b></span><span>转人工 <b className={r.handed_off ? "metric-warning" : "metric-ok"}>{r.handed_off ? "已转交" : "未转交"}</b></span></div>
      {r.handoff_reasons.length > 0 && <p className="route-handoff-reason"><Headset size={16} />{r.handoff_reasons.join("；")}</p>}{r.error && <p className="customer-error">{r.error}</p>}
      <details><summary>查看逐步耗时与路由<span>{r.steps.length} 个处理步骤</span></summary><div className="route-request-meta"><span>请求标识：{r.id}</span><span>消息标识：{r.message_id}</span><span>路由：{r.route || "尚未生成"}</span></div><ol className="route-steps">{r.steps.map((s, i) => <li key={i}><div className="route-step-index">{s.status === "completed" ? <CheckCircle2 size={17} /> : i + 1}</div><div><div className="route-step-heading"><strong>{s.agent}</strong><b>{s.duration_ms.toFixed(0)} ms</b></div><span>{serviceDisplayText(s.action)} · {s.status === "completed" ? "完成" : s.status === "failed" ? "失败" : "降级"}</span><p>{serviceDisplayText(s.summary)}</p></div></li>)}</ol></details>
    </article>)}
    {!loading && !rows.length && <CommerceEmpty title={activeQuery ? "没有找到匹配的记录" : "还没有提问记录"} description={activeQuery ? "换个问题关键词或工单编号再试试。" : "客户提交问题后，处理路径和耗时会自动记录在这里。"} />}
    <div className="commerce-pagination"><button disabled={offset === 0 || loading} onClick={() => { setLoading(true); setOffset(Math.max(0, offset - 100)); }}>上一页</button><span>第 {offset / 100 + 1} 页</span><button disabled={rows.length < 100 || loading} onClick={() => { setLoading(true); setOffset(offset + 100); }}>下一页</button></div>
  </div>;
}
