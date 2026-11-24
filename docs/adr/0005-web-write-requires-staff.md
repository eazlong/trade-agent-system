# Web 写入口要 `is_staff`，且明确它不是安全边界

机制的三个开关今天只有两个写入口：管理命令与 `/regime …` slash 命令，两者都只要求「有一个认得出来的人」（`actor_name` 非空），不查任何角色——这与 `CONTEXT.md:163`「v1 没有角色模型，所有用户平等，系统级 halt 谁都能解除」一致。给机制开 Web 面之后，同一批动作多了一个入口，而网页上的大按钮比聊天里的命令更容易误触，且其中一个动作（`flip_regime_gate`）会在同一事务里顺手写一批 `HaltDeclaration` 行。

决定：**只对 Web 写入口加 `is_staff`（= `User.is_admin`，`apps/authentication/models.py:59-61`），读仍然对所有登录用户开放，管理命令与 slash 通路一律不动。**

## Considered Options

- **所有登录用户可写**（与 slash 现状一致）。被否决：它会让「翻档」成为一次误触就改变全系统行为的动作，而网页上没有聊天那种「先发一条命令看页面、再发一条命令动手」的两步节奏。
- **API 与 slash 一起收紧**。被否决：Telegram 斜杠通路本身还有既有缺陷（`CONTEXT.md:124` 记录的「消息过滤器把斜杠挡在 Agent 之外」），动它是另一张票；把它捆进这次改动会让一张功能票变成一次跨渠道的权限改造。
- **完整角色模型**。被否决：超出本次范围，且 `CONTEXT.md:163` 已经把「无角色模型」写成已知缺口而不是遗漏——它要的是有朝一日的整体设计，不是被一个页面顺手定义。

## Consequences

- **这层权限是界面纪律，不是安全边界**。slash 通路没收紧，所以任何登录用户仍然可以在聊天里敲一行命令做同样的事。这句话必须同时出现在页面文案与 `CONTEXT.md` 里，否则下一个人会以为权限已经收口，而真正的后门在另一个渠道。
- **前端要知道「我是不是 staff」，而它今天不知道**：`UserSerializer.Meta.fields`（`apps/authentication/serializers.py:36-44`）不含 `is_staff`/`is_admin`，`AuthContext` 只暴露 `{username, email}`。所以必须扩 serializer 并让 `AuthContext` 透出它——否则「隐藏按钮」这件事无从判断。
- **后端也必须强制，不能只靠前端隐藏**：新增本仓第一个 `IsStaffUser` 权限类（今天全仓视图只有 `IsAuthenticated`）。
- **`is_staff` 是 `is_admin` 的别名，不是 Django 的 staff 概念**：`has_perm` / `has_module_perms` 恒返回 `True`，全仓无 Django admin。所以「谁是管理员」只有一个布尔，没有权限粒度——这次不引入粒度。
- 未来的 Web 写入口应复用这条判据，而不是各自判断；若哪天 slash 也收紧，这条 ADR 的「不是安全边界」那句随之失效，需要改写而不是续写。
