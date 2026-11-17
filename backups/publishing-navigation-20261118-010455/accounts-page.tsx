"use client";

import { useEffect, useState } from "react";

const API = "http://127.0.0.1:8000";

type Platform = {
  id: string;
  name: string;
};

type Account = {
  id: number;
  platform: string;
  account_name: string;
  authorization_type: string;
  enabled: boolean;
  authorized: boolean;
};

export default function PublishingAccountsPage() {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [platform, setPlatform] = useState("douyin");
  const [accountName, setAccountName] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    try {
      const [p, a] = await Promise.all([
        fetch(`${API}/api/publishing/platforms`),
        fetch(`${API}/api/publishing/accounts`),
      ]);

      if (!p.ok || !a.ok) throw new Error("无法连接发布中心 API");

      const pd = await p.json();
      const ad = await a.json();

      setPlatforms(pd.items || []);
      setAccounts(ad.items || []);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "读取失败");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function addAccount() {
    if (!accountName.trim()) {
      setMessage("请填写账号名称或备注");
      return;
    }

    setLoading(true);
    setMessage("");

    try {
      const response = await fetch(`${API}/api/publishing/accounts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform,
          account_name: accountName.trim(),
          authorization_type: "manual",
        }),
      });

      const result = await response.json();

      if (!response.ok) throw new Error(result.detail || "添加失败");

      setAccountName("");
      setMessage("账号记录添加成功。当前为人工辅助发布账号，尚未完成官方 API 授权。");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "添加失败");
    } finally {
      setLoading(false);
    }
  }

  async function disableAccount(id: number) {
    if (!window.confirm("确定停用这个账号记录吗？历史发布任务不会删除。")) return;

    try {
      const response = await fetch(`${API}/api/publishing/accounts/${id}`, {
        method: "DELETE",
      });

      const result = await response.json();

      if (!response.ok) throw new Error(result.detail || "停用失败");

      setMessage("账号已停用");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "操作失败");
    }
  }

  return (
    <main className="min-h-screen p-6 md:p-10 text-white">
      <div className="max-w-6xl mx-auto space-y-8">
        <header>
          <p className="text-sm text-purple-300 mb-2">多平台发布中心</p>
          <h1 className="text-3xl font-bold">发布账号管理</h1>
          <p className="text-white/50 mt-3">
            管理各个平台的账号记录。添加账号不代表已经完成官方授权。
          </p>
        </header>

        {message && (
          <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4 text-sm">
            {message}
          </div>
        )}

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">添加平台账号</h2>

          <label className="block space-y-2">
            <span className="text-sm text-white/60">选择平台</span>
            <select
              value={platform}
              onChange={e => setPlatform(e.target.value)}
              className="w-full rounded-xl border border-white/15 bg-[#171326] p-3"
            >
              {platforms.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/60">账号名称或备注</span>
            <input
              value={accountName}
              onChange={e => setAccountName(e.target.value)}
              placeholder="例如：我的抖音创作账号"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3"
            />
          </label>

          <button
            type="button"
            disabled={loading}
            onClick={addAccount}
            className="rounded-xl bg-purple-600 hover:bg-purple-500 px-7 py-3 disabled:opacity-50"
          >
            {loading ? "正在保存..." : "添加账号记录"}
          </button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">账号列表</h2>
            <button
              onClick={load}
              className="rounded-lg border border-white/20 px-4 py-2 text-sm"
            >
              刷新
            </button>
          </div>

          {accounts.length === 0 ? (
            <div className="text-center py-12 text-white/40">暂无账号记录</div>
          ) : (
            <div className="space-y-3">
              {accounts.map(account => (
                <div
                  key={account.id}
                  className="rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
                >
                  <div>
                    <div className="font-semibold">
                      {platforms.find(p => p.id === account.platform)?.name || account.platform}
                      {" · "}
                      {account.account_name}
                    </div>
                    <div className="text-xs text-white/50 mt-2">
                      {account.authorized ? "已保存授权凭据" : "尚未完成官方 API 授权"}
                      {" · "}
                      {account.enabled ? "启用中" : "已停用"}
                    </div>
                  </div>

                  {account.enabled && (
                    <button
                      onClick={() => disableAccount(account.id)}
                      className="rounded-lg border border-red-400/40 px-4 py-2 text-sm text-red-300 hover:bg-red-500/10"
                    >
                      停用账号
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <a
          href="/publishing"
          className="inline-block text-purple-300 underline"
        >
          返回视频发布中心
        </a>
      </div>
    </main>
  );
}
