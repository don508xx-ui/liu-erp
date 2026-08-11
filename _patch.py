import re

with open(r'e:\trae\liu\static\app.js', 'r', encoding='utf-8') as f:
    content = f.read()

new_code = '''// ============ 流程设计器 (N8N风格自由画布, Drawflow) ============
const FlowDesignPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('workflow',22)"></div>
        <div>
          <div class="ph-title">流程设计器</div>
          <div class="ph-sub">左侧拖入/点击添加节点 · 节点自由拖动 · 从右侧○拖到左侧○连线 · 双击节点编辑</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-select v-model="curBizType" placeholder="选择业务类型" style="width:180px" @change="loadDef">
          <el-option v-for="o in bizTypes" :key="o.v" :label="o.l" :value="o.v"/>
        </el-select>
        <el-select v-model="copyFrom" placeholder="从已有流程复制..." style="width:180px" clearable @change="copyFromTpl">
          <el-option v-for="d in allDefs" :key="d.id" :label="d.name" :value="d.id"/>
        </el-select>
        <el-button @click="clearAll"><span v-html="Icon.icon('trash',14)" style="vertical-align:middle;margin-right:4px"></span>清空</el-button>
        <el-button type="primary" @click="save"><span v-html="Icon.icon('save',14)" style="vertical-align:middle;margin-right:4px"></span>保存</el-button>
      </div>
    </div>
    <div class="nf-layout">
      <div class="nf-palette">
        <div class="nf-pal-title">节点仓库</div>
        <div class="nf-pal-item" v-for="pt in palTypes" :key="pt.type" :class="pt.cls" draggable="true" @dragstart="onDragStart($event,pt.type)" @click="quickAdd(pt.type)">
          <div class="nf-pal-ic" v-html="Icon.icon(pt.icon,18)"></div>
          <div class="nf-pal-body">
            <div class="nf-pal-name">{{pt.label}}</div>
            <div class="nf-pal-desc">{{pt.desc}}</div>
          </div>
        </div>
        <div class="nf-pal-hint">💡 点击=在画布中心添加<br>拖拽=放到指定位置<br>节点可自由拖动<br>从右○拖到左○连线<br>双击节点=编辑<br>Del键=删除选中节点</div>
        <div class="nf-pal-title" style="margin-top:16px">已有流程</div>
        <div class="nf-tpl-list">
          <div v-for="d in allDefs" :key="d.id" class="nf-tpl-item" @click="loadDefById(d.id)">
            <span class="nf-tpl-name">{{d.name}}</span>
            <span class="nf-tpl-meta">v{{d.version}} · {{d.nodes.length}}步</span>
          </div>
          <div v-if="!allDefs.length" class="nf-pal-hint">暂无</div>
        </div>
      </div>
      <div class="nf-canvas-wrap" ref="wrapRef" @dragover.prevent="onDragOver" @drop="onDrop">
        <div id="nf-drawflow" ref="dfRef" class="drawflow nf-canvas"></div>
        <div class="nf-zoom">
          <button class="nf-zoom-btn" @click="zoom(0.1)" title="放大">+</button>
          <button class="nf-zoom-btn" @click="zoom(-0.1)" title="缩小">−</button>
          <button class="nf-zoom-btn" @click="resetView" title="重置">⟲</button>
        </div>
      </div>
    </div>
    <el-dialog v-model="dlg.visible" :title="dlg.isNew?'添加节点':'编辑节点'" width="440px" :close-on-click-modal="false">
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item label="节点名称">
          <el-input v-model="dlg.data.name" placeholder="如:运营核对、主管审批、财务入账"/>
        </el-form-item>
        <el-form-item label="处理方式" v-if="dlg.isNew">
          <el-radio-group v-model="dlg.data.type">
            <el-radio label="trigger">开始(发起人提交)</el-radio>
            <el-radio label="approve">人工处理(角色审批)</el-radio>
            <el-radio label="process">系统流转(自动推进)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理角色" v-if="dlg.data.type==='approve'">
          <el-select v-model="dlg.data.role" style="width:100%" @change="onRoleChange">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v"/>
          </el-select>
        </el-form-item>
        <div class="muted tiny" style="margin-top:6px;line-height:1.6;padding:8px;background:var(--panel2);border-radius:6px">
          <template v-if="dlg.data.type==='trigger'">🟢 发起人提交表单，流程自动启动</template>
          <template v-else-if="dlg.data.type==='approve'">🔵 由「<b>{{roleLabel(dlg.data.role)}}</b>」人工处理，决定通过/驳回</template>
          <template v-else>🟢 系统自动执行，无需人工干预，直接推进到下一步</template>
        </div>
      </el-form>
      <template #footer>
        <el-button native-type="button" @click="dlg.visible=false">取消</el-button>
        <el-button native-type="button" type="danger" @click="deleteFromDlg" v-if="!dlg.isNew">删除</el-button>
        <el-button native-type="button" type="primary" @click="saveDlg">确定</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const bizTypes = [
      {v:'PURCHASE_REQUEST',l:'采购申请'},{v:'RECEIVING',l:'来货登记'},{v:'COMPLETION',l:'完工单'},
      {v:'EXPENSE',l:'费用报销'},{v:'SALES_ADJUSTMENT',l:'调价申请'},
    ];
    const roles = [
      {v:'DEPARTMENT_HEAD',l:'部门主管'},{v:'FINANCE',l:'财务'},{v:'GM',l:'总经理'},
      {v:'MANAGER',l:'厂长'},{v:'WAREHOUSE',l:'仓管'},{v:'SALES',l:'销售'},
      {v:'PURCHASE',l:'采购'},{v:'OPERATION',l:'运营'},{v:'ADMIN',l:'管理员'},
    ];
    const palTypes = [
      {type:'trigger',label:'开始节点',desc:'发起人提交',icon:'play',cls:'pal-trigger'},
      {type:'approve',label:'人工处理',desc:'选角色审批',icon:'check',cls:'pal-approve'},
      {type:'process',label:'系统流转',desc:'自动推进',icon:'zap',cls:'pal-process'},
    ];
    const ROLE_CN = Object.fromEntries(roles.map(r=>[r.v,r.l]));
    const TYPE_META = {
      trigger: {name:'发起人提交', inputs:0, outputs:1, cls:'nf-trigger', icon:'play'},
      approve: {name:'人工处理', inputs:1, outputs:1, cls:'nf-approve', icon:'check'},
      process: {name:'自动流转', inputs:1, outputs:1, cls:'nf-process', icon:'zap'},
    };

    const dfRef = ref(null);
    const wrapRef = ref(null);
    const curBizType = ref('');
    const copyFrom = ref('');
    const allDefs = ref([]);
    const currentDefId = ref(null);
    const dlg = reactive({visible:false, isNew:false, nodeId:null, data:{type:'approve',name:'',role:''}, pendingPos:null});
    let editor = null;
    let _seq = 1;
    const nodeMap = new Map();

    function roleLabel(code) { return ROLE_CN[code] || '未选择'; }
    function subText(n) {
      if (n.type==='trigger') return '发起人提交';
      if (n.type==='approve') return ROLE_CN[n.role] || '待选角色';
      return '系统自动推进';
    }
    function nodeHtml(n) {
      const meta = TYPE_META[n.type];
      const nameEsc = (n.name||'未命名').replace(/"/g,'&quot;');
      const dfidAttr = n._dfid;
      return '<div class="nf-node '+meta.cls+'">'
        + '<button class="nf-node-del" title="删除" onclick="window.__fdDel && window.__fdDel(' + JSON.stringify(dfidAttr) + ')">×</button>'
        + '<div class="nf-node-ic">'+Icon.icon(meta.icon,18)+'</div>'
        + '<div class="nf-node-body">'
        +   '<div class="nf-node-name">'+nameEsc+'</div>'
        +   '<div class="nf-node-sub">'+subText(n)+'</div>'
        + '</div></div>';
    }

    function doAddNode(type, name, role, px, py) {
      const meta = TYPE_META[type];
      const wrap = wrapRef.value;
      if (!wrap || !editor) return;
      const rect = wrap.getBoundingClientRect();
      // px/py are clientX/clientY (page coords) when dropped, else center
      let posx, posy;
      if (px != null && py != null) {
        posx = px - rect.left - 110;
        posy = py - rect.top - 40;
      } else {
        posx = 100 + _seq*40;
        posy = 100;
      }
      posx = Math.max(10, posx);
      posy = Math.max(10, posy);
      const id = editor.addNode(name, meta.inputs, meta.outputs, posx, posy, meta.cls, {_type:type,_role:role,_name:name}, '');
      const dfId = id.toString();
      nodeMap.set(dfId, {_dfid:dfId, type, name, role});
      _seq++;
      nextTick(() => {
        const el = document.getElementById('node-'+dfId);
        if (el) {
          const body = el.querySelector('.drawflow_content_node');
          if (body) body.innerHTML = nodeHtml({_dfid:dfId,type,name,role});
        }
      });
      return dfId;
    }

    function openAddDlg(type, px, py) {
      const meta = TYPE_META[type];
      dlg.data.type = type;
      dlg.data.name = meta.name;
      dlg.data.role = type==='approve' ? 'DEPARTMENT_HEAD' : '';
      dlg.isNew = true;
      dlg.nodeId = null;
      dlg.pendingPos = px!=null ? {x:px,y:py} : null;
      dlg.visible = true;
    }
    function quickAdd(type) {
      const wrap = wrapRef.value;
      openAddDlg(type, wrap?wrap.offsetLeft+wrap.clientWidth/2:400, wrap?wrap.offsetTop+wrap.clientHeight/2:200);
    }
    function onDragStart(e, type) {
      e.dataTransfer.setData('node-type', type);
      e.dataTransfer.effectAllowed = 'copy';
    }
    function onDragOver(e) { e.dataTransfer.dropEffect='copy'; e.preventDefault(); }
    function onDrop(e) {
      e.preventDefault();
      const type = e.dataTransfer.getData('node-type');
      if (!type) return;
      openAddDlg(type, e.clientX, e.clientY);
    }

    function openEdit(dfId) {
      const n = nodeMap.get(dfId);
      if (!n) return;
      dlg.data.type = n.type;
      dlg.data.name = n.name;
      dlg.data.role = n.role || '';
      dlg.isNew = false;
      dlg.nodeId = dfId;
      dlg.visible = true;
    }
    function deleteNode(dfId) {
      if (!dfId || !editor) return;
      try { editor.removeNodeId(dfId); } catch(e) {}
      nodeMap.delete(dfId);
    }
    window.__fdDel = (id) => { deleteNode(id); };

    function onRoleChange(roleCode) {
      if (!dlg.data.name || dlg.data.name==='人工处理' || dlg.data.name==='审批') {
        dlg.data.name = (ROLE_CN[roleCode]||'') + '审批';
      }
    }
    function deleteFromDlg() {
      if (dlg.nodeId) deleteNode(dlg.nodeId);
      dlg.visible = false;
    }
    function saveDlg() {
      const d = dlg.data;
      if (!d.name) { ElMessage.warning('请输入节点名称'); return; }
      if (d.type==='approve' && !d.role) { ElMessage.warning('请选择处理角色'); return; }
      if (dlg.isNew) {
        const pos = dlg.pendingPos;
        doAddNode(d.type, d.name, d.role, pos?pos.x:null, pos?pos.y:null);
      } else {
        const n = nodeMap.get(dlg.nodeId);
        if (n) {
          n.name = d.name; n.role = d.role;
          editor.updateNodeDataFromId(dlg.nodeId, {_type:d.type,_role:d.role,_name:d.name});
          const el = document.getElementById('node-'+dlg.nodeId);
          if (el) {
            el.classList.remove('nf-trigger','nf-approve','nf-process');
            el.classList.add(TYPE_META[d.type].cls);
            const body = el.querySelector('.drawflow_content_node');
            if (body) body.innerHTML = nodeHtml(n);
          }
        }
      }
      dlg.visible = false;
    }

    function zoom(delta) {
      if (!editor) return;
      const z = Math.min(1.8, Math.max(0.3, editor.zoom + delta));
      editor.zoom(z);
    }
    function resetView() {
      if (!editor) return;
      editor.zoom(1);
      editor.canvas_x = 0; editor.canvas_y = 0;
      editor.precanvas.style.transform = 'translate(0px, 0px)';
    }
    function clearAll() {
      ElMessageBox.confirm('清空画布上所有节点和连线？','提示',{type:'warning'}).then(()=>{
        editor.clear();
        nodeMap.clear();
        currentDefId.value = null;
      }).catch(()=>{});
    }

    async function fetchDefs() {
      try { const r = await api.get('/api/approvals/definitions'); allDefs.value = r.data || []; } catch(e) {}
    }
    function serialize() {
      const nodes = [];
      for (const [id, n] of nodeMap) {
        const el = document.getElementById('node-'+id);
        const pos = el ? {x: parseFloat(el.style.left||0), y: parseFloat(el.style.top||0)} : {x:0,y:0};
        nodes.push({dfId:id, type:n.type, name:n.name, role:n.role, x:pos.x, y:pos.y});
      }
      let connections = [];
      try {
        const exported = editor.export();
        if (exported.drawflow && exported.drawflow.Home && exported.drawflow.Home.data) {
          for (const [nid, nd] of Object.entries(exported.drawflow.Home.data)) {
            for (const out of (nd.outputs||{})) {
              if (out && out.connections) {
                for (const c of out.connections) {
                  connections.push({from:nid, to:c.node, fromPort:c.output||'output_1', toPort:c.input||'input_1'});
                }
              }
            }
          }
        }
      } catch(e) {}
      const sorted = [...nodes].sort((a,b)=> (a.x-b.x) || (a.y-b.y));
      const steps = sorted.map(n => {
        if (n.type==='trigger') return {name:n.name, type:n.type, approver_role:null};
        if (n.type==='approve') return {name:n.name, type:n.type, approver_role:n.role};
        return {name:n.name, type:n.type, approver_role:null};
      });
      const posMap = {};
      nodes.forEach(n => posMap[n.dfId] = {x:n.x, y:n.y});
      return {nodes: steps, _positions: posMap, _connections: connections};
    }
    function save() {
      if (!curBizType.value) { ElMessage.warning('请先选择业务类型'); return; }
      if (nodeMap.size === 0) { ElMessage.warning('请至少添加一个节点'); return; }
      const data = serialize();
      const name = (allDefs.value.find(f=>f.biz_type===curBizType.value)?.name) || (bizTypes.find(b=>b.v===curBizType.value)?.l + '流程');
      const body = Object.assign({name, biz_type: curBizType.value, nodes: data.nodes}, {_positions:data._positions, _connections:data._connections});
      const req = currentDefId.value
        ? api.put('/api/approvals/definitions/'+currentDefId.value, body)
        : api.post('/api/approvals/definitions', body);
      req.then(r => {
        if (r.data?.id) currentDefId.value = r.data.id;
        ElMessage.success(currentDefId.value ? '已更新版本' : '创建成功');
        fetchDefs();
      }).catch(e => ElMessage.error(e.message));
    }
    function loadNodesFromData(rawNodes, positions) {
      if (!editor) return;
      editor.clear();
      nodeMap.clear();
      _seq = 1;
      (rawNodes||[]).forEach((nd, i) => {
        const type = nd.type || (i===0?'trigger':'approve');
        const meta = TYPE_META[type];
        const name = nd.name || meta.name;
        const role = nd.approver_role || nd.role || (type==='approve'?'DEPARTMENT_HEAD':'');
        const pos = positions && positions[i] ? positions[i] : null;
        const x = pos ? pos.x : 120 + i*280;
        const y = pos ? pos.y : 120;
        const id = editor.addNode(name, meta.inputs, meta.outputs, x, y, meta.cls, {_type:type,_role:role,_name:name}, '');
        const dfId = id.toString();
        nodeMap.set(dfId, {_dfid:dfId, type, name, role});
        nextTick(() => {
          const el = document.getElementById('node-'+dfId);
          if (el) {
            const body = el.querySelector('.drawflow_content_node');
            if (body) body.innerHTML = nodeHtml({_dfid:dfId,type,name,role});
          }
        });
      });
    }
    async function loadDef() {
      copyFrom.value = '';
      if (!curBizType.value) return;
      await fetchDefs();
      const fd = allDefs.value.find(f => f.biz_type === curBizType.value);
      if (!fd) { if(editor) editor.clear(); nodeMap.clear(); currentDefId.value = null; ElMessage.info('暂无流程，开始设计吧'); return; }
      currentDefId.value = fd.id;
      loadNodesFromData(fd.nodes, fd._positions);
      ElMessage.success('已加载「'+fd.name+'」v'+fd.version);
    }
    async function loadDefById(id) {
      await fetchDefs();
      const fd = allDefs.value.find(f => f.id === id);
      if (!fd) return;
      curBizType.value = fd.biz_type;
      currentDefId.value = fd.id;
      loadNodesFromData(fd.nodes, fd._positions);
    }
    function copyFromTpl(id) {
      if (!id) return;
      const fd = allDefs.value.find(f => f.id === id);
      if (!fd) { copyFrom.value=''; return; }
      loadNodesFromData(fd.nodes, fd._positions);
      currentDefId.value = null;
      ElMessage.success('已从「'+fd.name+'」复制，选择业务类型后保存');
    }

    onMounted(() => {
      fetchDefs();
      nextTick(() => {
        if (!dfRef.value) return;
        editor = new Drawflow(dfRef.value, Vue);
        editor.reroute = true;
        editor.reroute_fix_curvature = true;
        editor.curvature = 0.25;
        editor.line_path = 1;
        editor.start();
        dfRef.value.addEventListener('dblclick', (e) => {
          const nodeEl = e.target.closest('.drawflow-node');
          if (nodeEl) openEdit(nodeEl.id.replace('node-',''));
        });
        dfRef.value.addEventListener('click', (e) => {
          document.querySelectorAll('.drawflow-node.selected').forEach(n => n.classList.remove('selected'));
          const nodeEl = e.target.closest('.drawflow-node');
          if (nodeEl) nodeEl.classList.add('selected');
        });
        window.addEventListener('keydown', (e) => {
          if (e.key === 'Delete') {
            const sel = dfRef.value.querySelector('.drawflow-node.selected');
            if (sel && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
              deleteNode(sel.id.replace('node-',''));
            }
          }
        });
      });
    });

    return { bizTypes, roles, palTypes, curBizType, copyFrom, allDefs, dlg, dfRef, wrapRef,
      roleLabel, quickAdd, onDragStart, onDragOver, onDrop, saveDlg, deleteFromDlg, onRoleChange,
      zoom, resetView, clearAll, loadDef, loadDefById, copyFromTpl, save, Icon };
  }
};
'''

pattern = re.compile(r'// ============ 流程设计器.*?// ============ 预警 ============', re.DOTALL)
new_content = pattern.sub(new_code + '\n\n// ============ 预警 ============', content, count=1)

with open(r'e:\trae\liu\static\app.js', 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK, length:', len(new_content))
