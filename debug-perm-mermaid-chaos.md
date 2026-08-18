# Debug Session: perm-mermaid-chaos

**Status:** [OPEN]
**Created:** 2026-08-18
**Session ID:** perm-mermaid-chaos

## 问题描述
1. 权限混乱：各角色（GM/ADMIN/销售/生产等）权限配置不一致，导航菜单缺失，工作流实例不可见
2. LLM输出经营分析时总是出现 "mermaid渲染中..." 卡住

## 假设 (Hypotheses)

### H1: 数据库role_permissions表pages字段配置与前端ROLE_PAGES不同步
- 观测点: 对比数据库中各角色的pages字段值 vs app.js中ROLE_PAGES常量
- 预期: GM应为'*'，ADMIN应为'*'或完整列表，其他角色应有完整pages

### H2: app.js的navItems计算属性对空数组rolePages的fallback逻辑有bug
- 观测点: 当API返回空数组时，是否正确使用ROLE_PAGES_FALLBACK
- 预期: 空数组时应fallback，不应返回空列表

### H3: 后端workbench API的_get_workflow_steps对非GM角色过滤了已完成实例
- 观测点: 检查SQL查询是否带status过滤条件
- 预期: 应返回所有用户参与的实例，不按status过滤

### H4: 前端formattedReply未正确处理mermaid代码块，导致渲染器卡在"渲染中..."
- 观测点: 检查formattedReply输出是否残留```mermaid标记
- 预期: mermaid块应被识别并正确渲染或移除

### H5: mermaid库CDN加载失败或renderMermaid函数异常未捕获
- 观测点: 浏览器console是否有mermaid加载错误
- 预期: mermaid库应正常加载并渲染

## 调试步骤

### Step 1: 静态审查 + 数据库对比（不修改代码）
- [ ] 读取数据库role_permissions表
- [ ] 读取app.js中ROLE_PAGES/ROLE_PAGES_FALLBACK/ALL_NAV
- [ ] 读取后端loadRolePages API
- [ ] 读取workbench _get_workflow_steps
- [ ] 读取formattedReply函数
- [ ] 读取mermaid渲染逻辑

### Step 2: 插桩收集运行时证据
- [ ] 在navItems计算属性添加日志
- [ ] 在loadRolePages API添加日志
- [ ] 在_get_workflow_steps添加日志
- [ ] 在formattedReply添加日志
- [ ] 在renderMermaid添加日志

### Step 3: 分析证据并修复
### Step 4: 验证修复
### Step 5: 清理

## 证据记录
（待填充）

## 修复记录
（待填充）
