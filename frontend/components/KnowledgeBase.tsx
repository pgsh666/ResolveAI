"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, BookOpen, CalendarDays, CheckCircle2, Database, FileText, Layers3, LoaderCircle, Search, Sparkles, Upload, X, Zap } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { api, watchBackgroundTask } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import type { BackgroundTask, Citation, HealthData, KnowledgeDocument, KnowledgeImportInput } from "@/lib/types";


const categoryLabels: Record<string, string> = {
  all: "全部文档",
  refund: "退款售后",
  logistics: "物流服务",
  payment: "支付问题",
  account: "账号安全",
  product: "商品问题",
  general: "通用规范",
};

export function KnowledgeBase() {
  const { user } = useAuth();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [results, setResults] = useState<Citation[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<KnowledgeDocument | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [importTask, setImportTask] = useState<BackgroundTask | null>(null);
  const [importNotice, setImportNotice] = useState("");
  const importInput = useRef<HTMLInputElement>(null);

  async function reloadKnowledge() {
    const [items, healthData] = await Promise.all([api.knowledgeDocuments(), api.health()]);
    setDocuments(items);
    setHealth(healthData);
  }

  useEffect(() => {
    Promise.all([api.knowledgeDocuments(), api.health()])
      .then(([items, healthData]) => { setDocuments(items); setHealth(healthData); })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedDocument) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setSelectedDocument(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedDocument]);

  const categories = useMemo(() => Array.from(new Set(documents.map((document) => document.category))), [documents]);
  const visibleDocuments = category === "all" ? documents : documents.filter((document) => document.category === category);

  async function searchKnowledge(event: FormEvent) {
    event.preventDefault();
    const keyword = query.trim();
    if (!keyword) return;
    setSearching(true);
    setError("");
    try {
      const response = await api.searchKnowledge(keyword);
      setSearchedQuery(response.query);
      setResults(response.results);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSearching(false);
    }
  }

  async function openDocument(document: KnowledgeDocument) {
    setSelectedDocument(document);
    setDetailLoading(true);
    setError("");
    try {
      setSelectedDocument(await api.knowledgeDocument(document.id));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setDetailLoading(false);
    }
  }

  async function importDocuments(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    setImportNotice("");
    try {
      const parsed = JSON.parse(await file.text()) as KnowledgeImportInput[] | { documents?: KnowledgeImportInput[] };
      const documents = Array.isArray(parsed) ? parsed : parsed.documents;
      if (!documents?.length) throw new Error("JSON 中没有 documents 数组");
      const queued = await api.importKnowledge(documents);
      setImportTask(queued);
      const completed = await watchBackgroundTask(queued.id, setImportTask);
      await reloadKnowledge();
      const result = completed.result ?? {};
      setImportNotice(`导入完成：新增 ${result.imported ?? 0} 篇，更新 ${result.updated ?? 0} 篇。`);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  return (
    <div className="page-wrap feature-page">
      <PageHeader title="知识库" description="管理 Agent 的政策依据，并直接验证向量召回效果。" />

      <section className="knowledge-hero panel">
        <div className="knowledge-hero-heading"><div className="knowledge-hero-copy"><h2>测试 RAG 知识检索</h2><p>输入自然语言问题，系统会生成查询向量，并返回语义最相近的政策文档。</p></div><button type="button" className="background-task-button" disabled={user?.role !== "admin" || Boolean(importTask && !["finished", "failed"].includes(importTask.status))} onClick={() => importInput.current?.click()}>{importTask && !["finished", "failed"].includes(importTask.status) ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}导入 JSON</button><input ref={importInput} hidden type="file" accept="application/json,.json" onChange={importDocuments} /></div>
        <form className="knowledge-search" onSubmit={searchKnowledge}>
          <Search size={19} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：退款超过 100 元是否需要审批？" />
          <button type="submit" disabled={searching || !query.trim()}>{searching ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}向量检索</button>
        </form>
        {error && <div className="inline-feature-error"><Zap size={15} />{error}；如刚更新代码，请先重启 FastAPI。</div>}
        {importTask && !["finished", "failed"].includes(importTask.status) && <div className="background-task-progress embedded"><div><strong>{importTask.message}</strong><span>{importTask.progress}%</span></div><div><i style={{ width: `${importTask.progress}%` }} /></div></div>}
        {importNotice && <div className="inline-feature-success"><CheckCircle2 size={15} />{importNotice}</div>}
      </section>

      {searchedQuery && (
        <section className="panel feature-panel retrieval-panel">
          <div className="feature-panel-heading compact"><div><h2>“{searchedQuery}”的召回结果</h2></div><span className="data-source-pill">前 {results.length} 条</span></div>
          <div className="retrieval-results">
            {results.map((result, index) => <article className="retrieval-card" key={result.id}>
              <div className="retrieval-rank">{String(index + 1).padStart(2, "0")}</div>
              <div className="retrieval-copy"><div><strong>{result.title}</strong><span>{result.source}</span></div><p>{result.snippet}</p><div className="retrieval-score"><span><i style={{ width: `${Math.round(result.score * 100)}%` }} /></span><strong>{Math.round(result.score * 100)}% 相似度</strong></div></div>
            </article>)}
          </div>
        </section>
      )}

      <section className="knowledge-layout">
        <aside className="panel knowledge-summary">
          <div className="summary-orb"><Database size={23} /></div><span>知识文档</span><strong>{documents.length}</strong><small>{health?.database ?? "正在识别数据源"}</small>
          <div className="summary-row"><span><Layers3 size={14} />知识分类</span><strong>{categories.length}</strong></div>
          <div className="summary-row"><span><Sparkles size={14} />检索方式</span><strong>余弦相似度</strong></div>
        </aside>

        <section className="panel feature-panel document-panel">
          <div className="feature-panel-heading compact"><div><h2>知识文档</h2></div></div>
          <div className="knowledge-filters">
            {["all", ...categories].map((item) => <button type="button" className={category === item ? "active" : ""} key={item} onClick={() => setCategory(item)}>{categoryLabels[item] ?? item}</button>)}
          </div>
          {loading ? <div className="feature-empty"><LoaderCircle className="spin" size={23} /><strong>正在加载知识库</strong></div> : <div className="document-grid">
            {visibleDocuments.map((document) => <button type="button" className="document-card" key={document.id} onClick={() => void openDocument(document)}>
              <div className="document-icon"><FileText size={18} /></div>
              <div><span>{categoryLabels[document.category] ?? document.category}</span><h3>{document.title}</h3><p>{document.content}</p><small>{document.source}</small><b className="document-card-open">查看全文 <ArrowUpRight size={12} /></b></div>
            </button>)}
          </div>}
          {!loading && visibleDocuments.length === 0 && <div className="feature-empty"><BookOpen size={23} /><strong>该分类还没有文档</strong></div>}
        </section>
      </section>

      {selectedDocument && <div className="document-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedDocument(null); }}>
        <section className="document-modal" role="dialog" aria-modal="true" aria-labelledby="document-detail-title">
          <header><div className="document-modal-icon"><FileText size={21} /></div><div><span>{categoryLabels[selectedDocument.category] ?? selectedDocument.category}</span><h2 id="document-detail-title">{selectedDocument.title}</h2></div><button type="button" aria-label="关闭文档详情" onClick={() => setSelectedDocument(null)}><X size={19} /></button></header>
          <div className="document-modal-meta"><span><Database size={14} />{selectedDocument.source}</span><span><CalendarDays size={14} />录入于 {new Date(selectedDocument.created_at).toLocaleDateString("zh-CN")}</span></div>
          <article><p>{selectedDocument.content}</p></article>
          <footer><span><Sparkles size={14} />已生成向量，可供 Agent 的 RAG 流程检索</span>{detailLoading && <span><LoaderCircle className="spin" size={14} />同步详情中</span>}</footer>
        </section>
      </div>}
    </div>
  );
}
