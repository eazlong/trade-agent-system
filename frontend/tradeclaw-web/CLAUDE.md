# CLAUDE.md - Frontend (tradeclaw-web)

@AGENTS.md

## Frontend 专属上下文

### 技术栈
- **框架**: Next.js (App Router)
- **语言**: TypeScript
- **样式**: Tailwind CSS
- **状态管理**: React Context (`src/context/`)

### 开发命令
```bash
npm install
npm run dev        # 开发服务器
npm run build      # 生产构建
npm run lint       # ESLint 检查
```

### 目录结构
| 目录 | 职责 |
|------|------|
| `src/app/` | 页面路由（App Router） |
| `src/components/` | 可复用组件 |
| `src/lib/api.ts` | API 调用封装 |
| `src/hooks/` | 自定义 Hooks |
| `src/context/` | React Context 状态管理 |

### API 对接
后端 API 通过 `src/lib/api.ts` 统一调用，新增 API 端点需同步更新该文件。
