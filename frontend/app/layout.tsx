import type { Metadata } from "next";
import { AppFrame } from "@/components/AppFrame";
import { AuthProvider } from "@/components/AuthProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "ResolveAI · 智能客服运营台",
  description: "由多 Agent 驱动的客服工单决策与执行平台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <AuthProvider><AppFrame>{children}</AppFrame></AuthProvider>
      </body>
    </html>
  );
}
