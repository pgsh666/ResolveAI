"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  Database,
  ExternalLink,
  LoaderCircle,
  Network,
  RefreshCw,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";
import type { HealthData } from "@/lib/types";


const API_DOCS_URL = "http://127.0.0.1:8000/docs";

export function ProjectSettings() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const checkConnection = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setHealth(await api.health());
      setCheckedAt(new Date());
    } catch (reason) {
      setHealth(null);
      setError((reason as Error).message);
      setCheckedAt(new Date());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.health()
      .then((healthData) => {
        if (cancelled) return;
        setHealth(healthData);
        setCheckedAt(new Date());
      })
      .catch((reason: Error) => {
        if (cancelled) return;
        setError(reason.message);
        setCheckedAt(new Date());
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="page-wrap feature-page">
      <PageHeader title="项目设置" description="查看 ResolveAI 当前运行配置和各服务连接状态。" />

      <section className={`settings-status panel ${error ? "offline" : ""}`}>
        <div className="settings-status-icon">
          {loading ? <LoaderCircle className="spin" size={23} /> : error ? <CircleAlert size={23} /> : <CheckCircle2 size={23} />}
        </div>
        <div>
          <h2>{loading ? "正在检测后端服务" : error ? "后端服务未连接" : "ResolveAI 运行正常"}</h2>
          <p>{error || (checkedAt ? `最近检测：${checkedAt.toLocaleTimeString("zh-CN")}` : "等待检测")}</p>
        </div>
        <button type="button" className="outline-button" onClick={checkConnection} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />重新检测</button>
      </section>

      <section className="settings-grid">
        <article className="panel settings-card">
          <div className="settings-card-heading"><div className="settings-card-icon purple"><Bot size={19} /></div><div><h2>模型配置</h2></div></div>
          <dl className="settings-list">
            <div><dt>运行模式</dt><dd><StatusDot active={Boolean(health)} />{health?.agent_mode ?? "未连接"}</dd></div>
            <div><dt>当前模型</dt><dd>{health?.model ?? "—"}</dd></div>
            <div><dt>编排框架</dt><dd>LangGraph</dd></div>
            <div><dt>失败降级</dt><dd>有限重试、显式规则降级或转人工</dd></div>
          </dl>
        </article>

        <article className="panel settings-card">
          <div className="settings-card-heading"><div className="settings-card-icon teal"><Database size={19} /></div><div><h2>数据与检索</h2></div></div>
          <dl className="settings-list">
            <div><dt>业务数据库</dt><dd>{health?.database ?? "—"}</dd></div>
            <div><dt>检索方式</dt><dd>向量余弦相似度</dd></div>
            <div><dt>嵌入模式</dt><dd>{health?.checks.embedding?.startsWith("fastembed:") ? "本地中文语义嵌入（512维）" : health?.checks.embedding ?? "未连接"}</dd></div>
            <div><dt>知识入口</dt><dd>/knowledge</dd></div>
          </dl>
        </article>

        <article className="panel settings-card">
          <div className="settings-card-heading"><div className="settings-card-icon orange"><Network size={19} /></div><div><h2>工具连接</h2></div></div>
          <dl className="settings-list">
            <div><dt>MCP 功能</dt><dd><StatusDot active={Boolean(health?.mcp_enabled)} />{health?.mcp_enabled ? "已启用" : "未启用"}</dd></div>
            <div><dt>MCP 服务地址</dt><dd>127.0.0.1:8001/mcp</dd></div>
            <div><dt>FastAPI 地址</dt><dd>127.0.0.1:8000</dd></div>
            <div><dt>前端地址</dt><dd>localhost:3000</dd></div>
          </dl>
        </article>

        <article className="panel settings-card">
          <div className="settings-card-heading"><div className="settings-card-icon blue"><ShieldCheck size={19} /></div><div><h2>安全与配置</h2></div></div>
          <dl className="settings-list">
            <div><dt>密钥存储</dt><dd>backend/.env</dd></div>
            <div><dt>Git 忽略</dt><dd><StatusDot active />已保护 .env</dd></div>
            <div><dt>人工审批门控</dt><dd><StatusDot active />已启用</dd></div>
            <div><dt>审计轨迹</dt><dd><StatusDot active />数据库留痕</dd></div>
          </dl>
        </article>
      </section>

      <section className="panel config-guide">
        <div className="config-guide-icon"><ServerCog size={24} /></div>
        <div><h2>运行参数由环境文件管理</h2><p>如需更换模型、数据库或 MCP 地址，请编辑项目中的 <code>backend/.env</code>，保存后重启 FastAPI。API Key 不会在本页面显示。</p></div>
        <a className="primary-button" href={API_DOCS_URL} target="_blank" rel="noreferrer">打开 API 文档<ExternalLink size={15} /></a>
      </section>
    </div>
  );
}

function StatusDot({ active }: { active?: boolean }) {
  return <i className={`settings-dot ${active ? "active" : ""}`} aria-hidden="true" />;
}
