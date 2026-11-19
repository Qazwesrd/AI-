"use client";

import { useEffect, useMemo, useState } from "react";

type Novel = { id: number; title: string; genre?: string };
type Chapter = { id: number; novel_id: number; chapter_number: number; title: string; word_count: number };
type Handoff = {
  novel_id: number; chapter_id: number; chapter_number: number; content_chars: number;
  truncated: boolean; instruction: string; output_schema: unknown; context: unknown;
  third_party_transfer_started: boolean; notice: string;
};
type Proposal = {
  id: number; chapter_number: number; asset_name: string; asset_category: string;
  change_type: string; event: string; appearance_after: string; clothing_after: string;
  holder: string; evidence: string; confidence: number; status: string; review_note?: string;
};
const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
  return data as T;
}

export default function AICollaborationPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [novelId, setNovelId] = useState("");
  const [chapterNumber, setChapterNumber] = useState("");
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [resultJson, setResultJson] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("pending_review");

  const selectedNovel = useMemo(() => novels.find((item) => String(item.id) === novelId), [novels, novelId]);
  const selectedChapter = useMemo(() => chapters.find((item) => String(item.chapter_number) === chapterNumber), [chapters, chapterNumber]);

  useEffect(() => {
    request<{ items: Novel[] }>("/api/novels")
      .then((data) => {
        setNovels(data.items || []);
        if (data.items?.length) setNovelId(String(data.items[0].id));
      })
      .catch((error) => setMessage(error.message));
  }, []);

  useEffect(() => {
    if (!novelId) { setChapters([]); setChapterNumber(""); setProposals([]); setHandoff(null); return; }
    request<{ items: Chapter[] }>(`/api/novels/${novelId}/chapters`)
      .then((data) => { setChapters(data.items || []); setChapterNumber(data.items?.length ? String(data.items[0].chapter_number) : ""); })
      .catch((error) => setMessage(error.message));
    void loadProposals(novelId, filter);
  }, [novelId]);

  async function loadProposals(id = novelId, status = filter) {
    if (!id) return;
    try {
      const data = await request<{ items: Proposal[] }>(`/api/ai-collaboration/novels/${id}/proposals?status=${encodeURIComponent(status)}`);
      setProposals(data.items || []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "读取待审核提议失败"); }
  }

  async function prepareHandoff() {
    if (!novelId || !chapterNumber) { setMessage("请先选择小说和章节。"); return; }
    if (!window.confirm(`即将读取“${selectedNovel?.title || "所选小说"}”第 ${chapterNumber} 章，并将章节正文及已确认分析摘要展示在交接预览中。内容不会自动发送给第三方网站。继续吗？`)) return;
    setBusy(true); setMessage("正在从本地数据库准备章节交接包……");
    try {
      const data = await request<Handoff>(`/api/ai-collaboration/novels/${novelId}/chapters/${chapterNumber}/handoff-package`, {
        method: "POST", body: JSON.stringify({ confirm_selected_chapter: true, include_confirmed_analysis: true }),
      });
      setHandoff(data);
      setMessage(data.truncated ? "交接包已准备，但章节正文已按长度上限截断，请先确认。" : "交接包已准备。请检查内容后再复制到 AI 网页。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "交接包准备失败"); }
    finally { setBusy(false); }
  }

  const handoffText = handoff ? [
    handoff.instruction,
    "【结构化输出规范】",
    JSON.stringify(handoff.output_schema, null, 2),
    "【软件交接资料】",
    JSON.stringify(handoff.context, null, 2),
    "【重要】请只返回符合结构规范的 JSON。剧情变化必须逐字引用章节证据，不得把推断当事实。",
  ].join("\n\n") : "";

  async function copyHandoff() {
    if (!handoffText) return;
    try { await navigator.clipboard.writeText(handoffText); setMessage("交接内容已复制。请自行打开所选 AI 网页并提交；软件不会代替你点击生成。"); }
    catch { setMessage("浏览器不允许自动复制，请选中预览内容手动复制。"); }
  }

  async function importResult() {
    if (!novelId || !chapterNumber) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(resultJson);
      const proposalsToImport = Array.isArray(parsed) ? parsed : parsed.proposals;
      if (!Array.isArray(proposalsToImport) || !proposalsToImport.length) throw new Error("JSON 中没有 proposals 数组。");
      const data = await request<{ created_count: number; duplicates_skipped: number; message: string }>(
        `/api/ai-collaboration/novels/${novelId}/proposals/import`,
        { method: "POST", body: JSON.stringify({
          novel_id: Number(novelId),
          source_text: handoff?.context ? (handoff.context as { chapter?: { content?: string } }).chapter?.content || "selected chapter" : "selected chapter",
          source_task_key: `web-ai-${novelId}-chapter-${chapterNumber}`,
          proposals: proposalsToImport,
        }) },
      );
      setMessage(`已导入 ${data.created_count} 项待审核剧情变化；跳过重复项 ${data.duplicates_skipped} 项。尚未覆盖正式素材。`);
      setResultJson("");
      await loadProposals();
    } catch (error) { setMessage(error instanceof Error ? error.message : "结果导入失败"); }
    finally { setBusy(false); }
  }

  async function reviewProposal(id: number, action: "approve" | "reject") {
    const note = window.prompt(action === "approve" ? "确认审核通过？可填写备注：" : "请填写拒绝原因（可留空）：", "") ?? null;
    if (note === null) return;
    setBusy(true);
    try {
      const data = await request<{ message?: string }>(`/api/ai-collaboration/proposals/${id}/review`, {
        method: "POST", body: JSON.stringify({ action, note }),
      });
      setMessage(data.message || (action === "approve" ? "已审核通过。" : "已拒绝该提议。"));
      await loadProposals();
    } catch (error) { setMessage(error instanceof Error ? error.message : "审核操作失败"); }
    finally { setBusy(false); }
  }

  return (
    <main className="cosmic min-h-screen px-4 py-8 text-white">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <p className="text-xs font-semibold tracking-[0.25em] text-cyan-300">DESKTOP · WEB AI COLLABORATION</p>
          <h1 className="mt-2 text-3xl font-bold">网页版 AI 协作执行中心</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">软件负责小说、人物、素材和数据库；网页 AI 负责分析与生成。所有交接都由你确认，结果经过证据校验和人工审核后才进入正式素材状态。</p>
        </header>

        {message && <div className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 px-4 py-3 text-sm text-cyan-100">{message}</div>}

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">① 选择小说与章节，准备 AI 交接包</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm text-slate-300">小说
              <select value={novelId} onChange={(event) => { setNovelId(event.target.value); setHandoff(null); }} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">选择小说</option>{novels.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-300">章节
              <select value={chapterNumber} onChange={(event) => { setChapterNumber(event.target.value); setHandoff(null); }} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">选择章节</option>{chapters.map((item) => <option key={item.id} value={item.chapter_number}>第 {item.chapter_number} 章 · {item.title}（{item.word_count} 字）</option>)}
              </select>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button onClick={() => void prepareHandoff()} disabled={busy || !novelId || !chapterNumber} className="rounded-lg bg-gradient-to-r from-violet-600 to-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">{busy ? "处理中…" : "确认章节并准备交接包"}</button>
            <span className="text-xs text-slate-400">只交接所选章节和已确认分析摘要，不会自动上传整本小说。</span>
          </div>
          {handoff && <div className="mt-5 space-y-3">
            <div className="flex flex-wrap gap-3 text-xs text-slate-300">
              <span>章节字数：{handoff.content_chars.toLocaleString()}</span>
              <span>正文截断：{handoff.truncated ? "是，请复核" : "否"}</span>
              <span>第三方发送：{handoff.third_party_transfer_started ? "已开始" : "未发送"}</span>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-xs text-amber-100">{handoff.notice}</div>
            <textarea readOnly value={handoffText} rows={14} className="w-full rounded-xl border border-white/10 bg-slate-950 p-3 text-xs leading-5 text-slate-200" />
            <button onClick={() => void copyHandoff()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100">复制完整交接包</button>
          </div>}
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">② 接收 AI 返回的剧情分析与变化提议</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">请让网页 AI 按交接包要求返回 JSON，再粘贴到这里。服务端会用数据库中保存的章节原文逐字核对证据；不匹配的证据不会入库。</p>
          <textarea value={resultJson} onChange={(event) => setResultJson(event.target.value)} rows={12} placeholder={'粘贴 AI 返回的 JSON，例如：{"chapter_number":1,"proposals":[...]}'} className="mt-3 w-full rounded-xl border border-white/10 bg-slate-950 p-3 font-mono text-xs leading-5 text-white" />
          <button onClick={() => void importResult()} disabled={busy || !resultJson.trim() || !novelId} className="mt-3 rounded-lg bg-emerald-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">校验并导入待审核提议</button>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">③ 剧情变化审核与素材状态记录</h2>
            <div className="flex gap-2">
              <select value={filter} onChange={(event) => { setFilter(event.target.value); void loadProposals(novelId, event.target.value); }} className="rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-xs">
                <option value="pending_review">待审核</option><option value="approved">已通过</option><option value="rejected">已拒绝</option><option value="all">全部</option>
              </select>
              <button onClick={() => void loadProposals()} className="rounded-lg border border-white/15 px-3 py-2 text-xs">刷新</button>
            </div>
          </div>
          <div className="mt-4 space-y-3">
            {proposals.map((item) => <article key={item.id} className="rounded-xl border border-white/10 bg-slate-950/70 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><h3 className="font-semibold">{item.asset_name} · {item.change_type}</h3><p className="mt-1 text-xs text-slate-400">第 {item.chapter_number} 章 · {item.asset_category} · 置信度 {(item.confidence * 100).toFixed(0)}% · {item.status}</p></div>
                {item.status === "pending_review" && <div className="flex gap-2"><button disabled={busy} onClick={() => void reviewProposal(item.id, "approve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs">通过并更新素材历史</button><button disabled={busy} onClick={() => void reviewProposal(item.id, "reject")} className="rounded-lg border border-red-300/30 px-3 py-2 text-xs text-red-200">拒绝</button></div>}
              </div>
              <p className="mt-2 text-sm text-slate-200">{item.event}</p>
              {item.appearance_after && <p className="mt-1 text-xs text-slate-300">外貌变化：{item.appearance_after}</p>}
              {item.clothing_after && <p className="mt-1 text-xs text-slate-300">服装变化：{item.clothing_after}</p>}
              {item.holder && <p className="mt-1 text-xs text-slate-300">持有者：{item.holder}</p>}
              <blockquote className="mt-3 border-l-2 border-cyan-400/50 pl-3 text-xs leading-5 text-cyan-100">原文证据：{item.evidence}</blockquote>
              {item.review_note && <p className="mt-2 text-xs text-slate-500">审核备注：{item.review_note}</p>}
            </article>)}
            {!proposals.length && <p className="rounded-xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-400">当前筛选条件下没有剧情变化提议。</p>}
          </div>
        </section>
        <p className="text-xs leading-5 text-slate-500">说明：本页面实现章节交接、JSON 结果接收和剧情变化审核。尚未实现第三方网页自动读取/自动提交，也尚未把所有人物状态和分镜素材匹配功能接入此页面。视频发布功能不在本模块修改范围内。</p>
      </div>
    </main>
  );
}
