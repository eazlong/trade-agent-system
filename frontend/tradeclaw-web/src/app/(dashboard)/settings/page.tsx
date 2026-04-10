"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import { useState } from "react";
import { useAuth } from "@/context/AuthContext";

export default function SettingsPage() {
  const { logout } = useAuth();
  const [activeTab, setActiveTab] = useState("general");
  const [saved, setSaved] = useState(false);

  // General settings
  const [llmProvider, setLlmProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-4o");
  const [fallbackProvider, setFallbackProvider] = useState("anthropic");
  const [temperature, setTemperature] = useState("0.3");
  const [maxTokens, setMaxTokens] = useState("4096");

  // Exchange settings
  const [exchange, setExchange] = useState("binance");
  const [apiKeyExchange, setApiKeyExchange] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [testnet, setTestnet] = useState(false);
  const [leverage, setLeverage] = useState("10");

  // Risk settings
  const [maxPosition, setMaxPosition] = useState("35");
  const [maxDrawdown, setMaxDrawdown] = useState("8");
  const [varLimit, setVarLimit] = useState("3.0");
  const [stopLoss, setStopLoss] = useState("2.0");
  const [autoStop, setAutoStop] = useState(true);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const TABS = [
    { key: "general", label: "通用" },
    { key: "exchange", label: "交易所" },
    { key: "risk", label: "风控" },
    { key: "account", label: "账户" },
  ];

  const inputClass = "w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none focus:border-green transition-colors";
  const labelClass = "text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1 block";

  return (
    <DashboardShell>
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-bold">系统配置</h1>
        {saved && (
          <div className="text-xs text-green font-semibold bg-green-dim px-3 py-1.5 rounded-md">
            已保存
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1.5">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold cursor-pointer transition-all border ${
              activeTab === tab.key
                ? "bg-green-dim border-green/20 text-green"
                : "bg-bg2 border-[rgba(255,255,255,0.07)] text-text3 hover:text-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="bg-bg1 border border-[rgba(255,255,255,0.07)] rounded-xl overflow-hidden">
        {activeTab === "general" && (
          <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">LLM 配置</div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>主 LLM 提供商</label>
                <select value={llmProvider} onChange={(e) => setLlmProvider(e.target.value)} className={inputClass}>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>模型</label>
                <select value={model} onChange={(e) => setModel(e.target.value)} className={inputClass}>
                  <option value="gpt-4o">GPT-4o</option>
                  <option value="gpt-4o-mini">GPT-4o-mini</option>
                  <option value="o1">O1</option>
                </select>
              </div>
            </div>

            <div>
              <label className={labelClass}>API Key</label>
              <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." className={inputClass} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>降级 LLM</label>
                <select value={fallbackProvider} onChange={(e) => setFallbackProvider(e.target.value)} className={inputClass}>
                  <option value="anthropic">Anthropic Claude</option>
                  <option value="none">无</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>Temperature</label>
                <input type="number" step="0.1" min="0" max="1" value={temperature} onChange={(e) => setTemperature(e.target.value)} className={inputClass} />
              </div>
            </div>

            <div>
              <label className={labelClass}>Max Tokens</label>
              <input type="number" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} className={inputClass} />
            </div>
          </form>
        )}

        {activeTab === "exchange" && (
          <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">交易所连接</div>

            <div>
              <label className={labelClass}>交易所</label>
              <select value={exchange} onChange={(e) => setExchange(e.target.value)} className={inputClass}>
                <option value="binance">Binance</option>
                <option value="okx">OKX</option>
                <option value="bybit">Bybit</option>
              </select>
            </div>

            <div>
              <label className={labelClass}>API Key</label>
              <input type="password" autoComplete="off" value={apiKeyExchange} onChange={(e) => setApiKeyExchange(e.target.value)} placeholder="交易所 API Key" className={inputClass} />
            </div>

            <div>
              <label className={labelClass}>API Secret</label>
              <input type="password" autoComplete="off" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} placeholder="交易所 API Secret" className={inputClass} />
            </div>

            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-text font-semibold">测试网模式</div>
                <div className="text-[10px] text-text3">使用模拟环境进行交易测试</div>
              </div>
              <label className="relative w-8 h-[18px]">
                <input type="checkbox" className="hidden" checked={testnet} onChange={() => setTestnet(!testnet)} />
                <div className={`absolute inset-0 rounded-[9px] cursor-pointer transition-all before:content-[''] before:absolute before:w-3 before:h-3 before:left-0.5 before:top-0.5 before:rounded-full before:bg-text3 before:transition-all before:duration-200 ${testnet ? "!bg-green-dim !border-green before:!translate-x-[14px] before:!bg-green" : "bg-bg border border-[rgba(255,255,255,0.12)]"}`} style={testnet ? { borderColor: "var(--color-green)" } : {}} />
              </label>
            </div>

            <div>
              <label className={labelClass}>默认杠杆 (x)</label>
              <input type="number" value={leverage} onChange={(e) => setLeverage(e.target.value)} className={inputClass} />
            </div>
          </form>
        )}

        {activeTab === "risk" && (
          <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">风控参数</div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>最大仓位集中度 (%)</label>
                <input type="number" value={maxPosition} onChange={(e) => setMaxPosition(e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>最大回撤限额 (%)</label>
                <input type="number" value={maxDrawdown} onChange={(e) => setMaxDrawdown(e.target.value)} className={inputClass} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>日 VaR 限额 (%)</label>
                <input type="number" step="0.1" value={varLimit} onChange={(e) => setVarLimit(e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>止损阈值 (%)</label>
                <input type="number" step="0.1" value={stopLoss} onChange={(e) => setStopLoss(e.target.value)} className={inputClass} />
              </div>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-text font-semibold">自动风控</div>
                <div className="text-[10px] text-text3">触发风控条件时自动平仓</div>
              </div>
              <label className="relative w-8 h-[18px]">
                <input type="checkbox" className="hidden" checked={autoStop} onChange={() => setAutoStop(!autoStop)} />
                <div className={`absolute inset-0 rounded-[9px] cursor-pointer transition-all before:content-[''] before:absolute before:w-3 before:h-3 before:left-0.5 before:top-0.5 before:rounded-full before:bg-text3 before:transition-all before:duration-200 ${autoStop ? "!bg-green-dim !border-green before:!translate-x-[14px] before:!bg-green" : "bg-bg border border-[rgba(255,255,255,0.12)]"}`} style={autoStop ? { borderColor: "var(--color-green)" } : {}} />
              </label>
            </div>

            {/* Current risk status */}
            <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-4 mt-2">
              <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-2">当前风控状态</div>
              <div className="grid grid-cols-4 gap-3">
                <div>
                  <div className="text-[9px] text-text3">仓位集中度</div>
                  <div className="font-mono text-sm font-semibold text-amber">38%</div>
                </div>
                <div>
                  <div className="text-[9px] text-text3">最大回撤</div>
                  <div className="font-mono text-sm font-semibold text-amber">-4.2%</div>
                </div>
                <div>
                  <div className="text-[9px] text-text3">日VaR</div>
                  <div className="font-mono text-sm font-semibold text-amber">-2.1%</div>
                </div>
                <div>
                  <div className="text-[9px] text-text3">触发次数</div>
                  <div className="font-mono text-sm font-semibold text-green">0</div>
                </div>
              </div>
            </div>
          </form>
        )}

        {activeTab === "account" && (
          <div className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">账户管理</div>

            <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-gradient-to-br from-purple/30 to-blue/30 rounded-full flex items-center justify-center text-sm font-bold">
                  A
                </div>
                <div>
                  <div className="text-xs font-semibold text-text">Admin</div>
                  <div className="text-[10px] text-text3">超级管理员</div>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <button className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-4 py-2.5 text-xs text-text hover:bg-bg3 transition-colors text-left cursor-pointer">
                修改密码
              </button>
              <button className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-4 py-2.5 text-xs text-text hover:bg-bg3 transition-colors text-left cursor-pointer">
                管理 API Keys
              </button>
              <button className="w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-4 py-2.5 text-xs text-text hover:bg-bg3 transition-colors text-left cursor-pointer">
                导出配置
              </button>
              <button
                onClick={logout}
                className="w-full bg-red-dim border border-red/20 rounded-lg px-4 py-2.5 text-xs text-red hover:bg-red/20 transition-colors text-left cursor-pointer"
              >
                退出登录
              </button>
            </div>
          </div>
        )}

        {/* Save button */}
        {activeTab !== "account" && (
          <div className="px-5 py-3 border-t border-[rgba(255,255,255,0.07)] flex justify-end">
            <button
              onClick={handleSave}
              className="bg-green text-black text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-85 transition-all cursor-pointer"
            >
              保存配置
            </button>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
