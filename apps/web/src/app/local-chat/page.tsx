"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type Runtime = { status: string; models: Array<{ name: string }>; message: string };
type Message = { role: "user" | "assistant"; content: string };

export default function LocalChatPage() {
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [model, setModel] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/runtime`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取本地模型失败");
      setRuntime(data);
      setModel(current => current && data.models?.some((item: {name:string}) => item.name === current) ? current : data.models?.[0]?.name || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法连接本地 Ollama");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function send() {
    const content = draft.trim();
    if (!content || busy) return;
    if (!model) { setError("请先安装并选择一个本地模型"); return; }
    const next: Message[] = [...messages, { role: "user", content }];
    setMessages(next);
    setDraft("");
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, messages: next.slice(-20) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地模型回答失败");
      setMessages(current => [...current, { role: "assistant", content: data.response || "模型没有返回文字。" }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "本地聊天失败；没有切换到云端");
    } finally { setBusy(false); }
  }

  function newChat() {
    if (busy) return;
    setMessages([]);
    setError("");
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-5 p-5 pb-24 text-white">
      <header>
        <p className="text-sm text-cyan-300">完全本机推理 · 不调用云端 API</p>
        <h1 className="mt-1 text-2xl font-bold">本地 AI 聊天</h1>
        <p className="mt-2 text-sm text-white/60">选择已安装的 Ollama 本地模型，直接提问。当前对话在本页内保留；刷新页面后不会自动上传或同步到云端。</p>
      </header>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">本地引擎状态</h2>
            <p className="mt-1 text-sm text-white/60">{runtime?.message || "正在检测 Ollama…"}</p>
          </div>
          <button onClick={() => void refresh()} className="rounded-lg border border-white/20 px-3 py-2 text-sm">重新检测</button>
        </div>
        <label className="mt-3 block text-sm">本地模型
          <select value={model} onChange={event => setModel(event.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
            <option value="">请选择本地模型</option>
            {(runtime?.models || []).map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
        {runtime?.status === "ready" && !runtime.models.length && <p className="mt-2 text-sm text-amber-200">Ollama 已连接，但尚未发现本地模型。请先在“本地模型管理”安装模型。</p>}
      </section>

      <section className="flex min-h-[48vh] flex-1 flex-col rounded-2xl border border-white/10 bg-slate-950/70 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="font-semibold">对话</h2>
          <button onClick={newChat} disabled={busy || messages.length === 0} className="rounded-lg border border-white/20 px-3 py-2 text-xs disabled:opacity-40">新建对话</button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto">
          {!messages.length && <div className="mx-auto max-w-xl py-12 text-center text-sm text-white/45">
            <p className="text-lg text-white/80">你好，我是你的本地漫剧助手。</p>
            <p className="mt-2">你可以问我小说分析、人物设定、剧本、分镜和图片提示词。</p>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              {["帮我分析一段小说，提取人物和场景","把这段剧情改编成漫剧剧本","为主角设计一份稳定的人物设定"].map(text => <button key={text} onClick={() => setDraft(text)} className="rounded-full border border-cyan-300/25 px-3 py-2 text-xs text-cyan-100">{text}</button>)}
            </div>
          </div>}
          {messages.map((message, index) => <div key={index} className={message.role === "user" ? "ml-auto max-w-[88%] rounded-2xl bg-cyan-700/30 p-3" : "mr-auto max-w-[92%] rounded-2xl border border-white/10 bg-white/5 p-3"}>
            <p className="mb-1 text-xs text-white/45">{message.role === "user" ? "我" : "本地 AI"}</p>
            <p className="whitespace-pre-wrap break-words text-sm leading-7">{message.content}</p>
          </div>)}
          {busy && <div className="mr-auto rounded-2xl border border-white/10 bg-white/5 p-3 text-sm text-cyan-100">本地模型正在思考…</div>}
          <div ref={bottomRef} />
        </div>
        {error && <p role="alert" className="mt-3 rounded-lg border border-red-400/20 bg-red-950/30 p-3 text-sm text-red-200">{error}</p>}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <textarea value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} rows={3} maxLength={12000} placeholder="输入你想问本地 AI 的内容；Enter 发送，Shift+Enter 换行" className="min-h-20 flex-1 resize-y rounded-xl border border-white/15 bg-slate-900 p-3 text-sm outline-none focus:border-cyan-300/60" />
          <button onClick={() => void send()} disabled={busy || !draft.trim() || !model} className="rounded-xl bg-cyan-500 px-6 py-3 font-semibold text-slate-950 disabled:opacity-40">{busy ? "正在回答…" : "发送"}</button>
        </div>
        <p className="mt-2 text-xs text-white/40">本地模型请求失败时只显示错误，不会自动切换云端。对话最多携带最近 20 条消息。</p>
      </section>
    </main>
  );
}
