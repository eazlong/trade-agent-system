"use client";

import DashboardShell from "@/components/layout/DashboardShell";
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";
import {
  exchangeApi,
  riskApi,
  RiskConfig as RiskConfigType,
} from "@/lib/api";

export default function SettingsPage() {
  const { logout } = useAuth();
  const [activeTab, setActiveTab] = useState("general");
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);

  // General settings (LLM - no backend API yet)
  const [llmProvider, setLlmProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-4o");
  const [fallbackProvider, setFallbackProvider] = useState("anthropic");
  const [temperature, setTemperature] = useState("0.3");
  const [maxTokens, setMaxTokens] = useState("4096");

  // Exchange settings - from backend
  const [exchange, setExchange] = useState("binance");
  const [apiKeyExchange, setApiKeyExchange] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [testnet, setTestnet] = useState(false);
  const [leverage, setLeverage] = useState("10");
  const [existingAccounts, setExistingAccounts] = useState<{
    id: string;
    exchange: string;
    label: string;
    is_active: boolean;
    created_at: string;
  }[]>([]);

  // Risk settings - from backend
  const [riskConfig, setRiskConfig] = useState<RiskConfigType | null>(null);
  const [maxPosition, setMaxPosition] = useState("35");
  const [maxDrawdown, setMaxDrawdown] = useState("8");
  const [varLimit, setVarLimit] = useState("3.0");
  const [stopLoss, setStopLoss] = useState("2.0");
  const [autoStop, setAutoStop] = useState(true);

  // Load existing exchange accounts and risk config
  const loadInitialData = useCallback(async () => {
    try {
      const [accounts, risk] = await Promise.all([
        exchangeApi.getAccounts(),
        riskApi.getConfig(),
      ]);
      setExistingAccounts(accounts);
      setRiskConfig(risk);
      if (risk.max_position_pct) setMaxPosition(String(risk.max_position_pct));
      if (risk.max_drawdown_pct) setMaxDrawdown(String(risk.max_drawdown_pct));
      if (risk.var_limit_pct) setVarLimit(String(risk.var_limit_pct));
      if (risk.stop_loss_pct) setStopLoss(String(risk.stop_loss_pct));
      if (risk.auto_stop !== undefined) setAutoStop(risk.auto_stop);
    } catch {
      // API not available or error - use defaults
    }
  }, []);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  const showSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleExchangeSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await exchangeApi.createAccount({
        exchange,
        label: `${exchange} ${testnet ? "(Testnet)" : ""}`,
        api_key: apiKeyExchange,
        api_secret: apiSecret,
        testnet,
        leverage: parseInt(leverage, 10),
      });
      setApiKeyExchange("");
      setApiSecret("");
      showSaved();
      loadInitialData();
    } catch (err) {
      alert(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  };

  const handleRiskSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await riskApi.updateConfig({
        max_position_pct: parseFloat(maxPosition),
        max_drawdown_pct: parseFloat(maxDrawdown),
        var_limit_pct: parseFloat(varLimit),
        stop_loss_pct: parseFloat(stopLoss),
        auto_stop: autoStop,
      });
      showSaved();
      loadInitialData();
    } catch (err) {
      alert(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteAccount = async (id: string) => {
    try {
      await exchangeApi.deleteAccount(id);
      loadInitialData();
    } catch (err) {
      alert(`删除失败: ${err instanceof Error ? err.message : "未知错误"}`);
    }
  };

  const TABS = [
    { key: "general", label: "通用" },
    { key: "exchange", label: "交易所" },
    { key: "risk", label: "风控" },
    { key: "account", label: "账户" },
  ];

  const inputClass =
    "w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none focus:border-green transition-colors";
  const labelClass =
    "text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1 block";

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
          <form
            onSubmit={(e) => {
              e.preventDefault();
              showSaved();
            }}
            className="p-5 flex flex-col gap-4"
          >
            <div className="text-xs font-semibold text-text mb-1">LLM 配置</div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>主 LLM 提供商</label>
                <select
                  value={llmProvider}
                  onChange={(e) => setLlmProvider(e.target.value)}
                  className={inputClass}
                >
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>模型</label>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className={inputClass}
                >
                  <option value="gpt-4o">GPT-4o</option>
                  <option value="gpt-4o-mini">GPT-4o-mini</option>
                  <option value="o1">O1</option>
                </select>
              </div>
            </div>

            <div>
              <label className={labelClass}>API Key</label>
              <input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                className={inputClass}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>降级 LLM</label>
                <select
                  value={fallbackProvider}
                  onChange={(e) => setFallbackProvider(e.target.value)}
                  className={inputClass}
                >
                  <option value="anthropic">Anthropic Claude</option>
                  <option value="none">无</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>Temperature</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={temperature}
                  onChange={(e) => setTemperature(e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>

            <div>
              <label className={labelClass}>Max Tokens</label>
              <input
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(e.target.value)}
                className={inputClass}
              />
            </div>
          </form>
        )}

        {activeTab === "exchange" && (
          <div className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">
              交易所连接
            </div>

            {/* Existing accounts */}
            <div className="flex flex-col gap-2">
              <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold">
                已连接账户 ({existingAccounts.length})
              </div>
              {existingAccounts.length === 0 ? (
                <div className="text-center py-6 text-[11px] text-text3 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg">
                  暂无已连接的交易所账户，请在下方添加
                </div>
              ) : (
                existingAccounts.map((acc) => (
                  <div
                    key={acc.id}
                    className="flex items-center justify-between bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3 py-2.5"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex flex-col">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-text capitalize">
                            {acc.exchange}
                          </span>
                          <span
                            className={`text-[9px] px-1.5 py-0.5 rounded-sm font-semibold ${
                              acc.is_active
                                ? "bg-green-dim text-green"
                                : "bg-red-dim text-red"
                            }`}
                          >
                            {acc.is_active ? "活跃" : "已停用"}
                          </span>
                        </div>
                        <span className="text-[10px] text-text3">
                          {acc.label}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[9px] text-text3">
                        {new Date(acc.created_at).toLocaleDateString("zh-CN")}
                      </span>
                      <button
                        onClick={() => handleDeleteAccount(acc.id)}
                        className="text-[10px] text-red hover:opacity-80 cursor-pointer"
                      >
                        删除
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="border-t border-[rgba(255,255,255,0.07)] pt-4 mt-1">
              <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-3">
                添加新账户
              </div>
              <form
                onSubmit={handleExchangeSave}
                className="flex flex-col gap-4"
              >
                <div>
                  <label className={labelClass}>交易所</label>
                  <select
                    value={exchange}
                    onChange={(e) => setExchange(e.target.value)}
                    className={inputClass}
                  >
                    <option value="binance">Binance</option>
                    <option value="okx">OKX</option>
                    <option value="bybit">Bybit</option>
                  </select>
                </div>

                <div>
                  <label className={labelClass}>API Key</label>
                  <input
                    type="password"
                    autoComplete="off"
                    value={apiKeyExchange}
                    onChange={(e) => setApiKeyExchange(e.target.value)}
                    placeholder="交易所 API Key"
                    className={inputClass}
                  />
                </div>

                <div>
                  <label className={labelClass}>API Secret</label>
                  <input
                    type="password"
                    autoComplete="off"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="交易所 API Secret"
                    className={inputClass}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs text-text font-semibold">
                      测试网模式
                    </div>
                    <div className="text-[10px] text-text3">
                      使用模拟环境进行交易测试
                    </div>
                  </div>
                  <label className="relative w-8 h-[18px]">
                    <input
                      type="checkbox"
                      className="hidden"
                      checked={testnet}
                      onChange={() => setTestnet(!testnet)}
                    />
                    <div
                      className={`absolute inset-0 rounded-[9px] cursor-pointer transition-all before:content-[''] before:absolute before:w-3 before:h-3 before:left-0.5 before:top-0.5 before:rounded-full before:bg-text3 before:transition-all before:duration-200 ${
                        testnet
                          ? "!bg-green-dim !border-green before:!translate-x-[14px] before:!bg-green"
                          : "bg-bg border border-[rgba(255,255,255,0.12)]"
                      }`}
                      style={
                        testnet ? { borderColor: "var(--color-green)" } : {}
                      }
                    />
                  </label>
                </div>

                <div>
                  <label className={labelClass}>默认杠杆 (x)</label>
                  <input
                    type="number"
                    value={leverage}
                    onChange={(e) => setLeverage(e.target.value)}
                    className={inputClass}
                  />
                </div>

                <div className="flex items-center justify-end gap-3">
                  {saved && (
                    <span className="text-xs text-green font-semibold">
                      已添加成功
                    </span>
                  )}
                  <button
                    type="submit"
                    disabled={loading || !apiKeyExchange || !apiSecret}
                    className="bg-green text-black text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-85 transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {loading ? "保存中..." : "添加交易所账户"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {activeTab === "risk" && (
          <form onSubmit={handleRiskSave} className="p-5 flex flex-col gap-4">
            <div className="text-xs font-semibold text-text mb-1">
              风控参数
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>最大仓位集中度 (%)</label>
                <input
                  type="number"
                  value={maxPosition}
                  onChange={(e) => setMaxPosition(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>最大回撤限额 (%)</label>
                <input
                  type="number"
                  value={maxDrawdown}
                  onChange={(e) => setMaxDrawdown(e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>日 VaR 限额 (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={varLimit}
                  onChange={(e) => setVarLimit(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>止损阈值 (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-text font-semibold">自动风控</div>
                <div className="text-[10px] text-text3">
                  触发风控条件时自动平仓
                </div>
              </div>
              <label className="relative w-8 h-[18px]">
                <input
                  type="checkbox"
                  className="hidden"
                  checked={autoStop}
                  onChange={() => setAutoStop(!autoStop)}
                />
                <div
                  className={`absolute inset-0 rounded-[9px] cursor-pointer transition-all before:content-[''] before:absolute before:w-3 before:h-3 before:left-0.5 before:top-0.5 before:rounded-full before:bg-text3 before:transition-all before:duration-200 ${
                    autoStop
                      ? "!bg-green-dim !border-green before:!translate-x-[14px] before:!bg-green"
                      : "bg-bg border border-[rgba(255,255,255,0.12)]"
                  }`}
                  style={autoStop ? { borderColor: "var(--color-green)" } : {}}
                />
              </label>
            </div>

            {/* Current risk status - show live data from backend */}
            {riskConfig && (
              <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg p-4 mt-2">
                <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-2">
                  当前风控状态
                </div>
                <div className="grid grid-cols-4 gap-3">
                  <div>
                    <div className="text-[9px] text-text3">仓位集中度</div>
                    <div className="font-mono text-sm font-semibold text-amber">
                      {riskConfig.max_position_pct.toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-text3">最大回撤</div>
                    <div className="font-mono text-sm font-semibold text-amber">
                      {riskConfig.max_drawdown_pct.toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-text3">日VaR</div>
                    <div className="font-mono text-sm font-semibold text-amber">
                      {riskConfig.var_limit_pct.toFixed(1)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] text-text3">自动风控</div>
                    <div
                      className={`font-mono text-sm font-semibold ${
                        riskConfig.auto_stop ? "text-green" : "text-red"
                      }`}
                    >
                      {riskConfig.auto_stop ? "开启" : "关闭"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={loading}
                className="bg-green text-black text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-85 transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? "保存中..." : "保存风控配置"}
              </button>
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

        {/* Save button for general tab only */}
        {activeTab === "general" && (
          <div className="px-5 py-3 border-t border-[rgba(255,255,255,0.07)] flex justify-end">
            <button
              onClick={() => showSaved()}
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
