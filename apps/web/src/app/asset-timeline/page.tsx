"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type Asset = {
  id: number; category: string; name: string; description: string; appearance: string;
  clothing: string; personality: string; consistency_prompt: string; style: string;
  confirmed: boolean; data?: { source_novel_id?: number; evolution_history?: Evolution[] };
};
type Evolution = {
  chapter_number: number; event: string; change_type: string; appearance_after?: string;
  clothing_after?: string; age_after?: string; hairstyle_after?: string; injury_after?: string;
  equipment_after?: string; condition_after?: string; holder?: string; evidence?: string; source?: string;
  confidence?: number; confirmed?: boolean; recorded_at?: string;
};
type EffectiveState = {
  chapter_number?: number; event?: string; change_type?: string; appearance: string;
  clothing: string; age: string; hairstyle: string; injury: string; equipment: string;
  condition: string; holder: string; confirmed: boolean; is_evolved: boolean;
};
type HistoryResponse = { history: Evolution[]; effective_state: EffectiveState };
type LocalProposal = { id: number; asset_name: string; change_type: string; event: string; evidence: string; confidence: number; appearance_after?: string; clothing_after?: string; age_after?: string; hairstyle_after?: string; injury_after?: string; equipment_after?: string; condition_after?: string; holder?: string };

const categories = [
  ["all", "全部类别"], ["characters", "人物"], ["scenes", "场景"], ["props", "道具"],
  ["weapons", "武器"], ["costumes", "服装"], ["buildings", "建筑"], ["special_items", "特殊物品"],
];
const styles = "w-full rounded-xl border border-purple-300/20 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none focus:border-purple-400";
const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/70 p-5 shadow-xl shadow-purple-950/20";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
  return data as T;
}

export default function AssetTimelinePage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [chapter, setChapter] = useState("");
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [localProposals, setLocalProposals] = useState<LocalProposal[]>([]);
  const [form, setForm] = useState({
    chapter_number: "", change_type: "状态变化", event: "", appearance_after: "",
    clothing_after: "", age_after: "", hairstyle_after: "", injury_after: "",
    equipment_after: "", condition_after: "", holder: "", evidence: "", confirmed: false,
  });

  const selected = useMemo(() => assets.find((item) => item.id === selectedId) || null, [assets, selectedId]);
  const filtered = useMemo(() => assets.filter((item) => {
    const matchesCategory = category === "all" || item.category === category;
    const term = query.trim().toLowerCase();
    return matchesCategory && (!term || item.name.toLowerCase().includes(term) || item.description.toLowerCase().includes(term));
  }), [assets, category, query]);

  const loadAssets = useCallback(async (preferredId?: number | null) => {
    const result = await api<Asset[]>("/api/visual/assets");
    setAssets(result);
    const next = preferredId ?? result[0]?.id ?? null;
    setSelectedId(result.some((item) => item.id === next) ? next : result[0]?.id ?? null);
  }, []);

  const loadHistory = useCallback(async (id: number, chapterNumber = chapter) => {
    const suffix = chapterNumber ? `?chapter_number=${encodeURIComponent(chapterNumber)}` : "";
    const result = await api<HistoryResponse>(`/api/visual/assets/${id}/evolution${suffix}`);
    setHistory(result);
  }, [chapter]);

  useEffect(() => { void loadAssets().catch((error) => setMessage(error.message)); }, [loadAssets]);
  useEffect(() => {
    setLocalProposals([]);
    if (!selectedId) { setHistory(null); setPrompt(""); return; }
    void loadHistory(selectedId).catch((error) => setMessage(error.message));
  }, [selectedId, chapter, loadHistory]);

  async function refresh() {
    setBusy(true);
    try { await loadAssets(selectedId); if (selectedId) await loadHistory(selectedId); setMessage("素材与剧情历史已刷新。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "刷新失败"); }
    finally { setBusy(false); }
  }

  async function saveEvolution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    if (!form.chapter_number || !form.event.trim()) { setMessage("请填写章节编号和剧情事件。"); return; }
    setBusy(true);
    try {
      await api(`/api/visual/assets/${selectedId}/evolution`, {
        method: "POST",
        body: JSON.stringify({
          chapter_number: Number(form.chapter_number), change_type: form.change_type,
          event: form.event, appearance_after: form.appearance_after,
          clothing_after: form.clothing_after, age_after: form.age_after,
          hairstyle_after: form.hairstyle_after, injury_after: form.injury_after,
          equipment_after: form.equipment_after, condition_after: form.condition_after,
          holder: form.holder, evidence: form.evidence, confirmed: form.confirmed,
          source: "manual", confidence: 1,
        }),
      });
      setForm({ chapter_number: "", change_type: "状态变化", event: "", appearance_after: "", clothing_after: "", age_after: "", hairstyle_after: "", injury_after: "", equipment_after: "", condition_after: "", holder: "", evidence: "", confirmed: false });
      await loadAssets(selectedId);
      await loadHistory(selectedId);
      setMessage("剧情变化已写入历史。未勾选确认的记录会标记为待确认，不会伪装成已核实事实。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存剧情变化失败"); }
    finally { setBusy(false); }
  }

  async function generatePrompt() {
    if (!selectedId) return;
    setBusy(true);
    try {
      const suffix = chapter ? `?chapter_number=${encodeURIComponent(chapter)}` : "";
      const result = await api<{ positive_prompt?: string; negative_prompt?: string; prompt?: string }>(`/api/visual/assets/${selectedId}/prompt${suffix}`);
      setPrompt([result.positive_prompt || result.prompt || "", result.negative_prompt ? "负面提示词：\n" + result.negative_prompt : ""].filter(Boolean).join("\n\n"));
      setMessage("已按所选章节状态生成素材一致性提示词。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "提示词生成失败"); }
    finally { setBusy(false); }
  }

  async function copyPrompt() {
    if (!prompt) return;
    try { await navigator.clipboard.writeText(prompt); setMessage("提示词已复制。"); }
    catch { setMessage("浏览器不允许自动复制，请手动选中提示词复制。"); }
  }

  async function analyzeLocalEvolution() {
    const novelId = selected?.data?.source_novel_id;
    const chapterNumber = Number(chapter);
    if (!novelId) { setMessage("该素材没有关联小说，请先从小说分析结果导入素材。"); return; }
    if (!Number.isInteger(chapterNumber) || chapterNumber < 1) { setMessage("请先填写要分析的章节编号。"); return; }
    setBusy(true); setLocalProposals([]);
    try {
      const result = await api<{ items: LocalProposal[]; message: string }>(`/api/ai-collaboration/novels/${novelId}/chapters/${chapterNumber}/local-analyze-evolution`, { method: "POST", body: JSON.stringify({}) });
      setLocalProposals(result.items || []);
      setMessage(result.message || "本机 AI 分析完成。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "本机 AI 分析失败"); }
    finally { setBusy(false); }
  }

  async function reviewLocalProposal(id: number, action: "approve" | "reject") {
    setBusy(true);
    try {
      const result = await api<{ message?: string; asset_id?: number }>(`/api/ai-collaboration/proposals/${id}/review`, { method: "POST", body: JSON.stringify({ action, note: "" }) });
      setLocalProposals(current => current.filter(item => item.id !== id));
      if (action === "approve" && result.asset_id) {
        setSelectedId(result.asset_id);
        await loadHistory(result.asset_id);
      } else if (selectedId) await loadHistory(selectedId);
      setMessage(result.message || (action === "approve" ? "已确认并写入素材状态。" : "已拒绝该变化提议。"));
    } catch (error) { setMessage(error instanceof Error ? error.message : "审核失败"); }
    finally { setBusy(false); }
  }

  return (
    <main className="cosmic min-h-screen space-y-6 p-5 text-white md:p-8">
      <header className="mx-auto max-w-7xl">
        <div className="text-xs tracking-[0.25em] text-purple-300">AI COMIC STUDIO / ASSET TIMELINE</div>
        <h1 className="mt-2 text-3xl font-bold">人物与素材剧情状态追踪</h1>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-purple-100/75">先让本机 Ollama 自动识别章节中的人物、场景、服装和道具变化，再核对原文证据并确认写入；历史版本会保留，分镜匹配使用对应章节的状态快照。</p>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className={panel}>
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-semibold">素材列表</h2>
            <button onClick={() => void refresh()} disabled={busy} className="rounded-lg border border-purple-300/25 px-3 py-1.5 text-xs disabled:opacity-50">刷新</button>
          </div>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索素材名称或描述" className={styles + " mt-4"} />
          <select value={category} onChange={(event) => setCategory(event.target.value)} className={styles + " mt-3"}>
            {categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <div className="mt-4 max-h-[65vh] space-y-2 overflow-auto pr-1">
            {filtered.map((item) => (
              <button key={item.id} onClick={() => setSelectedId(item.id)} className={`w-full rounded-xl border p-3 text-left transition ${selectedId === item.id ? "border-fuchsia-300/60 bg-purple-900/45" : "border-white/10 bg-slate-900/55 hover:border-purple-300/35"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{item.name}</span>
                  <span className="text-[10px] text-purple-200">{item.category}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{item.description || "暂无描述"}</p>
                <div className="mt-2 text-[10px] text-slate-500">ID {item.id} · {item.data?.evolution_history?.length || 0} 条历史</div>
              </button>
            ))}
            {!filtered.length && <p className="rounded-xl border border-dashed border-white/15 p-6 text-center text-sm text-slate-400">没有找到素材。</p>}
          </div>
        </aside>

        <div className="space-y-5">
          {message && <div className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3 text-sm text-cyan-100">{message}</div>}
          {selected ? <>
            <section className={panel}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><p className="text-xs text-purple-300">当前选择</p><h2 className="mt-1 text-2xl font-bold">{selected.name}</h2><p className="mt-1 text-xs text-slate-400">{selected.category} · 素材 ID {selected.id} · {selected.confirmed ? "素材已确认" : "素材待确认"}</p></div>
                <a href="/visual-studio" className="rounded-lg border border-purple-300/25 px-3 py-2 text-xs text-purple-100">返回视觉素材工作室</a>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                <div className="rounded-xl bg-slate-900/70 p-4"><p className="text-xs text-slate-400">基础外观</p><p className="mt-2 whitespace-pre-wrap text-sm">{selected.appearance || "未填写"}</p></div>
                <div className="rounded-xl bg-slate-900/70 p-4"><p className="text-xs text-slate-400">基础服装 / 装备</p><p className="mt-2 whitespace-pre-wrap text-sm">{selected.clothing || "未填写"}</p></div>
              </div>
              <div className="mt-4 flex flex-wrap items-end gap-3">
                <label className="min-w-44 flex-1 text-xs text-slate-300">查看截至章节（留空=最新）
                  <input value={chapter} onChange={(event) => setChapter(event.target.value)} type="number" min="1" placeholder="例如 25" className={styles + " mt-1"} />
                </label>
                <button onClick={() => void generatePrompt()} disabled={busy} className="rounded-lg bg-violet-700 px-4 py-2.5 text-sm disabled:opacity-50">生成该章节提示词</button>
                <button onClick={() => void analyzeLocalEvolution()} disabled={busy || !selected.data?.source_novel_id || !chapter.trim()} className="rounded-lg bg-emerald-700 px-4 py-2.5 text-sm disabled:opacity-50">本机 AI 自动识别本章素材变化</button>
                {!selected.data?.source_novel_id && <span className="text-xs text-amber-200">该素材尚未关联小说，无法自动分析。</span>}
                {prompt && <button onClick={() => void copyPrompt()} className="rounded-lg border border-cyan-300/30 px-4 py-2.5 text-sm text-cyan-100">复制提示词</button>}
              </div>
              {history?.effective_state && <div className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-950/20 p-4">
                <div className="text-xs font-semibold text-cyan-200">该章节生效状态 {history.effective_state.chapter_number ? "· 最近一次变化：第 " + history.effective_state.chapter_number + " 章" : "· 基础设定"}</div>
                <div className="mt-2 grid gap-3 text-sm md:grid-cols-3">
                  <div><span className="text-slate-400">外观：</span>{history.effective_state.appearance || "无记录"}</div>
                  <div><span className="text-slate-400">年龄：</span>{history.effective_state.age || "未明确"}</div>
                  <div><span className="text-slate-400">发型：</span>{history.effective_state.hairstyle || "未明确"}</div>
                  <div><span className="text-slate-400">伤势：</span>{history.effective_state.injury || "无已确认变化"}</div>
                  <div><span className="text-slate-400">服装：</span>{history.effective_state.clothing || "无记录"}</div>
                  <div><span className="text-slate-400">装备：</span>{history.effective_state.equipment || "无记录"}</div>
                  <div><span className="text-slate-400">道具/场景状态：</span>{history.effective_state.condition || "无已确认变化"}</div>
                  <div><span className="text-slate-400">持有人：</span>{history.effective_state.holder || "无记录"}</div>
                </div>
              </div>}
              {prompt && <textarea readOnly value={prompt} rows={8} className={styles + " mt-4 font-mono text-xs leading-5"} />}
            </section>

            {localProposals.length > 0 && <section className={panel}>
              <h2 className="text-lg font-semibold">本机 AI 发现的素材变化（待确认）</h2>
              <p className="mt-1 text-xs text-slate-400">AI 只提出候选，不会直接修改正式素材。每条都必须包含能在本章正文中逐字找到的证据。</p>
              <div className="mt-3 space-y-3">{localProposals.map(item => <article key={item.id} className="rounded-xl border border-emerald-300/20 bg-emerald-950/20 p-3">
                <div className="flex flex-wrap justify-between gap-2"><b>{item.asset_name} · {item.change_type}</b><span className="text-xs text-emerald-200">置信度 {Math.round(item.confidence * 100)}%</span></div>
                <p className="mt-2 text-sm">{item.event}</p>
                <p className="mt-2 border-l-2 border-cyan-400 pl-3 text-xs text-cyan-100">原文证据：{item.evidence}</p>
                <div className="mt-3 flex gap-2"><button disabled={busy} onClick={() => void reviewLocalProposal(item.id, "approve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs">确认并写入状态</button><button disabled={busy} onClick={() => void reviewLocalProposal(item.id, "reject")} className="rounded-lg border border-red-300/30 px-3 py-2 text-xs">拒绝</button></div>
              </article>)}</div>
            </section>}

            <section className={panel}>
              <h2 className="text-lg font-semibold">剧情状态历史</h2>
              <p className="mt-1 text-xs text-slate-400">历史记录按章节排序，不会自动覆盖旧版本。未确认的记录会明确标记。</p>
              <div className="mt-4 space-y-3">
                {[...(history?.history || [])].sort((a, b) => a.chapter_number - b.chapter_number).map((item, index) => (
                  <article key={item.recorded_at || index} className="rounded-xl border border-white/10 bg-slate-900/60 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">第 {item.chapter_number} 章 · {item.change_type}</h3><span className={`rounded-full px-2 py-1 text-[10px] ${item.confirmed !== false ? "bg-emerald-900/50 text-emerald-200" : "bg-amber-900/50 text-amber-200"}`}>{item.confirmed !== false ? "已确认" : "待确认"}</span></div>
                    <p className="mt-2 text-sm">{item.event}</p>
                    <div className="mt-2 grid gap-2 text-xs text-slate-300 md:grid-cols-3">
                      {item.appearance_after && <p>外观：{item.appearance_after}</p>}
                      {item.age_after && <p>年龄：{item.age_after}</p>}
                      {item.hairstyle_after && <p>发型：{item.hairstyle_after}</p>}
                      {item.injury_after && <p>伤势：{item.injury_after}</p>}
                      {item.clothing_after && <p>服装：{item.clothing_after}</p>}
                      {item.equipment_after && <p>装备：{item.equipment_after}</p>}
                      {item.condition_after && <p>道具/场景状态：{item.condition_after}</p>}
                      {item.holder && <p>持有人/归属：{item.holder}</p>}
                    </div>
                    {item.evidence && <blockquote className="mt-3 border-l-2 border-cyan-400/50 pl-3 text-xs text-cyan-100">原文证据：{item.evidence}</blockquote>}
                    <p className="mt-2 text-[10px] text-slate-500">来源：{item.source || "历史记录"} · 置信度：{Math.round((item.confidence ?? 1) * 100)}% · {item.recorded_at || ""}</p>
                  </article>
                ))}
                {!history?.history?.length && <p className="rounded-xl border border-dashed border-white/15 p-6 text-center text-sm text-slate-400">该素材还没有剧情变化记录。</p>}
              </div>
            </section>

            <section className={panel}>
              <h2 className="text-lg font-semibold">新增剧情变化记录</h2>
              <p className="mt-1 text-xs text-slate-400">可记录受伤、服装更换、年龄/外貌变化、装备升级、道具损坏或转交等事件。</p>
              <form onSubmit={saveEvolution} className="mt-4 grid gap-3 md:grid-cols-2">
                <label className="text-xs text-slate-300">发生章节<input required min="1" type="number" value={form.chapter_number} onChange={(e) => setForm({ ...form, chapter_number: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">变化类型<select value={form.change_type} onChange={(e) => setForm({ ...form, change_type: e.target.value })} className={styles + " mt-1"}>{["状态变化","年龄变化","外貌变化","发型变化","受伤","服装变化","装备变化","道具损坏","道具升级","持有人变化","场景变化","其他"].map((x) => <option key={x}>{x}</option>)}</select></label>
                <label className="md:col-span-2 text-xs text-slate-300">剧情事件<input required minLength={2} value={form.event} onChange={(e) => setForm({ ...form, event: e.target.value })} placeholder="例如：主角在第25章战斗中左臂受伤" className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">变化后的外观<textarea rows={2} value={form.appearance_after} onChange={(e) => setForm({ ...form, appearance_after: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">年龄（人物）<input value={form.age_after} onChange={(e) => setForm({ ...form, age_after: e.target.value })} placeholder="例如：从少年成长为青年" className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">发型（人物）<input value={form.hairstyle_after} onChange={(e) => setForm({ ...form, hairstyle_after: e.target.value })} placeholder="例如：长发束起" className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">伤势 / 疤痕<textarea rows={2} value={form.injury_after} onChange={(e) => setForm({ ...form, injury_after: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">服装变化<textarea rows={2} value={form.clothing_after} onChange={(e) => setForm({ ...form, clothing_after: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">装备变化<textarea rows={2} value={form.equipment_after} onChange={(e) => setForm({ ...form, equipment_after: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">道具/场景当前状态<textarea rows={2} value={form.condition_after} onChange={(e) => setForm({ ...form, condition_after: e.target.value })} placeholder="例如：破损、修复、升级、位置变化" className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">当前持有人 / 归属<input value={form.holder} onChange={(e) => setForm({ ...form, holder: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="text-xs text-slate-300">原文证据（建议逐字引用）<textarea rows={2} value={form.evidence} onChange={(e) => setForm({ ...form, evidence: e.target.value })} className={styles + " mt-1"} /></label>
                <label className="md:col-span-2 flex items-center gap-2 text-xs text-amber-200"><input type="checkbox" checked={form.confirmed} onChange={(e) => setForm({ ...form, confirmed: e.target.checked })} />我已核对原文并确认这条变化真实发生</label>
                <button disabled={busy} className="rounded-xl bg-gradient-to-r from-violet-700 to-fuchsia-700 px-5 py-3 text-sm font-semibold disabled:opacity-50 md:col-span-2">{busy ? "正在保存…" : "保存剧情历史记录"}</button>
              </form>
            </section>
          </> : <section className={panel}><p className="text-sm text-slate-400">请先从左侧选择一个素材。</p></section>}
        </div>
      </div>
    </main>
  );
}
