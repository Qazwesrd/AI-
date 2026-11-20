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
  age_after?: string; hairstyle_after?: string; injury_after?: string;
  equipment_after?: string; condition_after?: string;
  holder: string; evidence: string; confidence: number; status: string; review_note?: string;
};
type ResultAttachment = {
  id: number; novel_id: number; chapter_number?: number | null; original_filename: string;
  media_type: string; mime_type: string; file_size: number; sha256: string;
  description: string; status: string; review_note?: string; media_file_id?: number | null;
  created_at?: string;
};
const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

const AI_WEB_PROVIDERS = {
  chatgpt: { label: "ChatGPT", url: "https://chatgpt.com/" },
  deepseek: { label: "DeepSeek", url: "https://chat.deepseek.com/" },
  doubao: { label: "豆包", url: "https://www.doubao.com/chat/" },
  grok: { label: "Grok", url: "https://grok.com/" },
} as const;
type AIWebProvider = keyof typeof AI_WEB_PROVIDERS;

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
  const [attachments, setAttachments] = useState<ResultAttachment[]>([]);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [attachmentDescription, setAttachmentDescription] = useState("");
  const [attachmentChapter, setAttachmentChapter] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("pending_review");
  const [aiProvider, setAiProvider] = useState<AIWebProvider>("chatgpt");
  const [autoOpenAi, setAutoOpenAi] = useState(true);
  const selectedAi = AI_WEB_PROVIDERS[aiProvider];

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
    void loadAttachments(novelId);
  }, [novelId]);

  async function loadProposals(id = novelId, status = filter) {
    if (!id) return;
    try {
      const data = await request<{ items: Proposal[] }>(`/api/ai-collaboration/novels/${id}/proposals?status=${encodeURIComponent(status)}`);
      setProposals(data.items || []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "读取待审核提议失败"); }
  }

  async function loadAttachments(id = novelId) {
    if (!id) return;
    try {
      const data = await request<{ items: ResultAttachment[] }>(`/api/ai-collaboration/results/attachments?novel_id=${id}&status=all`);
      setAttachments(data.items || []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "读取 AI 结果文件失败"); }
  }

  async function uploadAttachment() {
    if (!novelId || !selectedFile) { setMessage("请先选择小说和 AI 返回的文件。"); return; }
    if (!window.confirm("确认把这个文件导入本地待审核区？文件不会发送到第三方，审核通过后才加入正式媒体素材库。")) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("novel_id", novelId);
      if (attachmentChapter) form.append("chapter_number", attachmentChapter);
      form.append("confirm_import", "true");
      form.append("description", attachmentDescription);
      form.append("source_task_key", `web-ai-${novelId}-${attachmentChapter || "general"}`);
      form.append("file", selectedFile);
      const response = await fetch(API + "/api/ai-collaboration/results/attachments", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "文件导入失败");
      setMessage(data.message || "文件已进入待审核区。");
      setSelectedFile(null);
      setAttachmentDescription("");
      await loadAttachments();
    } catch (error) { setMessage(error instanceof Error ? error.message : "文件导入失败"); }
    finally { setBusy(false); }
  }

  async function reviewAttachment(id: number, action: "approve" | "reject") {
    const note = window.prompt(action === "approve" ? "确认将该文件加入正式媒体素材库？可填写备注：" : "拒绝原因（可留空）：", "") ?? null;
    if (note === null) return;
    setBusy(true);
    try {
      const data = await request<{ message: string }>(`/api/ai-collaboration/results/attachments/${id}/review`, {
        method: "POST", body: JSON.stringify({ action, note }),
      });
      setMessage(data.message);
      await loadAttachments();
    } catch (error) { setMessage(error instanceof Error ? error.message : "文件审核失败"); }
    finally { setBusy(false); }
  }

  function openSelectedAiWebsite(existingTab?: Window | null) {
    if (existingTab && !existingTab.closed) {
      existingTab.location.assign(selectedAi.url);
      return;
    }
    window.open(selectedAi.url, "_blank", "noopener,noreferrer");
  }

  async function prepareHandoff() {
    if (!novelId || !chapterNumber) { setMessage("请先选择小说和章节。"); return; }
    if (!window.confirm(`即将读取“${selectedNovel?.title || "所选小说"}”第 ${chapterNumber} 章，并将章节正文及已确认分析摘要展示在交接预览中。只会打开所选 AI 网站，不会自动发送章节内容。继续吗？`)) return;
    // Open a blank tab synchronously inside the click gesture so popup blockers
    // do not block the later navigation after the chapter request completes.
    const aiTab = autoOpenAi ? window.open("about:blank", "_blank") : null;
    if (aiTab) aiTab.opener = null;
    setBusy(true); setMessage("正在从本地数据库准备章节交接包……");
    try {
      const data = await request<Handoff>(`/api/ai-collaboration/novels/${novelId}/chapters/${chapterNumber}/handoff-package`, {
        method: "POST", body: JSON.stringify({ confirm_selected_chapter: true, include_confirmed_analysis: true }),
      });
      setHandoff(data);
      if (aiTab && !aiTab.closed) aiTab.location.assign(selectedAi.url);
      const openNotice = autoOpenAi
        ? (aiTab ? `已在独立浏览器标签页打开${selectedAi.label}，请手动复制交接包并提交。` : `浏览器拦截了自动打开，请点击“打开${selectedAi.label}网页”。`)
        : "自动打开已关闭，可稍后手动打开 AI 网页。";
      setMessage(`${data.truncated ? "交接包已准备，但章节正文已按长度上限截断，请先确认。" : "交接包已准备。"}${openNotice}`);
    } catch (error) {
      if (aiTab && !aiTab.closed) aiTab.close();
      setMessage(error instanceof Error ? error.message : "交接包准备失败");
    } finally { setBusy(false); }
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
    try { await navigator.clipboard.writeText(handoffText); setMessage(`交接内容已复制。请切换到${selectedAi.label}网页手动粘贴并提交；不会自动上传或点击生成。`); }
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
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
            <label className="text-sm text-slate-300">读取章节后打开哪个 AI 网页？
              <select value={aiProvider} onChange={(event) => setAiProvider(event.target.value as AIWebProvider)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                {Object.entries(AI_WEB_PROVIDERS).map(([key, provider]) => <option key={key} value={key}>{provider.label}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-slate-950 px-4 py-3 text-sm text-slate-200">
              <input type="checkbox" checked={autoOpenAi} onChange={(event) => setAutoOpenAi(event.target.checked)} className="h-4 w-4 accent-cyan-500" />
              读取完成后自动打开 AI 网页
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button onClick={() => void prepareHandoff()} disabled={busy || !novelId || !chapterNumber} className="rounded-lg bg-gradient-to-r from-violet-600 to-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">{busy ? "处理中…" : "读取章节并打开 AI 网页"}</button>
            <span className="text-xs text-slate-400">只读取所选章节和已确认分析摘要；AI 网页在独立浏览器标签页打开，不会接管或关闭 AI 漫剧大师。</span>
          </div>
          {handoff && <div className="mt-5 space-y-3">
            <div className="flex flex-wrap gap-3 text-xs text-slate-300">
              <span>章节字数：{handoff.content_chars.toLocaleString()}</span>
              <span>正文截断：{handoff.truncated ? "是，请复核" : "否"}</span>
              <span>第三方发送：{handoff.third_party_transfer_started ? "已开始" : "未发送"}</span>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-xs text-amber-100">{handoff.notice}</div>
            <textarea readOnly value={handoffText} rows={14} className="w-full rounded-xl border border-white/10 bg-slate-950 p-3 text-xs leading-5 text-slate-200" />
            <div className="flex flex-wrap gap-2">
              <button onClick={() => void copyHandoff()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100">复制完整交接包</button>
              <button onClick={() => openSelectedAiWebsite()} className="rounded-lg border border-purple-300/30 px-4 py-2 text-sm text-purple-100">打开{selectedAi.label}网页</button>
            </div>
            <p className="text-xs leading-5 text-slate-400">软件与 AI 网站相互独立：关闭 AI 网页不会关闭、退出或中断 AI 漫剧大师。章节内容不会自动发送，需你手动复制、粘贴并提交。</p>
          </div>}
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">② 接收 AI 返回的剧情分析与变化提议</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">请让网页 AI 按交接包要求返回 JSON，再粘贴到这里。服务端会用数据库中保存的章节原文逐字核对证据；不匹配的证据不会入库。</p>
          <textarea value={resultJson} onChange={(event) => setResultJson(event.target.value)} rows={12} placeholder={'粘贴 AI 返回的 JSON，例如：{"chapter_number":1,"proposals":[...]}'} className="mt-3 w-full rounded-xl border border-white/10 bg-slate-950 p-3 font-mono text-xs leading-5 text-white" />
          <button onClick={() => void importResult()} disabled={busy || !resultJson.trim() || !novelId} className="mt-3 rounded-lg bg-emerald-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">校验并导入待审核提议</button>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">③ 接收图片、视频、音频和文档文件</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">可导入网页版 AI 生成的图片、视频、音频、字幕、TXT、MD、JSON 和 PDF。文件先进入待审核区，审核通过后才加入正式媒体素材库。</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm text-slate-300">关联章节（可选）
              <select value={attachmentChapter} onChange={event => setAttachmentChapter(event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">不指定章节</option>
                {chapters.map(item => <option key={item.id} value={item.chapter_number}>第 {item.chapter_number} 章 · {item.title}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-300">选择 AI 返回文件
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/x-matroska,video/webm,audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,.srt,.vtt,.txt,.md,.json,.pdf" onChange={event => setSelectedFile(event.target.files?.[0] || null)} className="mt-2 block w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-xs text-white" />
            </label>
          </div>
          <input value={attachmentDescription} onChange={event => setAttachmentDescription(event.target.value)} placeholder="文件说明（可选，例如：主角第三版正面设定图）" className="mt-3 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-sm text-white" />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {selectedFile && <span className="text-xs text-cyan-200">{selectedFile.name} · {(selectedFile.size / 1024 / 1024).toFixed(1)} MB</span>}
            <button disabled={busy || !selectedFile || !novelId} onClick={() => void uploadAttachment()} className="rounded-lg bg-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">确认导入待审核区</button>
            <button onClick={() => void loadAttachments()} className="rounded-lg border border-white/15 px-4 py-3 text-sm">刷新文件</button>
          </div>
          <div className="mt-5 space-y-2">
            {attachments.map(item => <article key={item.id} className="rounded-xl border border-white/10 bg-slate-950/70 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all text-sm font-medium">{item.original_filename}</p>
                  <p className="mt-1 text-xs text-slate-400">{item.media_type} · {(item.file_size / 1024 / 1024).toFixed(1)} MB · {item.chapter_number ? `第 ${item.chapter_number} 章` : "未指定章节"}</p>
                  <p className="mt-1 break-all text-[10px] text-slate-500">SHA-256：{item.sha256}</p>
                  <a href={`${API}/api/ai-collaboration/results/attachments/${item.id}/file`} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-cyan-300 underline">预览 / 打开文件</a>
                  {item.description && <p className="mt-1 text-xs text-slate-300">{item.description}</p>}
                  {item.media_file_id && <p className="mt-1 text-xs text-emerald-300">媒体素材 ID：{item.media_file_id}</p>}
                </div>
                <span className={`rounded-full px-2 py-1 text-[10px] ${item.status === "approved" ? "bg-emerald-900/50 text-emerald-200" : item.status === "rejected" ? "bg-red-900/50 text-red-200" : "bg-amber-900/50 text-amber-200"}`}>{item.status === "approved" ? "已入库" : item.status === "rejected" ? "已拒绝" : "待审核"}</span>
              </div>
              {item.status === "pending_review" && <div className="mt-3 flex gap-2">
                <button disabled={busy} onClick={() => void reviewAttachment(item.id, "approve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs disabled:opacity-40">审核通过并入库</button>
                <button disabled={busy} onClick={() => void reviewAttachment(item.id, "reject")} className="rounded-lg border border-red-300/30 px-3 py-2 text-xs text-red-200 disabled:opacity-40">拒绝</button>
              </div>}
            </article>)}
            {!attachments.length && <p className="rounded-xl border border-dashed border-white/15 p-5 text-center text-xs text-slate-400">还没有导入 AI 结果文件。</p>}
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">④ 剧情变化审核与素材状态记录</h2>
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
              {item.age_after && <p className="mt-1 text-xs text-slate-300">年龄变化：{item.age_after}</p>}
              {item.hairstyle_after && <p className="mt-1 text-xs text-slate-300">发型变化：{item.hairstyle_after}</p>}
              {item.injury_after && <p className="mt-1 text-xs text-slate-300">伤势变化：{item.injury_after}</p>}
              {item.equipment_after && <p className="mt-1 text-xs text-slate-300">装备变化：{item.equipment_after}</p>}
              {item.condition_after && <p className="mt-1 text-xs text-slate-300">道具/场景状态：{item.condition_after}</p>}
              {item.holder && <p className="mt-1 text-xs text-slate-300">持有者：{item.holder}</p>}
              <blockquote className="mt-3 border-l-2 border-cyan-400/50 pl-3 text-xs leading-5 text-cyan-100">原文证据：{item.evidence}</blockquote>
              {item.review_note && <p className="mt-2 text-xs text-slate-500">审核备注：{item.review_note}</p>}
            </article>)}
            {!proposals.length && <p className="rounded-xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-400">当前筛选条件下没有剧情变化提议。</p>}
          </div>
        </section>
        <p className="text-xs leading-5 text-slate-500">说明：本页面实现章节交接、JSON 结果接收、图片/视频/音频/文档文件导入与审核，以及剧情变化审核。第三方网页的登录、上传和提交仍由用户主动完成；视频发布功能保持独立。</p>
      </div>
    </main>
  );
}
