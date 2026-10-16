"use client";
import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { PageHeader } from "@/components/PageHeader";

export function Accounts() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      if (!user) return;
      setUser(await api.updateUser(user.id, {display_name:name.trim()}));
      setNotice("个人资料已保存。");
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <div className="page-wrap"><PageHeader title="我的账号" description="仅维护您自己的管理员账号，不提供客户或其他账号的管理操作。" />
    {error && <p className="customer-error" role="alert">{error}</p>}
    {notice && <p className="commerce-notice" role="status">{notice}</p>}
    <section className="panel feature-panel own-account-panel"><h2>个人资料</h2><form onSubmit={save}>
      <label>登录名<input value={user?.username ?? ""} readOnly /></label>
      <label>角色<input value="管理员" readOnly /></label>
      <label>显示名称<input required maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label>
      <p>登录名与角色固定。修改密码请使用左下角“修改密码”，不支持创建、停用或修改客户账号。</p>
      <button type="submit" className="primary-button" disabled={busy || !name.trim()}>{busy ? "正在保存…" : "保存个人资料"}</button>
    </form></section>
  </div>;
}
