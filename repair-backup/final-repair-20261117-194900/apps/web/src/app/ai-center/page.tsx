"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Provider = {
  id: number;
  provider: string;
  display_name: string;
  base_url: string;
  model: string;
  enabled: boolean;
  paid: boolean;
  purpose: string;
  has_api_key: boolean;
};

const presets = [
  { id: "deepseek", name: "DeepSeek", url: "https://api.deepseek.com", model: "deepseek-chat", paid: true, purpose: "text" },
  { id: "doubao", name: "豆包", url: "https://ark.cn-beijing.volces.com/api/v3", model: "", paid: true, purpose: "image" },
  { id: "openai", name: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4.1-mini", paid: true, purpose: "text" },
  { id: "grok", name: "Grok", url: "https://api.x.ai/v1", model: "grok-3-mini", paid: true, purpose: "text" },
  { id: "gemini", name: "Google Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.5-flash", paid: true, purpose: "text" },
  { id: "cloudflare", name: "Cloudflare Workers AI", url: "", model: "", paid: true, purpose: "text" },
];

export default function AICenterPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [network, setNetwork] = useState(false);
  const [paid, setPaid] = useState(false);
  const [selected, setSelected] = useState(presets[0]);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(presets[0].url);
  const [model, setModel] = useState(presets[0].model);
  const [enabled, setEnabled] = useState(false);
  const [logs, setLogs] = useState<any[]>([]);
  const [message, setMessage] = useState("");

  async function api(path: string, options?: RequestInit) {
    const response = await fetch(`${API}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败");
    return data;
  }

  async function refresh() {
    try {
      const [p, r, l] = await Promise.all([
        api("/api/ai/providers"),
        api("/api/ai/runtime"),
        api("/api/ai/usage-logs"),
      ]);
      setProviders(p.items || []);
      setNetwork(r.network_enabled);
      setPaid(r.paid_api_allowed);
      setLogs(l.items || []);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "读取失败");
    }
  }

  useEffect(() => { refresh(); }, []);

  function choose(item: typeof presets[number]) {
    setSelected(item);
    setBaseUrl(item.url);
    setModel(item.model);
    setApiKey("");
    const old = providers.find((x) => x.provider === item.id);
    setEnabled(old?.enabled || false);
  }

  async function save() {
    try {
      await api("/api/ai/providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: selected.id,
          display_name: selected.name,
          base_url: baseUrl,
          model,
          api_key: apiKey,
          enabled,
          paid: selected.paid,
          purpose: selected.purpose,
        }),
      });
      setApiKey("");
      await refresh();
      setMessage(`${selected.name} 配置已保存`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function saveRuntime(nextNetwork: boolean, nextPaid: boolean) {
    try {
      await api("/api/ai/runtime", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          network_enabled: nextNetwork,
          paid_api_allowed: nextPaid,
        }),
      });
      setNetwork(nextNetwork);
      setPaid(nextPaid);
      setMessage("调用权限设置已保存");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "设置失败");
    }
  }

  async function removeProvider() {
    if (!confirm(`确定删除 ${selected.name} 配置吗？`)) return;
    try {
      await api(`/api/ai/providers/${selected.id}`, { method: "DELETE" });
      await refresh();
      setMessage("配置已删除");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "删除失败");
    }
  }

  return (
    <main className="cosmic min-h-screen text-white">
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="relative z-10 min-h-screen p-5 lg:p-8">
        <header className="mb-7">
          <a href="/" className="text-sm text-purple-300">← 返回首页</a>
          <h1 className="mt-3 text-3xl font-black">⚙️ AI 模型管理中心</h1>
          <p className="mt-2 text-sm text-purple-200">配置 AI 服务、控制网络权限、管理调用记录</p>
        </header>

        {message && <div className="mb-5 rounded-xl border border-purple-400/30 bg-purple-950/70 p-3 text-sm">{message}</div>}

        <section className="mb-5 grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
            <h2 className="font-bold">网络调用总开关</h2>
            <p className="my-2 text-xs text-purple-300">关闭后，所有外部 AI 请求都会被阻止。</p>
            <button onClick={() => saveRuntime(!network, paid)} className={`rounded-lg px-5 py-2 text-sm font-bold ${network ? "bg-emerald-700" : "bg-gray-700"}`}>
              {network ? "已开启" : "已关闭"}
            </button>
          </div>
          <div className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
            <h2 className="font-bold">付费 API 总授权</h2>
            <p className="my-2 text-xs text-purple-300">开启仅表示允许付费请求，还必须在具体请求时再次确认。</p>
            <button onClick={() => saveRuntime(network, !paid)} className={`rounded-lg px-5 py-2 text-sm font-bold ${paid ? "bg-amber-700" : "bg-gray-700"}`}>
              {paid ? "已授权" : "未授权"}
            </button>
          </div>
        </section>

        <section className="grid grid-cols-1 gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
            <h2 className="mb-4 text-lg font-bold">AI 服务商</h2>
            <div className="space-y-2">
              {presets.map((p) => {
                const saved = providers.find((x) => x.provider === p.id);
                return (
                  <button key={p.id} onClick={() => choose(p)} className={`w-full rounded-xl border p-3 text-left ${selected.id === p.id ? "border-purple-300 bg-purple-700/30" : "border-purple-400/15 bg-black/20"}`}>
                    <div className="flex justify-between gap-2">
                      <span className="font-semibold">{p.name}</span>
                      <span className={`text-xs ${saved?.enabled ? "text-emerald-300" : "text-gray-400"}`}>{saved?.enabled ? "已启用" : "未启用"}</span>
                    </div>
                    <div className="mt-1 text-xs text-purple-300">{p.purpose === "image" ? "视觉生成" : "文本与分析"} · {p.paid ? "需付费授权与本次确认" : "免费额度/需核实"}</div>
                  </button>
                );
              })}
            </div>
          </aside>

          <div className="space-y-5">
            <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
              <h2 className="mb-5 text-xl font-bold">{selected.name} 配置</h2>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="text-sm text-purple-200">API 地址
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-2 w-full rounded-lg border border-purple-400/30 bg-black/30 p-3 text-white" />
                </label>
                <label className="text-sm text-purple-200">模型名称
                  <input value={model} onChange={(e) => setModel(e.target.value)} className="mt-2 w-full rounded-lg border border-purple-400/30 bg-black/30 p-3 text-white" />
                </label>
                <label className="text-sm text-purple-200 md:col-span-2">API 密钥（留空则保留已保存的密钥）
                  <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="输入 API Key" className="mt-2 w-full rounded-lg border border-purple-400/30 bg-black/30 p-3 text-white" />
                </label>
              </div>
              <label className="mt-4 flex items-center gap-3 text-sm">
                <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                启用此服务商
              </label>
              <div className="mt-5 flex flex-wrap gap-3">
                <button onClick={save} className="rounded-lg bg-purple-600 px-5 py-2 font-semibold">保存配置</button>
                <button onClick={removeProvider} className="rounded-lg border border-red-400/30 px-5 py-2 text-red-300">删除配置</button>
              </div>
              <p className="mt-4 text-xs leading-6 text-purple-300">
                密钥保存在本机加密文件中，接口不会返回密钥明文。不同服务商的模型名称和 API 地址请以官方文档为准。视觉生成接口要求所选模型支持 images/generations；请按服务商官方文档填写模型名称和 API 地址。
              </p>
            </section>

            <section id="usage-logs" className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
              <h2 className="mb-4 text-lg font-bold">最近调用记录</h2>
              <div className="max-h-72 space-y-2 overflow-y-auto">
                {logs.map((log) => (
                  <div key={log.id} className="flex flex-wrap justify-between gap-2 rounded-lg bg-black/20 p-3 text-xs">
                    <span>{log.provider} · {log.model} · {log.task}</span>
                    <span className={log.status === "success" ? "text-emerald-300" : "text-red-300"}>{log.status}</span>
                  </div>
                ))}
                {logs.length === 0 && <p className="text-sm text-purple-300">暂无调用记录。</p>}
              </div>
            </section>
          </div>
        </section>
      </div>
    </main>
  );
}
