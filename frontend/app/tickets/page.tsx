import { Suspense } from "react";
import { TicketList } from "@/components/TicketList";

export default function TicketsPage() {
  return <Suspense fallback={<div className="center-state"><strong>正在载入工单中心</strong></div>}><TicketList /></Suspense>;
}
