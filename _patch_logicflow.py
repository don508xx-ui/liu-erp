import re

with open(r'e:\trae\liu\static\app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 新的FlowDesignPage组件 - 使用LogicFlow
new_flow = r'''const FlowDesignPage = {
  template: `
  <div class="fd-page">
    <div class="fd-header">
      <h3>流程设计器</h3>
      <div class="fd-toolbar">
        <el-button size="small" @click="zoomOut"><i class="iconfont icon-minus"></i></el-button>
        <span class="fd-zoom">{{ Math.round(zoom*100) }}%</span>
        <el-button size="small" @click="zoomIn"><i class="iconfont icon-plus"></i></el-button>
        <el-button size="small" @click="resetView"><i class="iconfont icon-expand"></i> 重置视图</el-button>
        <el-button type="primary" size="small" @click="saveFlow"><i class="iconfont icon-save"></i> 保存流程</el-button>
      </div>
    </div>
    <div class="fd-body">
      <div class="fd-sidebar">
        <h4>节点仓库</h4>
        <div class="fd-nodelist">
          <div v-for="n in nodeTypes" :key="n.type" class="fd-node-item" draggable="true"
               @dragstart="onDragStart($event, n.type)">
            <div :class="['fd-node-mini', 'fd-mini-'+n.type]">
              <i :class="'iconfont icon-'+n.icon"></i>
            </div>
            <span>{{ n.name }}</span>
          </div>
        </div>
        <h4>节点说明</h4>
        <div class="fd-tip">
          <p><b>开始/结束</b>：流程起止点</p>
          <p><b>审批节点</b>：指定角色审批处理</p>
          <p><b>流转节点</b>：自动流转到下一节点</p>
          <p><b>分支节点</b>：条件判断分支</p>
          <p style="margin-top:8px;color:#00d4ff">💡 拖拽节点到画布 | 双击编辑 | 点击连线端点拖拽连接</p>
        </div>
      </div>
      <div class="fd-canvas" ref="canvas" @drop="onDrop" @dragover.prevent></div>
    </div>
    <el-dialog v-model="editVisible" title="编辑节点" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px" size="default">
        <el-form-item label="节点名称">
          <el-input v-model="editing.name" placeholder="请输入节点名称"></el-input>
        </el-form-item>
        <el-form-item label="节点类型" v-if="editing.type !== 'start' && editing.type !== 'end'">
          <el-select v-model="editing.type" @change="onTypeChange" style="width:100%">
            <el-option v-for="n in nodeTypes.filter(t=>t.type!=='start'&&t.type!=='end')" :key="n.type" :label="n.name" :value="n.type"></el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="负责角色" v-if="editing.type==='approve'">
          <el-select v-model="editing.role" style="width:100%">
            <el-option label="部门主管" value="DEPARTMENT_HEAD"></el-option>
            <el-option label="财务" value="FINANCE"></el-option>
            <el-option label="运营助理" value="OPERATION"></el-option>
            <el-option label="厂长" value="FACTORY_MANAGER"></el-option>
            <el-option label="总经理" value="GM"></el-option>
            <el-option label="仓管" value="WAREHOUSE"></el-option>
            <el-option label="销售" value="SALES"></el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" type="danger" @click="deleteNode" v-if="editing.type!=='start' && editing.type!=='end'" style="float:left">删除节点</el-button>
        <el-button @click="editVisible=false">取消</el-button>
        <el-button type="primary" @click="saveNode">确定</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ref, reactive, onMounted, nextTick } = Vue;
    const canvas = ref(null);
    const lf = ref(null);
    const zoom = ref(1);
    const editVisible = ref(false);
    const editing = reactive({id:'',type:'approve',name:'',role:'DEPARTMENT_HEAD'});
    let dragType = '';

    const nodeTypes = [
      {type:'start', name:'开始节点', icon:'play', color:'#10b981'},
      {type:'approve', name:'审批节点', icon:'check', color:'#8b5cf6'},
      {type:'flow', name:'流转节点', icon:'arrow-right', color:'#06b6d4'},
      {type:'branch', name:'分支节点', icon:'fork', color:'#f59e0b'},
      {type:'end', name:'结束节点', icon:'stop', color:'#ef4444'},
    ];

    function getNodeColor(type) {
      const m = nodeTypes.find(n=>n.type===type);
      return m ? m.color : '#8b5cf6';
    }

    function initLogicFlow() {
      const { LogicFlow } = window;
      if (!LogicFlow) {
        console.error('LogicFlow not loaded');
        return;
      }

      // 注册自定义审批节点
      class ApproveNodeModel extends LogicFlow.RectNodeModel {
        initNodeData(data) {
          super.initNodeData(data);
          this.width = 160;
          this.height = 56;
          this.radius = 12;
        }
        getNodeStyle() {
          const style = super.getNodeStyle();
          const color = getNodeColor(this.properties.nodeType || 'approve');
          style.fill = color;
          style.stroke = '#fff';
          style.strokeWidth = 2;
          style.shadowColor = 'rgba(0,0,0,0.3)';
          style.shadowBlur = 12;
          return style;
        }
        getTextStyle() {
          const style = super.getTextStyle();
          style.color = '#fff';
          style.fontSize = 14;
          style.fontWeight = 'bold';
          return style;
        }
      }

      class ApproveNodeView extends LogicFlow.RectNode {}

      lf.value = new LogicFlow({
        container: canvas.value,
        grid: { size: 20, visible: true, type: 'dot', config: { color: '#ababab' } },
        background: { color: '#0f172a' },
        edgeType: 'bezier',
        keyboard: { enabled: true },
      });

      // 注册所有自定义节点类型
      nodeTypes.forEach(nt => {
        if (nt.type === 'start' || nt.type === 'end') {
          // 开始/结束用圆形
          class CircleModel extends LogicFlow.CircleNodeModel {
            initNodeData(data) {
              super.initNodeData(data);
              this.r = 30;
            }
            getNodeStyle() {
              const style = super.getNodeStyle();
              style.fill = nt.color;
              style.stroke = '#fff';
              style.strokeWidth = 3;
              style.shadowColor = nt.color + '80';
              style.shadowBlur = 15;
              return style;
            }
            getTextStyle() {
              const style = super.getTextStyle();
              style.color = '#fff';
              style.fontSize = 13;
              style.fontWeight = 'bold';
              return style;
            }
          }
          lf.value.register({ type: nt.type, view: LogicFlow.CircleNode, model: CircleModel });
        } else if (nt.type === 'branch') {
          // 分支用菱形
          class DiamondModel extends LogicFlow.PolygonNodeModel {
            initNodeData(data) {
              super.initNodeData(data);
              this.width = 80;
              this.height = 80;
            }
            getNodeStyle() {
              const style = super.getNodeStyle();
              style.fill = nt.color;
              style.stroke = '#fff';
              style.strokeWidth = 2;
              style.shadowColor = 'rgba(245,158,11,0.4)';
              style.shadowBlur = 12;
              return style;
            }
            getTextStyle() {
              const style = super.getTextStyle();
              style.color = '#fff';
              style.fontSize = 12;
              style.fontWeight = 'bold';
              return style;
            }
          }
          lf.value.register({ type: nt.type, view: LogicFlow.PolygonNode, model: DiamondModel });
        } else {
          // 审批/流转用圆角矩形
          lf.value.register({
            type: nt.type,
            view: ApproveNodeView,
            model: class extends ApproveNodeModel {
              getNodeStyle() {
                const style = super.getNodeStyle();
                style.fill = nt.color;
                style.shadowColor = nt.color + '40';
                return style;
              }
            }
          });
        }
      });

      // 连线样式
      lf.value.setTheme({
        bezier: { stroke: '#00d4ff', strokeWidth: 2.5, shadowColor: 'rgba(0,212,255,0.5)', shadowBlur: 8 },
        polyline: { stroke: '#00d4ff', strokeWidth: 2.5 },
        line: { stroke: '#00d4ff', strokeWidth: 2.5 },
        anchor: { stroke: '#00d4ff', fill: '#0f172a', r: 5, hoverStroke: '#22d3ee', hoverFill: '#fff' },
        anchorLine: { stroke: '#00d4ff', strokeWidth: 2, strokeDasharray: '4 4' },
        edgeText: { color: '#e2e8f0', fontSize: 12, background: { fill: '#1e293b' } },
        outline: { stroke: '#00d4ff', strokeWidth: 2, hover: { stroke: '#22d3ee' } },
      });

      // 事件监听
      lf.value.on('node:dblclick', ({ data }) => {
        Object.assign(editing, {
          id: data.id,
          type: data.properties.nodeType || data.type,
          name: data.text.value || '',
          role: data.properties.role || 'DEPARTMENT_HEAD'
        });
        editVisible.value = true;
      });

      lf.value.on('blank:contextmenu', ({ e }) => {
        // 右键菜单预留
      });

      lf.value.on('node:delete', ({ data }) => {
        console.log('deleted:', data.id);
      });

      lf.value.render({ nodes: [], edges: [] });

      // 初始添加开始节点
      addNodeAtPos('start', '开始', 150, 200);
    }

    function addNodeAtPos(type, name, x, y, role) {
      if (!lf.value) return null;
      const m = nodeTypes.find(n=>n.type===type);
      const nodeName = name || (m ? m.name : '节点');
      const properties = { nodeType: type };
      if (role) properties.role = role;

      if (type === 'start' || type === 'end') {
        return lf.value.addNode({ type, x, y, text: nodeName, properties });
      } else if (type === 'branch') {
        return lf.value.addNode({ type, x, y, text: nodeName, properties });
      } else {
        return lf.value.addNode({ type, x, y, text: nodeName, properties });
      }
    }

    function onDragStart(e, type) {
      dragType = type;
      e.dataTransfer.effectAllowed = 'copy';
    }

    function onDrop(e) {
      e.preventDefault();
      if (!dragType || !lf.value) return;
      const rect = canvas.value.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      // 转换为LogicFlow坐标（考虑缩放平移）
      const pos = lf.value.getPointByClient(e.clientX, e.clientY);
      addNodeAtPos(dragType, null, pos.x || x, pos.y || y);
      dragType = '';
    }

    function onTypeChange() {
      // 类型改变时更新节点
    }

    function saveNode() {
      if (!editing.id || !lf.value) { editVisible.value=false; return; }
      const node = lf.value.getNodeModelById(editing.id);
      if (node) {
        node.text.value = editing.name;
        node.setProperties({ nodeType: editing.type, role: editing.role });
        // 如果类型变了需要重新添加（LogicFlow不支持动态改类型，简单处理：更新样式即可）
      }
      editVisible.value = false;
    }

    function deleteNode() {
      if (!editing.id) return;
      lf.value.deleteNode(editing.id);
      editVisible.value = false;
    }

    function zoomIn() {
      if (lf.value) {
        lf.value.zoom(true);
        zoom.value = lf.value.getTransform().SCALE_X;
      }
    }

    function zoomOut() {
      if (lf.value) {
        lf.value.zoom(false);
        zoom.value = lf.value.getTransform().SCALE_X;
      }
    }

    function resetView() {
      if (lf.value) {
        lf.value.resetZoom();
        lf.value.focusOn({ coordinate: { x: 400, y: 200 } });
        zoom.value = 1;
      }
    }

    function saveFlow() {
      if (!lf.value) return;
      const data = lf.value.getGraphData();
      console.log('流程数据:', JSON.stringify(data, null, 2));
      ElementPlus.ElMessage.success('流程已保存！数据已输出到控制台');
    }

    onMounted(() => {
      nextTick(() => {
        setTimeout(initLogicFlow, 100);
      });
    });

    return {
      canvas, zoom, editVisible, editing, nodeTypes,
      onDragStart, onDrop, onTypeChange, saveNode, deleteNode,
      zoomIn, zoomOut, resetView, saveFlow
    };
  }
};'''

# 找到FlowDesignPage的定义并替换
# 匹配 const FlowDesignPage = { ... };
pattern = r'const\s+FlowDesignPage\s*=\s*\{[^;]*?^\};'
match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
if match:
    content = content[:match.start()] + new_flow + content[match.end():]
    print(f"FlowDesignPage replaced, from {match.start()} to {match.end()}")
else:
    print("FlowDesignPage not found with pattern, trying alternative...")
    # 尝试找到注释标记
    start_marker = '// ============ 流程设计器'
    end_marker = '// ============ 数据分析页'
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx != -1 and end_idx != -1:
        # 找到const FlowDesignPage开始的位置
        fd_start = content.rfind('const FlowDesignPage', 0, start_idx + 100)
        fd_end = end_idx
        # 找};结束
        brace_end = content.find('};', start_idx)
        if brace_end != -1 and brace_end < end_idx:
            fd_end = brace_end + 2
        content = content[:fd_start] + new_flow + '\n' + content[fd_end:]
        print(f"FlowDesignPage replaced (alt method), from {fd_start} to {fd_end}")
    else:
        print("ERROR: Could not find FlowDesignPage")

with open(r'e:\trae\liu\static\app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
