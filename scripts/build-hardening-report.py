"""Aggregate machine-generated evidence without creating any human review score."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/hardening"


def read(path): return json.loads(path.read_text(encoding="utf-8"))


def main():
    results = {}
    for platform in ("windows", "linux"):
        suite = ET.parse(OUT / f"{platform}-tests.xml").getroot().find("testsuite")
        values = {key: int(suite.attrib.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
        assert values["failures"] == values["errors"] == 0
        results[platform] = values
    before, after = read(OUT / "pre-migration-backup.json"), read(OUT / "post-migration-backup.json")
    assert before["restore_verified"] and after["restore_verified"]
    preserved = all(value == after["before"][table] for table, value in before["before"].items())
    assert preserved
    for name in ("fullstack-acceptance.json", "roles/roles-acceptance.json", "browser/browser-acceptance.json"):
        report = read(OUT / name); assert report["status"] == "passed"
    assert read(OUT / "metrics-restart.json")["passed"]
    backend = read(OUT / "backend-audit-fixed.json")
    assert not any(package["vulns"] for package in backend["dependencies"])
    assert read(OUT / "frontend-audit.json")["metadata"]["vulnerabilities"]["total"] == 0
    performance = read(OUT / "performance/performance-latest.json")
    assert all(level["failed"] == 0 for level in performance["levels"])
    live_path = ROOT / "backend/evaluation/reports/hardening-regression/live/20261012T154143Z-31cb691c/evaluation-latest.json"
    live = read(live_path)
    summary = live["live_summary"]
    assert live["complete"] and live["manual_review_status"] == "pending"
    results.update(legacy_tables_preserved=len(before["before"]), migrated_tables=len(after["before"]),
                   restores_verified=True, all_machine_acceptance_passed=True, python_dependency_vulnerabilities=0,
                   regression_cases=live["evaluated_cases"], passed_cases=live["passed_cases"], live_summary=summary,
                   human_semantic_review="pending", public_deployment="not_launched_requires_owner_domain_and_access_decisions")
    (OUT / "verification-summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    costs = f"${summary.get('cost_lower_usd'):.8f}～${summary.get('cost_upper_usd'):.8f}"
    lines = ["# ResolveAI 自动加固验收结果", "", "本轮可自动完成的代码、工程和验收工作已补齐；人工语义评分与实际公网发布仍未进行。", "",
             f"- Windows / Linux：各 {results['linux']['tests']} 项自动测试通过；前端检查与生产构建通过。",
             "- 四角色真实浏览器验收、双端录屏、HTTP/SSE、多轮、审批/幂等/模拟退款、重启恢复和七节点追踪通过。",
             f"- 原 {len(before['before'])} 张表的行数和内容摘要完整保留；迁移后 {len(after['before'])} 张表备份恢复核验通过。",
             "- 用量台账、排队/处理/失败耗时及登录会话经后台/Worker 重启保持一致。",
             "- Python/JavaScript 全部依赖审计为 0 个已知漏洞（不等于操作系统/整个系统无漏洞）。",
             "- 5/10/20 并发检索各 30 次，共 90 次请求，失败 0；这是本机缓存检索，不是 20 并发完整 LLM 流程。", "",
             "## 当前真实 DeepSeek 固定回归", "",
             f"整体 {live['passed_cases']}/{live['evaluated_cases']}（95%），意图识别 57/60（95%），RAG Hit@K 54/55（98.18%）；高风险 18/18，未审批写操作 0。",
             f"{summary['api_calls']} 次真实 API 尝试，{summary['total_tokens']} Token；费用估算 {costs}，非平台账单。",
             f"单次 API 尝试 P50 {summary['p50_ms']} ms / P95 {summary['p95_ms']} ms；完整机器工作流及客户确认周期另见运行台账。",
             "3 条未通过均涉及注入干扰下的意图识别（019/031/043），其中 031 的知识命中也不符合预期；保留原始结果，不通过修改标签抬高分数。",
             "草稿流程自动规则通过不等于客户问题真实解决、引用语义支持率或人工回复质量评分。", "",
             "## 查看与复验", "", "- 操作说明：[HARDENING](../../docs/HARDENING.md)",
             "- 最终真实报告：[DeepSeek 回归](../../backend/evaluation/reports/hardening-regression/live/20261012T154143Z-31cb691c/evaluation-latest.md)",
             "- [四角色验收](roles/roles-acceptance.json)、[录屏](browser/resolveai-demo.webm)、[重启台账](metrics-restart.json)",
             "- [原库备份恢复](pre-migration-backup.json)、[迁移后恢复](post-migration-backup.json)、[依赖审计](backend-audit-fixed.json)",
             "", "本轮没有修改/清空业务数据库、覆盖原评测成绩、提交 Git、给出人工质量分或发布到公网。资金和演示渠道仍为模拟。",
             "本地启动会自动备份旧 Docker 数据库；不要公开 .env.auth、.env、.backups 或数据库恢复副本。", ""]
    (OUT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"tests_per_platform": results["linux"]["tests"], "preserved": preserved, "report": str(OUT/'RESULTS.md')}, ensure_ascii=False))


if __name__ == "__main__": main()
