"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Novel = {
  id: number;
  title: string;
  description: string;
  genre: string;
  status: string;
  chapter_count: number;
  word_count: number;
};

type Chapter = {
  id: number;
  chapter_number: number;
  title: string;
  content: string;
  word_count: number;
};

type AnalysisItem = {
  id: number;
  category: string;
  name: string;
  description: string;
  confirmed: boolean;
};

const categoryNames: Record<string, string> = {
  character: "人物",
  scene: "场景",
  prop: "道具",
  event: "故事事件",
  relationship: "人物关系",
  world: "世界观",
};

export default function NovelsPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [selected, setSelected] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [currentChapter, setCurrentChapter] = useState<Chapter | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisItem[]>([]);
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("奇幻");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败");
    return data;
  }

  async function loadNovels() {
    try {
      const data = await request("/api/novels");
      setNovels(data.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法连接后端");
    }
  }

  async function openNovel(novel: Novel) {
    setSelected(novel);
    setCurrentChapter(null);
    try {
      const [chapterData, analysisData] = await Promise.all([
        request(`/api/novels/${novel.id}/chapters`),
        request(`/api/novels/${novel.id}/analysis`),
      ]);
      setChapters(chapterData.items || []);
      setAnalysis(analysisData.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取失败");
    }
  }

  useEffect(() => {
    loadNovels();
  }, []);

  async function createNovel() {
    if (!title.trim()) {
      setMessage("请先填写小说名称");
      return;
    }
    setLoading(true);
    try {
      const item = await request("/api/novels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), genre }),
      });
      setTitle("");
      await loadNovels();
      await openNovel(item);
      setMessage("小说项目创建成功");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    } finally {
      setLoading(false);
    }
  }

  async function importTxt() {
    if (!selected) {
      setMessage("请先在左侧选择或创建一个小说项目");
      return;
    }

    const file = fileRef.current?.files?.[0];
    if (!file) {
      setMessage("请选择小说文件");
      fileRef.current?.click();
      return;
    }

    const allowed = [".txt", ".md", ".markdown", ".csv", ".log", ".docx", ".pdf", ".epub", ".rtf"];
    const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
    if (!allowed.includes(ext)) {
      setMessage("暂不支持此文件格式，请选择 TXT、MD、CSV、LOG、DOCX、PDF、EPUB 或 RTF 文件");
      return;
    }

    setLoading(true);
    setMessage("正在上传并解析小说，请稍候……");

    try {
      const form = new FormData();
      form.append("file", file);

      const data = await request(`/api/novels/${selected.id}/import-txt`, {
        method: "POST",
        body: form,
      });

      setCurrentChapter(null);
      await loadNovels();
      await openNovel(selected);
      setMessage(`导入成功：${data.chapter_count ?? 0} 章，共 ${data.word_count ?? 0} 字`);
      if (fileRef.current) fileRef.current.value = "";
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "小说导入失败，请检查文件格式或后端服务");
    } finally {
      setLoading(false);
    }
  }

  async function saveChapter() {
    if (!currentChapter) return;
    setLoading(true);
    try {
      await request(`/api/chapters/${currentChapter.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: currentChapter.title,
          content: currentChapter.content,
        }),
      });
      await openNovel(selected!);
      setMessage("章节保存成功");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setLoading(false);
    }
  }

  async function analyze() {
    if (!selected) return;
    setLoading(true);
    try {
      const data = await request(`/api/novels/${selected.id}/analyze`, {
        method: "POST",
      });
      const result = await request(`/api/novels/${selected.id}/analysis`);
      setAnalysis(result.items || []);
      setMessage(data.message || "初步解析完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "解析失败");
    } finally {
      setLoading(false);
    }
  }

  async function confirmAnalysis() {
    if (!selected) return;
    try {
      await request(`/api/novels/${selected.id}/analysis/confirm`, {
        method: "POST",
      });
      const result = await request(`/api/novels/${selected.id}/analysis`);
      setAnalysis(result.items || []);
      setMessage("解析结果已确认并保存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认失败");
    }
  }

  async function removeNovel() {
    if (!selected || !window.confirm("确定删除这部小说及其章节和解析数据吗？")) return;
    try {
      await request(`/api/novels/${selected.id}`, { method: "DELETE" });
      setSelected(null);
      setChapters([]);
      setAnalysis([]);
      await loadNovels();
      setMessage("小说已删除");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除失败");
    }
  }

  async function editAnalysis(item: AnalysisItem) {
    const next = window.prompt("修改名称", item.name);
    if (next === null || !next.trim()) return;
    try {
      await request(`/api/analysis/${item.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: next.trim() }),
      });
      const result = await request(`/api/novels/${selected!.id}/analysis`);
      setAnalysis(result.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "修改失败");
    }
  }

  async function deleteAnalysis(id: number) {
    try {
      await request(`/api/analysis/${id}`, { method: "DELETE" });
      setAnalysis((items) => items.filter((item) => item.id !== id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除失败");
    }
  }

  return (
    <main className="cosmic min-h-screen text-white">
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="main-layout relative z-10 min-h-screen p-5 lg:p-8">
        <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
          <div>
            <a href="/" className="text-sm text-purple-300 hover:text-white">← 返回首页</a>
            <h1 className="mt-3 text-3xl font-black">📚 小说创作工作室</h1>
            <p className="mt-2 text-sm text-purple-200">小说管理、TXT导入、章节编辑与内容解析</p>
          </div>
          <div className="rounded-full border border-purple-400/30 bg-purple-950/60 px-4 py-2 text-sm">
            本地数据库 · 自动保存项目数据
          </div>
        </header>

        {message && (
          <div className="mb-5 rounded-xl border border-purple-400/30 bg-purple-950/70 p-3 text-sm text-purple-100">
            {message}
          </div>
        )}

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
            <h2 className="mb-4 text-lg font-bold">小说项目</h2>
            <div className="space-y-3">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="输入小说名称"
                className="w-full rounded-xl border border-purple-400/30 bg-black/30 px-4 py-3 outline-none focus:border-purple-300"
              />
              <select
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
                className="w-full rounded-xl border border-purple-400/30 bg-[#211338] px-4 py-3"
              >
                {["奇幻", "玄幻", "都市", "言情", "科幻", "悬疑", "历史", "武侠", "其他"].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
              <button
                onClick={createNovel}
                disabled={loading}
                className="w-full rounded-xl bg-gradient-to-r from-purple-600 to-fuchsia-600 px-4 py-3 font-bold disabled:opacity-50"
              >
                ＋ 新建小说项目
              </button>
            </div>

            <div className="mb-3 mt-7 flex items-center justify-between">
              <h3 className="font-semibold">我的小说</h3>
              <button onClick={loadNovels} className="text-xs text-purple-300">刷新</button>
            </div>
            <div className="space-y-2">
              {novels.map((novel) => (
                <button
                  key={novel.id}
                  onClick={() => openNovel(novel)}
                  className={`w-full rounded-xl border p-3 text-left transition ${
                    selected?.id === novel.id
                      ? "border-purple-300 bg-purple-700/30"
                      : "border-purple-400/15 bg-black/20 hover:bg-purple-900/40"
                  }`}
                >
                  <div className="font-semibold">{novel.title}</div>
                  <div className="mt-2 text-xs text-purple-300">
                    {novel.genre} · {novel.chapter_count} 章 · {novel.word_count} 字
                  </div>
                </button>
              ))}
              {novels.length === 0 && (
                <p className="rounded-xl bg-black/20 p-4 text-sm text-purple-300">还没有小说，先创建一个项目。</p>
              )}
            </div>
          </aside>

          <section className="min-w-0 space-y-5">
            {!selected ? (
              <div className="flex min-h-[430px] flex-col items-center justify-center rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-8 text-center">
                <div className="text-6xl">📖</div>
                <h2 className="mt-5 text-2xl font-bold">开始你的小说创作</h2>
                <p className="mt-3 max-w-lg text-sm leading-7 text-purple-200">
                  创建小说项目后，可以导入 TXT、管理章节、编辑内容，并进行人物、场景、道具和剧情线索的初步识别。
                </p>
              </div>
            ) : (
              <>
                <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="text-2xl font-bold">{selected.title}</h2>
                      <p className="mt-1 text-sm text-purple-300">{selected.genre} · {chapters.length} 个章节</p>
                    </div>
                    <button onClick={removeNovel} className="rounded-lg border border-red-400/30 px-3 py-2 text-sm text-red-300">
                      删除项目
                    </button>
                  </div>
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <input ref={fileRef} type="file" accept=".txt,.md,.markdown,.csv,.log,.docx,.pdf,.epub,.rtf,text/plain,text/markdown,application/pdf,application/epub+zip,application/rtf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="max-w-full text-sm text-purple-200" />
                    <button onClick={importTxt} disabled={loading} className="rounded-xl bg-purple-600 px-4 py-2 text-sm font-semibold disabled:opacity-50">
                      导入小说文件（支持多种格式）
                    </button>
                    <button onClick={analyze} disabled={loading || chapters.length === 0} className="rounded-xl bg-fuchsia-600 px-4 py-2 text-sm font-semibold disabled:opacity-50">
                      ✨ 初步解析小说
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-5 2xl:grid-cols-2">
                  <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
                    <h3 className="mb-4 text-lg font-bold">章节目录</h3>
                    <div className="max-h-[460px] space-y-2 overflow-y-auto">
                      {chapters.map((chapter) => (
                        <button
                          key={chapter.id}
                          onClick={() => setCurrentChapter(chapter)}
                          className={`w-full rounded-lg border p-3 text-left ${
                            currentChapter?.id === chapter.id
                              ? "border-purple-300 bg-purple-700/30"
                              : "border-purple-400/10 bg-black/20 hover:bg-purple-900/30"
                          }`}
                        >
                          <div className="text-sm font-medium">{chapter.chapter_number}. {chapter.title}</div>
                          <div className="mt-1 text-xs text-purple-400">{chapter.word_count} 字</div>
                        </button>
                      ))}
                      {chapters.length === 0 && <p className="text-sm text-purple-300">导入 TXT 后自动建立章节目录。</p>}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
                    <h3 className="mb-4 text-lg font-bold">章节内容编辑</h3>
                    {currentChapter ? (
                      <div className="space-y-3">
                        <input
                          value={currentChapter.title}
                          onChange={(e) => setCurrentChapter({ ...currentChapter, title: e.target.value })}
                          className="w-full rounded-lg border border-purple-400/30 bg-black/30 p-3 font-semibold outline-none"
                        />
                        <textarea
                          value={currentChapter.content}
                          onChange={(e) => setCurrentChapter({ ...currentChapter, content: e.target.value })}
                          className="min-h-[320px] w-full resize-y rounded-lg border border-purple-400/30 bg-black/30 p-3 text-sm leading-7 outline-none"
                        />
                        <button onClick={saveChapter} disabled={loading} className="rounded-lg bg-purple-600 px-5 py-2 font-semibold">
                          保存章节
                        </button>
                      </div>
                    ) : (
                      <p className="py-12 text-center text-sm text-purple-300">从左侧选择章节，查看和编辑正文。</p>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-lg font-bold">🔎 小说解析结果</h3>
                      <p className="mt-1 text-xs text-purple-300">先进行基础线索提取，后续接入 AI 模型进行深度分析。</p>
                    </div>
                    <button onClick={confirmAnalysis} disabled={analysis.length === 0} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">
                      确认全部结果
                    </button>
                  </div>
                  <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                    {analysis.map((item) => (
                      <div key={item.id} className="rounded-xl border border-purple-400/20 bg-black/20 p-4">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs text-purple-300">{categoryNames[item.category] || item.category}</span>
                          <span className={`text-xs ${item.confirmed ? "text-emerald-300" : "text-yellow-300"}`}>
                            {item.confirmed ? "已确认" : "待确认"}
                          </span>
                        </div>
                        <div className="mt-2 font-semibold">{item.name}</div>
                        <p className="mt-1 text-xs leading-5 text-purple-300">{item.description}</p>
                        <div className="mt-3 flex gap-3 text-xs">
                          <button onClick={() => editAnalysis(item)} className="text-purple-200">修改</button>
                          <button onClick={() => deleteAnalysis(item.id)} className="text-red-300">删除</button>
                        </div>
                      </div>
                    ))}
                    {analysis.length === 0 && (
                      <p className="py-5 text-sm text-purple-300">尚无解析结果。导入小说后点击“初步解析小说”。</p>
                    )}
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
