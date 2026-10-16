"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SessionActions } from "@/components/AuthProvider";

export function CustomerHeader() {
  const path = usePathname();
  return <header className="customer-header"><Link className="customer-brand" href="/customer"><div><strong>ResolveAI 电商智能客服</strong><small>选购、订单与售后，为您提供帮助</small></div></Link><nav className="customer-navigation" aria-label="客户导航">{[["/customer", "智能客服"], ["/customer/shop", "商城"], ["/customer/orders", "我的订单"]].map(([href, label]) => <Link href={href} className={path === href ? "active" : ""} key={href}>{label}</Link>)}</nav><SessionActions /></header>;
}
