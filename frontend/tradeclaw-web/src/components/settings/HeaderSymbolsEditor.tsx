"use client";

/**
 * 「配置 → 通用 → 顶栏行情币种」的列表编辑器。
 *
 * 三条产品约束在这里落地（数值与文案的唯一出处是 `lib/headerSymbols.ts`；后端另有一份
 * 硬门槛在 `apps/authentication/preferences.py`，那份才是真正拦人的）：
 *
 *   1. **3–5 个**：删到 3 个时删除按钮置灰、到 5 个时输入框与「添加」置灰。边界不是审美
 *      ——顶栏与导航、通知挤在同一行 52px 里，多了会把导航压到折行，少了不成「行情条」。
 *   2. **顺序即展示顺序**：用户能上移/下移。做成有序列表而不是集合，是因为「先看哪个币」
 *      是真实需求（多数人把 BTC 放最左）。
 *   3. **默认 BTC/ETH/SOL/BNB**，随时可「恢复默认」。这一支的置灰读后端 `is_default`，
 *      不靠「与默认数组逐个比对」推：默认值变更那天，比对会把改过偏好的用户显示成没改过。
 *
 * 写路径的纪律：**不做乐观更新**。`save` 失败（后端 400 的原文）时页面显示的仍是上次真正
 * 落库的那份，红字说明原因；乐观更新会让页面显示一份「并没有被接受」的清单，而顶栏随后
 * 照着它去取数——那几档会一直「—」，且没人知道是没保存成功。
 *
 * 结构上拆成「外层的取数 + 内层按已保存清单重挂载的表单」：
 *
 * - 外层的 `useHeaderSymbols()` 持有真相（后端那份），
 * - 内层 `key={symbols.join(",")}`，**已保存清单变了就重挂载**，草稿用 `useState` 初值接住。
 *
 * 这样草稿与真相的同步不需要在 effect 里 `setState`（React 19 的
 * `react-hooks/set-state-in-effect` 判红，而且那确实是级联渲染）。「已保存」那一闪放在
 * 外层：内层重挂载会把它清掉，那样保存成功的反馈正好在需要看见的时刻消失。
 */
import { useCallback, useState } from "react";
import { useHeaderSymbols, type HeaderSymbolsState } from "@/hooks/useHeaderSymbols";
import {
  DEFAULT_HEADER_SYMBOLS,
  HEADER_SYMBOLS_MAX,
  HEADER_SYMBOLS_MIN,
  HEADER_SYMBOLS_QUOTE,
  normalizeHeaderSymbol,
  validateHeaderSymbols,
} from "@/lib/headerSymbols";

const inputClass =
  "w-full bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-2 text-xs text-text outline-none focus:border-green transition-colors";
const labelClass =
  "text-[10px] text-text3 uppercase tracking-wider font-semibold mb-1 block";

export default function HeaderSymbolsEditor() {
  const { symbols, save, ...rest } = useHeaderSymbols();
  const [saved, setSaved] = useState(false);
  /**
   * **保存代际**。只把 `symbols.join(",")` 当 key 是不够的：用户手上有一份脏草稿时点
   * 「恢复默认」，落库的那份可能与上一次已保存的完全相同（比如本来就是默认四档、草稿
   * 被自己改成了五档再点恢复默认）——key 不变，表单不重挂载，草稿就挂在那里显示五档，
   * 而库里是四档。实测在 txy 上就是这样：点「恢复默认」后顶栏变了、编辑器还显示 5 档。
   *
   * 每次保存成功就 +1，于是**任何一次真实落库都会重置草稿**，与落库内容是否变化无关。
   */
  const [revision, setRevision] = useState(0);

  const saveAndFlash = useCallback(
    async (next: string[]) => {
      const ok = await save(next);
      if (ok) {
        setRevision((r) => r + 1);
        setSaved(true);
        window.setTimeout(() => setSaved(false), 2000);
      }
      return ok;
    },
    [save],
  );

  return (
    <HeaderSymbolsForm
      // 已保存清单变了（首屏读到）**或** 刚保存过一次 → 表单重挂载，草稿回到真相
      key={`${revision}:${symbols.join(",")}`}
      symbols={symbols}
      save={saveAndFlash}
      saved={saved}
      {...rest}
    />
  );
}

type FormProps = Omit<HeaderSymbolsState, "symbols" | "save"> & {
  symbols: string[];
  save: (symbols: string[]) => Promise<boolean>;
  saved: boolean;
};

function HeaderSymbolsForm({
  symbols,
  isDefault,
  loading,
  saving,
  error,
  save,
  saved,
  clearError,
}: FormProps) {
  /** 编辑器里正在编的那一份。初值就是已保存清单——之后由外层 key 负责重置。 */
  const [draft, setDraft] = useState<string[]>(symbols);
  const [input, setInput] = useState("");

  const atMax = draft.length >= HEADER_SYMBOLS_MAX;
  const atMin = draft.length <= HEADER_SYMBOLS_MIN;
  const dirty = draft.join(",") !== symbols.join(",");
  const localProblem = validateHeaderSymbols(draft);

  const addSymbol = () => {
    clearError();
    const symbol = normalizeHeaderSymbol(input);
    if (symbol === null || atMax || draft.includes(symbol)) return;
    setDraft([...draft, symbol]);
    setInput("");
  };

  const move = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= draft.length) return;
    const next = [...draft];
    [next[index], next[target]] = [next[target], next[index]];
    setDraft(next);
  };

  const remove = (index: number) => {
    clearError();
    if (atMin) return;
    setDraft(draft.filter((_, i) => i !== index));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dirty || localProblem !== null) return;
    await save(draft);
  };

  return (
    <div className="border-t border-[rgba(255,255,255,0.07)] pt-4">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-semibold text-text">顶栏行情币种</div>
        {saved && !dirty && (
          <span className="text-[10px] text-green font-semibold">已保存</span>
        )}
      </div>
      <p className="text-[10px] text-text3 mb-3 leading-relaxed">
        顶部横条展示的 {HEADER_SYMBOLS_MIN}–{HEADER_SYMBOLS_MAX} 个{" "}
        {HEADER_SYMBOLS_QUOTE} 交易对，顺序即展示顺序。点顶栏任一档可看该品种 K 线。
      </p>

      <form onSubmit={submit} className="flex flex-col gap-3">
        {/* 当前清单：可上移 / 下移 / 删除 */}
        <div className="flex flex-col gap-1.5">
          {draft.map((symbol, index) => (
            <div
              key={symbol}
              className="flex items-center gap-2 bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-3 py-1.5"
            >
              <span className="text-[10px] text-text3 font-mono w-4">
                {index + 1}
              </span>
              <span className="text-xs font-mono text-text flex-1">{symbol}</span>
              <button
                type="button"
                onClick={() => move(index, -1)}
                disabled={index === 0}
                title="上移"
                aria-label={`${symbol} 上移`}
                className="px-1.5 text-[10px] text-text3 hover:text-text disabled:opacity-25 disabled:cursor-not-allowed cursor-pointer"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => move(index, 1)}
                disabled={index === draft.length - 1}
                title="下移"
                aria-label={`${symbol} 下移`}
                className="px-1.5 text-[10px] text-text3 hover:text-text disabled:opacity-25 disabled:cursor-not-allowed cursor-pointer"
              >
                ↓
              </button>
              <button
                type="button"
                onClick={() => remove(index)}
                disabled={atMin}
                title={atMin ? `至少保留 ${HEADER_SYMBOLS_MIN} 个` : "删除"}
                aria-label={`删除 ${symbol}`}
                className="px-1.5 text-[10px] text-red/80 hover:text-red disabled:opacity-25 disabled:cursor-not-allowed cursor-pointer"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        {/* 添加 */}
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className={labelClass} htmlFor="header-symbol-input">
              添加币种
            </label>
            <input
              id="header-symbol-input"
              type="text"
              value={input}
              onChange={(e) => {
                clearError();
                setInput(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addSymbol();
                }
              }}
              placeholder={`如 BTC 或 BTC/${HEADER_SYMBOLS_QUOTE}`}
              disabled={atMax}
              className={`${inputClass} disabled:opacity-50`}
            />
          </div>
          <button
            type="button"
            onClick={addSymbol}
            disabled={atMax || normalizeHeaderSymbol(input) === null}
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-md px-4 py-2 text-xs text-text hover:bg-bg3 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            添加
          </button>
        </div>

        {atMax && (
          <p className="text-[10px] text-amber">
            已到上限 {HEADER_SYMBOLS_MAX} 个。要换品种请先删掉一个。
          </p>
        )}
        {input.trim() !== "" && normalizeHeaderSymbol(input) === null && (
          <p className="text-[10px] text-amber">
            写法看不懂：{input.trim()}（示例：BTC 或 BTC/{HEADER_SYMBOLS_QUOTE}，只支持{" "}
            {HEADER_SYMBOLS_QUOTE} 计价）
          </p>
        )}

        {/* 校验/保存错误：后端的原文直接显示，不按状态码自己翻译 */}
        {(error ?? (dirty ? localProblem : null)) && (
          <p className="text-[10px] text-red">{error ?? localProblem}</p>
        )}

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={!dirty || saving || localProblem !== null}
            className="bg-green text-black text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-85 transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            {saving ? "保存中..." : "保存币种"}
          </button>
          <button
            type="button"
            onClick={() => void save(DEFAULT_HEADER_SYMBOLS)}
            // 置灰条件是「**存的就是默认** 且 **草稿也是默认**」。只看前者不够：用户手上
            // 可能有一份改过的草稿而库里本来就是默认——那时按下去应当把草稿收回默认
            // （一次不改变库内容的保存，外层靠保存代际重置草稿），而不是什么都不做。
            disabled={
              loading ||
              saving ||
              (isDefault && draft.join(",") === DEFAULT_HEADER_SYMBOLS.join(","))
            }
            title={
              isDefault
                ? "恢复 BTC/ETH/SOL/BNB（会丢弃当前未保存的改动）"
                : "恢复 BTC/ETH/SOL/BNB"
            }
            className="bg-bg2 border border-[rgba(255,255,255,0.07)] rounded-lg px-4 py-2 text-xs text-text2 hover:text-text hover:bg-bg3 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            恢复默认
          </button>
        </div>
      </form>
    </div>
  );
}
