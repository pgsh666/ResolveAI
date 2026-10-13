import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "在线客服 · ResolveAI",
  description: "提交服务问题、查看处理进度并接收客服回复",
};

export default function CustomerLayout({ children }: { children: ReactNode }) {
  return children;
}
