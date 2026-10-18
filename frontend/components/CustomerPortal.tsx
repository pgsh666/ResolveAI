"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { CheckCircle2, LoaderCircle, MessageCircleMore, Plus, Send, Star, Trash2, UserRound } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { CustomerHeader } from "@/components/CustomerHeader";
import { DeleteChatDialog } from "@/components/DeleteChatDialog";
import { formatBusinessTime } from "@/lib/time";
import { serviceDisplayText } from "@/lib/presentation";
import type { CustomerTicket, CustomerTicketStage } from "@/lib/types";

const stageLabels: Record<CustomerTicketStage, string> = {
  submitted: "等待回复", processing: "正在回复", reviewing: "人工处理中",
  answered: "已回复", resolved: "对话已结束", follow_up: "继续为您提供帮助",
};
const suggestions = ["帮我找一款蓝牙耳机", "比较商城商品价格", "查看我的订单物流"];

export function CustomerPortal() {
  const { user } = useAuth();
  const storageKey = `resolveai_customer_ticket_id:${user?.id ?? "anonymous"}`;
  const [ticket, setTicket] = useState<CustomerTicket | null>(null);
  const [history, setHistory] = useState<CustomerTicket[]>([]);
  const [boundOrder, setBoundOrder] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [sending, setSending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState("");
  const [messageText, setMessageText] = useState("");
  const [error, setError] = useState("");
  const [rating, setRating] = useState(5);
  const [feedback, setFeedback] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CustomerTicket | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const deletingRef = useRef(false);
  const sendingRef = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const ticketId = ticket?.id;
  const ticketStage = ticket?.stage;
  const lastMessage = ticket?.messages.at(-1);
  const messages = ticket?.messages ?? [];
  const busy = sending || confirming || restoring || deleteTarget !== null;
  const round = Math.max(messages.filter(m => m.role === "customer").length - 1, 0);
  const mascotTop = 98 + Math.min(round, 5) * 94;

  useEffect(() => {
    let active = true;
    const order = new URLSearchParams(window.location.search).get("order");
    const savedId = window.localStorage.getItem(storageKey);
    api.customerHistory().then(async items => {
      if (!active) return;
      setHistory(items);
      if (order) {
        setBoundOrder(order);
        setTicket(null);
      } else if (savedId) {
        try {
          const saved = await api.customerTicket(savedId);
          if (active) setTicket(saved);
        } catch {
          window.localStorage.removeItem(storageKey);
        }
      }
    }).catch(() => undefined).finally(() => { if (active) setRestoring(false); });
    return () => { active = false; };
  }, [storageKey]);

  useEffect(() => {
    if (!ticketId || ticketStage === "resolved" || sending || deleteTarget) return;
    let active = true;
    const timer = window.setInterval(() => {
      api.customerTicket(ticketId).then(value => { if (active) setTicket(value); }).catch(() => undefined);
    }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [ticketId, ticketStage, sending, deleteTarget]);

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [ticket?.messages.length, pendingMessage]);

  useEffect(() => {
    if (!busy) composerRef.current?.focus();
  }, [busy, ticketId]);

  async function reloadHistory() {
    try { setHistory(await api.customerHistory()); } catch { /* The active chat is already saved. */ }
  }

  async function sendMessage(text: string) {
    const content = text.trim();
    if (!content || sendingRef.current || busy) return;
    sendingRef.current = true;
    setSending(true);
    setPendingMessage(content);
    setMessageText("");
    setError("");
    setFeedbackOpen(false);
    try {
      let updated: CustomerTicket;
      if (!ticket || ticket.stage === "resolved") {
        if (ticket?.stage === "resolved") setTicket(null);
        updated = await api.createCustomerTicket({ message: content, order_id: boundOrder });
      } else {
        updated = await api.sendCustomerMessage(ticket.id, content);
      }
      window.localStorage.setItem(storageKey, updated.id);
      setTicket(updated);
      setFeedback("");
      setRating(5);
      await reloadHistory();
    } catch (reason) {
      setError((reason as Error).message);
      setMessageText(content);
    } finally {
      setPendingMessage("");
      setSending(false);
      sendingRef.current = false;
    }
  }

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(messageText);
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function openConversation(id: string) {
    if (busy || id === ticketId) return;
    setRestoring(true);
    setError("");
    setFeedbackOpen(false);
    setBoundOrder(null);
    setMessageText("");
    try {
      const selected = await api.customerTicket(id);
      setTicket(selected);
      window.localStorage.setItem(storageKey, id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally { setRestoring(false); }
  }

  function startNewConversation() {
    if (busy) return;
    resetConversation();
  }

  function resetConversation() {
    window.localStorage.removeItem(storageKey);
    window.history.replaceState(null, "", "/customer");
    setTicket(null);
    setBoundOrder(null);
    setMessageText("");
    setRating(5);
    setFeedback("");
    setFeedbackOpen(false);
    setError("");
  }


  async function deleteConversation() {
    if (!deleteTarget || deletingRef.current) return;
    deletingRef.current = true;
    setDeleting(true);
    setDeleteError("");
    try {
      await api.deleteCustomerChat(deleteTarget.id);
      setHistory(items => items.filter(item => item.id !== deleteTarget.id));
      if (ticketId === deleteTarget.id) resetConversation();
      else if (window.localStorage.getItem(storageKey) === deleteTarget.id) window.localStorage.removeItem(storageKey);
      setDeleteTarget(null);
    } catch (reason) {
      setDeleteError((reason as Error).message || "删除失败，请稍后重试");
    } finally {
      deletingRef.current = false;
      setDeleting(false);
    }
  }

  async function confirmResult(resolved: boolean) {
    if (!ticket || busy) return;
    setConfirming(true);
    setError("");
    try {
      setTicket(await api.confirmCustomerTicket(ticket.id, resolved, rating, feedback.trim()));
      setFeedbackOpen(false);
      await reloadHistory();
    } catch (reason) {
      setError((reason as Error).message);
    } finally { setConfirming(false); }
  }

  return (
    <div className="customer-portal assistant-portal">
      <CustomerHeader />
      <main className="assistant-workspace">
        <aside className="assistant-history panel" aria-label="对话记录">
          <button type="button" className="assistant-new-chat" disabled={busy} onClick={startNewConversation}><Plus size={17} />新对话</button>
          <h2>对话记录</h2>
          <div className="assistant-history-list">
            {history.map(item => <div className={`assistant-history-item${item.id === ticketId ? " active" : ""}`} key={item.id} data-conversation-id={item.id}>
              <button type="button" className="assistant-history-open" aria-current={item.id === ticketId ? "page" : undefined} disabled={busy} onClick={() => void openConversation(item.id)}><MessageCircleMore size={16} /><span><strong>{item.subject}</strong><small>{item.ticket_no}</small></span></button>
              <button type="button" className="assistant-history-delete" aria-label={`删除聊天：${item.subject}（${item.ticket_no}）`} title="删除聊天" aria-haspopup="dialog" disabled={busy} onClick={() => { setDeleteError(""); setDeleteTarget(item); }}><Trash2 size={16} aria-hidden="true" /></button>
            </div>)}
            {!history.length && <p>您的对话会保存在这里。</p>}
          </div>
        </aside>

        <section className="customer-chat-card assistant-chat-card" aria-label="电商客服聊天">
          {ticket && <div className="customer-chat-mascot" style={{ top: `${mascotTop}px` }} aria-hidden="true"><Image src="/customer-mascot-peek-v4.png" alt="" width={1024} height={1536} sizes="128px" /></div>}
          <header className="customer-chat-head">
            <div><div className="customer-message-avatar customer-support-avatar"><Image src="/customer-mascot-original.jpg" alt="电商智能客服" width={1192} height={1197} sizes="54px" /></div><div><strong>ResolveAI 电商智能客服</strong><small>{sending ? "正在为您回复…" : ticket?.handed_off ? "人工客服正在接续处理" : ticket ? stageLabels[ticket.stage] : "商品、订单、物流与售后，直接向我提问"}</small></div></div>
            <div>{ticket && <span>{ticket.ticket_no}</span>}<button type="button" disabled={busy} onClick={startNewConversation}><Plus size={14} />新对话</button></div>
          </header>

          <div className="customer-chat-body assistant-chat-body" role="log" aria-live="polite" aria-label="对话消息">
            {restoring ? <div className="assistant-restoring"><LoaderCircle className="spin" size={23} /><span>正在加载对话…</span></div> : <>
              {!ticket && !pendingMessage && <div className="assistant-welcome">
                <Image src="/customer-mascot-wave.png" alt="" width={1365} height={1136} sizes="(max-width: 700px) 120px, 175px" className="assistant-welcome-mascot" preload />
                <h1><span>您好</span><span>有什么可以帮到您？</span></h1>
                <div className="assistant-suggestions">{suggestions.map(text => <button type="button" disabled={busy} key={text} onClick={() => void sendMessage(text)}>{text}</button>)}</div>
              </div>}
              {boundOrder && !ticket && <div className="assistant-order-context">本次对话关联订单 {boundOrder}。您可以直接描述想了解的内容。<button type="button" disabled={busy} onClick={() => setBoundOrder(null)}>取消关联</button></div>}
              {ticket?.handed_off && <div className="commerce-notice">已转人工客服：{ticket.handoff_reasons.join("；") || "需要进一步核验"}。您可以继续在此对话补充信息。</div>}
              {messages.map(message => <div className={`customer-message-row ${message.role === "customer" ? "user" : "support"}`} key={message.id}>
                {message.role === "customer" ? <div className="customer-message-avatar"><UserRound size={18} /></div> : <div className="customer-message-avatar customer-support-avatar"><Image src="/customer-mascot-original.jpg" alt="电商智能客服" width={1192} height={1197} sizes="54px" /></div>}
                <div><span>{message.role === "customer" ? ticket?.customer_name : "ResolveAI"} · {formatBusinessTime(message.created_at, {hour: "2-digit", minute: "2-digit"})}</span><div className="customer-bubble"><p>{message.role === "customer" ? message.content : serviceDisplayText(message.content, true)}</p></div></div>
              </div>)}
              {pendingMessage && <div className="customer-message-row user assistant-pending-message"><div className="customer-message-avatar"><UserRound size={18} /></div><div><span>{user?.display_name} · 刚刚</span><div className="customer-bubble"><p>{pendingMessage}</p></div></div></div>}
              {(sending || lastMessage?.role === "customer" && ticket?.stage !== "resolved") && <div className="customer-waiting-message"><LoaderCircle className="spin" size={18} /><p>{ticket?.handed_off ? "已收到您的消息，等待人工客服回复。" : "正在思考并为您回复…"}</p></div>}
              {ticket?.customer_confirmed === true && <div className="assistant-conversation-ended"><CheckCircle2 size={16} />本次对话已结束，继续发送消息即可开始新对话。</div>}
              <div ref={chatEnd} />
            </>}
          </div>

          {lastMessage?.role === "support" && ticket?.customer_confirmed == null && !sending && <div className="assistant-feedback-toggle"><button type="button" disabled={busy} onClick={() => setFeedbackOpen(!feedbackOpen)}><Star size={14} />评价本次对话</button></div>}
          {feedbackOpen && <section className="customer-feedback-box assistant-feedback">
            <h2>这次回复解决您的问题了吗？</h2>
            <div className="customer-rating"><span>服务评分</span>{[1, 2, 3, 4, 5].map(value => <button type="button" disabled={busy} aria-label={`${value} 星`} className={value <= rating ? "active" : ""} key={value} onClick={() => setRating(value)}><Star size={18} /></button>)}</div>
            <textarea rows={2} maxLength={500} value={feedback} disabled={busy} onChange={event => setFeedback(event.target.value)} placeholder="补充您的意见（选填）" />
            <div className="customer-feedback-actions"><button type="button" className="customer-secondary-action" disabled={busy} onClick={() => void confirmResult(false)}>仍需帮助</button><button type="button" className="customer-primary-action" disabled={busy} onClick={() => void confirmResult(true)}>问题已解决</button></div>
          </section>}

          <div className="assistant-composer-wrap">
            {error && <div className="customer-error" role="alert">{error}</div>}
            <form className="customer-chat-composer assistant-composer" onSubmit={submitMessage}>
              <textarea ref={composerRef} aria-label="聊天消息" rows={2} maxLength={4000} disabled={busy} value={messageText} onChange={event => setMessageText(event.target.value)} onKeyDown={onComposerKeyDown} placeholder="咨询商品、价格、订单、物流或售后问题…" />
              <button type="submit" aria-label="发送消息" disabled={busy || !messageText.trim()}>{sending ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}</button>
            </form>
            <small>Enter 发送 · Shift + Enter 换行</small>
          </div>
        </section>
      </main>
      {deleteTarget && <DeleteChatDialog subject={deleteTarget.subject} busy={deleting} error={deleteError}
        onCancel={() => { if (!deletingRef.current) setDeleteTarget(null); }} onConfirm={() => void deleteConversation()} />}
    </div>
  );
}
