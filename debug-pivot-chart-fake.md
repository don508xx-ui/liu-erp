[CLOSED]

# 调试会话: pivot-chart-fake

## 问题描述
用户反馈: "你这种简单的逻辑都如此多的错误！！！！！！什么柱状图也是写来骗人的"

## 根因分析

### 根因1: 后端自动图表类型判断错误 (主因)
- **文件**: `d:\trae\liu\app\core\pivot.py` L409-410
- **问题**: 当用户选择"自动"图表类型时，后端对3-8个分组的单系列数据自动选择了饼图(pie)，而非柱状图(bar)
- **影响**: 用户期望看到柱状图，实际得到饼图（或饼图渲染问题导致看不到）
- **修复**: 移除自动选择饼图的逻辑，默认统一使用柱状图

### 根因2: 前端图表实例管理不当
- **文件**: `d:\trae\liu\static\app.js` L5756-5760
- **问题**: `setChartRef`在DOM元素销毁时未清理旧的chartInstance，导致重建时使用已失效的实例
- **影响**: 切换筛选条件后图表可能不显示
- **修复**: 
  1. 在`setChartRef`中处理null时dispose旧实例
  2. 添加`watch`监听`pivotResult`变化自动重渲染
  3. 添加`onBeforeUnmount`清理资源
  4. 添加window resize事件响应

## 修复清单

1. **pivot.py** - 修改图表类型自动判断逻辑
   - 移除: `elif single_series and 3 <= len(row_keys) <= 8: chart_type = "pie"`
   - 默认: 统一使用柱状图(bar)，时间维度使用折线图(line)

2. **app.js** - 修复图表实例管理
   - `setChartRef`: 添加null处理，dispose旧实例
   - 新增`watch(pivotResult)`: 数据变化时自动重渲染
   - 新增`onBeforeUnmount`: 清理chartInstance和事件监听
   - 新增resize事件: 窗口大小变化时调整图表

3. **index.html** - 更新缓存版本号
   - `v=20260817-54` → `v=20260818-01`

## 测试结果
- 9个测试场景全部通过
- 图表类型正确: 单系列→bar, 交叉→bar(堆叠), 时间→line
- 聚合逻辑正确: count统计记录数, sum统计金额
- 筛选功能正确: 枚举/日期/数值筛选均通过

## 状态: 已修复
