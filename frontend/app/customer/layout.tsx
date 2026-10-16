import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "电商智能客服 · ResolveAI",
  description: "直接咨询商品选购、价格库存、订单支付、物流配送及售后问题",
};

export default function CustomerLayout({ children }: { children: ReactNode }) {
  return children;
}
