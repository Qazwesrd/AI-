"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Category = { id: number; name: string; description?: string; is_system: boolean };
type Prompt = { id: number; name: string; category_id: number | null; category_name: string; content: string; notes: string; tags: string[]; current_version: number };
type Ranking = { rank: number; prompt_id: number; prompt_name: string; version: number; test_count: number; average_score: number; score_type: string };
type Version = { id: number; version: number; name: string; content: string; change_reason: string; created_at: string };

const dimensions = [
  { value: "overall", label: "综合榜" },
  { value: "story", label: "剧本榜" },
  { value: "consistency", label: "人物一致性榜" },
  { value: "executability", label: "分镜可执行性榜" },
  { value: "visual", label: "视觉效果榜" },
];

export default function PromptCenterPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [content, setContent] = useState("");
  const [notes, setNotes] = useState("");
  const [changeReason, setChangeReason] = useState("创建提示词");
  const [search, setSearch] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [versions, setVersions] = useState<Version[]>([]);
  const [ranking, setRanking] = useState<Ranking[]>([]);
  const [dimension, setDimension] = useState("overall");
  const [testScores, setTestScores] = useState({ overall: "80", story: "80", consistency: "80", executability: "80", visual: "80" });
  const [dataset, setDataset] = useState("同一小说测试素材");
  const [modelName, setModelName] = useState("本地模型/人工评估");
  const importRef = useRef<HTMLInputElement>(null);

  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(API + path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败：" + response.status);
    return data as T;
  }

  async function load() {
    try {
      const c = await api<{ items: Category[] }>("/api/prompts/categories");
      setCategories(c.items || []);
      const p = await api<{ items: Prompt[] }>("/api/prompts" + (search ? "?q=" + encodeURIComponent(search) : ""));
      setPrompts(p.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载提示词失败");
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const selected = useMemo(() => prompts.find(item => item.id === selectedId) || null, [prompts, selectedId]);

  function startNew() {
    setSelectedId(null);
    setName("");
    setCategoryId("");
    setContent("");
    setNotes("");
    setChangeReason("创建提示词");
    setVersions([]);
  }

  async function editPrompt(item: Prompt) {
    setSelectedId(item.id);
    setName(item.name);
    setCategoryId(item.category_id ? String(item.category_id) : "");
    setContent(item.content);
    setNotes(item.notes || "");
    setChangeReason("编辑提示词");
    try {
      const result = await api<{ items: Version[] }>(`/api/prompts/${item.id}/versions`);
      setVersions(result.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取版本历史失败");
    }
  }

  async function savePrompt() {
    if (!name.trim() || !content.trim()) {
      setMessage("请填写提示词名称和内容。");
      return;
    }
    setBusy(true);
    try {
      const body = { name: name.trim(), category_id: categoryId ? Number(categoryId) : null, content, notes, change_reason: changeReason };
      const result = await api<{ item: Prompt }>(selectedId ? `/api/prompts/${selectedId}` : "/api/prompts", {
        method: selectedId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setMessage(selectedId ? "提示词已保存，新版本已留档。" : "提示词已创建并保存在本地数据库。");
      await load();
      if (result.item) await editPrompt(result.item);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally { setBusy(false); }
  }

  async function addCategory() {
    if (!newCategory.trim()) return;
    try {
      await api("/api/prompts/categories", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newCategory.trim() }),
      });
      setNewCategory("");
      setMessage("自定义分类已添加。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "添加分类失败"); }
  }

  async function refreshRanking() {
    setBusy(true);
    try {
      const result = await api<{ items: Ranking[]; notice: string }>("/api/prompts/ranking/refresh?dimension=" + dimension, { method: "POST" });
      setRanking(result.items || []);
      setMessage(result.notice);
    } catch (error) { setMessage(error instanceof Error ? error.message : "刷新排行榜失败"); }
    finally { setBusy(false); }
  }

  async function saveTest() {
    if (!selectedId) return setMessage("请先选择或保存一个提示词。");
    setBusy(true);
    try {
      const result = await api<{ message: string }>(`/api/prompts/${selectedId}/tests`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset_name: dataset, model_name: modelName,
          score_overall: Number(testScores.overall), score_story: Number(testScores.story),
          score_consistency: Number(testScores.consistency), score_executability: Number(testScores.executability),
          score_visual: Number(testScores.visual), notes: "由用户录入的实测评分",
        }),
      });
      setMessage(result.message);
    } catch (error) { setMessage(error instanceof Error ? error.message : "记录实测失败"); }
    finally { setBusy(false); }
  }

  async function exportPrompts() {
    try {
      const data = await api<any>("/api/prompts/export", { method: "POST" });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "AI漫剧提示词库.json"; a.click();
      URL.revokeObjectURL(url);
      setMessage("提示词库导出完成。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "导出失败"); }
  }

  async function importPrompts(file?: File) {
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const result = await api<{ imported_count: number; message: string }>("/api/prompts/import", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      setMessage(`导入完成：新增 ${result.imported_count} 条。现有提示词不会被覆盖。`);
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "导入失败，请检查 JSON 文件"); }
    finally { if (importRef.current) importRef.current.value = ""; }
  }

  async function archivePrompt() {
    if (!selectedId || !window.confirm("确定归档这条提示词？历史版本和实测记录会保留。")) return;
    try {
      await api(`/api/prompts/${selectedId}`, { method: "DELETE" });
      setMessage("提示词已归档，历史记录保留。");
      startNew(); await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "归档失败"); }
  }

  return (
    <main className="min-h-screen p-5 md:p-9 text-white">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs tracking-[4px] text-fuchsia-300">PROMPT LAB</p>
            <h1 className="mt-2 text-3xl font-black">爆款研究与智能提示词中心</h1>
            <p className="mt-2 text-sm text-purple-200/70">管理自己的提示词、版本和实测成绩。正式提示词更新必须由你确认。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={exportPrompts} className="rounded-xl border border-white/15 px-4 py-2 text-sm">导出提示词</button>
            <button onClick={() => importRef.current?.click()} className="rounded-xl border border-white/15 px-4 py-2 text-sm">导入提示词</button>
            <input ref={importRef} type="file" accept="application/json,.json" className="hidden" onChange={e => void importPrompts(e.target.files?.[0])} />
            <button onClick={startNew} className="rounded-xl bg-fuchsia-600 px-4 py-2 text-sm font-semibold">＋ 新建提示词</button>
          </div>
        </header>

        {message && <div role="status" className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3 text-sm text-cyan-100">{message}</div>}

        <section className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <h2 className="font-bold">我的提示词库</h2>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索名称、内容、备注" className="mt-3 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm" />
            <div className="mt-3 max-h-[520px] space-y-2 overflow-y-auto">
              {prompts.map(item => <button key={item.id} onClick={() => void editPrompt(item)} className={`w-full rounded-xl border p-3 text-left ${selectedId === item.id ? "border-fuchsia-400 bg-fuchsia-500/10" : "border-white/10 bg-black/10"}`}>
                <div className="font-medium">{item.name}</div>
                <div className="mt-1 text-xs text-purple-200/60">{item.category_name || "未分类"} · v{item.current_version}</div>
              </button>)}
              {!prompts.length && <p className="py-8 text-center text-sm text-white/40">暂无提示词，点击“新建提示词”添加。</p>}
            </div>
            <div className="mt-5 border-t border-white/10 pt-4">
              <h3 className="text-sm font-semibold">自定义分类</h3>
              <div className="mt-2 flex gap-2">
                <input value={newCategory} onChange={e => setNewCategory(e.target.value)} placeholder="输入新分类名称" className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm" />
                <button onClick={() => void addCategory()} className="rounded-lg border border-white/15 px-3 text-sm">添加</button>
              </div>
            </div>
          </aside>

          <section className="space-y-5">
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-bold">{selectedId ? "编辑提示词" : "创建自己的提示词"}</h2>
                {selected && <span className="text-xs text-fuchsia-200">当前正式版本 v{selected.current_version}</span>}
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <label className="text-sm">提示词名称
                  <input value={name} onChange={e => setName(e.target.value)} placeholder="例如：强钩子短剧剧本生成器" className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2" />
                </label>
                <label className="text-sm">分类
                  <select value={categoryId} onChange={e => setCategoryId(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-[#171326] px-3 py-2">
                    <option value="">未分类</option>
                    {categories.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </label>
              </div>
              <label className="mt-3 block text-sm">提示词正文
                <textarea value={content} onChange={e => setContent(e.target.value)} rows={10} placeholder="写入角色、任务、输出格式、限制条件和质量标准……" className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-3 font-mono text-sm leading-6" />
              </label>
              <label className="mt-3 block text-sm">备注 / 使用说明
                <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm" />
              </label>
              <label className="mt-3 block text-sm">本次修改原因
                <input value={changeReason} onChange={e => setChangeReason(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm" />
              </label>
              <div className="mt-4 flex flex-wrap gap-2">
                <button disabled={busy} onClick={() => void savePrompt()} className="rounded-xl bg-fuchsia-600 px-5 py-2 text-sm font-semibold disabled:opacity-50">{busy ? "处理中…" : "保存提示词"}</button>
                <button onClick={() => { void navigator.clipboard.writeText(content); setMessage("提示词正文已复制。"); }} className="rounded-xl border border-white/15 px-4 py-2 text-sm">复制正文</button>
                {selectedId && <button onClick={() => void archivePrompt()} className="rounded-xl border border-red-300/30 px-4 py-2 text-sm text-red-200">归档</button>}
              </div>
            </div>

            {selectedId && <div className="grid gap-5 lg:grid-cols-2">
              <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
                <h3 className="font-bold">实测评分记录</h3>
                <p className="mt-1 text-xs text-white/50">先用相同素材和模型测试，再录入评分；当前为人工录入，不会伪称自动测评。</p>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <label className="text-xs">测试素材<input value={dataset} onChange={e => setDataset(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
                  <label className="text-xs">模型 / 评估方式<input value={modelName} onChange={e => setModelName(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {([["overall","综合分"],["story","剧情连贯"],["consistency","人物一致性"],["executability","分镜可执行性"],["visual","视觉效果"]] as const).map(([key,label]) => <label key={key} className="text-xs">{label}（0-100）
                    <input type="number" min="0" max="100" value={testScores[key]} onChange={e => setTestScores(old => ({...old,[key]:e.target.value}))} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" />
                  </label>)}
                </div>
                <button onClick={() => void saveTest()} disabled={busy} className="mt-4 rounded-xl border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100">保存本次实测成绩</button>
              </div>
              <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
                <h3 className="font-bold">版本历史</h3>
                <div className="mt-3 max-h-64 space-y-2 overflow-auto">
                  {versions.map(item => <div key={item.id} className="rounded-lg bg-black/20 p-3 text-sm">
                    <div className="font-semibold">v{item.version} · {item.name}</div>
                    <div className="mt-1 text-xs text-white/50">{item.change_reason} · {item.created_at?.slice(0,19).replace("T"," ")}</div>
                    <button onClick={() => { setName(item.name); setContent(item.content); setNotes(item.notes || ""); setChangeReason("从历史版本 v"+item.version+" 恢复并创建新版本"); }} className="mt-2 text-xs text-cyan-200">载入编辑区（保存后生成新版本）</button>
                  </div>)}
                  {!versions.length && <p className="text-sm text-white/40">暂无版本记录。</p>}
                </div>
              </div>
            </div>}
          </section>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-lg font-bold">提示词实测排行榜</h2><p className="mt-1 text-xs text-white/50">随时刷新；只统计已录入的测试成绩，刷新不会修改正式提示词。</p></div>
            <div className="flex gap-2">
              <select value={dimension} onChange={e => setDimension(e.target.value)} className="rounded-lg border border-white/10 bg-[#171326] px-3 py-2 text-sm">
                {dimensions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <button disabled={busy} onClick={() => void refreshRanking()} className="rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">↻ 立即刷新排名</button>
            </div>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-sm">
              <thead className="text-xs text-white/50"><tr><th className="p-2">排名</th><th className="p-2">提示词</th><th className="p-2">版本</th><th className="p-2">测试次数</th><th className="p-2">平均得分</th><th className="p-2">成绩口径</th></tr></thead>
              <tbody>{ranking.map(item => <tr key={item.prompt_id+"-"+item.version} className="border-t border-white/10">
                <td className="p-2 font-bold">{item.rank}</td><td className="p-2">{item.prompt_name}</td><td className="p-2">v{item.version}</td><td className="p-2">{item.test_count}</td><td className="p-2 text-cyan-200">{item.average_score}</td><td className="p-2 text-xs text-white/50">{item.score_type}</td>
              </tr>)}</tbody>
            </table>
            {!ranking.length && <p className="py-6 text-center text-sm text-white/40">尚未刷新，或当前维度还没有实测成绩。</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
