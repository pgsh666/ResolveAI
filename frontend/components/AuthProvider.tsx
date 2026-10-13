"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type SessionUser } from "@/lib/api";

const Context = createContext<{ user: SessionUser | null; setUser: (user: SessionUser | null) => void; loading: boolean }>({ user: null, setUser: () => {}, loading: true });
export const useAuth = () => useContext(Context);
export const roleLabels = { customer: "客户", agent: "客服坐席", reviewer: "审批人", admin: "管理员" };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();
  useEffect(() => {
    let active = true;
    api.session().then((value) => { if (active) setUser(value); }).catch(() => { if (active) setUser(null); }).finally(() => { if (active) setLoading(false); });
    const expire = () => { setUser(null); router.replace("/login"); };
    window.addEventListener("resolveai-session-expired", expire);
    return () => { active = false; window.removeEventListener("resolveai-session-expired", expire); };
  }, [router]);
  useEffect(() => {
    if (loading || pathname === "/login") return;
    if (!user) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    else if (user.role === "customer" && !pathname.startsWith("/customer")) router.replace("/customer");
    else if (pathname.startsWith("/accounts") && user.role !== "admin") router.replace("/tickets");
  }, [loading, user, pathname, router]);
  const allowed = pathname === "/login" || !loading && user && (user.role !== "customer" || pathname.startsWith("/customer")) && (!pathname.startsWith("/accounts") || user.role === "admin");
  return <Context.Provider value={{ user, setUser, loading }}>{allowed ? children : <div className="center-state"><strong>正在验证登录状态…</strong></div>}</Context.Provider>;
}

export function SessionActions() {
  const { user, setUser } = useAuth();
  const router = useRouter();
  const [changing, setChanging] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  if (!user) return null;
  return <div className="session-actions"><span>{user.display_name} · {roleLabels[user.role]}</span><button type="button" onClick={() => setChanging(!changing)}>修改密码</button><button type="button" onClick={async () => { await api.logout(); setUser(null); router.replace("/login"); }}>退出登录</button>
    {changing && <form className="password-dialog panel" onSubmit={async (event) => { event.preventDefault(); setError(""); try { setUser(await api.changePassword(currentPassword, newPassword)); setCurrentPassword(""); setNewPassword(""); setChanging(false); } catch (e) { setError((e as Error).message); } }}><h3>修改密码</h3><label>当前密码<input required type="password" autoComplete="current-password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label><label>新密码<input required minLength={12} maxLength={128} type="password" autoComplete="new-password" value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label>{error && <p role="alert">{error}</p>}<button type="submit">保存并注销其他登录</button><button type="button" onClick={() => setChanging(false)}>取消</button></form>}
  </div>;
}
