"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Asset = {
  id: number;
  category: string;
  name: string;
  description: string;
  appearance: string;
  clothing: string;
  personality: string;
  consistency_prompt: string;
  style: string;
  image_path: string;
  confirmed: boolean;
};

type Task = {
  id: number;
  task_type: string;
  prompt: string;
  status: string;
  error: string;
  result_path: string;
};

const categories = [
  { key: "characters", label: "人物角色" },
  { key: "scenes", label: "场景环境" },
  { key: "props", label: "普通道具" },
  { key: "weapons", label: "武器与刀具" },
  { key: "costumes", label: "服装与装备" },
  { key: "buildings", label: "建筑与地标" },
  { key: "special_items", label: "特殊物品" },
];

const styles = [
  "3D动漫电影风格",
  "真人电影风格",
  "2D动漫风格",
  "国风漫画风格",
  "水墨动画风格",
  "自定义参考图风格",
];

export default function VisualStudioPage() {
  const [category, setCategory] = useState("characters");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [appearance, setAppearance] = useState("");
  const [clothing, setClothing] = useState("");
  const [personality, setPersonality] = useState("");
  const [consistency, setConsistency] = useState("");
  const [style, setStyle] = useState(styles[0]);
  const [prompt, setPrompt] = useState("");
  const [selectedAsset, setSelectedAsset] = useState<number | null>(null);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadAssets() {
    try {
      const response = await fetch(`${API}/api/visual/assets?category=${category}`);
      if (response.ok) setAssets(await response.json());
    } catch {}
  }

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/visual/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  useEffect(() => {
    loadAssets();
  }, [category]);

  useEffect(() => {
    loadTasks();
  }, []);

  async function saveAsset() {
    if (!name.trim()) {
      setMessage("请先填写素材名称");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category, name, description, appearance, clothing,
          personality, consistency_prompt: consistency, style, data: {},
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "保存失败");
      setMessage("素材设定已保存");
      setName("");
      setDescription("");
      setAppearance("");
      setClothing("");
      setPersonality("");
      setConsistency("");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  function buildPrompt() {
    const selected = assets.find((item) => item.id === selectedAsset);
    const base = selected
      ? `${selected.name}。${selected.description}。外貌：${selected.appearance}。服装：${selected.clothing}。角色固定特征：${selected.consistency_prompt}。`
      : "";
    const full = `${base}${prompt}。风格：${style}。高质量电影级画面，精细材质，清晰细节，统一角色设计。`;
    setPrompt(full);
  }

  async function createTask() {
    if (!prompt.trim()) {
      setMessage("请填写图片生成提示词");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          asset_id: selectedAsset,
          provider: "doubao",
          task_type: category,
          prompt,
          size: "2K",
          confirm_paid: confirmPaid,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "创建任务失败");
      setMessage(`图片任务 #${result.task_id} 已加入队列`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建任务失败");
    } finally {
      setBusy(false);
    }
  }

  async function runTask(id: number) {
    setBusy(true);
    try {
      const response = await fetch(
        `${API}/api/visual/tasks/${id}/run?confirm_paid=${confirmPaid}`,
        { method: "POST" }
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "生成失败");
      setMessage(`图片任务 #${id} 已完成`);
      await loadTasks();
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "生成失败");
      await loadTasks();
    } finally {
      setBusy(false);
    }
  }

  async function uploadReference(file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (selectedAsset) form.append("asset_id", String(selectedAsset));
    try {
      const response = await fetch(`${API}/api/visual/references`, {
        method: "POST",
        body: form,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "上传失败");
      setMessage("参考图片已上传");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20";
  const field = "w-full rounded-xl border border-purple-300/20 bg-slate-900/80 px-3 py-2.5 text-sm text-white outline-none focus:border-purple-400";
  const button = "rounded-xl border border-purple-300/30 bg-purple-600/80 px-4 py-2 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-50";

  return (
    <main className="min-h-screen space-y-6 p-6 text-white">
      <header>
        <a
          href="/"
          className="mb-5 inline-flex items-center gap-2 rounded-xl border border-purple-300/30 bg-purple-950/60 px-4 py-2.5 text-sm font-medium text-white transition hover:border-purple-300/70 hover:bg-purple-800/60"
        >← 返回主页</a>
        <div className="text-sm text-purple-300">AI COMIC STUDIO / VISUAL LAB</div>
        <h1 className="mt-2 text-3xl font-bold">AI视觉素材生产中心</h1>
        <p className="mt-2 text-sm text-slate-300">
          统一管理人物、场景、道具、角色一致性和 AI 图片生成任务。
        </p>
      </header>

      {message && (
        <div className="rounded-xl border border-purple-300/20 bg-purple-950/50 p-3 text-sm text-purple-100">
          {message}
        </div>
      )}

      <section className="grid gap-3 md:grid-cols-3">
        {categories.map((item) => (
          <button
            key={item.key}
            onClick={() => setCategory(item.key)}
            className={`rounded-xl border p-4 text-left transition ${
              category === item.key
                ? "border-purple-400 bg-purple-700/40"
                : "border-purple-300/20 bg-slate-950/60 hover:bg-purple-950/40"
            }`}
          >
            <div className="text-lg font-semibold">{item.label}</div>
            <div className="mt-1 text-xs text-slate-400">
              {item.key === "characters" ? "人物设定、服装、三视图与一致性" :
               item.key === "scenes" ? "地点、环境、时间、氛围与场景图" :
               "物品外观、材质、尺寸与道具图"}
            </div>
          </button>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">建立视觉素材设定</h2>
          <div className="space-y-3">
            <input className={field} placeholder="素材名称" value={name} onChange={(e) => setName(e.target.value)} />
            <textarea className={field} rows={2} placeholder="基础描述 / 故事背景" value={description} onChange={(e) => setDescription(e.target.value)} />
            <textarea className={field} rows={3} placeholder="外貌、外形、颜色、体型、材质等" value={appearance} onChange={(e) => setAppearance(e.target.value)} />
            <textarea className={field} rows={2} placeholder="服装、装备或表面细节（场景和道具可填写对应特征）" value={clothing} onChange={(e) => setClothing(e.target.value)} />
            <textarea className={field} rows={2} placeholder="性格、气质或视觉氛围" value={personality} onChange={(e) => setPersonality(e.target.value)} />
            <textarea className={field} rows={3} placeholder="固定特征：发型、脸型、颜色、标志物等，供后续保持一致" value={consistency} onChange={(e) => setConsistency(e.target.value)} />
            <select className={field} value={style} onChange={(e) => setStyle(e.target.value)}>
              {styles.map((item) => <option key={item}>{item}</option>)}
            </select>
            <button className={button} disabled={busy} onClick={saveAsset}>保存素材设定</button>
          </div>
        </div>

        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">AI图片生成</h2>
          <div className="space-y-3">
            <label className="block text-sm text-slate-300">关联素材</label>
            <select
              className={field}
              value={selectedAsset ?? ""}
              onChange={(e) => setSelectedAsset(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">不关联素材</option>
              {assets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <textarea
              className={field}
              rows={7}
              placeholder="填写图片生成要求，例如：角色正面、侧面、背面三视图，纯色背景，全身站立，三个人物视角保持同一比例……"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <button className="rounded-xl border border-sky-300/30 bg-sky-700/60 px-4 py-2 text-sm text-white hover:bg-sky-600" onClick={buildPrompt}>
              根据素材设定组合提示词
            </button>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={confirmPaid} onChange={(e) => setConfirmPaid(e.target.checked)} />
              我已了解本次图片生成可能产生费用，并确认执行
            </label>
            <div className="flex flex-wrap gap-2">
              <button className={button} disabled={busy} onClick={createTask}>创建图片任务</button>
              <label className="cursor-pointer rounded-xl border border-purple-300/30 px-4 py-2 text-sm hover:bg-purple-950/50">
                上传参考图片
                <input type="file" accept="image/*" className="hidden" onChange={(e) => uploadReference(e.target.files?.[0])} />
              </label>
            </div>
            <p className="text-xs leading-5 text-slate-400">
              图片生成只在你主动点击执行后调用。付费服务还需要 AI 管理中心开启付费授权并在本页确认。
            </p>
          </div>
        </div>
      </section>

      <section className={panel}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">当前素材库</h2>
          <button className={button} onClick={loadAssets}>刷新素材</button>
        </div>
        {assets.length === 0 ? (
          <p className="text-sm text-slate-400">当前分类还没有素材。</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {assets.map((item) => (
              <article key={item.id} className="rounded-xl border border-purple-300/15 bg-slate-900/70 p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold">{item.name}</h3>
                  <span className="text-xs text-slate-400">{item.confirmed ? "已确认" : "待确认"}</span>
                </div>
                <p className="mt-2 line-clamp-3 text-sm text-slate-400">{item.description || item.appearance}</p>
                <p className="mt-2 text-xs text-purple-300">{item.style}</p>
                {item.image_path && (
                  <p className="mt-2 break-all text-xs text-slate-500">图片已生成：{item.image_path}</p>
                )}
                <button
                  className="mt-3 rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs hover:bg-purple-900/50"
                  onClick={() => {
                    setSelectedAsset(item.id);
                    setName(item.name);
                    setDescription(item.description || "");
                    setAppearance(item.appearance || "");
                    setClothing(item.clothing || "");
                    setPersonality(item.personality || "");
                    setConsistency(item.consistency_prompt || "");
                    setPrompt("");
                  }}
                >
                  使用此素材生成图片
                </button>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className={panel}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">图片生成任务队列</h2>
          <button className={button} onClick={loadTasks}>刷新任务</button>
        </div>
        <div className="space-y-3">
          {tasks.length === 0 && <p className="text-sm text-slate-400">还没有图片生成任务。</p>}
          {tasks.map((task) => (
            <div key={task.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-purple-300/15 bg-slate-900/70 p-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">任务 #{task.id} · {task.task_type}</div>
                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{task.prompt}</p>
                {task.error && <p className="mt-1 text-xs text-red-300">{task.error}</p>}
              </div>
              <span className="text-xs text-purple-300">{task.status}</span>
              {task.status === "pending" && (
                <button className={button} disabled={busy} onClick={() => runTask(task.id)}>执行生成</button>
              )}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
