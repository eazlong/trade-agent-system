# MUI 到 Mantine 组件替换指南

## 已完成的替换

1. **package.json** - ✅ 已移除MUI依赖，添加Mantine依赖
2. **HeaderRight.jsx** - ✅ 替换完成
3. **Header.jsx** - ✅ 替换完成  
4. **TradingPlanModal.jsx** - ✅ 替换完成
5. **Layout/index.jsx** - ✅ 替换完成

## 剩余需要替换的文件和组件

### 主要MUI组件替换映射：
- `@mui/material/Button` → `@mantine/core/Button`
- `@mui/material/Box` → `@mantine/core/Box`
- `@mui/material/Typography` → `@mantine/core/Text` 或 `@mantine/core/Title`
- `@mui/material/Modal` → `@mantine/core/Modal`
- `@mui/material/Dialog` → `@mantine/core/Modal` 或 `@mantine/modals`
- `@mui/material/Select` → `@mantine/core/Select`
- `@mui/material/MenuItem` → Select 的 data 属性
- `@mui/material/IconButton` → `@mantine/core/ActionIcon`
- `@mui/material/CircularProgress` → `@mantine/core/Loader`
- `@mui/material/List` → `@mantine/core/List` 或手动实现
- `@mui/material/ListItem` → `@mantine/core/List.Item`
- `@mui/material/Divider` → `@mantine/core/Divider`
- `@mui/material/useMediaQuery` → `@mantine/hooks/useMediaQuery`
- `@mui/icons-material/*` → `@tabler/icons-react`

### 需要手动处理的文件：
1. `Components/Assistant/TradingPlan.jsx` - 大文件，需要分步替换
2. `Components/QtBot/UserBot.jsx` - 已开始替换，需要完成
3. `Components/Notify/Notify.jsx`
4. `Components/Assistant/HotCoin.jsx`
5. `Components/QtBot/UserBotConfigs.jsx`
6. 其他包含MUI的文件

### 特殊注意事项：
1. Modal的API变化：`open` → `opened`
2. Dialog需要使用`@mantine/modals`的confirmModal等方法
3. 主题系统需要适配Mantine的主题结构
4. 事件处理方式可能有差异

## 状态
- 基础配置：✅ 完成
- 核心文件：🟡 部分完成 
- 全部文件：❌ 需要继续处理
