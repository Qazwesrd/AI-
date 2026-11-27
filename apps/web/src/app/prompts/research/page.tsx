"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Prompt = { id: number; name: string; current_version: number; content: string };
type Evidence = { id: number; platform: string; work_title: string; work_url: string; observed_at: string; metric_summary: Record<string, unknown>; reliability: string; is_verified: boolean; verification_note: string };
type Candidate = { id: number; prompt_id: number; base_version: number; proposed_name: string; proposed_content: string; change_reason: string; expected_impact: string; evidence_ids: number[]; status: string; decision_note: string };

export default function PromptResearchPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedPrompt, setSelectedPrompt] = useState("");
  const [platform, setPlatform] = useState("红果");
  const [workTitle, setWorkTitle] = useState("");
  const [workUrl, setWorkUrl] = useState("");
  const [observedAt, setObservedAt] = useState(new Date().toISOString().slice(0, 10));
  const [metrics, setMetrics] = useState("");
  const [verifyNote, setVerifyNote] = useState("已人工核对公开页面、榜单位置及采样日期。");
  const [candidateName, setCandidateName] = useState("");
  const [candidateContent, setCandidateContent] = useState("");
  const [reason, setReason] = useState("");
  const [impact, setImpact] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<number[]>([]);
  const [decisionNote, setDecisionNote] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(API + path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败：" + response.status);
    return data as T;
  }

  async function load() {
    try {
      const [p, e, c] = await Promise.all([
        api<{items: Prompt[]}>("/api/prompts"),
        api<{items: Evidence[]}>("/api/prompts/research/evidence"),
        api<{items: Candidate[]}>("/api/prompts/research/candidates"),
      ]);
      setPrompts(p.items || []);
      setEvidence(e.items || []);
      setCandidates(c.items || []);
      if (!selectedPrompt && p.items?.length) setSelectedPrompt(String(p.items[0].id));
    } catch (error) { setMessage(error instanceof Error ? error.message : "加载研究数据失败"); }
  }

  useEffect(() => { void load(); }, []);

  const currentPrompt = prompts.find(p => String(p.id) === selectedPrompt) || null;

  async function addEvidence() {
    if (!platform.trim() || !workTitle.trim() || !observedAt) return setMessage("请填写平台、作品名称和采样日期。");
    setBusy(true);
    try {
      let metric_summary: Record<string, unknown> = {};
      if (metrics.trim()) metric_summary = JSON.parse(metrics);
      await api("/api/prompts/research/evidence", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({platform, work_title:workTitle, work_url:workUrl, observed_at:observedAt, metric_summary}),
      });
      setWorkTitle(""); setWorkUrl(""); setMetrics("");
      setMessage("证据已保存为待核验；系统不会仅凭一次榜单记录认定爆款。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存证据失败；指标请使用合法 JSON"); }
    finally { setBusy(false); }
  }

  async function verifyEvidence(id: number) {
    setBusy(true);
    try {
      await api(`/api/prompts/research/evidence/${id}/verify`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({verification_note:verifyNote}),
      });
      setMessage("证据已标记为人工核验。请确保来源、日期和指标真实可追溯。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "核验证据失败"); }
    finally { setBusy(false); }
  }

  async function createCandidate() {
    if (!currentPrompt) return setMessage("请先选择正式提示词。");
    if (!candidateName.trim() || !candidateContent.trim() || !reason.trim()) return setMessage("请填写候选名称、正文和修改理由。");
    if (selectedEvidence.length < 2) return setMessage("至少选择两条已核验证据。");
    setBusy(true);
    try {
      await api("/api/prompts/research/candidates", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          prompt_id:currentPrompt.id, base_version:currentPrompt.current_version,
          proposed_name:candidateName, proposed_content:candidateContent,
          change_reason:reason, expected_impact:impact, evidence_ids:selectedEvidence,
        }),
      });
      setCandidateName(""); setCandidateContent(""); setReason(""); setImpact(""); setSelectedEvidence([]);
      setMessage("升级候选已创建，正式提示词没有变化；需你在下方明确批准后才会生效。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建候选失败"); }
    finally { setBusy(false); }
  }

  async function decideCandidate(id: number, decision: "approve" | "reject") {
    const prompt = decision === "approve"
      ? "确认批准该候选并更新正式提示词？系统将创建新版本并保留旧版本。"
      : "确认拒绝该候选？正式提示词不会变化。";
    if (!window.confirm(prompt)) return;
    setBusy(true);
    try {
      const result = await api<{message:string}>(`/api/prompts/research/candidates/${id}/${decision}`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({decision_note:decisionNote}),
      });
      setMessage(result.message);
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "处理候选失败"); }
    finally { setBusy(false); }
  }

  return (
    <main className="min-h-screen p-5 md:p-9 text-white">
      <div className="mx-auto max-w-7xl space-y-6">
        <header>
          <a href="/prompts" className="text-sm text-cyan-200 hover:underline">← 返回提示词中心</a>
          <p className="mt-4 text-xs tracking-[4px] text-fuchsia-300">TREND RESEARCH</p>
          <h1 className="mt-2 text-3xl font-black">爆款研究证据库与提示词升级审批</h1>
          <p className="mt-2 text-sm text-purple-200/70">公开来源或人工录入 → 人工核验 → 多平台、多日期交叉验证 → 生成候选 → 你明确批准后更新正式版本。</p>
        </header>
        {message && <div role="status" className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3 text-sm text-cyan-100">{message}</div>}

        <section className="grid gap-5 lg:grid-cols-2">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
            <h2 className="text-lg font-bold">录入公开作品证据</h2>
            <p className="mt-1 text-xs text-white/50">当前支持手工登记公开榜单、作品页面和公开互动数据；尚未接入平台自动采集。</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="text-sm">平台 / 来源<input value={platform} onChange={e=>setPlatform(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">作品名称<input value={workTitle} onChange={e=>setWorkTitle(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">采样日期<input type="date" value={observedAt} onChange={e=>setObservedAt(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">公开页面链接<input value={workUrl} onChange={e=>setWorkUrl(e.target.value)} placeholder="https://" className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
            </div>
            <label className="mt-3 block text-sm">榜单、播放、互动等指标（JSON，可选）
              <textarea value={metrics} onChange={e=>setMetrics(e.target.value)} rows={3} placeholder='例如：{"榜单":"TOP20","点赞":"公开页面显示值"}' className="mt-1 w-full rounded-lg bg-black/20 p-3 font-mono text-xs" />
            </label>
            <button disabled={busy} onClick={()=>void addEvidence()} className="mt-3 rounded-xl bg-fuchsia-600 px-4 py-2 text-sm font-semibold disabled:opacity-50">保存待核验证据</button>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
            <h2 className="text-lg font-bold">研究证据清单</h2>
            <p className="mt-1 text-xs text-white/50">核验操作代表人工确认记录真实，不是平台官方认证。</p>
            <label className="mt-3 block text-xs">核验备注<input value={verifyNote} onChange={e=>setVerifyNote(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
            <div className="mt-3 max-h-[390px] space-y-2 overflow-y-auto">
              {evidence.map(item=><div key={item.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{item.work_title}</strong><span className={item.is_verified?"text-xs text-emerald-300":"text-xs text-amber-200"}>{item.is_verified?"已人工核验":"待核验"}</span></div>
                <p className="mt-1 text-xs text-white/60">{item.platform} · {item.observed_at} · {item.reliability}</p>
                {item.work_url && <a href={item.work_url} target="_blank" rel="noreferrer" className="mt-1 block break-all text-xs text-cyan-200 underline">查看公开来源</a>}
                <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] text-white/50">{JSON.stringify(item.metric_summary)}</pre>
                {!item.is_verified && <button disabled={busy} onClick={()=>void verifyEvidence(item.id)} className="mt-2 rounded-lg border border-amber-200/30 px-3 py-1 text-xs text-amber-100">人工核验此条</button>}
              </div>)}
              {!evidence.length && <p className="py-8 text-center text-sm text-white/40">还没有研究证据。</p>}
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <h2 className="text-lg font-bold">建立提示词升级候选</h2>
          <p className="mt-1 text-xs text-white/50">系统强制要求至少两个不同平台、两个不同日期的已核验证据。创建候选不会改动正式提示词。</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">要升级的正式提示词
              <select value={selectedPrompt} onChange={e=>setSelectedPrompt(e.target.value)} className="mt-1 w-full rounded-lg bg-[#171326] p-2">
                {prompts.map(p=><option key={p.id} value={p.id}>{p.name} · v{p.current_version}</option>)}
              </select>
            </label>
            <label className="text-sm">候选名称<input value={candidateName} onChange={e=>setCandidateName(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
          </div>
          {currentPrompt && <p className="mt-2 text-xs text-white/50">当前正式版本 v{currentPrompt.current_version}；候选基于此版本创建，若正式版本先发生变化，候选将自动失效。</p>}
          <label className="mt-3 block text-sm">候选提示词正文<textarea value={candidateContent} onChange={e=>setCandidateContent(e.target.value)} rows={7} className="mt-1 w-full rounded-lg bg-black/20 p-3 font-mono text-sm" /></label>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="text-sm">修改理由<textarea value={reason} onChange={e=>setReason(e.target.value)} rows={3} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
            <label className="text-sm">预期改善点<textarea value={impact} onChange={e=>setImpact(e.target.value)} rows={3} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
          </div>
          <div className="mt-4">
            <h3 className="text-sm font-semibold">选择已核验证据</h3>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {evidence.filter(e=>e.is_verified).map(e=><label key={e.id} className="flex items-start gap-2 rounded-lg border border-white/10 p-3 text-xs">
                <input type="checkbox" checked={selectedEvidence.includes(e.id)} onChange={event=>setSelectedEvidence(old=>event.target.checked?[...old,e.id]:old.filter(id=>id!==e.id))} />
                <span>{e.work_title} · {e.platform} · {e.observed_at}</span>
              </label>)}
            </div>
          </div>
          <button disabled={busy} onClick={()=>void createCandidate()} className="mt-4 rounded-xl bg-fuchsia-600 px-5 py-2 text-sm font-semibold disabled:opacity-50">生成待审批候选</button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <h2 className="text-lg font-bold">升级候选审批</h2>
          <label className="mt-3 block max-w-xl text-xs">审批备注<input value={decisionNote} onChange={e=>setDecisionNote(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
          <div className="mt-3 space-y-3">
            {candidates.map(c=><article key={c.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2"><strong>{c.proposed_name}</strong><span className="text-xs text-white/60">{c.status==="pending"?"待你确认":c.status==="approved"?"已批准":"已拒绝"}</span></div>
              <p className="mt-1 text-xs text-white/50">提示词 #{c.prompt_id} · 基准 v{c.base_version} · 证据 {c.evidence_ids.length} 条</p>
              <p className="mt-2 text-sm">{c.change_reason}</p>
              {c.expected_impact && <p className="mt-1 text-xs text-cyan-100/70">预期改善：{c.expected_impact}</p>}
              <details className="mt-3"><summary className="cursor-pointer text-xs text-cyan-200">查看候选完整正文</summary><pre className="mt-2 whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-xs">{c.proposed_content}</pre></details>
              {c.status==="pending" && <div className="mt-3 flex gap-2"><button disabled={busy} onClick={()=>void decideCandidate(c.id,"approve")} className="rounded-lg bg-emerald-700 px-4 py-2 text-xs font-semibold">确认更新正式提示词</button><button disabled={busy} onClick={()=>void decideCandidate(c.id,"reject")} className="rounded-lg border border-red-300/30 px-4 py-2 text-xs text-red-200">拒绝候选</button></div>}
              {c.decision_note && <p className="mt-2 text-xs text-white/40">处理备注：{c.decision_note}</p>}
            </article>)}
            {!candidates.length && <p className="py-6 text-center text-sm text-white/40">暂无升级候选。</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
