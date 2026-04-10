export default function NotFound() {
  return (
    <div className="h-screen w-screen bg-bg flex items-center justify-center">
      <div className="text-center">
        <div className="text-6xl font-bold text-text mb-2">404</div>
        <div className="text-sm text-text3 mb-6">页面未找到</div>
        <a
          href="/overview"
          className="inline-block bg-green text-black text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-85 transition-all"
        >
          返回首页
        </a>
      </div>
    </div>
  );
}
