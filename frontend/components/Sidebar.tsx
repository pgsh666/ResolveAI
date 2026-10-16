"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Activity, BookOpen, Bot, ChevronDown, LayoutDashboard, Menu, MessageCircle, Package, Route, Settings, ShoppingBag, TicketCheck, Users, X } from "lucide-react";
import { SessionActions } from "@/components/AuthProvider";

export function Sidebar() {
  const pathname = usePathname();
  const [managementOpen, setManagementOpen] = useState(true);
  const [customersOpen, setCustomersOpen] = useState(true);
  const [agentOpen, setAgentOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const active = (href: string) => href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");
  const managementActive = ["/products", "/chats", "/orders", "/tickets", "/settings", "/questions"].some(active);
  const agentActive = ["/agents", "/knowledge", "/analytics"].some(active);
  const customerLinks = [{label:"聊天管理",href:"/chats",icon:MessageCircle},{label:"订单管理",href:"/orders",icon:ShoppingBag},{label:"售后处理",href:"/tickets",icon:TicketCheck}];
  const agentLinks = [{label:"Agent运行",href:"/agents",icon:Bot},{label:"知识库",href:"/knowledge",icon:BookOpen},{label:"效果分析",href:"/analytics",icon:Activity}];
  function item(label: string, href: string, Icon: typeof Package, level = 2) {
    return <Link key={href} href={href} aria-current={active(href) ? "page" : undefined} className={"admin-nav-link level-" + level + (active(href) ? " active" : "")} onClick={() => setDrawerOpen(false)}><Icon size={17} /><span>{label}</span></Link>;
  }
  return <>
    <button type="button" className="admin-sidebar-toggle" aria-label="打开导航" aria-expanded={drawerOpen} aria-controls="merchant-sidebar" onClick={() => setDrawerOpen(!drawerOpen)}><Menu size={21} /><span>导航</span></button>
    {drawerOpen && <button type="button" className="admin-sidebar-backdrop" aria-label="关闭导航遮罩" onClick={() => setDrawerOpen(false)} />}
    <aside id="merchant-sidebar" className={"admin-sidebar-panel" + (drawerOpen ? " is-open" : "")} aria-label="商户侧边栏">
      <div className="admin-sidebar-brand"><Link href="/" onClick={() => setDrawerOpen(false)}>ResolveAI</Link><button type="button" className="admin-sidebar-close" aria-label="关闭导航" onClick={() => setDrawerOpen(false)}><X size={20} /></button></div>
      <nav className="admin-sidebar-nav" aria-label="主导航">
        <ul className="admin-primary-list">
          <li>{item("总览", "/", LayoutDashboard, 1)}</li>
          <li>
            <button type="button" className={"admin-nav-group" + (managementActive ? " active" : "")} aria-expanded={managementOpen} aria-controls="management-menu" onClick={() => setManagementOpen(!managementOpen)}><Settings size={18} /><span>管理</span><ChevronDown size={16} className={managementOpen ? "expanded" : ""} /></button>
            {managementOpen && <div id="management-menu" className="admin-submenu">
              {item("商品管理", "/products", Package)}
              <button type="button" className={"admin-nav-group level-2" + (["/chats","/orders","/tickets"].some(active) ? " active" : "")} aria-expanded={customersOpen} aria-controls="customers-menu" onClick={() => setCustomersOpen(!customersOpen)}><Users size={17} /><span>客户管理</span><ChevronDown size={16} className={customersOpen ? "expanded" : ""} /></button>
              {customersOpen && <div id="customers-menu" className="admin-submenu">{customerLinks.map(link => item(link.label, link.href, link.icon, 3))}</div>}
              {item("项目设置", "/settings", Settings)}
              {item("提问与路由记录", "/questions", Route)}
            </div>}
          </li>
          <li>
            <button type="button" className={"admin-nav-group" + (agentActive ? " active" : "")} aria-expanded={agentOpen} aria-controls="agent-menu" onClick={() => setAgentOpen(!agentOpen)}><Bot size={18} /><span>Agent</span><ChevronDown size={16} className={agentOpen ? "expanded" : ""} /></button>
            {agentOpen && <div id="agent-menu" className="admin-submenu">{agentLinks.map(link => item(link.label, link.href, link.icon))}</div>}
          </li>
        </ul>
      </nav>
      <footer className="admin-sidebar-footer"><Link href="/accounts" className="admin-account-link" onClick={() => setDrawerOpen(false)}><Settings size={15} />我的账号</Link><SessionActions /></footer>
    </aside>
  </>;
}
