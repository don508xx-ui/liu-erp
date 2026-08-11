import re

p = r"e:/trae/liu/static/app.js"
src = open(p, 'r', encoding='utf-8').read()

# 找到FlowDesignPage
start_marker = "const FlowDesignPage = {"
end_marker = "\n};\n\nconst app = createApp"

si = src.index(start_marker)
ei = src.index(end_marker) + 2  # include }

new_code = r'''const FlowDesignPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('workflow',22)"></div>
        <div>
          <div class="ph-title">流程设计器</div>
          <div class="ph-sub">左侧拖入/点击添加节点 · 节点自由拖动 · 从右●拖到左●连线 · 双击节点编辑</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-select v-model="curBizType" placeholder="选择业务类型" style="width:160px" @change="onBizChange">
          <el-option v-for="o in bizTypes" :key="o.v" :label="o.l" :value="o.v"/>
        </el-select>
        <el-select v-model="copyFrom" placeholder="复制已有流程..." style="width:160px" clearable @change="onCopyFrom">
          <el-option v-for="d in allDefs" :key="d.id" :label="d.name" :value="d.id"/>
        </el-select>
        <el-button @click="doClear"><span v-html="Icon.icon('trash',14)" style="vertical-align:middle;margin-right:4px"></span>清空</el-button>
        <el-button type="primary" @click="doSave"><span v-html="Icon.icon('save',14)" style="vertical-align:middle;margin-right:4px"></span>保存</el-button>
      </div>
    </div>
    <div class="fd-layout">
      <div class="fd-palette">
        <div class="fd-pal-title">节点仓库</div>
        <div v-for="pt in palTypes" :key="pt.type" class="fd-pal-item" :class="pt.cls"
             draggable="true" @dragstart="onPalDragStart($event,pt.type)" @click="onPalClick(pt.type)">
          <div class="fd-pal-ic" v-html="Icon.icon(pt.icon,18)"></div>
          <div class="fd-pal-body">
            <div class="fd-pal-name">{{pt.label}}</div>
            <div class="fd-pal-desc">{{pt.desc}}</div>
          </div>
        </div>
        <div class="fd-hint">💡 点击=中心添加<br>拖拽=放置指定位置<br>拖动节点头部移动<br>从右●拖到左●连线<br>双击=编辑 · Del=删除</div>
        <div class="fd-pal-title" style="margin-top:14px">已有流程</div>
        <div class="fd-tpl-list">
          <div v-for="d in allDefs" :key="d.id" class="fd-tpl-item" @click="loadDefById(d.id)">
            <span class="fd-tpl-name">{{d.name}}</span>
            <span class="fd-tpl-meta">v{{d.version}} · {{d.nodes.length}}步</span>
          </div>
          <div v-if="!allDefs.length" class="fd-hint">暂无</div>
        </div>
      </div>
      <div class="fd-canvas-wrap" ref="wrapRef"
           @dragover.prevent="onDragOver" @drop="onDrop"
           @mousedown="onCanvasMouseDown" @wheel="onWheel"
           @mousemove="onMouseMove" @mouseup="onMouseUp" @mouseleave="onMouseUp">
        <svg class="fd-svg" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 Z" fill="#00d4ff"/>
            </marker>
            <filter id="glow"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <path v-for="c in connections" :key="c.id" :d="c.path" stroke="#00d4ff" stroke-width="2.5" fill="none"
                :class="{'fd-conn-sel': c.id===selConn}" filter="url(#glow)" marker-end="url(#arrow)"
                @click.stop="selConn=c.id" @dblclick.stop="delConn(c.id)" style="cursor:pointer"/>
          <path v-if="tempConn" :d="tempConn" stroke="#f59e0b" stroke-width="2" fill="none" stroke-dasharray="6,4"/>
        </svg>
        <div class="fd-nodes" :style="{transform:'translate('+panX+'px,'+panY+'px) scale('+zoom+')'}">
          <div v-for="n in nodes" :key="n.id" class="fd-node" :class="[meta(n.type).cls,{'fd-selected':n.id===selNode}]"
               :style="{left:n.x+'px',top:n.y+'px'}" @dblclick.stop="openEdit(n.id)">
            <div class="fd-node-head" @mousedown.stop="onNodeHeadDown($event,n.id)">
              <div class="fd-node-ic" v-html="Icon.icon(meta(n.type).icon,16)"></div>
              <span class="fd-node-name">{{n.name}}</span>
              <button class="fd-node-del" @click.stop="delNode(n.id)" title="删除">×</button>
            </div>
            <div class="fd-node-sub">{{subText(n)}}</div>
            <div v-if="n.type!=='trigger'" class="fd-port fd-port-in" @mousedown.stop.prevent
                 @mouseup.stop="onPortInUp($event,n.id)"></div>
            <div v-if="n.type!=='end'" class="fd-port fd-port-out" @mousedown.stop="onPortOutDown($event,n.id)"></div>
          </div>
        </div>
        <div class="fd-zoom-bar">
          <button class="fd-zoom-btn" @click="doZoom(0.1)">+</button>
          <span class="fd-zoom-val">{{Math.round(zoom*100)}}%</span>
          <button class="fd-zoom-btn" @click="doZoom(-0.1)">−</button>
          <button class="fd-zoom-btn" @click="resetView">⟲</button>
        </div>
        <div v-if="!nodes.length" class="fd-empty">
          <div v-html="Icon.icon('workflow',48)" style="opacity:.3;margin-bottom:12px"></div>
          <div>从左侧拖入节点开始设计流程</div>
        </div>
      </div>
    </div>
    <el-dialog v-model="dlg.vis" :title="dlg.isNew?'添加节点':'编辑节点'" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item label="节点名称">
          <el-input v-model="dlg.name" placeholder="如:主管审批、财务入账"/>
        </el-form-item>
        <el-form-item label="处理方式" v-if="dlg.isNew">
          <el-radio-group v-model="dlg.type">
            <el-radio value="trigger">开始(发起人)</el-radio>
            <el-radio value="approve">人工审批</el-radio>
            <el-radio value="process">系统流转</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理角色" v-if="dlg.type==='approve'">
          <el-select v-model="dlg.role" style="width:100%" @change="onRoleChange">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v"/>
          </el-select>
        </el-form-item>
        <div style="margin-top:4px;padding:8px;background:var(--panel2);border-radius:6px;font-size:13px;line-height:1.6;color:var(--text2)">
          <template v-if="dlg.type==='trigger'">🟢 发起人提交表单启动流程</template>
          <template v-else-if="dlg.type==='approve'">🔵 「<b>{{roleLabel(dlg.role)}}</b>」人工审批，通过/驳回</template>
          <template v-else>🟢 系统自动执行，推进到下一步</template>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dlg.vis=false">取消</el-button>
        <el-button type="danger" @click="delFromDlg" v-if="!dlg.isNew">删除</el-button>
        <el-button type="primary" @click="saveDlg">确定</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } = Vue;
    const bizTypes = [
      {v:'PURCHASE_REQUEST',l:'采购申请'},{v:'RECEIVING',l:'来货登记'},{v:'COMPLETION',l:'完工单'},
      {v:'EXPENSE',l:'费用报销'},{v:'SALES_ADJUSTMENT',l:'调价申请'},
    ];
    const roles = [
      {v:'DEPARTMENT_HEAD',l:'部门主管'},{v:'FINANCE',l:'财务'},{v:'GM',l:'总经理'},
      {v:'MANAGER',l:'厂长'},{v:'WAREHOUSE',l:'仓管'},{v:'SALES',l:'销售'},
      {v:'PURCHASE',l:'采购'},{v:'OPERATION',l:'运营'},{v:'ADMIN',l:'管理员'},
    ];
    const ROLE_CN = Object.fromEntries(roles.map(r=>[r.v,r.l]));
    const TYPE_META = {
      trigger: {name:'发起人提交', cls:'fd-trigger', icon:'play'},
      approve: {name:'人工处理', cls:'fd-approve', icon:'check'},
      process: {name:'自动流转', cls:'fd-process', icon:'zap'},
    };
    const palTypes = [
      {type:'trigger',label:'开始节点',desc:'发起人提交',icon:'play',cls:'pal-trigger'},
      {type:'approve',label:'人工处理',desc:'选角色审批',icon:'check',cls:'pal-approve'},
      {type:'process',label:'系统流转',desc:'自动推进',icon:'zap',cls:'pal-process'},
    ];

    const wrapRef = ref(null);
    const curBizType = ref('');
    const copyFrom = ref('');
    const allDefs = ref([]);
    const nodes = ref([]);
    const connections = ref([]);
    const selNode = ref(null);
    const selConn = ref(null);
    const panX = ref(0), panY = ref(0), zoom = ref(1);
    const dlg = reactive({vis:false,isNew:true,id:null,name:'',type:'approve',role:''});

    let _nid = 1, _cid = 1;
    let dragType = null; // 'node'|'pan'|'conn'|null
    let dragData = null;
    let tempFromNode = null, tempFromX=0, tempFromY=0;
    const tempConn = ref(null);

    function meta(t){ return TYPE_META[t]||TYPE_META.approve; }
    function roleLabel(c){ return ROLE_CN[c]||'未选择'; }
    function subText(n){
      if(n.type==='trigger') return '开始节点';
      if(n.type==='approve') return ROLE_CN[n.role]||'待选角色';
      return '自动推进';
    }
    function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

    function portPos(nid, side) {
      const n = nodes.value.find(x=>x.id===nid);
      if(!n) return {x:0,y:0};
      const w=180, h=68;
      if(side==='out') return {x:n.x+w, y:n.y+h/2};
      return {x:n.x, y:n.y+h/2};
    }
    function bezierPath(x1,y1,x2,y2){
      const dx=Math.abs(x2-x1)*0.5;
      return `M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;
    }
    function recalcConns(){
      connections.value.forEach(c=>{
        const sp=portPos(c.from,'out'), ep=portPos(c.to,'in');
        c._sx=sp.x; c._sy=sp.y; c._ex=ep.x; c._ey=ep.y;
        c.path=bezierPath(sp.x,sp.y,ep.x,ep.y);
      });
    }

    function addNode(type, name, role, x, y) {
      const m = meta(type);
      const id = 'n'+(_nid++);
      const node = {id, type, name: name||m.name, role: role||(type==='approve'?'DEPARTMENT_HEAD':'')};
      if(x!=null && y!=null){
        node.x = (x - panX.value)/zoom.value - 90;
        node.y = (y - panY.value)/zoom.value - 34;
      } else {
        node.x = 200 + nodes.value.length*60;
        node.y = 150;
      }
      nodes.value.push(node);
      nextTick(recalcConns);
      return id;
    }
    function delNode(id){
      nodes.value = nodes.value.filter(n=>n.id!==id);
      connections.value = connections.value.filter(c=>c.from!==id&&c.to!==id);
      if(selNode.value===id) selNode.value=null;
    }
    function addConn(fromId, toId){
      if(fromId===toId) return;
      if(connections.value.some(c=>c.from===fromId&&c.to===toId)) return;
      // Check for cycles (simple)
      const id='c'+(_cid++);
      connections.value.push({id,from:fromId,to:toId,path:''});
      nextTick(recalcConns);
    }
    function delConn(id){
      connections.value = connections.value.filter(c=>c.id!==id);
      if(selConn.value===id) selConn.value=null;
    }

    // Palette drag
    function onPalDragStart(e, type){
      e.dataTransfer.setData('text/fd-type', type);
      e.dataTransfer.effectAllowed='copy';
    }
    function onPalClick(type){
      openAdd(type);
    }
    function onDragOver(e){ e.dataTransfer.dropEffect='copy'; }
    function onDrop(e){
      e.preventDefault();
      const type = e.dataTransfer.getData('text/fd-type');
      if(!type) return;
      const rect = wrapRef.value.getBoundingClientRect();
      addNode(type, null, null, e.clientX-rect.left, e.clientY-rect.top);
    }

    // Canvas interactions
    function canvasPt(e){
      const r = wrapRef.value.getBoundingClientRect();
      return {x:e.clientX-r.left, y:e.clientY-r.top};
    }
    function onCanvasMouseDown(e){
      if(e.target.closest('.fd-node') || e.target.closest('.fd-port')) return;
      selNode.value=null; selConn.value=null;
      dragType='pan';
      dragData={sx:e.clientX,sy:e.clientY,px:panX.value,py:panY.value};
    }
    function onNodeHeadDown(e, nid){
      selNode.value=nid; selConn.value=null;
      dragType='node';
      const n = nodes.value.find(x=>x.id===nid);
      const p = canvasPt(e);
      dragData={nid, dx:(p.x-panX.value)/zoom.value-n.x, dy:(p.y-panY.value)/zoom.value-n.y};
      e.target.closest('.fd-node-head').style.cursor='grabbing';
    }
    function onPortOutDown(e, nid){
      dragType='conn';
      tempFromNode=nid;
      const p=portPos(nid,'out');
      tempFromX=p.x; tempFromY=p.y;
      const ep=canvasPt(e);
      tempConn.value=bezierPath(p.x,p.y,(ep.x-panX.value)/zoom.value,(ep.y-panY.value)/zoom.value);
    }
    function onPortInUp(e, nid){
      if(dragType==='conn' && tempFromNode){
        addConn(tempFromNode, nid);
      }
      tempConn.value=null; tempFromNode=null; dragType=null;
    }
    function onMouseMove(e){
      if(!dragType) return;
      if(dragType==='pan'){
        panX.value = dragData.px + (e.clientX-dragData.sx);
        panY.value = dragData.py + (e.clientY-dragData.sy);
      } else if(dragType==='node'){
        const p=canvasPt(e);
        const n = nodes.value.find(x=>x.id===dragData.nid);
        if(n){
          n.x=(p.x-panX.value)/zoom.value - dragData.dx;
          n.y=(p.y-panY.value)/zoom.value - dragData.dy;
          n.x=Math.max(0,n.x); n.y=Math.max(0,n.y);
          recalcConns();
        }
      } else if(dragType==='conn'){
        const p=canvasPt(e);
        const mx=(p.x-panX.value)/zoom.value, my=(p.y-panY.value)/zoom.value;
        tempConn.value=bezierPath(tempFromX,tempFromY,mx,my);
      }
    }
    function onMouseUp(){
      if(dragType==='node'){
        document.querySelectorAll('.fd-node-head').forEach(h=>h.style.cursor='grab');
      }
      if(dragType==='conn'){
        tempConn.value=null; tempFromNode=null;
      }
      dragType=null; dragData=null;
    }
    function onWheel(e){
      if(!e.ctrlKey) return;
      e.preventDefault();
      const d = e.deltaY>0?-0.1:0.1;
      doZoom(d);
    }
    function doZoom(d){
      const z = Math.max(0.3, Math.min(2, zoom.value+d));
      zoom.value = Math.round(z*10)/10;
      nextTick(recalcConns);
    }
    function resetView(){ zoom.value=1; panX.value=20; panY.value=20; nextTick(recalcConns); }

    // Dialog
    function openAdd(type){
      dlg.vis=true; dlg.isNew=true; dlg.id=null;
      dlg.type=type||'trigger';
      dlg.name=meta(dlg.type).name;
      dlg.role=dlg.type==='approve'?'DEPARTMENT_HEAD':'';
    }
    function openEdit(id){
      const n = nodes.value.find(x=>x.id===id);
      if(!n) return;
      dlg.vis=true; dlg.isNew=false; dlg.id=id;
      dlg.type=n.type; dlg.name=n.name; dlg.role=n.role||'';
    }
    function onRoleChange(roleCode){
      if(!dlg.name||dlg.name==='人工处理'||dlg.name==='审批'){
        dlg.name = (ROLE_CN[roleCode]||'')+'审批';
      }
    }
    function saveDlg(){
      if(!dlg.name){ ElMessage.warning('请输入节点名称'); return; }
      if(dlg.type==='approve' && !dlg.role){ ElMessage.warning('请选择处理角色'); return; }
      if(dlg.isNew){
        addNode(dlg.type, dlg.name, dlg.role, wrapRef.value.clientWidth/2, wrapRef.value.clientHeight/2);
      } else {
        const n = nodes.value.find(x=>x.id===dlg.id);
        if(n){ n.name=dlg.name; n.role=dlg.role; n.type=dlg.type; }
      }
      dlg.vis=false;
    }
    function delFromDlg(){
      if(dlg.id) delNode(dlg.id);
      dlg.vis=false;
    }

    // Save/Load
    async function fetchDefs(){
      try{ const r=await api.get('/api/approvals/definitions'); allDefs.value=r.data||[]; }catch(e){}
    }
    function onBizChange(){}
    function onCopyFrom(id){
      if(!id) return;
      const fd = allDefs.value.find(f=>f.id===id);
      if(fd){
        loadFromData(fd.nodes, fd._positions, fd._connections);
        curBizType.value = fd.biz_type;
        ElMessage.success('已从「'+fd.name+'」复制');
      }
      copyFrom.value='';
    }
    function loadFromData(nodeList, positions, conns){
      nodes.value=[]; connections.value=[]; _nid=1; _cid=1;
      const idMap={};
      (nodeList||[]).forEach((nd,i)=>{
        const pos = (positions&&positions[i])||{x:200+i*200,y:150};
        const nid = addNode(nd.type, nd.name, nd.role, 0, 0);
        const n = nodes.value[nodes.value.length-1];
        n.x=pos.x||200+i*200; n.y=pos.y||150;
        idMap[nd.dfId||i]=nid;
      });
      nextTick(()=>{
        (conns||[]).forEach(c=>{
          const from=idMap[c.from], to=idMap[c.to];
          if(from&&to) addConn(from,to);
        });
      });
    }
    async function loadDefById(id){
      await fetchDefs();
      const fd = allDefs.value.find(f=>f.id===id);
      if(!fd) return;
      curBizType.value = fd.biz_type;
      loadFromData(fd.nodes, fd._positions, fd._connections);
      ElMessage.success('已加载「'+fd.name+'」');
    }
    function doClear(){
      if(!nodes.value.length) return;
      ElMessageBox.confirm('清空所有节点和连线？','提示',{type:'warning'}).then(()=>{
        nodes.value=[]; connections.value=[]; _nid=1; _cid=1;
        selNode.value=null; selConn.value=null;
      }).catch(()=>{});
    }
    async function doSave(){
      if(!curBizType.value){ ElMessage.warning('请选择业务类型'); return; }
      if(!nodes.value.length){ ElMessage.warning('请至少添加一个节点'); return; }
      const nodeList = nodes.value.map(n=>({dfId:n.id,type:n.type,name:n.name,role:n.role}));
      const positions = nodes.value.map(n=>({x:n.x,y:n.y}));
      const connList = connections.value.map(c=>({from:c.from,to:c.to}));
      try{
        await api.post('/api/approvals/definitions', {
          biz_type:curBizType.value,
          name: (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程',
          nodes: nodeList,
          _positions: positions,
          _connections: connList,
        });
        ElMessage.success('保存成功');
        fetchDefs();
      }catch(e){ ElMessage.error(e.message); }
    }

    // Keyboard
    function onKey(e){
      if(dlg.vis) return;
      if((e.key==='Delete'||e.key==='Backspace') && !['INPUT','TEXTAREA','SELECT'].includes((document.activeElement||{}).tagName)){
        if(selNode.value){ delNode(selNode.value); e.preventDefault(); }
        else if(selConn.value){ delConn(selConn.value); e.preventDefault(); }
      }
    }

    onMounted(()=>{
      fetchDefs();
      resetView();
      window.addEventListener('keydown', onKey);
    });
    onBeforeUnmount(()=>{ window.removeEventListener('keydown',onKey); });

    return { bizTypes, roles, palTypes, curBizType, copyFrom, allDefs, nodes, connections,
      selNode, selConn, panX, panY, zoom, tempConn, wrapRef, dlg,
      meta, roleLabel, subText, Icon,
      onPalDragStart, onPalClick, onDragOver, onDrop,
      onCanvasMouseDown, onNodeHeadDown, onPortOutDown, onPortInUp,
      onMouseMove, onMouseUp, onWheel, doZoom, resetView,
      openEdit, saveDlg, delFromDlg, onRoleChange,
      onBizChange, onCopyFrom, loadDefById, doClear, doSave };
  }
};'''

src = src[:si] + new_code + src[ei:]

# Update version
src = re.sub(r'app\.js\?v=[\d-]+', 'app.js?v=20260804-40', src)

with open(p, 'w', encoding='utf-8') as f:
    f.write(src)
print('OK - replaced FlowDesignPage with custom canvas, v=40')
