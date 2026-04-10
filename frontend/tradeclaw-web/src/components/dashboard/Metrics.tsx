export default function Metrics() {
  return (
    <div className="grid grid-cols-3 gap-3">
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">净值</div>
        <div className="font-mono text-2xl font-semibold text-green leading-none">1.2847</div>
        <div className="font-mono text-xs mt-1 text-green">↑ +0.34% 今日</div>
      </div>
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">夏普比率</div>
        <div className="font-mono text-2xl font-semibold leading-none" style={{ color: "var(--color-teal)" }}>2.41</div>
        <div className="font-mono text-xs mt-1 text-text2">年化 · 滚动30天</div>
      </div>
      <div className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-3.5 py-3">
        <div className="text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1.5">最大回撤</div>
        <div className="font-mono text-2xl font-semibold leading-none text-amber">-4.2%</div>
        <div className="font-mono text-xs mt-1 text-text2">本月 · 限额 -8%</div>
      </div>
    </div>
  );
}
