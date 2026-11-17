"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Asset = {
  id: number;
  source: "uploaded" | "generated" | "visual";
  filename: string;
  category: string;
  mime_type: string;
  file_size: number;
  description: string;
  url: string;
};

const categories = [
  ["image", "图片素材"],
  ["audio", "音频素材"],
  ["video", "视频素材"],
  ["other", "其他文件"],
];

export default function AssetsPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [category, setCategory] = useState("all");
  const [uploadCategory, setUploadCategory] = useState("image");
  const [file, setFile] = useState<File | null>(null);
  const [description, setDescription] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function refresh() {
    const suffix = category === "all" ? "" : `?category=${encodeURIComponent(category)}`;
    const [uploadedResponse, generatedResponse, visualResponse] = await Promise.all([
      fetch(`${API}/api/assets${suffix}`),
      fetch(`${API}/api/media/files${suffix}`),
      category === "all" || category === "image"
        ? fetch(`${API}/api/visual/assets`)
        : Promise.resolve(null),
    ]);
    const [uploadedData, generatedData, visualData] = await Promise.all([
      uploadedResponse.json(),
      generatedResponse.json(),
      visualResponse ? visualResponse.json() : Promise.resolve([]),
    ]);
    if (!uploadedResponse.ok) throw new Error(uploadedData.detail || "读取已上传素材失败");
    if (!generatedResponse.ok) throw new Error(generatedData.detail || "读取生成媒体失败");
    if (visualResponse && !visualResponse.ok) throw new Error(visualData.detail || "读取 AI 视觉素材失败");

    const uploaded: Asset[] = (uploadedData.items || []).map((item: {
      id: number; filename: string; category: string; mime_type: string;
      file_size: number; description: string; url: string;
    }) => ({ ...item, source: "uploaded" as const }));
    const generated: Asset[] = (Array.isArray(generatedData) ? generatedData : []).map((item: {
      id: number; name: string; category: string; mime_type: string;
      size: number; description?: string; url: string;
    }) => ({
      id: item.id,
      source: "generated" as const,
      filename: item.name,
      category: item.category,
      mime_type: item.mime_type || "",
      file_size: item.size || 0,
      description: item.description || "AI生成或视频处理输出",
      url: item.url,
    }));
    const visual: Asset[] = (Array.isArray(visualData) ? visualData : []).map((item: {
      id: number; category: string; name: string; description: string;
      appearance: string; image_path: string; reference_path: string;
    }) => {
      const imageName = String(item.image_path || "").split(/[\\/]/).pop() || "";
      const referenceName = String(item.reference_path || "").split(/[\\/]/).pop() || "";
      const url = imageName
        ? `/api/visual/files/${encodeURIComponent(imageName)}`
        : referenceName
          ? `/api/visual/references/${encodeURIComponent(referenceName)}`
          : "";
      return {
        id: item.id,
        source: "visual" as const,
        filename: item.name,
        category: "image",
        mime_type: "image/*",
        file_size: 0,
        description: [item.category, item.description, item.appearance].filter(Boolean).join(" · "),
        url,
      };
    });
    setAssets([...uploaded, ...generated, ...visual].sort((a, b) => b.id - a.id));
  }

  useEffect(() => {
    refresh().catch((e) => setMessage(e.message));
  }, [category]);

  async function upload() {
    if (!file) {
      setMessage("请先选择文件");
      return;
    }
    setLoading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("category", uploadCategory);
      form.append("description", description);
      const response = await fetch(`${API}/api/assets/upload`, { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "上传失败");
      setFile(null);
      setDescription("");
      setMessage("素材上传成功");
      await refresh();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "上传失败");
    } finally {
      setLoading(false);
    }
  }

  async function remove(asset: Asset) {
    if (!confirm("确定删除这份素材吗？")) return;
    if (asset.source === "visual") {
      setMessage("视觉素材请在三维视觉素材工作室中管理，避免误删角色和场景设定。");
      return;
    }
    const endpoint = asset.source === "uploaded"
      ? `${API}/api/assets/${asset.id}`
      : `${API}/api/media/files/${asset.id}`;
    const response = await fetch(endpoint, { method: "DELETE" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      setMessage(data.detail || "删除失败");
      return;
    }
    setMessage("素材已删除");
    await refresh();
  }

  function sizeText(size: number) {
    if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(2)} GB`;
    if (size >= 1024 ** 2) return `${(size / 1024 ** 2).toFixed(2)} MB`;
    if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${size} B`;
  }

  return (
    <main className="cosmic min-h-screen text-white">
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="relative z-10 min-h-screen p-5 lg:p-8">
        <header className="mb-7">
          <a href="/" className="text-sm text-purple-300">← 返回首页</a>
          <h1 className="mt-3 text-3xl font-black">🗄️ 统一素材库</h1>
          <p className="mt-2 text-sm text-purple-200">集中管理上传素材、AI生成人物图、场景图、道具图、音频和视频成品</p>
        </header>

        {message && <div className="mb-5 rounded-xl border border-purple-400/30 bg-purple-950/70 p-3 text-sm">{message}</div>}

        <section className="mb-5 rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
          <h2 className="mb-4 text-lg font-bold">上传素材</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} className="rounded-lg border border-purple-400/30 bg-black/30 p-3 text-sm" />
            <select value={uploadCategory} onChange={(e) => setUploadCategory(e.target.value)} className="rounded-lg border border-purple-400/30 bg-[#211338] p-3">
              {categories.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
            <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="素材说明（可选）" className="rounded-lg border border-purple-400/30 bg-black/30 p-3 md:col-span-2" />
          </div>
          <button onClick={upload} disabled={loading} className="mt-4 rounded-lg bg-purple-600 px-6 py-2 font-semibold disabled:opacity-50">
            {loading ? "上传中..." : "上传到本地素材库"}
          </button>
          <p className="mt-3 text-xs text-purple-300">支持最大 2 GB 文件；素材和生成成品统一展示，分别保存在本地素材目录。</p>
        </section>

        <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">素材列表（{assets.length}）</h2>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-lg border border-purple-400/30 bg-[#211338] px-4 py-2">
              <option value="all">全部素材</option>
              {categories.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {assets.map((asset) => (
              <article key={`${asset.source}-${asset.id}`} className="rounded-xl border border-purple-400/20 bg-black/20 p-4">
                <div className="text-2xl">{asset.category === "image" ? "🖼️" : asset.category === "audio" ? "🎵" : asset.category === "video" ? "🎬" : "📦"}</div>
                <h3 className="mt-3 break-all font-semibold">{asset.filename}</h3>
                <p className="mt-1 text-xs text-purple-300">{sizeText(asset.file_size)} · {asset.category} · {asset.source === "uploaded" ? "上传素材" : "生成成品"}</p>
                <p className="mt-2 break-words text-xs text-purple-200">{asset.description}</p>
                <div className="mt-4 flex gap-4 text-sm">
                  {asset.url
                    ? <a href={`${API}${asset.url}`} target="_blank" rel="noreferrer" className="text-purple-300">打开文件</a>
                    : <a href="/visual-studio" className="text-purple-300">管理素材</a>}
                  {asset.source === "visual"
                    ? <a href="/visual-studio" className="text-purple-300">视觉工作室</a>
                    : <button onClick={() => remove(asset)} className="text-red-300">删除</button>}
                </div>
              </article>
            ))}
            {assets.length === 0 && <p className="text-sm text-purple-300">当前分类还没有素材。</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
