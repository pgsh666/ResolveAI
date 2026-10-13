"use client";
import { useEffect, useState, type FormEvent } from "react";
import { api, type SessionUser } from "@/lib/api";
import { useAuth, roleLabels } from "@/components/AuthProvider";
import { PageHeader } from "@/components/PageHeader";

export function Accounts() {
  const { user } = useAuth();
  const [accounts, setAccounts] = useState<SessionUser[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "agent" as SessionUser["role"] });
  useEffect(() => { api.users().then(setAccounts).catch((e: Error) => setError(e.message)); }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await api.createUser(form); setForm({ ...form, username: "", display_name: "", password: "" }); setAccounts(await api.users()); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function update(id: string, values: { active?: boolean; role?: SessionUser["role"] }) {
    setBusy(true); setError("");
    try { await api.updateUser(id, values); setAccounts(await api.users()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="page-wrap"><PageHeader title="账号管理" description="客户仅能访问自己的会话；客服处理工单；审批人确认高风险操作。角色变更会注销原有登录。" />
    {error && <p role="alert">{error}</p>}
    <section className="panel feature-panel"><h2>创建账号</h2><form className="accounts-list" onSubmit={save}>
      <label>用户名 <input required value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label>
      <label>显示名称 <input required value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} /></label>
      <label>初始密码 <input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
      <label>角色 <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value as SessionUser["role"] })}>{Object.entries(roleLabels).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select></label>
      <button className="primary-button" disabled={busy}>创建账号</button></form></section>
    <section className="panel feature-panel accounts-list"><h2>当前工作空间账号</h2>{accounts.map(account => <div className="account-row" key={account.id}><span>{account.display_name}（{account.username}）</span>
      <select aria-label={`${account.username}角色`} disabled={busy || account.id === user?.id} value={account.role} onChange={e => update(account.id, { role: e.target.value as SessionUser["role"] })}>{Object.entries(roleLabels).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select>
      <span>{account.active ? "启用" : "停用"}</span><button disabled={busy || account.id === user?.id} onClick={() => update(account.id, { active: !account.active })}>{account.active ? "停用" : "启用"}</button></div>)}</section>
  </div>;
}
