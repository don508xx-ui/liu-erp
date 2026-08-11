new_flow = r'''const FlowDesignPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('workflow',22)"></div>
        <div>
          <div class="ph-title">流程设计器</div>
          <div class="ph-sub">左侧拖入节点 · 节点自由拖动 · 端点拖拽连线 · 双击编辑</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-select v-model="curBizType" placeholder="选择业务类型" style="width:160px">
          <el-option v-for="o in bizTypes" :key="o.v" :label="o.l" :value="o.v"/>
        </el-select>
        <el-button @click="doClear"><span v-html="Icon.icon('trash',14)" style="vertical-align:middle;margin-right:4px"></span>清空</el-button>
        <el-button type="primary" @click="doSave"><span v-html="Icon.icon('save',14)" style="vertical-align:middle;margin-right:4px"></span>保存</el-button>
      </div>
    </div>
    <div class="fd-layout">
      <div class="fd-palette">
        <div class="fd-pal-title">节点仓库</div>
        <div v-for="pt in palTypes" :key="pt.type" class="fd-pal-item" :class="'pal-'+pt.type"
             draggable="true" @dragstart="onPalDragStart($event,pt.type)">
          <div class="fd-pal-ic" v-html="Icon.icon(pt.icon,18)"></div>
          <div class="fd-pal-body">
            <div class="fd-pal-name">{{pt.label}}</div>
            <div class="fd-pal-desc">{{pt.desc}}</div>
          </div>
        </div>
        <div class="fd-hint">💡 拖拽到画布添加<br>拖动节点移动<br>端点拖出连线<br>双击编辑 · Del删除</div>
      </div>
      <div class="lf-container" ref="lfContainer"></div>
    </div>
    <el-dialog v-model="dlg.vis" :title="dlg.isNew?'添加节点':'编辑节点'" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item label="节点名称">
          <el-input v-model="dlg.name" placeholder="如:主管审批、财务入账"/>
        </el-form-item>
        <el-form-item label="处理角色" v-if="dlg.type==='approve'">
          <el-select v-model="dlg.role" style="width:100%" @change="onRoleChange">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v"/>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" type="danger" @click="delFromDlg" v-if="!dlg.isNew && dlg.type!=='start' && dlg.type!=='end'" style="float:left">删除节点</el-button>
        <el-button @click="dlg.vis=false">取消</el-button>
        <el-button type="primary" @click="saveDlg">确定</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ref, reactive, onMounted, onBeforeUnmount, nextTick } = Vue;
    const lfContainer = ref(null);
    let lf = null;
    const curBizType = ref('core_production');
    const dlg = reactive({vis:false,isNew:true,id:null,type:'approve',name:'',role:'DEPARTMENT_HEAD'});
    let dragType = '';

    const bizTypes = [
      {v:'core_production',l:'核心生产流'},
      {v:'procurement',l:'采购审批流'},
      {v:'expense',l:'费用报销流'},
      {v:'price_adjust',l:'调价审批流'},
    ];
    const roles = [
      {v:'DEPARTMENT_HEAD',l:'部门主管'},
      {v:'FINANCE',l:'财务'},
      {v:'OPERATION',l:'运营助理'},
      {v:'FACTORY_MANAGER',l:'厂长'},
      {v:'GM',l:'总经理'},
      {v:'WAREHOUSE',l:'仓管'},
      {v:'SALES',l:'销售'},
    ];
    const palTypes = [
      {type:'start', label:'开始节点', icon:'play', desc:'流程起点', ntype:'circle', color:'#10b981'},
      {type:'approve', label:'审批节点', icon:'check', desc:'指定角色审批', ntype:'rect', color:'#8b5cf6'},
      {type:'flow', label:'流转节点', icon:'arrow-right', desc:'自动流转', ntype:'rect', color:'#06b6d4'},
      {type:'branch', label:'分支节点', icon:'fork', desc:'条件判断', ntype:'diamond', color:'#f59e0b'},
      {type:'end', label:'结束节点', icon:'stop', desc:'流程终点', ntype:'circle', color:'#ef4444'},
    ];

    function typeMeta(type) { return palTypes.find(p=>p.type===type) || palTypes[1]; }

    function initLF() {
      if (!window.LogicFlow) { setTimeout(initLF, 200); return; }
      try {
        lf = new LogicFlow({
          container: lfContainer.value,
          grid: { size: 20, type: 'dot', config: { color: '#334155' } },
          background: { color: '#0f172a' },
          edgeType: 'bezier',
          keyboard: { enabled: true },
        });

        lf.setTheme({
          rect: { fill: '#8b5cf6', stroke: '#fff', strokeWidth: 2, radius: 12, width: 160, height: 56 },
          circle: { fill: '#10b981', stroke: '#fff', strokeWidth: 2, r: 32 },
          diamond: { fill: '#f59e0b', stroke: '#fff', strokeWidth: 2 },
          edge: { stroke: '#00d4ff', strokeWidth: 2.5 },
          anchor: { stroke: '#00d4ff', fill: '#0f172a', r: 5, hoverStroke: '#22d3ee', hoverFill: '#fff' },
          anchorLine: { stroke: '#00d4ff', strokeWidth: 2, strokeDasharray: '4 4' },
          edgeText: { color: '#e2e8f0', fontSize: 12, background: { fill: '#1e293b' } },
          outline: { stroke: '#00d4ff', strokeWidth: 2 },
          text: { color: '#fff', fontSize: 14, fontWeight: 'bold' },
        });

        // 双击编辑节点
        lf.on('node:dblclick', ({ data }) => {
          dlg.vis = true; dlg.isNew = false;
          dlg.id = data.id;
          dlg.type = data.properties.nodeType || 'approve';
          dlg.name = data.text || '';
          dlg.role = data.properties.role || 'DEPARTMENT_HEAD';
        });

        // 键盘删除
        document.addEventListener('keydown', onKey);

        lf.render({ nodes: [], edges: [] });

        addNode('start', '开始', 160, 220);
        addNode('end', '结束', 480, 220);
      } catch(e) {
        console.error('LogicFlow init error:', e);
      }
    }

    function addNode(type, name, x, y, role) {
      if (!lf) return null;
      const meta = typeMeta(type);
      const text = name || meta.label;
      const props = { nodeType: type };
      if (role) props.role = role;
      // 根据类型设置样式
      const style = {};
      if (meta.ntype === 'circle') { style.fill = meta.color; style.stroke = '#fff'; style.strokeWidth = 2; style.r = type==='start'?32:32; }
      else if (meta.ntype === 'diamond') { style.fill = meta.color; style.stroke = '#fff'; style.strokeWidth = 2; }
      else { style.fill = meta.color; style.stroke = '#fff'; style.strokeWidth = 2; style.radius = 12; }
      return lf.addNode({ type: meta.ntype, x, y, text, properties: props, ...(meta.ntype==='rect'?{width:160,height:56}:meta.ntype==='circle'?{r:32}:{}) });
    }

    function onPalDragStart(e, type) {
      dragType = type;
      e.dataTransfer.effectAllowed = 'copy';
    }

    function onCanvasDrop(e) {
      e.preventDefault();
      if (!dragType || !lf) return;
      const pos = lf.getPointByClient(e.clientX, e.clientY);
      const meta = typeMeta(dragType);
      const isSE = dragType==='start'||dragType==='end';
      addNode(dragType, meta.label, pos.x, pos.y, isSE?null:'DEPARTMENT_HEAD');
      dragType = '';
    }

    function onRoleChange(roleCode) {
      const roleCN = {DEPARTMENT_HEAD:'部门主管',FINANCE:'财务',OPERATION:'运营助理',FACTORY_MANAGER:'厂长',GM:'总经理',WAREHOUSE:'仓管',SALES:'销售'};
      if (!dlg.name || dlg.name==='审批' || dlg.name==='人工审批') {
        dlg.name = (roleCN[roleCode]||'')+'审批';
      }
    }

    function saveDlg() {
      if (!dlg.name) { ElementPlus.ElMessage.warning('请输入节点名称'); return; }
      if (dlg.type==='approve' && !dlg.role) { ElementPlus.ElMessage.warning('请选择处理角色'); return; }
      if (dlg.isNew) {
        addNode(dlg.type, dlg.name, lfContainer.value.clientWidth/2, lfContainer.value.clientHeight/2, dlg.role);
      } else {
        const node = lf.getNodeModelById(dlg.id);
        if (node) {
          node.text = dlg.name;
          node.setProperties({ nodeType: dlg.type, role: dlg.role });
          // 更新颜色
          const meta = typeMeta(dlg.type);
          const model = lf.getModelById(dlg.id);
          if (model && model.setStyle) {
            model.setStyle({ fill: meta.color, stroke: '#fff', strokeWidth: 2 });
          }
        }
      }
      dlg.vis = false;
    }

    function delFromDlg() {
      if (dlg.id) lf.deleteNode(dlg.id);
      dlg.vis = false;
    }

    function onKey(e) {
      if (dlg.vis) return;
      const tag = (document.activeElement||{}).tagName||'';
      if (['INPUT','TEXTAREA','SELECT'].includes(tag)) return;
      if (e.key==='Delete' || e.key==='Backspace') {
        const sel = lf && lf.getSelectElements && lf.getSelectElements();
        if (sel && sel.nodes && sel.nodes.length) {
          sel.nodes.forEach(n => { if (n.type!=='start'&&n.type!=='end') lf.deleteNode(n.id); });
        }
        if (sel && sel.edges && sel.edges.length) {
          sel.edges.forEach(ed => lf.deleteEdge(ed.id));
        }
      }
    }

    function doClear() {
      ElementPlus.ElMessageBox.confirm('清空所有节点和连线？','提示',{type:'warning'}).then(()=>{
        if (lf) { lf.clearData(); addNode('start','开始',160,220); addNode('end','结束',480,220); }
      }).catch(()=>{});
    }

    async function doSave() {
      if (!curBizType.value) { ElementPlus.ElMessage.warning('请选择业务类型'); return; }
      const data = lf.getGraphData();
      if (!data.nodes || !data.nodes.length) { ElementPlus.ElMessage.warning('请至少添加一个节点'); return; }
      const nodeList = data.nodes.map(n => ({
        type: n.properties.nodeType || (n.type==='circle'?'start':'approve'),
        name: n.text || '',
        role: n.properties.role || '',
        _x: n.x, _y: n.y, _id: n.id, _shape: n.type,
      }));
      const connList = (data.edges||[]).map(e => ({ from: e.sourceNodeId, to: e.targetNodeId }));
      try {
        await api.post('/api/approvals/definitions', {
          biz_type: curBizType.value,
          name: (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程',
          nodes: nodeList,
          _connections: connList,
        });
        ElementPlus.ElMessage.success('保存成功');
      } catch(e) { ElementPlus.ElMessage.error(e.message); }
    }

    onMounted(() => {
      nextTick(() => {
        const container = lfContainer.value;
        if (container) {
          container.addEventListener('drop', onCanvasDrop);
          container.addEventListener('dragover', e => e.preventDefault());
        }
        setTimeout(initLF, 100);
      });
    });

    onBeforeUnmount(() => {
      document.removeEventListener('keydown', onKey);
    });

    return {
      lfContainer, curBizType, dlg, bizTypes, roles, palTypes, Icon,
      onPalDragStart, onRoleChange, saveDlg, delFromDlg, doClear, doSave
    };
  }
};'''

with open(r'e:\trae\liu\static\app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换 FlowDesignPage 组件
import re
pattern = r'const FlowDesignPage = \{[\s\S]*?^\};'
match = re.search(pattern, content, re.MULTILINE)
if match:
    content = content[:match.start()] + new_flow + content[match.end():]
    print(f"FlowDesignPage replaced at {match.start()}-{match.end()}")
else:
    print("Pattern not found, using line-based replacement")
    lines = content.split('\n')
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.startswith('const FlowDesignPage = {') and start_idx is None:
            start_idx = i
        if start_idx is not None and line.strip() == '};' and i > start_idx:
            # 确认这是FlowDesignPage的结束（后面是空行+const App）
            if i+1 < len(lines) and (lines[i+1].strip() == '' or lines[i+1].strip().startswith('const App')):
                end_idx = i
                break
    if start_idx is not None and end_idx is not None:
        new_lines = lines[:start_idx] + [new_flow] + lines[end_idx+1:]
        content = '\n'.join(new_lines)
        print(f"Replaced lines {start_idx+1}-{end_idx+1}")
    else:
        print(f"Could not find range: start={start_idx}, end={end_idx}")

with open(r'e:\trae\liu\static\app.js', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
