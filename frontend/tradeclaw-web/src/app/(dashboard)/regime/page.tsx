"use client";

/**
 * 机制读面 `/regime`（P5 map #40；本票 #49 做上半页 + 把 #44 的下半页挂进这个路由）。
 *
 * 这一页回答三个问题：**现在哪一档 / 今天为什么停或没停 / 这一代池化表谁何时算的**。
 * 它不是配置面（阈值配置面开写不在本期），也不是恢复入口（恢复走 slash）。
 *
 * 三条纪律，与后端的分工写死在 `CONTEXT.md` 的「机制的 Web 读面与写入口」一节：
 *
 *   1. **枚举的 display 与叙述段落全由后端渲染**，这里一处都不重拼：三个开关的档位、
 *      切换方向、切换回执、池化格子的状态与原因、冷启动与阶段的那句话，全部照抄后端字段。
 *   2. **读是所有登录用户，写要 `isStaff`**（ADR 0005）。这一层是界面纪律、不是安全边界：
 *      服务端会挡，而 slash 通路没有跟着收紧，所以页面上不许写「已收口」这类话。
 *   3. **「数据截止」常驻**：行情阶段日频、池化表要人手动重算，没有这个时刻，一张三天没
 *      重算的表与一小时前算出来的表在页面上长得完全一样——而「这张表是什么时候的」正是
 *      这一页存在的理由之一。它取 `overview` 的两个时刻，后端不另造一个「现在」。
 *
 * 刷新节奏是**两档**：`overview` 60 秒轮询（廉价只读），三张确认页只在点开时拉一次
 * （`gate_switch.page()` 会重导入全部策略模块、`mechanism_switch.page()` 会全量重算历史
 * 标签——把它们放进轮询等于每 60 秒重导入一次全部策略）。池化表与代际历史按代变化，
 * 重算收场时刷新一次即可，不进轮询。
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import DashboardShell from "@/components/layout/DashboardShell";
import GenerationHistory from "@/components/regime/GenerationHistory";
import PoolTable from "@/components/regime/PoolTable";
import RebuildButton from "@/components/regime/RebuildButton";
import StrategyLifecycleButton from "@/components/regime/StrategyLifecycleButton";
import SwitchCard from "@/components/regime/SwitchCard";
import { useAuth } from "@/context/AuthContext";
import { useRegimeOverview } from "@/hooks/useRegimeOverview";
import {
  regimeApi,
  type RegimeGeneration,
  type RegimePool,
  type RegimePoolCell,
} from "@/lib/api";
import { formatMoment } from "@/lib/format";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 内联失败提示条：与部署弹窗同一个口径（`bg-red-dim/20 border border-red/30`），不用 `alert`。 */
function ErrorBar({ testId, children }: { testId: string; children: ReactNode }) {
  return (
    <div
      data-testid={testId}
      className="bg-red-dim/20 border border-red/30 rounded-lg px-3 py-2 text-xs text-red whitespace-pre-wrap"
    >
      {children}
    </div>
  );
}

export default function RegimePage() {
  const { isStaff } = useAuth();
  const { data: overview, loading, error: overviewError, refetch } = useRegimeOverview();

  const [pool, setPool] = useState<RegimePool | null>(null);
  const [generations, setGenerations] = useState<RegimeGeneration[]>([]);
  const [poolError, setPoolError] = useState<string | null>(null);
  const [poolLoading, setPoolLoading] = useState(true);

  /**
   * 当前代 + 代际历史。**按代变化，不进轮询**：重算收场时由 `RebuildButton` 回调刷新。
   *
   * **`evidence: false`**（2026-10-23，在 txy 上实测后定的）：只拉矩阵，不带依据。
   * 带上依据的整份响应是 278 KB gzip，在实测约 2 KB/s、20% 丢包的链路上要 50 秒——
   * 画面就是一直转圈。矩阵只有几十 KB，依据在展开某一格时由 `loadCellEvidence` 按需拉。
   */
  const loadPool = useCallback(async () => {
    setPoolLoading(true);
    try {
      const [nextPool, nextGenerations] = await Promise.all([
        regimeApi.getPool({ evidence: false }),
        regimeApi.getGenerations({ page_size: 20 }),
      ]);
      setPool(nextPool);
      setGenerations(nextGenerations.results);
      setPoolError(null);
    } catch (caught) {
      setPoolError(messageOf(caught));
    } finally {
      setPoolLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPool();
  }, [loadPool]);

  const generation = overview?.pool.has_generation ? overview.pool.generation : null;

  /**
   * 两维里至少有一维有值。全空是**合法状态**（#63 之前的旧行，或这一天没有周期结论），
   * 那时整段不渲染——写「没有结论」是噪音，而拿 `sideways` 填上是一个假结论。
   *
   * **判据用 `!= null` 而不是 `!== null`**：类型上这两维是 `Labelled | null`，但滚动部署
   * 或响应被裁时那个键可能整个不在（`undefined`）。`undefined !== null` 为真，于是会
   * 进入下面的分支取 `.display` —— 一次属性访问就能把整页打成白屏，而这正是「键在、值
   * 为 null」与「键不在」必须分开写的理由。宽松比较把两者一起挡住。
   */
  const hasAxes =
    overview != null &&
    (overview.regime_state.cycle != null ||
      overview.regime_state.volatility != null);

  /**
   * 展开某一格时拉它的依据。**`generation` 用页面当前显示的那一代**：不钉住的话，
   * 展开的瞬间正好翻代会拿到「新一代的证据配旧一代的矩阵」——格子上的状态与新表的
   * 依据对不上，而两者看起来都正常。
   */
  const loadCellEvidence = useCallback(
    (cell: RegimePoolCell) => {
      const [strategyId, regime] = cell.key.split("|");
      return regimeApi
        .getCellEvidence({
          strategy: strategyId,
          regime,
          generation: generation?.id,
        })
        .then((payload) => payload.evidence);
    },
    [generation?.id]
  );

  return (
    <DashboardShell>
      <header className="space-y-1">
        <h1 className="text-base font-bold text-text">机制</h1>
        <p className="text-[11px] text-text3">
          现在哪一档 / 今天为什么停或没停某条策略 / 这一代池化表谁何时算的。
        </p>
        {/* 常驻：这一页上的每一个数都是「到这一刻为止」的。 */}
        <p data-testid="data-cutoff" className="text-[11px] text-text2 font-mono">
          数据截止：池化表 {formatMoment(generation?.finished_at)} · 判定生效{" "}
          {formatMoment(overview?.regime_state.effective_at)}
        </p>
        <p data-testid="regime-state" className="text-[11px] text-text3">
          当前生效阶段：
          {overview === null
            ? "—"
            : overview.regime_state.regime !== null
              ? overview.regime_state.regime.display
              : overview.regime_state.display}
        </p>
        {/*
          两维并存（#67 验收第 4 条）：这一页必须能看出「牛市里的暴跌日」。
          **两维各自独立渲染、互不覆盖**——`cycle` 有值而 `volatility=event_spike` 时
          两句话同时出现，不是二选一。旧结构里波动吃掉了周期，所以 `cycle` 可能是
          `null`；那时整段不渲染，**不拿「横盘」之类填上**（后端也是这么发的）。
        */}
        {overview !== null && hasAxes && (
          <div data-testid="regime-axes" className="text-[11px] text-text3 space-y-0.5">
            {overview.regime_state.cycle != null && (
              <p data-testid="regime-axis-cycle">
                周期：{overview.regime_state.cycle.display}
              </p>
            )}
            {overview.regime_state.volatility != null && (
              <p data-testid="regime-axis-volatility">
                波动：{overview.regime_state.volatility.display}
              </p>
            )}
            {overview.regime_state.cycle != null &&
              overview.regime_state.volatility != null && (
                <p className="text-text3">（两维同时成立、互不覆盖：高波动是叠加层，不吞掉周期）</p>
              )}
            {/*
              来源（#67 验收第 2 条）：只在后端发出来时渲染（= 波动维是 event_spike）。
              **display 原样转述后端**，前端不把 `proxy` 翻成中文——那句话里的
              「没有消息面确认」正是代理判据最容易被高估的地方，不能在前端另写一份。
            */}
            {overview.regime_state.volatility_source != null && (
              <p data-testid="regime-axis-volatility-source" className="text-amber">
                波动来源：{overview.regime_state.volatility_source.display}
              </p>
            )}
          </div>
        )}
        {!isStaff && (
          <p data-testid="write-permission-hint" className="text-[11px] text-text3">
            翻开关、重算池化表与删除策略（退役）需要管理员权限。（这是界面纪律，不是安全边界：服务端同样会挡。）
          </p>
        )}
      </header>

      {overviewError !== null && (
        <ErrorBar testId="overview-error">读取机制状态失败：{overviewError}</ErrorBar>
      )}

      {overview === null && loading && (
        <p data-testid="overview-loading" className="text-xs text-text3">
          正在读取机制状态…
        </p>
      )}

      {overview !== null && (
        <section
          data-testid="switch-cards"
          className="grid grid-cols-1 xl:grid-cols-3 gap-3"
        >
          {overview.switches.map((sw) => (
            <SwitchCard
              key={sw.kind.value}
              sw={sw}
              isStaff={isStaff}
              onSwitched={() => void refetch()}
            />
          ))}
        </section>
      )}

      <section className="space-y-2">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-xs font-semibold text-text2">池化表（策略 × 阶段）</h2>
          <span className="text-[10px] text-text3">
            这一代是谁算的、变了哪几格，都在下面。
          </span>
        </div>

        <RebuildButton
          isStaff={isStaff}
          onFinished={() => {
            void loadPool();
            void refetch();
          }}
        />

        {poolError !== null && (
          <ErrorBar testId="pool-error">读取池化表失败：{poolError}</ErrorBar>
        )}

        {pool === null && poolLoading && (
          <p data-testid="pool-loading" className="text-xs text-text3">
            正在读取池化表…
          </p>
        )}

        {/* 冷启动由 `PoolTable` 自己说那句后端的话（不画空表格）。
            行尾的「删除策略 / 恢复启用」只在有写权限时注入——非 staff 的表与从前逐像素
            一致（`renderStrategyAction` 不给就不多那一列）。 */}
        {pool !== null && (
          <PoolTable
            pool={pool}
            loadEvidence={loadCellEvidence}
            renderStrategyAction={
              isStaff
                ? (strategy) => (
                    <StrategyLifecycleButton
                      strategy={strategy}
                      isStaff
                      onChanged={() => void loadPool()}
                    />
                  )
                : undefined
            }
          />
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text2">代际历史</h2>
        {pool !== null && (
          <GenerationHistory
            generations={generations}
            currentGenerationId={generation?.id ?? null}
          />
        )}
      </section>
    </DashboardShell>
  );
}
