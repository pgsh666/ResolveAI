"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type SessionUser } from "@/lib/api";
import { PasswordDialog } from "@/components/PasswordDialog";

const Context = createContext<{ user: SessionUser | null; setUser: (user: SessionUser | null) => void; loading: boolean }>({ user: null, setUser: () => {}, loading: true });
export const useAuth = () => useContext(Context);
export const roleLabels = { customer: "客户", admin: "管理员" };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();
  const customerPage = pathname === "/customer" || pathname.startsWith("/customer/");
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
    else if (user.role === "customer" && !customerPage) router.replace("/customer");
    else if (user.role !== "customer" && customerPage) router.replace("/");
    else if (pathname.startsWith("/accounts") && user.role !== "admin") router.replace("/tickets");
  }, [loading, user, pathname, router, customerPage]);
  const allowed = pathname === "/login" || !loading && user && (user.role === "customer" ? customerPage : !customerPage) && (!pathname.startsWith("/accounts") || user.role === "admin");
  return <Context.Provider value={{ user, setUser, loading }}>{allowed ? children : <div className="center-state"><strong>正在验证登录状态…</strong></div>}</Context.Provider>;
}

export function SessionActions() {
  const { user, setUser } = useAuth();
  const router = useRouter();
  const [changing, setChanging] = useState(false);
  if (!user) return null;
  return <div className="session-actions"><span>{user.display_name} · {roleLabels[user.role]}</span><button type="button" onClick={() => setChanging(true)}>修改密码</button><button type="button" onClick={async () => { await api.logout(); setUser(null); router.replace("/login"); }}>退出登录</button>
    {changing && <PasswordDialog onClose={() => setChanging(false)} onSaved={setUser} />}
  </div>;
}
