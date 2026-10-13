import { Suspense } from "react";
import { LoginForm } from "@/components/LoginForm";

export default function LoginPage() { return <Suspense fallback={<div className="center-state">正在加载…</div>}><LoginForm /></Suspense>; }
