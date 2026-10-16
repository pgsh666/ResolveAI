import { Suspense } from "react";
import { TicketList } from "@/components/TicketList";
export default function Page() { return <Suspense fallback={<div className="center-state">正在加载对话…</div>}><TicketList mode="chats" /></Suspense>; }
