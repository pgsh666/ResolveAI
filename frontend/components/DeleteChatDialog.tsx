"use client";

import { useEffect, useId, useRef, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { LoaderCircle, Trash2, X } from "lucide-react";

export function DeleteChatDialog({ subject, busy, error, onCancel, onConfirm }: {
  subject: string; busy: boolean; error: string; onCancel: () => void; onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    cancelRef.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      if (dialog.open) dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  function closeOnBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (busy || event.target !== event.currentTarget) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onCancel();
  }

  if (typeof document === "undefined") return null;
  return createPortal(
    <dialog ref={dialogRef} className="chat-delete-dialog panel" aria-labelledby={titleId} aria-describedby={descriptionId}
      onClick={closeOnBackdrop} onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}>
      <form onSubmit={event => { event.preventDefault(); if (!busy) onConfirm(); }} aria-busy={busy}>
        <div className="chat-delete-dialog-head">
          <span className="chat-delete-icon"><Trash2 size={22} aria-hidden="true" /></span>
          <h2 id={titleId}>删除这条聊天？</h2>
          <button type="button" aria-label="关闭删除聊天" disabled={busy} onClick={onCancel}><X size={19} aria-hidden="true" /></button>
        </div>
        <p className="chat-delete-subject">{subject}</p>
        <p id={descriptionId}>删除后，这条聊天将从您的列表中移除，无法在客户端重新打开。商户端的服务记录仍会保留。</p>
        {error && <p className="chat-delete-error" role="alert">{error}</p>}
        <div className="chat-delete-actions">
          <button ref={cancelRef} type="button" disabled={busy} onClick={onCancel}>取消</button>
          <button type="submit" className="chat-delete-confirm" disabled={busy}>{busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}{busy ? "正在删除…" : "确认删除聊天"}</button>
        </div>
      </form>
    </dialog>, document.body,
  );
}

