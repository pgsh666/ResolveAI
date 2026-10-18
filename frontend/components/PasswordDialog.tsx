"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { KeyRound, LoaderCircle, X } from "lucide-react";
import { api, type SessionUser } from "@/lib/api";

export function PasswordDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (user: SessionUser) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const currentPasswordRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submittingRef = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    // A modal dialog enters the browser's top layer. The portal also keeps it
    // outside the blurred customer header and transformed merchant sidebar.
    dialog.showModal();
    currentPasswordRef.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      if (dialog.open) dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  async function savePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    setError("");
    try {
      const user = await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      onSaved(user);
      onClose();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  }

  function closeOnBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (busy || event.target !== event.currentTarget) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
  }

  if (typeof document === "undefined") return null;

  return createPortal(
    <dialog ref={dialogRef} className="password-dialog panel" aria-labelledby={titleId} aria-describedby={descriptionId}
      onClick={closeOnBackdrop} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
      <form className="password-dialog-form" onSubmit={savePassword} aria-busy={busy}>
        <div className="password-dialog-head">
          <span className="password-dialog-icon"><KeyRound size={23} aria-hidden="true" /></span>
          <div><h3 id={titleId}>修改密码</h3><p id={descriptionId}>修改成功后，其他设备的登录将失效。</p></div>
          <button className="password-dialog-close" type="button" aria-label="关闭修改密码" disabled={busy} onClick={onClose}><X size={19} aria-hidden="true" /></button>
        </div>
        <label>当前密码<input ref={currentPasswordRef} required disabled={busy} type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <label>新密码<input required disabled={busy} minLength={12} maxLength={128} type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>
        {error && <p className="password-dialog-error" role="alert">{error}</p>}
        <div className="password-dialog-actions">
          <button type="button" disabled={busy} onClick={onClose}>取消</button>
          <button className="password-dialog-save" type="submit" disabled={busy}>{busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}{busy ? "正在保存…" : "保存并注销其他登录"}</button>
        </div>
      </form>
    </dialog>,
    document.body,
  );
}
