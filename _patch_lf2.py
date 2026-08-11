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
        <div v-for="pt in palTypes" :key="pt.type" class="fd-pal-item"
             draggable="true" @dragstart="onPalDragStart($event,pt.type)">
          <div class="fd-pal-ic" v-html="Icon.icon(pt.icon,18)"></div>
          <div class="fd-pal-body">
            <div class="fd-pal-name">{{pt.label}}</div>
            <div class="fd-pal-desc">{{pt.desc}}</div>
          </div>
        </div>
        <div class="fd-hint">💡 拖拽到画布添加<br>拖动节点头部移动<br>从端点拖出连线<br>双击节点编辑<br>Del键删除选中项</div>
      </div>
      <div class="lf-container" ref="lfContainer"></div>
    </div>
    <el-dialog v-model="dlg.vis" :title="dlg.isNew?'添加节点':'编辑节点'" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item label="节点名称">
          <el-input v-model="dlg.name" placeholder="如:主管审批、财务入账"/>
        </el-form-item>
        <el-form-item label="处理方式" v-if="dlg.isNew">
          <el-radio-group v-model="dlg.type">
            <el-radio value="start">开始</el-radio>
            <el-radio value="approve">人工审批</el-radio>
            <el-radio value="flow">系统流转</el-radio>
            <el-radio value="branch">分支</el-radio>
            <el-radio value="end">结束</el-radio>
          </el-radio-group>
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
    const dlg = reactive({vis:false,isNew:true,id:null,type:'start',name:'',role:'DEPARTMENT_HEAD'});
    let dragType = '';
    const nodeMap = {};

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
      {type:'start', label:'开始节点', icon:'play', desc:'流程起点', color:'#10b981'},
      {type:'approve', label:'审批节点', icon:'check', desc:'指定角色审批', color:'#8b5cf6'},
      {type:'flow', label:'流转节点', icon:'arrow-right', desc:'自动流转', color:'#06b6d4'},
      {type:'branch', label:'分支节点', icon:'fork', desc:'条件判断', color:'#f59e0b'},
      {type:'end', label:'结束节点', icon:'stop', desc:'流程终点', color:'#ef4444'},
    ];

    function getColor(type) {
      const m = palTypes.find(p=>p.type===type);
      return m?m.color:'#8b5cf6';
    }

    function initLF() {
      const { LogicFlow } = window;
      if (!LogicFlow) { setTimeout(initLF, 200); return; }

      lf = new LogicFlow({
        container: lfContainer.value,
        grid: { size: 20, visible: true, type: 'dot', config: { color: '#334155' } },
        background: { color: '#0f172a' },
        edgeType: 'bezier',
        keyboard: { enabled: true },
      });

      // 注册自定义节点
      palTypes.forEach(pt => {
        if (pt.type === 'start' || pt.type === 'end') {
          const Model = class extends LogicFlow.CircleNodeModel {
            initNodeData(data) {
              super.initNodeData(data);
              this.r = 32;
            }
            getNodeStyle() {
              const s = super.getNodeStyle();
              s.fill = pt.color; s.stroke = '#fff'; s.strokeWidth = 2;
              s.shadowColor = pt.color+'60'; s.shadowBlur = 15;
              return s;
            }
            getTextStyle() {
              const s = super.getTextStyle();
              s.color = '#fff'; s.fontSize = 13; s.fontWeight = 'bold';
              return s;
            }
          };
          lf.register({ type: pt.type, view: LogicFlow.CircleNode, model: Model });
        } else if (pt.type === 'branch') {
          const Model = class extends LogicFlow.PolygonNodeModel {
            initNodeData(data) {
              super.initNodeData(data);
              this.width = 80; this.height = 70;
            }
            getNodeStyle() {
              const s = super.getNodeStyle();
              s.fill = pt.color; s.stroke = '#fff'; s.strokeWidth = 2;
              s.shadowColor = pt.color+'40'; s.shadowBlur = 12;
              return s;
            }
            getTextStyle() {
              const s = super.getTextStyle();
              s.color = '#fff'; s.fontSize = 12; s.fontWeight = 'bold';
              return s;
            }
          };
          lf.register({ type: pt.type, view: LogicFlow.PolygonNode, model: Model });
        } else {
          const Model = class extends LogicFlow.RectNodeModel {
            initNodeData(data) {
              super.initNodeData(data);
              this.width = 160; this.height = 56; this.radius = 12;
            }
            getNodeStyle() {
              const s = super.getNodeStyle();
              s.fill = pt.color; s.stroke = '#fff'; s.strokeWidth = 2;
              s.shadowColor = pt.color+'40'; s.shadowBlur = 12;
              return s;
            }
            getTextStyle() {
              const s = super.getTextStyle();
              s.color = '#fff'; s.fontSize = 14; s.fontWeight = 'bold';
              return s;
            }
          };
          lf.register({ type: pt.type, view: LogicFlow.RectNode, model: Model });
        }
      });

      // 主题
      lf.setTheme({
        bezier: { stroke: '#00d4ff', strokeWidth: 2.5 },
        polyline: { stroke: '#00d4ff', strokeWidth: 2.5 },
        anchor: { stroke: '#00d4ff', fill: '#0f172a', r: 5, hoverStroke: '#22d3ee', hoverFill: '#fff' },
        anchorLine: { stroke: '#00d4ff', strokeWidth: 2, strokeDasharray: '4 4' },
        edgeText: { color: '#e2e8f0', fontSize: 12, background: { fill: '#1e293b' } },
        outline: { stroke: '#00d4ff', strokeWidth: 2 },
      });

      // 事件
      lf.on('node:dblclick', ({ data }) => {
        dlg.vis = true; dlg.isNew = false;
        dlg.id = data.id;
        dlg.type = data.properties.nodeType || data.type;
        dlg.name = (data.text && data.text.value) || '';
        dlg.role = data.properties.role || 'DEPARTMENT_HEAD';
      });

      lf.on('node:click,edge:click', () => {});

      // 键盘删除
      document.addEventListener('keydown', onKey);

      lf.render({ nodes: [], edges: [] });

      // 添加初始节点
      addNodeAt('start', '开始', 160, 220);
      addNodeAt('end', '结束', 500, 220);
    }

    function addNodeAt(type, name, x, y, role) {
      if (!lf) return null;
      const pt = palTypes.find(p=>p.type===type);
      const label = name || (pt?pt.label:'节点');
      const props = { nodeType: type };
      if (role) props.role = role;
      const node = lf.addNode({ type, x, y, text: label, properties: props });
      if (node && node.id) nodeMap[node.id] = { type, name: label, role: role||props.role };
      return node;
    }

    function onPalDragStart(e, type) {
      dragType = type;
      e.dataTransfer.effectAllowed = 'copy';
    }

    function onCanvasDrop(e) {
      e.preventDefault();
      if (!dragType || !lf) return;
      const pos = lf.getPointByClient(e.clientX, e.clientY);
      const pt = palTypes.find(p=>p.type===dragType);
      const isStartEnd = dragType==='start'||dragType==='end';
      addNodeAt(dragType, pt?pt.label:'节点', pos.x, pos.y, isStartEnd?null:'DEPARTMENT_HEAD');
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
        // 添加新节点
        addNodeAt(dlg.type, dlg.name, lfContainer.value.clientWidth/2, lfContainer.value.clientHeight/2, dlg.role);
      } else {
        const node = lf.getNodeModelById(dlg.id);
        if (node) {
          node.text.value = dlg.name;
          node.setProperties({ nodeType: dlg.type, role: dlg.role });
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
        const sel = lf && lf.getSelectElements();
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
        if (lf) { lf.clearData(); initStartEnd(); }
      }).catch(()=>{});
    }

    function initStartEnd() {
      addNodeAt('start', '开始', 160, 220);
      addNodeAt('end', '结束', 500, 220);
    }

    async function doSave() {
      if (!curBizType.value) { ElementPlus.ElMessage.warning('请选择业务类型'); return; }
      const data = lf.getGraphData();
      if (!data.nodes || !data.nodes.length) { ElementPlus.ElMessage.warning('请至少添加一个节点'); return; }
      const nodeList = data.nodes.map(n => ({
        type: n.properties.nodeType || n.type,
        name: (n.text && n.text.value) || '',
        role: n.properties.role || '',
        _x: n.x, _y: n.y, _id: n.id,
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
    lines = f.readlines()

# FlowDesignPage 从第2321行(index 2320)开始，到2755行(index 2754)的};结束
# 替换 2320 到 2754 (inclusive)
new_lines = lines[:2320] + [new_flow + '\n'] + lines[2755:]

with open(r'e:\trae\liu\static\app.js', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Replaced lines 2321-2755 ({2755-2321+1} lines) with new FlowDesignPage")
