"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Asset = {
  id: number;
  name: string;
  category: string;
  mime_type: string;
  file_size: number;
  description: string;
  url: string;
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
    const response = await fetch(`${API}/api/media/files${category === "all" ? "" : `?category=${encodeURIComponent(category)}`}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "读取素材失败");
    setAssets(Array.isArray(data) ? data : []);
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
      form.append("description", description);
      const response = await fetch(`${API}/api/media/files/upload?category=${encodeURIComponent(uploadCategory)}`, { method: "POST", body: form });
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

  async function remove(id: number) {
    if (!confirm("确定删除这份素材吗？")) return;
    const response = await fetch(`${API}/api/media/files/${id}`, { method: "DELETE" });
    if (!response.ok) {
      setMessage("删除失败");
      return;
    }
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
          <p className="mt-2 text-sm text-purple-200">集中管理人物图、场景图、道具图、音频、视频及其他文件</p>
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
          <p className="mt-3 text-xs text-purple-300">文件保存在本地素材目录。当前单文件上限为 2 GB。</p>
        </section>

        <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">素材列表</h2>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-lg border border-purple-400/30 bg-[#211338] px-4 py-2">
              <option value="all">全部素材</option>
              {categories.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {assets.map((asset) => (
              <article key={asset.id} className="rounded-xl border border-purple-400/20 bg-black/20 p-4">
                <div className="text-2xl">{asset.category === "image" ? "🖼️" : asset.category === "audio" ? "🎵" : asset.category === "video" ? "🎬" : "📦"}</div>
                <h3 className="mt-3 break-all font-semibold">{asset.name}</h3>
                <p className="mt-1 text-xs text-purple-300">{sizeText(asset.file_size)} · {asset.category}</p>
                <p className="mt-2 break-words text-xs text-purple-200">{asset.description}</p>
                <div className="mt-4 flex gap-4 text-sm">
                  <a href={`${API}${asset.url}`} target="_blank" rel="noreferrer" className="text-purple-300">打开文件</a>
                  <button onClick={() => remove(asset.id)} className="text-red-300">删除</button>
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
