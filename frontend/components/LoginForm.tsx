"use client";

import { useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";

export function LoginForm() {
  const [registering, setRegistering] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const router = useRouter();
  const params = useSearchParams();
  const { setUser } = useAuth();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError(""); setNotice("");
    try {
      if (registering) {
        await api.register(username, password, name, false);
        setPassword(""); setRegistering(false); setNotice("注册成功，请使用刚才的账号和密码登录。");
        return;
      }
      const user = await api.login(username, password);
      setPassword(""); setUser(user);
      const target = params.get("next") ?? "/";
      const staffTarget = /^\/(?!\/)[a-zA-Z0-9/_-]*$/.test(target) && !/^\/customer(?:\/|$)/.test(target) ? target : "/";
      router.replace(user.role === "customer" ? "/customer" : staffTarget);
    } catch (reason) { setError((reason as Error).message); } finally { setPending(false); }
  }
  return <div className="login-page"><form className="login-card" onSubmit={submit}><h1>ResolveAI</h1><h2>{registering ? "注册用户账号" : "登录您的账号"}</h2><p>客户、客服与审批操作均按账号权限隔离。</p>
    <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required minLength={2} maxLength={80} placeholder="英文或数字用户名" /></label>
    {registering && <label>您的称呼<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} /></label>}
    <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={registering ? "new-password" : "current-password"} required maxLength={128} /></label>
    {error && <div role="alert" className="customer-error">{error}</div>}
    {notice && <div role="status" className="commerce-notice">{notice}</div>}
    <button className="primary-button" disabled={pending}>{pending ? "正在处理…" : registering ? "注册" : "登录"}</button>
    <button type="button" className="outline-button" disabled={pending} onClick={() => { setRegistering(!registering); setPassword(""); setError(""); setNotice(""); }}>{registering ? "已有账号，返回登录" : "用户注册"}</button>
    <small>后台账号由管理员创建。首次启动生成的凭据保存在本地 .env.auth，请勿公开。</small>
  </form></div>;
}
