"use client";

import { Suspense, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";

export function AppFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (pathname === "/login") return <>{children}</>;

  if (pathname.startsWith("/customer")) {
    return <main className="customer-shell">{children}</main>;
  }

  return (
    <div className="app-shell">
      <Suspense fallback={<div className="topbar" aria-hidden="true" />}><Sidebar /></Suspense>
      <main className="main-shell">{children}</main>
    </div>
  );
}
