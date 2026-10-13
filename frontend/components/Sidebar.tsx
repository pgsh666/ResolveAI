"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  BookOpen,
  Bot,
  FileCheck2,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  TicketCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { SessionActions, useAuth } from "@/components/AuthProvider";


const navigation = [
  { label: "总览", href: "/", icon: LayoutDashboard },
  { label: "工单中心", href: "/tickets", icon: TicketCheck, count: "total" },
  { label: "Agent 运行", href: "/agents", icon: Bot },
  { label: "人工审批", href: "/tickets?status=pending_approval", icon: FileCheck2, count: "pending" },
  { label: "知识库", href: "/knowledge", icon: BookOpen },
  { label: "效果分析", href: "/analytics", icon: Activity },
  { label: "项目设置", href: "/settings", icon: Settings },
  { label: "客户模拟端", href: "/customer", icon: MessagesSquare },
  { label: "账号管理", href: "/accounts", icon: Settings },
];

export function Sidebar() {
  const { user } = useAuth();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const ticketStatus = searchParams.get("status");
  const [ticketCounts, setTicketCounts] = useState({ total: 0, pending: 0 });

  useEffect(() => {
    let cancelled = false;
    api.tickets().then((tickets) => {
      if (cancelled) return;
      setTicketCounts({
        total: tickets.length,
        pending: tickets.filter((ticket) => ticket.status === "pending_approval").length,
      });
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [pathname, ticketStatus]);

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    if (href === "/tickets") return pathname.startsWith("/tickets") && ticketStatus !== "pending_approval";
    if (href === "/tickets?status=pending_approval") {
      return pathname === "/tickets" && ticketStatus === "pending_approval";
    }
    return pathname === href;
  };

  return (
    <header className="topbar">
      <Link className="topbar-brand" href="/" aria-label="ResolveAI 首页">
        <span className="topbar-brand-copy"><strong>ResolveAI</strong></span>
      </Link>

      <nav className="topbar-nav" aria-label="主导航">
        {navigation.filter((item) => item.href !== "/accounts" || user?.role === "admin").map((item) => {
          const active = isActive(item.href);
          const Icon = item.icon;
          const count = item.count === "total" ? ticketCounts.total : item.count === "pending" ? ticketCounts.pending : 0;
          return (
            <Link className={`topbar-item ${active ? "active" : ""}`} href={item.href} key={item.label}>
              <Icon size={16} />
              <span>{item.label}</span>
              {count > 0 && <i>{count}</i>}
            </Link>
          );
        })}
      </nav>
      <SessionActions />
    </header>
  );
}
