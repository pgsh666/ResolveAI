"use client";

import Link from "next/link";
import Image from "next/image";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock3,
  Headphones,
  LoaderCircle,
  MessageCircleMore,
  RotateCcw,
  Send,
  Star,
  UserRound,
} from "lucide-react";
import { api } from "@/lib/api";
import { SessionActions, useAuth } from "@/components/AuthProvider";
import type { CustomerTicket, CustomerTicketCreateInput, CustomerTicketStage } from "@/lib/types";


const issueOptions = [
  "退款与售后",
  "物流进度异常",
  "支付问题",
  "商品问题",
  "账号与登录",
  "其他问题",
];

const stageCopy: Record<CustomerTicketStage, { label: string; description: string }> = {
  submitted: { label: "待处理", description: "客服平台已收到您的问题" },
  processing: { label: "处理中", description: "智能客服正在分析问题与订单信息" },
  reviewing: { label: "处理中", description: "处理方案正在进行人工确认" },
  answered: { label: "处理中", description: "客服已回复，请确认问题是否解决" },
  resolved: { label: "已解决", description: "感谢您确认本次服务结果" },
  follow_up: { label: "处理中", description: "您的反馈已转交客服继续处理" },
};

const progressSteps = ["待处理", "处理中", "已解决"];

function stageIndex(stage: CustomerTicketStage) {
  return ({ submitted: 0, processing: 1, reviewing: 1, answered: 1, resolved: 2, follow_up: 1 } as const)[stage];
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function CustomerPortal() {
  const { user } = useAuth();
  const STORAGE_KEY = `resolveai_customer_ticket_id:${user?.id ?? "anonymous"}`;
  const mascotRef = useRef<HTMLDivElement>(null);
  const [ticket, setTicket] = useState<CustomerTicket | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState<"resolved" | "follow_up" | null>(null);
  const [error, setError] = useState("");
  const [rating, setRating] = useState(5);
  const [feedback, setFeedback] = useState("");
  const [messageText, setMessageText] = useState("");
  const [sendingMessage, setSendingMessage] = useState(false);
  const [form, setForm] = useState({
    customer_name: "",
    subject: issueOptions[0],
    order_id: "",
    amount: "",
    message: "",
  });
  const ticketId = ticket?.id;
  const ticketStage = ticket?.stage;

  useEffect(() => {
    const savedId = window.localStorage.getItem(STORAGE_KEY);
    if (!savedId) {
      const timer = window.setTimeout(() => setRestoring(false), 0);
      return () => window.clearTimeout(timer);
    }
    api.customerTicket(savedId)
      .then(setTicket)
      .catch(() => window.localStorage.removeItem(STORAGE_KEY))
      .finally(() => setRestoring(false));
  }, [STORAGE_KEY]);

  useEffect(() => {
    if (!ticketId || ticketStage === "resolved") return;
    const timer = window.setInterval(() => {
      api.customerTicket(ticketId).then(setTicket).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [ticketId, ticketStage]);

  useEffect(() => {
    function followPointer(event: PointerEvent) {
      const mascot = mascotRef.current;
      if (!mascot) return;
      const bounds = mascot.getBoundingClientRect();
      const centerX = bounds.left + bounds.width / 2;
      const centerY = bounds.top + bounds.height * .58;
      const x = Math.max(-1, Math.min(1, (event.clientX - centerX) / Math.max(bounds.width * .55, 1)));
      const y = Math.max(-1, Math.min(1, (event.clientY - centerY) / Math.max(bounds.height * .65, 1)));
      mascot.style.setProperty("--look-x", `${(x * 3).toFixed(2)}px`);
      mascot.style.setProperty("--look-y", `${(y * 2).toFixed(2)}px`);
      mascot.style.setProperty("--look-rotate", `${(x * .65).toFixed(2)}deg`);
    }

    window.addEventListener("pointermove", followPointer, { passive: true });
    return () => window.removeEventListener("pointermove", followPointer);
  }, []);

  const currentStage = useMemo(() => ticket ? stageCopy[ticket.stage] : null, [ticket]);
  const lastMessage = ticket?.messages[ticket.messages.length - 1];
  const conversationRound = Math.max(Math.ceil((ticket?.messages.length ?? 1) / 2) - 1, 0);
  const mascotTop = 108 + Math.min(conversationRound, 6) * 138;

  async function submitTicket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    const payload: CustomerTicketCreateInput = {
      customer_name: form.customer_name.trim(),
      subject: form.subject,
      message: form.message.trim(),
      order_id: form.order_id.trim() || null,
      amount: form.amount ? Number(form.amount) : null,
    };
    try {
      const created = await api.createCustomerTicket(payload);
      window.localStorage.setItem(STORAGE_KEY, created.id);
      setTicket(created);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmResult(resolved: boolean) {
    if (!ticket) return;
    setConfirming(resolved ? "resolved" : "follow_up");
    setError("");
    try {
      setTicket(await api.confirmCustomerTicket(ticket.id, resolved, rating, feedback.trim()));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setConfirming(null);
    }
  }

  async function sendFollowUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ticket || !messageText.trim()) return;
    setSendingMessage(true);
    setError("");
    try {
      setTicket(await api.sendCustomerMessage(ticket.id, messageText.trim()));
      setMessageText("");
      setFeedback("");
      setRating(5);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSendingMessage(false);
    }
  }

  function startNewTicket() {
    window.localStorage.removeItem(STORAGE_KEY);
    setTicket(null);
    setRating(5);
    setFeedback("");
    setError("");
    setForm({ customer_name: form.customer_name, subject: issueOptions[0], order_id: "", amount: "", message: "" });
  }

  if (restoring) {
    return <div className="customer-loading"><LoaderCircle className="spin" size={28} /><span>正在恢复咨询记录…</span></div>;
  }

  return (
    <div className="customer-portal">
      <header className="customer-header">
        <div className="customer-brand"><div><strong>ResolveAI 在线客服</strong><small>随时为您解决问题</small></div></div>
        <SessionActions />
        {user?.role !== "customer" && <Link href="/">进入客服工作台<ChevronRight size={16} /></Link>}
      </header>

      {!ticket ? (
        <main className="customer-entry">
          <section className="customer-welcome">
            <h1><span>您好</span><span>有什么可以帮到您？</span></h1>
            <div className="customer-mascot" ref={mascotRef} aria-hidden="true">
              <svg viewBox="0 0 1365 1136" role="presentation">
                <image href="/customer-mascot-wave.png" width="1365" height="1136" />
              </svg>
            </div>
          </section>

          <form className="customer-form" onSubmit={submitTicket}>
            <div className="customer-form-title"><div><h2>发起在线咨询</h2><p>请描述您遇到的问题</p></div><span><MessageCircleMore size={20} /></span></div>
            <label><span>您的称呼</span><input required maxLength={100} value={form.customer_name} onChange={(event) => setForm({ ...form, customer_name: event.target.value })} placeholder="例如：林晓雨" /></label>
            <label><span>问题类型</span><select value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })}>{issueOptions.map((option) => <option key={option}>{option}</option>)}</select></label>
            <div className="customer-form-row">
              <label><span>订单编号（选填）</span><input maxLength={30} value={form.order_id} onChange={(event) => setForm({ ...form, order_id: event.target.value })} placeholder="例如：ORD-8821" /></label>
              <label><span>涉及金额（选填）</span><input min="0" step="0.01" type="number" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} placeholder="0.00" /></label>
            </div>
            <label><span>问题描述</span><textarea required minLength={2} maxLength={4000} rows={5} value={form.message} onChange={(event) => setForm({ ...form, message: event.target.value })} placeholder="请尽量说明发生了什么，以及您希望如何处理…" /></label>
            {error && <div className="customer-error">{error}</div>}
            <button className="customer-submit" type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}{submitting ? "正在提交…" : "提交问题"}</button>
            <small className="customer-privacy">提交后将生成一张真实工单，信息仅用于本项目流程演示。</small>
          </form>
        </main>
      ) : (
        <main className="customer-workspace">
          <section className="customer-chat-card">
            <div className="customer-chat-mascot" style={{ top: `${mascotTop}px` }} aria-hidden="true">
              <Image src="/customer-mascot-peek-v4.png" alt="" width={1024} height={1536} />
            </div>
            <header className="customer-chat-head">
              <div><span className={`customer-stage-dot ${ticket.stage}`} /><div><strong>{currentStage?.label}</strong><small>{currentStage?.description}</small></div></div>
              <div><span>{ticket.ticket_no}</span><button type="button" onClick={startNewTicket}><RotateCcw size={14} />新咨询</button></div>
            </header>

            <div className="customer-chat-body">
              {ticket.messages.map((message, index) => (
                <div className={`customer-message-row ${message.role === "customer" ? "user" : "support"}`} key={message.id}>
                  {message.role === "customer" ? <div className="customer-message-avatar"><UserRound size={17} /></div> : <div className="customer-message-avatar customer-support-avatar"><Image src="/customer-mascot-original.jpg" alt="在线客服" width={1192} height={1197} /></div>}
                  <div><span>{message.role === "customer" ? ticket.customer_name : "在线客服"} · {timeLabel(message.created_at)}</span><div className="customer-bubble">{index === 0 && message.role === "customer" && <strong>{ticket.subject}</strong>}<p>{message.content}</p></div></div>
                </div>
              ))}

              {lastMessage?.role === "customer" && ticket.stage !== "resolved" && (
                <div className="customer-waiting-message"><span><LoaderCircle className="spin" size={18} /></span><div><strong>{currentStage?.label}</strong><p>{currentStage?.description}，页面会自动更新。</p></div></div>
              )}
            </div>

            {lastMessage?.role === "support" && ticket.customer_confirmed == null && (
              <div className="customer-feedback-box">
                <div><h2>这次回复解决您的问题了吗？</h2><p>您的反馈会同步回客服工作台。</p></div>
                <div className="customer-rating"><span>服务评分</span>{[1, 2, 3, 4, 5].map((value) => <button type="button" aria-label={`${value} 星`} className={value <= rating ? "active" : ""} key={value} onClick={() => setRating(value)}><Star size={18} /></button>)}</div>
                <textarea rows={3} maxLength={500} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="可以补充您的意见（选填）" />
                {error && <div className="customer-error">{error}</div>}
                <div className="customer-feedback-actions">
                  <button type="button" className="customer-secondary-action" disabled={Boolean(confirming)} onClick={() => void confirmResult(false)}>{confirming === "follow_up" ? <LoaderCircle className="spin" size={17} /> : <MessageCircleMore size={17} />}仍需帮助</button>
                  <button type="button" className="customer-primary-action" disabled={Boolean(confirming)} onClick={() => void confirmResult(true)}>{confirming === "resolved" ? <LoaderCircle className="spin" size={17} /> : <Check size={17} />}问题已解决</button>
                </div>
              </div>
            )}

            {ticket.customer_confirmed != null && (
              <div className={`customer-result ${ticket.customer_confirmed ? "resolved" : "follow-up"}`}>
                {ticket.customer_confirmed ? <CheckCircle2 size={24} /> : <MessageCircleMore size={24} />}
                <div><strong>{ticket.customer_confirmed ? "本次咨询已完成" : "已通知客服继续处理"}</strong><p>{ticket.customer_confirmed ? "感谢您的反馈，祝您生活愉快。" : "客服处理后，新的回复会继续显示在这里。"}</p></div>
              </div>
            )}

            {ticket.stage !== "resolved" && (
              <form className="customer-chat-composer" onSubmit={sendFollowUp}>
                <textarea rows={2} maxLength={4000} value={messageText} onChange={(event) => setMessageText(event.target.value)} placeholder="继续输入您想咨询的内容…" />
                <button type="submit" aria-label="发送消息" disabled={sendingMessage || !messageText.trim()}>{sendingMessage ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}</button>
                {error && <div className="customer-error">{error}</div>}
              </form>
            )}
          </section>

          <aside className="customer-progress-card">
            <div className="customer-progress-heading"><span><Clock3 size={19} /></span><div><h2>处理进度</h2><p>{ticket.ticket_no}</p></div></div>
            <div className="customer-progress-list">
              {progressSteps.map((step, index) => {
                const current = stageIndex(ticket.stage);
                const done = index < current || ticket.stage === "resolved";
                const active = index === current && ticket.stage !== "resolved";
                return <div className={`${done ? "done" : ""} ${active ? "active" : ""}`} key={step}><span>{done ? <Check size={13} /> : active ? <LoaderCircle className="spin" size={13} /> : <Circle size={11} />}</span><div><strong>{step}</strong>{active && <small>{currentStage?.description}</small>}</div></div>;
              })}
            </div>
            <div className="customer-progress-note"><Headphones size={16} /><p>此页面每 3 秒自动同步一次，无需手动刷新。</p></div>
          </aside>
        </main>
      )}
    </div>
  );
}
