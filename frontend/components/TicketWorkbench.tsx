"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  FileText,
  LoaderCircle,
  MessageSquareText,
  PackageCheck,
  RefreshCw,
  Send,
  ShieldAlert,
  Sparkles,
  Star,
  ThumbsDown,
  ThumbsUp,
  UserRound,
  X,
} from "lucide-react";
import { api, watchBackgroundTask } from "@/lib/api";
import { serviceDisplayText } from "@/lib/presentation";
import { useAuth } from "@/components/AuthProvider";
import type { BackgroundTask, TicketDetail } from "@/lib/types";
import { categoryLabel, priorityLabel, statusLabel } from "@/components/Dashboard";


export function TicketWorkbench() {
  const { user } = useAuth();
  const canHandle = user?.role === "admin";
  const canReview = user?.role === "admin";
  const params = useParams<{ id: string }>();
  const pathname = usePathname();
  const listPath = pathname.startsWith("/chats/") ? "/chats" : "/tickets";
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<"analyze" | "approve" | "reject" | "rewrite" | "send" | "refund" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [composerText, setComposerText] = useState("");
  const [backgroundTask, setBackgroundTask] = useState<BackgroundTask | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(() => {
    api.ticket(params.id).then(setTicket).catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, [params.id]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function analyze() {
    setAction("analyze"); setError(""); setNotice("");
    try {
      const queued = await api.startAgentAnalysis(params.id);
      setBackgroundTask(queued);
      await watchBackgroundTask(queued.id, setBackgroundTask);
      setTicket(await api.ticket(params.id));
      setComposerText("");
      setNotice("Agent 后台分析已完成，结果和审计轨迹已写入数据库。");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setAction(null);
    }
  }

  async function decide(decision: "approve" | "reject") {
    setAction(decision); setError(""); setNotice("");
    try { setTicket(await api.decide(params.id, decision, decision === "approve" ? "方案合理，同意执行" : "需要人工进一步核验")); } catch (reason) { setError((reason as Error).message); } finally { setAction(null); }
  }

  async function executeRefund() {
    if (!ticket?.approval || ticket.approval.state !== "approved" || !ticket.order_id || !ticket.approval.amount) return;
    setAction("refund"); setError(""); setNotice("");
    try {
      const result = await api.executeApprovedRefund(ticket.id, ticket.approval.id, ticket.order_id, ticket.approval.amount);
      if (!result.result.success || result.result.mode !== "simulated") throw new Error("退款工具未返回有效的执行结果");
      setTicket(await api.ticket(ticket.id));
      setNotice(result.replayed ? "该退款已执行，本次为幂等回放，未重复执行。" : "退款操作已完成并写入审计记录，不涉及真实资金。您可改写回复后发送。");
    } catch (reason) { setError((reason as Error).message); } finally { setAction(null); }
  }

  function adoptDraft() {
    if (!ticket?.draft_response) return;
    setComposerText(serviceDisplayText(ticket.draft_response, true));
    setNotice("AI 草稿已放入编辑框，您可以修改后发送。");
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  async function rewriteDraft() {
    setAction("rewrite"); setError(""); setNotice("");
    try {
      setTicket(await api.rewriteDraft(params.id));
      setComposerText("");
      setNotice("已生成一版新的 AI 回复草稿。");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setAction(null);
    }
  }

  async function sendResponse() {
    const response = composerText.trim();
    if (!response) {
      setError("请先采用草稿或输入回复内容");
      composerRef.current?.focus();
      return;
    }
    setAction("send"); setError(""); setNotice("");
    try {
      setTicket(await api.sendResponse(params.id, response));
      setComposerText("");
      setNotice("回复已发送，并写入审计记录。");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setAction(null);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void sendResponse();
    }
  }

  if (loading) return <div className="center-state"><LoaderCircle className="spin" /><strong>正在载入工单工作台</strong></div>;
  if (!ticket) return <div className="center-state error-state"><ShieldAlert /><strong>无法打开对话</strong><span>{error}</span><Link href={listPath}>返回列表</Link></div>;
  const analyzed = Boolean(ticket.assessment?.confidence_breakdown);
  const confidenceBreakdown = ticket.assessment?.confidence_breakdown;
  const feedbacks = ticket.customer_feedbacks ?? [];

  return <div className="workbench-page">
    <header className="workbench-header">
      <div className="workbench-title">
        <Link href={listPath} className="back-button" aria-label="返回列表"><ArrowLeft size={18} /></Link>
        <div><div className="title-line"><span>{ticket.ticket_no}</span><h1>{ticket.subject}</h1><span className={`status-badge ${ticket.status}`}>{statusLabel(ticket.status)}</span></div><p>创建于 {formatDate(ticket.created_at)} · SLA 剩余 {ticket.sla_minutes} 分钟</p></div>
      </div>
      <div className="workbench-actions">
        <button className="outline-button" onClick={() => { setLoading(true); load(); }}><RefreshCw size={16} />刷新</button>
        <button className="primary-button" onClick={analyze} disabled={Boolean(action) || !canHandle}>{action === "analyze" ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}<span>{action === "analyze" ? `${backgroundTask?.progress ?? 0}%` : (analyzed ? "重新运行 Agent" : "运行 Agent 分析")}</span></button>
      </div>
    </header>
    {action === "analyze" && backgroundTask && <div className="background-task-progress"><div><strong>{backgroundTask.message}</strong><span>{backgroundTask.progress}%</span></div><div><i style={{ width: `${backgroundTask.progress}%` }} /></div></div>}
    {error && <div className="toast-error"><ShieldAlert size={17} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
    {notice && <div className="toast-success"><CheckCircle2 size={17} />{notice}<button onClick={() => setNotice("")}><X size={15} /></button></div>}

    <div className="workbench-grid">
      <aside className="workbench-sidebar">
        <section className="detail-card customer-profile">
          <div className="large-avatar">{ticket.customer_name.slice(0, 1)}<span /></div>
          <h2>{ticket.customer_name}</h2><p>{ticket.customer_tier} 客户</p>
          <div className="profile-stats"><div><span>渠道</span><strong>{ticket.channel}</strong></div><div><span>历史工单</span><strong>3</strong></div></div>
        </section>
        <section className="detail-card info-list">
          <div className="card-title"><PackageCheck size={17} /><h3>关联信息</h3></div>
          <dl><div><dt>订单编号</dt><dd>{ticket.order_id ?? "—"}</dd></div><div><dt>订单金额</dt><dd>{ticket.amount ? `¥${ticket.amount.toFixed(2)}` : "—"}</dd></div><div><dt>问题分类</dt><dd>{ticket.category ? categoryLabel(ticket.category) : "待分析"}</dd></div><div><dt>优先级</dt><dd><i className={`priority-dot ${ticket.priority}`} />{priorityLabel(ticket.priority)}</dd></div><div><dt>用户情绪</dt><dd>{sentimentLabel(ticket.sentiment)}</dd></div></dl>
        </section>
        {ticket.citations.length > 0 && <section className="detail-card citations-card" id="knowledge"><div className="card-title"><FileText size={17} /><h3>RAG 引用依据</h3><span>{ticket.citations.length}</span></div>{ticket.citations.map((citation) => <div className="citation" key={citation.id}><div><strong>{citation.title}</strong><span>{Math.round(citation.score * 100)}% 匹配</span></div><p>{citation.snippet}</p><small>{citation.source}</small></div>)}</section>}
      </aside>

      <main className="conversation-panel">
        <div className="conversation-heading"><div><h2>对话与处置</h2><span>Agent 已读取完整上下文</span></div><span className="channel-chip">{ticket.channel.toUpperCase()}</span></div>
        <div className="message-stream">
          <div className="message-time">今天 · {new Date(ticket.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</div>
          {ticket.conversation.map((message) => message.role === "customer" ? <div className="message customer-message" key={message.id}><div className="message-avatar"><UserRound size={17} /></div><div><div className="message-meta"><strong>{ticket.customer_name}</strong><span>客户 · {new Date(message.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span></div><div className="bubble">{message.content}</div></div></div> : <div className="message agent-message sent-message" key={message.id}><div className="message-avatar"><CheckCircle2 size={17} /></div><div><div className="message-meta"><strong>ResolveAI</strong><span>已发送 · {new Date(message.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span></div><div className="bubble"><span className="sent-tag"><CheckCircle2 size={13} />已发送回复</span><p>{serviceDisplayText(message.content, true)}</p></div></div></div>)}
          {ticket.draft_response && <div className="message agent-message"><div className="message-avatar"><Bot size={17} /></div><div><div className="message-meta"><strong>ResolveAI</strong><span>回复草稿 · 等待发送</span></div><div className="bubble"><span className="draft-tag"><Sparkles size={13} />AI 草稿</span><p>{serviceDisplayText(ticket.draft_response, true)}</p><div className="draft-actions"><button type="button" onClick={adoptDraft} disabled={Boolean(action) || !canHandle}><ThumbsUp size={14} />{composerText === serviceDisplayText(ticket.draft_response, true) ? "已采用" : "采用"}</button><button type="button" onClick={rewriteDraft} disabled={Boolean(action) || !canHandle}>{action === "rewrite" ? <LoaderCircle className="spin" size={14} /> : <ThumbsDown size={14} />}改写</button></div></div></div></div>}
          {!analyzed && <div className="analysis-placeholder"><div className="placeholder-orb"><Sparkles size={24} /></div><strong>让 Agent 分析这张工单</strong><p>系统将并行查询订单与知识库，生成可追溯的处置建议。</p><button className="primary-button" onClick={analyze} disabled={Boolean(action) || !canHandle}>{action === "analyze" ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{action === "analyze" ? `${backgroundTask?.progress ?? 0}%` : "开始分析"}</button></div>}
          <section className="workbench-feedback" aria-label="客户反馈">
            <header><MessageSquareText size={17} /><h3>客户反馈</h3><span>{feedbacks.length} 条</span></header>
            {feedbacks.length ? <ol>{[...feedbacks].reverse().map(feedback => <li className="workbench-feedback-item" key={feedback.id}>
              <div className="feedback-meta"><span className={`feedback-outcome ${feedback.resolved === true ? "resolved" : feedback.resolved === false ? "unresolved" : "unknown"}`}>{feedback.resolved === true ? "问题已解决" : feedback.resolved === false ? "仍需帮助" : "处理结果未记录"}</span><span className="feedback-rating"><Star size={13} />{feedback.rating == null ? "未评分" : `${feedback.rating} / 5 分`}</span>{feedback.created_at ? <time dateTime={feedback.created_at}>{formatDate(feedback.created_at)}</time> : <span>时间未记录</span>}</div>
              <p className={feedback.feedback ? "feedback-comment" : "feedback-comment empty"}>{feedback.feedback || "客户未填写留言"}</p>
            </li>)}</ol> : <p className="feedback-empty">暂无客户反馈，客户提交评价后会在这里显示。</p>}
          </section>
        </div>
        <div className="composer"><textarea ref={composerRef} aria-label="回复内容" value={composerText} onChange={(event) => setComposerText(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder={ticket.draft_response ? "点击“采用”带入草稿，或直接输入回复…" : "输入回复…"} rows={1} disabled={!canHandle} /><span className="composer-hint">Ctrl + Enter 发送</span><button type="button" className="send-button" aria-label="发送回复" onClick={sendResponse} disabled={Boolean(action) || !canHandle || !composerText.trim()}>{action === "send" ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button></div>
      </main>

      <aside className="agent-inspector">
        <section className="inspector-hero">
          <div className="inspector-title"><div className="ai-orb"><Sparkles size={19} /></div><div><h2>Agent 决策面板</h2></div>{analyzed && <span className="analysis-done"><Check size={13} />已完成</span>}</div>
          {analyzed ? <><div className="confidence-row"><span>综合置信度</span><strong>{Math.round((ticket.confidence ?? 0) * 100)}%</strong></div><div className="confidence-bar"><i style={{ width: `${(ticket.confidence ?? 0) * 100}%` }} /></div>{confidenceBreakdown ? <details className="confidence-details"><summary>查看评分依据</summary><div className="confidence-components">{confidenceBreakdown.components.map((component) => <div key={component.key}><span><strong>{component.label}</strong><small>{component.evidence}</small></span><i>{Math.round(component.score * 100)}% × {Math.round(component.weight * 100)}%</i></div>)}</div>{confidenceBreakdown.penalties.length > 0 && <div className="confidence-penalties">{confidenceBreakdown.penalties.map((penalty) => <p key={penalty.key}>-{Math.round(penalty.value * 100)} 分 · {penalty.label}：{penalty.reason}</p>)}</div>}</details> : <p className="legacy-confidence">旧版评分；重新运行 Agent 后可查看五项评分依据。</p>}</> : <p className="inspector-empty">运行工作流后，这里会展示 Agent 的判断依据、协作轨迹和审批建议。</p>}
        </section>

        {ticket.recommended_action && <section className="inspector-section"><div className="section-label"><CircleDollarSign size={15} />建议动作</div><div className="recommendation"><strong>{serviceDisplayText(ticket.recommended_action)}</strong><p>{serviceDisplayText(ticket.assessment?.rationale)}</p>{ticket.assessment?.refund_amount ? <span>涉及金额 ¥{ticket.assessment.refund_amount}</span> : null}</div></section>}

        {ticket.approval && <section className={`approval-box ${ticket.approval.state}`}><div className="approval-box-head"><ShieldAlert size={19} /><div><strong>{approvalTitle(ticket.approval.state)}</strong></div></div><p>{ticket.approval.risk_reason}</p>{ticket.approval.state === "pending" && <div className="decision-buttons"><button className="approve-button" disabled={Boolean(action) || !canReview} onClick={() => decide("approve")}>{action === "approve" ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}批准执行</button><button className="reject-button" disabled={Boolean(action) || !canReview} onClick={() => decide("reject")}><X size={15} />驳回</button></div>}{ticket.approval.state !== "pending" && <div className="review-result"><CheckCircle2 size={15} />{ticket.approval.reviewed_by} · {ticket.approval.reviewer_note}</div>}</section>}
        {ticket.approval?.state === "approved" && ticket.approval.amount && ticket.order_id && ticket.approval.action.includes("退款") ? <section className="approval-box approved"><strong>退款执行</strong><p>仅执行已批准的退款，不涉及真实资金；重复请求由幂等键保护。</p><button type="button" className="approve-button" onClick={() => void executeRefund()} disabled={Boolean(action) || !canReview || ticket.audit_events.some((event) => event.details?.tool_name === "execute_refund" && event.status === "completed")}>{action === "refund" ? "正在执行…" : ticket.audit_events.some((event) => event.details?.tool_name === "execute_refund" && event.status === "completed") ? "退款已执行" : "执行退款"}</button></section> : null}

        {ticket.audit_events.length > 0 && <section className="inspector-section timeline-section"><div className="section-label"><Clock3 size={15} />Agent 协作轨迹 <span>{ticket.audit_events.length} 步</span></div><div className="agent-timeline">{ticket.audit_events.map((event, index) => <div className="timeline-item" key={event.id}><div className="timeline-rail"><i className={event.agent === "人工审批" ? "human" : ""}>{event.agent === "人工审批" ? <UserRound size={12} /> : <Check size={12} />}</i>{index < ticket.audit_events.length - 1 && <span />}</div><div><div><strong>{event.agent}</strong><small>{event.duration_ms}ms</small></div><p>{serviceDisplayText(event.summary)}</p><span>{serviceDisplayText(event.action)}</span></div></div>)}</div></section>}

        {ticket.assessment?.quality && <section className="quality-strip"><CheckCircle2 size={16} /><div><strong>{ticket.assessment.quality.passed ? "质检通过" : "需人工复核"}</strong><span>{ticket.assessment.quality.passed ? "自动规则校验通过；语义质量仍需人工复核" : "部分检查未通过，请查看运行轨迹和失败原因"}</span></div><ChevronRight size={16} /></section>}
      </aside>
    </div>
  </div>;
}

const sentimentLabel = (value?: string | null) => ({ positive: "积极", neutral: "中性", negative: "负面", angry: "愤怒" }[value ?? ""] ?? "待分析");
const approvalTitle = (value: string) => ({ pending: "等待人工审批", approved: "方案已批准", rejected: "方案已驳回" }[value] ?? value);
const formatDate = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
