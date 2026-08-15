/* 喷涂加工ERP 前端 - Vue3 + Element Plus */
const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch, nextTick, h } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

// ============ API ============
const TOKEN_KEY = 'erp_token';
const USER_KEY = 'erp_user';
const api = {
  async req(method, url, body) {
    const opt = { method, headers: {} };
    const tk = localStorage.getItem(TOKEN_KEY);
    if (tk) opt.headers['Authorization'] = 'Bearer ' + tk;
    if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    try {
      const r = await fetch(url, opt);
      if (r.status === 401) {
        // 登录接口401 = 账号或密码错误, 绝不能当成"登录已过期"(admin密码admin123非123456, 会误报!)
        if (url.includes('/api/auth/login')) {
          throw new Error('账号或密码错误');
        }
        // 其余接口401 = 凭证失效: 清localStorage + 通知App清空user.value(否则v-if=!user不成立, 卡在Dashboard转圈)
        localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY);
        if (typeof window.__forceLogout === 'function') window.__forceLogout();
        if (!location.hash.startsWith('#/login')) {
          location.hash = '#/login';
        }
        throw new Error('登录已过期');
      }
      const txt = await r.text();
      let j; try { j = txt ? JSON.parse(txt) : {}; } catch { j = { detail: txt }; }
      if (!r.ok) {
        const msg = (() => {
          if (Array.isArray(j.detail)) return j.detail.map(d => d.msg || d.message || (typeof d === 'string' ? d : JSON.stringify(d))).join('；');
          if (j.detail && typeof j.detail === 'object') return j.detail.msg || j.detail.message || JSON.stringify(j.detail);
          return j.detail || j.msg || j.message || ('HTTP ' + r.status);
        })();
        throw new Error(msg);
      }
      return j;
    } catch (e) {
      // 网络层失败(隧道断连/cors/离线)也给个明确提示, 不要抛undefined
      if (e && (e.name === 'TypeError' || e.message?.includes('fetch') || !e.message)) {
        ElMessage.error('网络连接失败, 请检查隧道是否在线');
      }
      throw e;
    }
  },
  get(u) { return this.req('GET', u); },
  post(u, b) { return this.req('POST', u, b); },
  put(u, b) { return this.req('PUT', u, b); },
  del(u) { return this.req('DELETE', u); },
};

// ============ FlowTrack 流转轨迹(真实时间轴,调instances接口) ============
const FlowTrack = {
  props: ['bizType', 'bizId'],
  template: `
  <div class="flow-track" v-loading="loading">
    <div v-if="!loading && !instance" class="ft-empty">该单据暂未接入流程</div>
    <template v-else-if="instance">
      <div class="ft-head">
        <span class="ft-pill" :class="instance.status">{{statusLabel}}</span>
        <span class="muted tiny">共 {{nodes.length}} 个环节</span>
      </div>
      <div class="ft-nodes">
        <div v-for="(n,i) in nodes" :key="n.seq" :class="['ft-node', n.status]">
          <div class="ft-dot">
            <span v-if="n.status==='done'" v-html="Icon.icon('check',15)"></span>
            <span v-else-if="n.status==='rejected'" v-html="Icon.icon('close',15)"></span>
            <span v-else>{{i+1}}</span>
          </div>
          <div class="ft-body">
            <div class="ft-name">{{n.name}}
              <span class="ft-tag">{{n.type==='process'?'流转':(n.type==='cc'?'抄送':'审批')}}</span>
            </div>
            <div class="ft-meta" v-if="n.status==='done'">
              <b>{{n.assignee_name||'系统自动'}}</b> · {{fmtTime(n.handled_at)}}
              <span class="ft-comment" v-if="n.comment && n.comment!=='流转自动推进'">{{n.comment}}</span>
            </div>
            <div class="ft-meta" v-else-if="n.status==='current'">
              <b>{{n.assignee_name||'待分配'}}</b> · <span class="ft-now">已停留 {{n.duration||'-'}}</span>
            </div>
            <div class="ft-meta" v-else-if="n.type==='cc'">抄送角色: {{ccRoleLabel(n.cc_roles)}}</div>
            <div class="ft-meta" v-else>待处理 · 预计角色: {{roleLabel(n.role)}}</div>
          </div>
        </div>
      </div>
    </template>
  </div>`,
  setup(props) {
    const instance = ref(null);
    const nodes = ref([]);
    const loading = ref(false);
    const statusLabel = computed(() => ({RUNNING:'进行中',APPROVED:'已完成',REJECTED:'已驳回',CANCELLED:'已取消'}[instance.value?.status]||''));
    const ROLE_CN = {WAREHOUSE:'仓管',OPS:'运营',FINANCE:'财务',GM:'总经理',SALES:'销售',PRODUCTION:'厂长',PURCHASE:'采购',ADMIN:'管理员',DEPARTMENT_HEAD:'部门主管',OPERATION:'运营助理',FACTORY_MANAGER:'厂长',MANAGER:'厂长'};
    const roleLabel = c => ROLE_CN[c] || c || '-';
    const ccRoleLabel = roles => (roles||[]).map(r => ROLE_CN[r]||r).join('、') || '-';
    async function load() {
      if (!props.bizType || !props.bizId) return;
      loading.value = true;
      try {
        const r = await api.get(`/api/approvals/instances/${props.bizType}/${props.bizId}`);
        instance.value = r.data?.instance || null;
        nodes.value = r.data?.nodes || [];
      } catch(e) { instance.value = null; }
      loading.value = false;
    }
    function fmtTime(s){ return s ? new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }
    watch(() => [props.bizType, props.bizId], load);
    onMounted(load);
    return { instance, nodes, loading, statusLabel, fmtTime, roleLabel, ccRoleLabel, Icon };
  }
};

// ============ FlowMini 卡片内嵌微型流程条(一眼看到当前位置) ============
const FlowMini = {
  props: ['bizType', 'bizId'],
  template: `
  <div class="flow-mini" v-if="nodes.length">
    <div class="fm-label">
      <span class="fm-cur">{{curName}}</span>
      <span class="fm-rest">· 剩余{{rest}}步</span>
    </div>
    <div class="fm-bar">
      <div v-for="(n,i) in nodes" :key="n.seq" :class="['fm-node',n.status]">
        <div class="fm-dot"><span v-if="n.status==='done'" v-html="Icon.icon('check',10)"></span></div>
        <div class="fm-name">{{n.name}}</div>
      </div>
    </div>
  </div>`,
  setup(props) {
    const nodes = ref([]);
    const curName = computed(() => (nodes.value.find(n=>n.status==='current') || nodes.value.find(n=>n.status==='pending') || {}).name || '');
    const rest = computed(() => {
      const idx = nodes.value.findIndex(n=>n.status==='current');
      if (idx<0) return nodes.value.filter(n=>n.status!=='done'&&n.status!=='rejected').length;
      return nodes.value.length - idx - 1;
    });
    async function load() {
      if (!props.bizType || !props.bizId) return;
      try {
        const r = await api.get(`/api/approvals/instances/${props.bizType}/${props.bizId}`);
        nodes.value = r.data?.nodes || [];
      } catch(e) { nodes.value = []; }
    }
    watch(() => [props.bizType, props.bizId], load);
    onMounted(load);
    return { nodes, curName, rest, Icon };
  }
};

// ============ 通用列表页工厂 (卡片列表模式) ============
function makeListPage(cfg) {
  const card = cfg.card || {};
  const statusMap = card.statusMap || {};
  const STATUS_LABEL = s => statusMap[s] || s || '';
  const FMT = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  const FMT_DATE = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
  const FMT_DATE_SHORT = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';

  // 卡片列表模板(若cfg提供template则用cfg的)
  const cardTemplate = `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('${card.icon||'file-text'}', 22)"></div>
        <div>
          <div class="ph-title">${cfg.title||''}</div>
          <div class="ph-sub">${cfg.sub||''}</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate" v-if="cfg.createUrl"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>${cfg.createLabel||'新建'}</el-button>
      </div>
    </div>

    <div class="filter-bar" v-if="cfg.query">
      <template v-for="(v,k) in cfg.query" :key="k">
        <el-input v-if="v==='text'" v-model="query[k]" :placeholder="cfg.queryPlaceholders?.[k]||'搜索'" style="width:200px" clearable @keyup.enter="search"/>
        <el-select v-else-if="v==='select'" v-model="query[k]" :placeholder="cfg.queryPlaceholders?.[k]||'全部'" style="width:140px" clearable>
          <el-option v-for="o in (cfg.queryOptions?.[k]||[])" :key="o.v" :label="o.l" :value="o.v"/>
        </el-select>
      </template>
      <el-button @click="search">查询</el-button>
      <el-button v-if="cfg.query" @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+pSt(row)"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{pNo(row)}}</span>
            <span class="pill" :class="pSt(row)" v-if="pSt(row)">{{pStLabel(row)}}</span>
            <span class="doc-cust" v-if="pCust(row)">{{pCust(row)}}</span>
            <span class="doc-amount" v-if="pAmt(row)!=null" :class="pAmtNeg(row)?'neg':''">{{pAmt(row)}}</span>
          </div>
          <div class="doc-fields">
            <div v-for="f in card.fields" :key="f.key" class="doc-field">
              <span class="df-label">{{f.label}}</span>
              <span class="df-value" :title="pFv(row,f)">{{pFv(row,f)}}</span>
            </div>
          </div>
          <flow-mini v-if="${!!cfg.bizType}" biz-type="${cfg.bizType||''}" :biz-id="row.id"/>
        </div>
        <div class="doc-actions" @click.stop v-if="card.actions && card.actions.length">
          <template v-for="a in card.actions" :key="a.key">
            <el-button v-if="pShowAct(row,a)" size="small" :type="a.type||'primary'" link @click="pDoAct(row,a)">{{a.label}}</el-button>
          </template>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox', 56)"></div>
        <div class="de-title">暂无数据</div>
        <div class="de-desc">${cfg.emptyHint||'点击右上方按钮创建第一条'}</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="dialog.visible" :title="dialog.title" width="${cfg.dialogWidth||'600px'}">
      <el-form :model="dialog.data" label-width="${cfg.labelWidth||'100px'}">
        <template v-for="f in (cfg.formFields||[])" :key="f.key">
          <el-form-item :label="f.label">
            <el-input v-if="f.type==='text'" v-model="dialog.data[f.key]" :placeholder="f.ph||''" :style="{width:(f.w||240)+'px'}"/>
            <el-input v-else-if="f.type==='textarea'" v-model="dialog.data[f.key]" type="textarea" :rows="f.rows||2"/>
            <el-input-number v-else-if="f.type==='number'" v-model="dialog.data[f.key]" :min="f.min||0" :precision="f.precision||0" :style="{width:(f.w||200)+'px'}"/>
            <el-select v-else-if="f.type==='select'" v-model="dialog.data[f.key]" :style="{width:(f.w||200)+'px'}" :placeholder="f.ph||'选择'">
              <el-option v-for="o in f.options" :key="o.v" :label="o.l" :value="o.v"/>
            </el-select>
            <div v-else-if="f.type==='items'" style="width:100%">
              <div v-for="(it,idx) in (dialog.data[f.key]||[])" :key="idx" style="display:flex;gap:6px;margin-bottom:6px;align-items:center;flex-wrap:wrap">
                <template v-for="c in f.columns" :key="c.key">
                  <el-input v-if="c.type==='text'" v-model="it[c.key]" :placeholder="c.ph||c.label" :style="{width:(c.w||140)+'px'}"/>
                  <el-input-number v-else-if="c.type==='number'" v-model="it[c.key]" :min="0" :style="{width:(c.w||120)+'px'}"/>
                </template>
                <el-button size="small" type="danger" link @click="dialog.data[f.key].splice(idx,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
              </div>
              <el-button size="small" @click="(dialog.data[f.key]=dialog.data[f.key]||[]).push(f.emptyItem?({...f.emptyItem()}):{})">+ 加一行</el-button>
            </div>
          </el-form-item>
        </template>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">保存</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" :title="(cfg.title||'')+'详情'" size="${cfg.detailWidth||'560px'}">
      <div class="detail-hero" v-if="detail.data">
        <div class="dh-row">
          <span class="dh-no">{{pNo(detail.data)}}</span>
          <span class="pill" :class="pSt(detail.data)" v-if="pSt(detail.data)">{{pStLabel(detail.data)}}</span>
          <span class="dh-amount" v-if="pAmt(detail.data)!=null">{{pAmt(detail.data)}}</span>
        </div>
      </div>
      <div class="detail-section" v-if="detail.data && detail.data.id && cfg.bizType">
        <div class="ds-title">🔄 流转轨迹</div>
        <flow-track :biz-type="cfg.bizType" :biz-id="detail.data.id"/>
      </div>
      <div class="detail-section" v-if="detail.data">
        <div class="ds-title">基本信息</div>
        <div class="info-grid">
          <div v-for="f in card.fields" :key="f.key" class="ig-item">
            <div class="ig-label">{{f.label}}</div>
            <div class="ig-value">{{pFv(detail.data,f)}}</div>
          </div>
        </div>
      </div>
      <div class="detail-section" v-if="detail.data && card.subTable && detail.data[card.subTable.itemsKey]">
        <div class="ds-title">{{card.subTable.title||'明细'}}</div>
        <el-table :data="detail.data[card.subTable.itemsKey]" border size="small">
          <el-table-column type="index" label="#" width="44"/>
          <el-table-column v-for="c in card.subTable.columns" :key="c.key" :prop="c.key" :label="c.label" :width="c.w||''">
            <template v-if="c.fmt==='money'" #default="{row}">¥{{fmt(row[c.key])}}</template>
          </el-table-column>
        </el-table>
      </div>
      <div class="detail-section" v-if="detail.data && card.detailActions && card.detailActions.length">
        <div class="ds-title">操作</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <el-button v-for="a in card.detailActions" :key="a.key" :type="a.type||'primary'" plain @click="pDoAct(detail.data,a)">{{a.label}}</el-button>
        </div>
      </div>
    </el-drawer>
  </div>`;

  return {
    template: cfg.template || cardTemplate,
    components: { FlowTrack, FlowMini },
    setup() {
      const rows = ref([]);
      const total = ref(0);
      const loading = ref(false);
      const page = reactive({ page: 1, size: 15 });
      const query = reactive({ ...(cfg.query ? Object.fromEntries(Object.keys(cfg.query).map(k=>[k,''])) : {}) });
      const dialog = reactive({ visible: false, title: '', data: {} });
      const detail = reactive({ visible: false, data: {} });

      async function load() {
        loading.value = true;
        try {
          const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v !== '' && v !== null && v !== undefined)) }).toString();
          const res = await api.get(cfg.listUrl + '?' + qs);
          rows.value = res.data || [];
          total.value = res.total ?? rows.value.length;
        } catch (e) { ElMessage.error(e.message); }
        loading.value = false;
      }
      function search() { page.page = 1; load(); }
      function reset() { Object.keys(query).forEach(k => query[k] = ''); search(); }
      function openCreate() { dialog.visible = true; dialog.title = cfg.createLabel || '新增'; dialog.data = cfg.emptyForm ? cfg.emptyForm() : {}; }
      async function openEdit(row) {
        dialog.visible = true; dialog.title = '编辑';
        if (cfg.detailUrl) { const r = await api.get(cfg.detailUrl(row)); dialog.data = r.data || {}; }
        else { dialog.data = { ...row }; }
      }
      async function openDetail(row) {
        if (cfg.detailUrl) { try { const r = await api.get(cfg.detailUrl(row)); detail.data = r.data || {}; } catch(e){ detail.data = { ...row }; } }
        else detail.data = { ...row };
        detail.visible = true;
      }
      async function submit() {
        try {
          if (dialog.data.id && cfg.updateUrl) await api.put(cfg.updateUrl(dialog.data), dialog.data);
          else await api.post(cfg.createUrl, dialog.data);
          ElMessage.success('保存成功');
          dialog.visible = false; load();
        } catch (e) { ElMessage.error(e.message); }
      }
      async function doAction(row, a) {
        if (a.action === 'edit') return openEdit(row);
        if (a.action === 'detail') return openDetail(row);
        if (a.action && extra[a.action]) return extra[a.action](row, a);
        try {
          if (a.confirm !== false) await ElMessageBox.confirm(a.confirmMsg || '确认执行此操作?', '提示', { type: 'warning' });
          let body = null;
          if (a.input) { const { value } = await ElMessageBox.prompt(a.inputLabel || '请输入', '提示', {}); body = a.inputKey ? { [a.inputKey]: value } : { reason: value }; }
          if (a.url) await api.post(a.url(row), body);
          ElMessage.success(a.successMsg || '操作成功');
          if (a.refresh !== false) load();
          if (a.reload) location.reload();
        } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
      }
      // 卡片字段辅助 (避免Vue3 $/_前缀冲突)
      const pNo = r => r[card.noField] || ('#'+(r.id||''));
      const pCust = r => card.custField ? r[card.custField] : '';
      const pSt = r => card.statusField ? (r[card.statusField]||'') : '';
      const pStLabel = r => STATUS_LABEL(pSt(r));
      const pAmt = r => { if(!card.amountField) return null; const v=r[card.amountField]; return v!=null ? (card.amountPrefix||'¥')+FMT(v) : null; };
      const pAmtNeg = r => { if(!card.amountField) return false; const v=r[card.amountField]; return v<0; };
      const pFv = (r,f) => { const v=r[f.key]; if(v==null||v==='') return '-'; if(f.fmt==='date') return FMT_DATE(v); if(f.fmt==='dateShort') return FMT_DATE_SHORT(v); if(f.fmt==='money') return '¥'+FMT(v); if(f.map) return f.map[v]||v; return v; };
      const pShowAct = (r,a) => !a.show || a.show(r);
      const pDoAct = (r,a) => doAction(r,a);

      const extra = cfg.setupExtra ? (cfg.setupExtra({ load, rows, dialog, detail, doAction }) || {}) : {};
      onMounted(load);
      return { rows, total, page, loading, query, dialog, detail, cfg, card, load, search, reset, openCreate, openEdit, openDetail, submit, action: doAction, fmt: FMT, fmtDate: FMT_DATE, pNo, pCust, pSt, pStLabel, pAmt, pAmtNeg, pFv, pShowAct, pDoAct, Icon, ...extra };
    }
  };
}

// ============ 登录页 ============
const LoginPage = {
  template: `
  <div class="login-bg">
    <div class="login-card">
      <div class="login-title">喷涂加工 ERP</div>
      <div class="login-sub">SURFACE COATING · 业财一体化</div>
      <el-form :model="f" @submit.prevent="login" label-position="top">
        <el-form-item label="账号">
          <el-input v-model="f.username" placeholder="admin / sales01 / ops01 ..." prefix-icon="User"></el-input>
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="f.password" type="password" placeholder="123456" show-password @keyup.enter="login"></el-input>
        </el-form-item>
        <el-button type="primary" native-type="submit" style="width:100%;margin-top:8px" :loading="loading">登 录</el-button>
        <div class="muted tiny" style="margin-top:16px;text-align:center;line-height:1.8">
          admin管理员(密码admin123) / 其余账号密码统一123456<br>sales01销售 / ops01运营 / fin01财务 / wh01仓管 / gm01总经理 / mgr_a厂长
        </div>
      </el-form>
    </div>
  </div>`,
  setup() {
    const f = reactive({ username: '', password: '' });
    const loading = ref(false);
    // 从localStorage读记住的账号
    const saved = localStorage.getItem('erp_last_user');
    if (saved) { try { f.username = saved; } catch {} }
    async function login() {
      if (!f.username || !f.password) { ElMessage.warning('请输入账号和密码'); return; }
      loading.value = true;
      try {
        const r = await api.post('/api/auth/login', f);
        localStorage.setItem(TOKEN_KEY, r.token);
        localStorage.setItem(USER_KEY, JSON.stringify(r.user));
        localStorage.setItem('erp_last_user', r.user.username || f.username);
        ElMessage.success('登录成功: ' + (r.user.name || r.user.username));
        // 告诉App根组件登录OK, 直接进Dashboard不整页刷新
        if (typeof window.__onLoginOk === 'function') {
          window.__onLoginOk(r.user);
        } else {
          location.hash = '#/dashboard'; location.reload();
        }
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    return { f, loading, login };
  }
};

// ============ 工作台 ============
const DashboardPage = {
  template: `
  <div class="page wb-page">
    <div class="wb-header">
      <div class="wb-greeting">{{greeting}}<span class="role">{{user?.name}} · {{roleLabel}}</span></div>
      <div class="wb-date">{{today}}</div>
    </div>

    <!-- 工作流指引带 (全宽顶部) -->
    <div class="wf-pipeline wf-pipeline--hero">
      <div class="wf-title">🔀 业务流程</div>
      <div class="wf-rows">
        <div v-for="(wf, wi) in workflowSteps" :key="wi" class="wf-row">
          <div class="wf-row-head">
            <div class="wf-row-title">{{wf.title}}</div>
            <div class="wf-row-actions" v-if="isAdmin && wf.definition_id">
              <el-button size="small" type="warning" plain @click.stop="editFlow(wf)" title="编辑流程">
                <span v-html="Icon.icon('pencil', 12)" style="vertical-align:middle"></span> 编辑
              </el-button>
              <el-button size="small" type="danger" plain @click.stop="deleteFlow(wf)" title="删除流程">
                <span v-html="Icon.icon('trash', 12)" style="vertical-align:middle"></span> 删除
              </el-button>
            </div>
          </div>
          <div class="wf-flow">
            <div v-for="(n, i) in wf.nodes" :key="i"
                 :class="['wf-step', n.status]"
                 @click="n.route && go(n.route)">
              <div class="wf-connector" v-if="i>0">
                <div class="wf-line" :class="['wf-line-done', (wf.nodes[i-1].status==='active'||wf.nodes[i-1].status==='auto') ? 'wf-line-active' : '']"></div>
                <div class="wf-arrow" v-html="Icon.icon('arrow-right', 8)"></div>
              </div>
              <div :class="['wf-node', n.status]">
                <div class="wf-icon">
                  <span v-html="Icon.icon(n.icon, 16)"></span>
                  <span class="wf-badge" v-if="n.count">{{n.count}}</span>
                </div>
                <div class="wf-label">{{n.name}}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 下方内容: 统一Grid，左右共享行坐标 -->
    <div class="wb-grid">
      <!-- 左列: 每一个应用分组占一个grid行 -->
      <template v-for="(group, gi) in groupList" :key="group.name">
        <div class="nav-group">
          <div class="wb-section-title">{{group.name}}</div>
          <div class="app-grid">
            <div v-for="a in group.apps" :key="a.key" class="app-card" @click="go(a.key)">
              <div :class="'icon-wrap ic-'+a.color" v-html="Icon.icon(a.icon, 26)"></div>
              <div class="label">{{a.label}}</div>
              <span class="badge" v-if="badge(a.key)">{{badge(a.key)}}</span>
            </div>
          </div>
        </div>
      </template>
      <!-- 右列第1行: 经营概览 (与左栏第1组同高度) -->
      <div class="right-block rb-1">
        <div class="wb-section-title">📈 经营概览</div>
        <div class="kpi-bar" v-show="kpis.length">
          <div v-for="k in kpis" :key="k.key" :class="'kpi-num kpi-'+k.color">
            <span class="kpi-num-v">{{k.value}}</span>
            <span class="kpi-num-l">{{k.label}}</span>
          </div>
        </div>
      </div>
      <!-- 右列第2行: 我的待办 (与左栏第2组同高度) -->
      <div class="right-block rb-2">
        <div class="wb-section-title">
          <span>📋 我的待办</span>
          <span class="cc-more" @click="go('my-todos')">查看全部 ›</span>
        </div>
        <div class="content-card todo-body">
          <div class="todo-list" v-if="todos.length">
            <div v-for="t in todos" :key="t.type" :class="'todo-row todo-'+t.color" @click="go(t.route)">
              <span class="todo-prio">{{t.color==='red'?'紧急':t.color==='orange'?'重要':'普通'}}</span>
              <span class="todo-text">{{t.text}}</span>
              <span class="todo-arrow" v-html="Icon.icon('chevron-right', 16)"></span>
            </div>
          </div>
          <div class="cc-empty" v-else>
            <span v-html="Icon.icon('check-circle', 28)"></span>
            <p>暂无待办，一切顺利 🎉</p>
          </div>
        </div>
      </div>
      <!-- 右列第3-4行: 最近已办+团队动态 (跨左栏第3-4组) -->
      <div class="right-block rb-34">
        <div class="wb-section-title"><span>📊 工作台</span></div>
        <div class="content-row">
          <div class="content-card">
            <div class="cc-head"><h3>✅ 最近已办</h3><span class="cc-more" @click="go('my-done')">更多 ›</span></div>
            <div class="timeline-list" v-if="doneItems.length">
              <div v-for="d in doneItems" :key="d.id" class="tl-item">
                <span :class="'tl-dot tl-'+d.color"></span>
                <div class="tl-body">
                  <div class="tl-text">{{d.text}}</div>
                  <div class="tl-time">{{d.time}}</div>
                </div>
              </div>
            </div>
          </div>
          <div class="content-card">
            <div class="cc-head"><h3>📢 团队动态</h3></div>
            <ul class="timeline-list">
              <li v-for="n in news" :key="n.id" class="tl-item">
                <span :class="'tl-dot tl-'+n.color"></span>
                <div class="tl-body">
                  <div class="tl-text"><b>{{n.who}}</b> {{n.action}} <span class="hl">{{n.target}}</span></div>
                  <div class="tl-time">{{n.time}}</div>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>`,
  setup() {
    const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'));
    const todos = ref([]);
    const workflowSteps = ref([]);
    const appGroups = ref({});
    const kpis = ref([]);
    const groupList = computed(() => Object.entries(appGroups.value).map(([name, apps]) => ({ name, apps })));
    const roleCode = user.value?.role || '';
    const isAdmin = roleCode === 'ADMIN' || roleCode === 'GM';
    const roleLabel = { ADMIN: '管理员', GM: '总经理', SALES: '销售', FINANCE: '财务', MANAGER: '厂长', WAREHOUSE: '仓管', PURCHASE: '采购', OPERATION: '运营', DEPARTMENT_HEAD: '部门主管', AGENT: 'AI助手' }[roleCode] || roleCode || '用户';

    const hour = new Date().getHours();
    const greeting = hour < 6 ? '凌晨好' : hour < 12 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
    const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });

    // 工作流节点样式
    function wfClass(s) {
      return (s.count || 0) > 0 ? 'wf-active' : 'wf-pending';
    }
    function wfLineClass(i, steps) {
      if (!steps || !steps.length) return '';
      const prev = steps[i - 1];
      if (!prev) return '';
      if (prev.status === 'active') return 'wf-line-done wf-line-active';
      if (prev.status === 'auto') return 'wf-line-active';
      return '';
    }

    // 已办示例
    const doneItems = ref([
      { id: 1, text: '订单 SO-20260803-005 已生效', time: '10 分钟前', color: 'green' },
      { id: 2, text: '加工单 WO-088 已下达至 A车间', time: '32 分钟前', color: 'blue' },
      { id: 3, text: '领料单 RQ-202 完成出库 ¥3,250', time: '1 小时前', color: 'purple' },
      { id: 4, text: '收款单 RV-512 核销 应收 ¥18,000', time: '2 小时前', color: 'green' },
    ]);

    // 团队动态
    const news = ref([
      { id: 1, who: '张销售', action: '提交了', target: '订单 SO-20260803-012', time: '5 分钟前', color: 'blue' },
      { id: 2, who: '李厂长', action: '下达了', target: '加工单 WO-089', time: '18 分钟前', color: 'purple' },
      { id: 3, who: '王财务', action: '审批通过', target: '报销单 EX-203 ¥1,280', time: '40 分钟前', color: 'green' },
      { id: 4, who: '陈仓管', action: '完成了', target: '采购入库 PR-45', time: '1 小时前', color: 'orange' },
      { id: 5, who: 'AI 助手', action: '生成了', target: '本月销售分析报告', time: '2 小时前', color: 'cyan' },
    ]);

    // 快捷入口(按角色)
    const quickMap = {
      ADMIN: [
        { key: 'orders', label: '新建订单', icon: 'plus', color: 'blue' },
        { key: 'work-orders', label: '下达工单', icon: 'wrench', color: 'purple' },
        { key: 'ai-analysis', label: 'AI 提问', icon: 'sparkles', color: 'cyan' },
        { key: 'screen', label: '车间大屏', icon: 'tv', color: 'green' },
      ],
      SALES: [
        { key: 'orders', label: '新建订单', icon: 'plus', color: 'blue' },
        { key: 'customers', label: '客户档案', icon: 'users', color: 'orange' },
        { key: 'ai-analysis', label: 'AI 提问', icon: 'sparkles', color: 'cyan' },
        { key: 'requisitions', label: '领料查询', icon: 'cube', color: 'purple' },
      ],
      GM: [
        { key: 'ai-analysis', label: 'AI 经营分析', icon: 'sparkles', color: 'cyan' },
        { key: 'approvals', label: '待审批', icon: 'check', color: 'orange' },
        { key: 'screen', label: '车间大屏', icon: 'tv', color: 'green' },
        { key: 'finance', label: '财务报表', icon: 'cash', color: 'blue' },
      ],
      FINANCE: [
        { key: 'finance', label: '财务单据', icon: 'cash', color: 'blue' },
        { key: 'approvals', label: '待审批', icon: 'check', color: 'orange' },
        { key: 'payroll', label: '工资管理', icon: 'users', color: 'purple' },
        { key: 'expense', label: '报销审核', icon: 'receipt', color: 'green' },
      ],
    };
    const quickEntries = ref(quickMap[user.value?.role] || quickMap.ADMIN);

    async function load() {
      try {
        const r = await api.get('/api/workbench');
        const d = r.data || {};
        todos.value = d.todos || [];
        appGroups.value = d.apps || {};
        workflowSteps.value = d.workflow_steps || [];
        if (d.kpis && d.kpis.length) kpis.value = d.kpis;
        try {
          const dr = await api.get('/api/workbench/done?page=1&size=4');
          if (dr.data && dr.data.length) doneItems.value = dr.data.map(x => ({
            id: x.id, text: x.title, time: x.time, color: x.color || 'blue'
          }));
        } catch(e) {}
      } catch (e) {
        if (e.message && !e.message.includes('登录已过期')) ElMessage.error('加载工作台数据失败: ' + e.message);
      }
    }
    function go(key) {
      window.location.hash = '#/' + key;
      if (window.__go) window.__go(key);
    }
    function deleteFlow(wf) {
      ElementPlus.ElMessageBox.confirm(
        '确定删除「' + wf.title + '」？删除后不可恢复。',
        '删除流程', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      ).then(async () => {
        try {
          await api.del('/api/approvals/definitions/' + wf.definition_id);
          ElementPlus.ElMessage.success('已删除');
          load();
        } catch(e) {
          ElementPlus.ElMessage.error(e.message || '删除失败');
        }
      }).catch(() => {});
    }
    function editFlow(wf) {
      // 跳转到流程设计器并通过全局事件传递要加载的definition_id
      window.__flowDesignerLoadId = wf.definition_id;
      window.location.hash = '#/approval-flows';
      if (window.__go) window.__go('approval-flows');
    }
    function badge(key) {
      const t = todos.value.find(t => t.route === key);
      return t ? t.count : 0;
    }
    onMounted(load);
    return { user, todos, workflowSteps, appGroups, groupList, roleLabel, isAdmin, greeting, today, go, badge, Icon,
      kpis, doneItems, news, quickEntries, wfClass, wfLineClass, deleteFlow, editFlow };
  }
};

// ============ 我的待办 / 我的已办 (独立详情页面) ============
function makeMyListPage({kind, title, sub, icon, apiPath, emptyText}) {
  return {
    template: `
    <div class="page">
      <div class="page-head">
        <div class="ph-left">
          <div class="ph-icon" v-html="Icon.icon(icon, 22)"></div>
          <div>
            <div class="ph-title">${title}</div>
            <div class="ph-sub">${sub} <span class="muted" v-if="total!=null"> · 共 {{total}} 条</span></div>
          </div>
        </div>
      </div>
      <div class="filter-bar">
        <el-input v-model="kw" placeholder="搜索标题/说明/标签" style="width:260px" clearable @keyup.enter="search" @clear="reset"/>
        <el-select v-model="tagFilter" placeholder="按类型筛选" style="width:180px" clearable>
          <el-option v-for="t in tagTypes" :key="t" :label="t" :value="t"/>
        </el-select>
        <el-button @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
        <div class="grow"></div>
      </div>
      <div class="doc-list" :class="{loading}" v-loading="loading">
        <div v-for="r in rows" :key="r.id" class="doc-card" @click="openItem(r)">
          <div :class="'doc-bar '+ (r.color||'blue')"></div>
          <div class="doc-main">
            <div class="doc-top">
              <span class="doc-no">{{r.type_label||r.type}}</span>
              <span class="pill" :class="r.color||'blue'" v-if="r.tag">{{r.tag}}</span>
              <span class="doc-cust" v-if="r.title">{{r.title}}</span>
              <span class="doc-time muted" v-if="r.time">{{r.time}}</span>
            </div>
            <div class="doc-fields">
              <div class="doc-field" style="grid-column: span 4;">
                <span class="df-label">说明</span>
                <span class="df-value">{{r.sub||'-'}}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="!loading && !rows.length" class="doc-empty">
          <div v-html="Icon.icon('check-circle', 44)"></div>
          <div class="title">${emptyText}</div>
          <div class="desc">稍作休息,稍后回来看看</div>
        </div>
      </div>
      <el-pagination v-if="total>size" style="margin-top:14px;justify-content:flex-end;display:flex"
        v-model:current-page="page" :page-size="size" :total="total" background layout="total, prev, pager, next"
        @current-change="load"/>
    </div>`,
    setup() {
      const rows = ref([]);
      const total = ref(0);
      const page = ref(1);
      const size = ref(20);
      const loading = ref(false);
      const kw = ref('');
      const tagFilter = ref('');
      const tagTypes = ref([]);
      const dialog = reactive({show:false, item:null});

      async function load() {
        loading.value = true;
        try {
          const r = await api.get('${apiPath}', { params: { page: page.value, size: size.value } });
          const all = (r.data?.items || []);
          tagTypes.value = Array.from(new Set(all.map(x=>x.type_label).filter(Boolean)));
          let list = all;
          if (kw.value) { const k=kw.value.trim().toLowerCase(); list = list.filter(x => (x.title+x.sub+x.tag+x.type_label).toLowerCase().includes(k)); }
          if (tagFilter.value) list = list.filter(x => x.type_label === tagFilter.value);
          total.value = list.length < size.value && page.value===1 ? list.length : (r.data?.total || list.length);
          rows.value = list;
        } catch(e) { console.error(e); rows.value = []; total.value = 0; }
        finally { loading.value = false; }
      }
      function search() { page.value=1; load(); }
      function reset() { kw.value=''; tagFilter.value=''; page.value=1; load(); }
      function openItem(r) { dialog.show=true; dialog.item=r; }
      function closeDialog() { dialog.show=false; dialog.item=null; }
      function jumpRoute() {
        const r = dialog.item; if (!r || !r.route) return;
        window.location.hash = '#/'+r.route;
        if (window.__go) window.__go(r.route);
        closeDialog();
      }
      onMounted(load);
      return { rows, total, page, size, loading, kw, tagFilter, tagTypes, dialog, load, search, reset, openItem, closeDialog, jumpRoute, Icon };
    }
  };
}
const MyTodosPage = makeMyListPage({
  kind: 'todos', title: '我的待办', sub: '个人相关的待处理事项(按紧急度排序)',
  icon: 'check', apiPath: '/api/workbench/todos', emptyText: '暂无待办事项'
});
const MyDonePage = makeMyListPage({
  kind: 'done', title: '我的已办', sub: '近14天已完成/确认的业务单据(按时间倒序)',
  icon: 'clipboard-check', apiPath: '/api/workbench/done', emptyText: '最近暂无已办记录'
});

// ============ 用户管理 / 角色管理 (仅ADMIN) ============
const UsersPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('users', 22)"></div>
        <div>
          <div class="ph-title">用户管理</div>
          <div class="ph-sub">维护系统登录用户、角色分配、密码重置<span class="muted" v-if="total!=null"> · 共 {{total}} 个用户</span></div>
        </div>
      </div>
      <div>
        <el-button type="primary" @click="openCreate">+ 新增用户</el-button>
      </div>
    </div>
    <div class="filter-bar">
      <el-input v-model="kw" placeholder="搜索账号/姓名" style="width:240px" clearable @keyup.enter="load(1)" @clear="load(1)"/>
      <el-select v-model="roleFilter" placeholder="按角色过滤" style="width:180px" clearable @change="load(1)">
        <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id"/>
      </el-select>
      <el-button @click="load(1)">查询</el-button>
      <div class="grow"></div>
    </div>
    <el-table :data="rows" v-loading="loading" stripe style="width:100%;--el-table-bg-color:transparent;--el-table-tr-bg-color:transparent;">
      <el-table-column label="ID" width="60" prop="id"/>
      <el-table-column label="账号" width="160" prop="username"/>
      <el-table-column label="姓名" width="140" prop="real_name"/>
      <el-table-column label="角色" width="160">
        <template #default="s"><el-tag size="small">{{s.row.role?.name||'-'}}</el-tag></template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="s">
          <el-tag size="small" :type="s.row.status==='ACTIVE'?'success':'danger'">{{s.row.status==='ACTIVE'?'启用':'停用'}}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280">
        <template #default="s">
          <el-button size="small" type="primary" is-plain @click="openEdit(s.row)">编辑</el-button>
          <el-button size="small" type="warning" is-plain @click="resetPwd(s.row)">重置密码</el-button>
          <el-button size="small" type="danger" is-plain @click="remove(s.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="total>size" style="margin-top:14px;justify-content:flex-end;display:flex"
      v-model:current-page="page" :page-size="size" :total="total" background layout="total, prev, pager, next" @current-change="load"/>

    <el-dialog v-model="dlg.show" :title="dlg.id?'编辑用户':'新增用户'" width="520px">
      <el-form :model="dlg" label-width="90px">
        <el-form-item label="账号"><el-input v-model="dlg.username" :disabled="!!dlg.id"/></el-form-item>
        <el-form-item label="姓名"><el-input v-model="dlg.real_name"/></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="dlg.role_id" style="width:100%">
            <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="dlg.status">
            <el-radio value="ACTIVE">启用</el-radio>
            <el-radio value="DISABLED">停用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="密码" v-if="!dlg.id">
          <el-input v-model="dlg.password" placeholder="默认 123456" show-password/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.show=false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ElMessage, ElMessageBox } = ElementPlus;
    const rows = ref([]), total = ref(0), page = ref(1), size = ref(20), loading = ref(false);
    const kw = ref(''), roleFilter = ref(null), roles = ref([]);
    const dlg = reactive({ show:false, id:null, username:'', real_name:'', role_id:null, status:'ACTIVE', password:'123456' });
    async function loadRoles() {
      try { roles.value = (await api.get('/api/admin/roles')).data || []; } catch(e){}
    }
    async function load(p) {
      if (p) page.value = p;
      loading.value = true;
      try {
        const params = { page: page.value, size: size.value };
        if (kw.value) params.keyword = kw.value.trim();
        if (roleFilter.value) params.role_id = roleFilter.value;
        const r = await api.get('/api/admin/users', { params });
        rows.value = r.data || []; total.value = r.total || 0;
      } catch(e) { ElMessage.error(e.message||'加载失败'); }
      finally { loading.value=false; }
    }
    function openCreate() {
      Object.assign(dlg, { show:true, id:null, username:'', real_name:'', role_id: roles.value[0]?.id||null, status:'ACTIVE', password:'123456' });
    }
    function openEdit(r) {
      Object.assign(dlg, { show:true, id:r.id, username:r.username, real_name:r.real_name||r.name, role_id:r.role?.id, status:r.status, password:'' });
    }
    async function submit() {
      try {
        if (!dlg.username || !dlg.real_name || !dlg.role_id) { ElMessage.warning('必填项不能为空'); return; }
        const body = { real_name: dlg.real_name, role_id: dlg.role_id, status: dlg.status };
        if (dlg.id) {
          if (dlg.password) body.password = dlg.password;
          await api.put('/api/admin/users/' + dlg.id, body);
        } else {
          await api.post('/api/admin/users', { ...body, username: dlg.username, password: dlg.password || '123456' });
        }
        ElMessage.success(dlg.id ? '已更新' : '已创建');
        dlg.show = false; load();
      } catch(e) { ElMessage.error(e.message||'保存失败'); }
    }
    async function resetPwd(r) {
      try {
        await ElMessageBox.confirm(`确定将「${r.username}」密码重置为 123456？`, '重置密码', { type:'warning' });
        await api.put('/api/admin/users/' + r.id, { password: '123456' });
        ElMessage.success('已重置为 123456');
      } catch(e){}
    }
    async function remove(r) {
      try {
        await ElMessageBox.confirm(`确定删除用户「${r.username}」？此操作不可恢复。`, '删除用户', { type:'error' });
        await api.del('/api/admin/users/' + r.id);
        ElMessage.success('已删除'); load();
      } catch(e){}
    }
    onMounted(async () => { await loadRoles(); load(); });
    return { rows, total, page, size, loading, kw, roleFilter, roles, dlg, load, openCreate, openEdit, submit, resetPwd, remove, Icon };
  }
};

const RolesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('shield', 22)"></div>
        <div>
          <div class="ph-title">角色管理</div>
          <div class="ph-sub">维护业务角色编码与名称,角色权限在流程节点中绑定<span class="muted" v-if="roles.length"> · 共 {{roles.length}} 个角色</span></div>
        </div>
      </div>
      <div>
        <el-button type="primary" @click="openCreate">+ 新增角色</el-button>
      </div>
    </div>
    <el-table :data="roles" stripe style="width:100%">
      <el-table-column label="ID" width="80" prop="id"/>
      <el-table-column label="编码" width="200">
        <template #default="s"><code style="color:var(--primary2)">{{s.row.code}}</code></template>
      </el-table-column>
      <el-table-column label="名称" width="220" prop="name"/>
      <el-table-column label="说明" prop="description"/>
      <el-table-column label="操作" width="200">
        <template #default="s">
          <el-button size="small" type="primary" is-plain @click="openEdit(s.row)">编辑</el-button>
          <el-button size="small" type="danger" is-plain @click="remove(s.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg.show" :title="dlg.id?'编辑角色':'新增角色'" width="480px">
      <el-form :model="dlg" label-width="80px">
        <el-form-item label="编码"><el-input v-model="dlg.code" :disabled="!!dlg.id" placeholder="大写英文,例: QA"/></el-form-item>
        <el-form-item label="名称"><el-input v-model="dlg.name" placeholder="例: 质检"/></el-form-item>
        <el-form-item label="说明"><el-input v-model="dlg.description" type="textarea" :rows="3"/></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.show=false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ElMessage, ElMessageBox } = ElementPlus;
    const roles = ref([]);
    const dlg = reactive({ show:false, id:null, code:'', name:'', description:'' });
    async function load() {
      try { roles.value = (await api.get('/api/admin/roles')).data || []; }
      catch(e) { ElMessage.error(e.message||'加载失败'); }
    }
    function openCreate() {
      Object.assign(dlg, { show:true, id:null, code:'', name:'', description:'' });
    }
    function openEdit(r) {
      Object.assign(dlg, { show:true, id:r.id, code:r.code, name:r.name, description:r.description||'' });
    }
    async function submit() {
      try {
        if (!dlg.code || !dlg.name) { ElMessage.warning('必填项不能为空'); return; }
        if (dlg.id) await api.put('/api/admin/roles/' + dlg.id, { name: dlg.name, description: dlg.description });
        else await api.post('/api/admin/roles', { code: dlg.code.toUpperCase(), name: dlg.name, description: dlg.description });
        ElMessage.success(dlg.id ? '已更新' : '已创建');
        dlg.show = false; load();
      } catch(e) { ElMessage.error(e.message||'保存失败'); }
    }
    async function remove(r) {
      try {
        await ElMessageBox.confirm(`确定删除角色「${r.name}」？若该角色下有用户将无法删除。`, '删除角色', { type:'error' });
        await api.del('/api/admin/roles/' + r.id);
        ElMessage.success('已删除'); load();
      } catch(e){}
    }
    onMounted(load);
    return { roles, dlg, load, openCreate, openEdit, submit, remove, Icon };
  }
};

// ============ 客户 ============
const CustomersPage = makeListPage({
  title: '客户管理', sub: '维护客户档案与结算信息', createLabel: '新增客户', icon: 'users',
  listUrl: '/api/customers', createUrl: '/api/customers', updateUrl: r => `/api/customers/${r.id}`,
  query: { keyword: 'text' }, queryPlaceholders: { keyword: '搜索客户名称/编码' },
  card: {
    icon: 'users', noField: 'code', custField: 'name',
    fields: [
      { label: '联系人', key: 'contact_name' },
      { label: '电话', key: 'contact_phone' },
      { label: '行业', key: 'industry' },
      { label: '结算周期', key: 'settlement_cycle' },
      { label: '开户行', key: 'bank_name' },
    ],
    actions: [{ key: 'edit', label: '编辑', type: 'primary', action: 'edit' }],
  },
  formFields: [
    { key: 'code', label: '编码', type: 'text', ph: 'C001', w: 200 },
    { key: 'name', label: '名称', type: 'text', w: 300 },
    { key: 'tax_no', label: '税号', type: 'text', w: 260 },
    { key: 'address', label: '地址', type: 'text', w: 360 },
    { key: 'contact_name', label: '联系人', type: 'text', w: 160 },
    { key: 'contact_phone', label: '电话', type: 'text', w: 180 },
    { key: 'industry', label: '行业', type: 'text', w: 160 },
    { key: 'settlement_cycle', label: '结算周期', type: 'text', ph: '月结30/60/90/款到发货', w: 200 },
    { key: 'bank_name', label: '开户行', type: 'text', w: 200 },
    { key: 'bank_account', label: '账号', type: 'text', w: 240 },
  ],
});

// ============ 订单 ============
const ORDER_STATUS = { DRAFT: '草稿', SUBMITTED: '待生效', EFFECTIVE: '已生效', PROCESSING: '生产中', PENDING_DELIVERY: '待发货', DELIVERED: '已发货', CLOSED: '已结算', RETURNED: '已退单', CANCELLED: '已取消' };
const ORDER_FLOW = [
  { key: 'DRAFT', label: '草稿', idx: 0 },
  { key: 'SUBMITTED', label: '待生效', idx: 1 },
  { key: 'EFFECTIVE', label: '已生效', idx: 2 },
  { key: 'PROCESSING', label: '生产中', idx: 3 },
  { key: 'PENDING_DELIVERY', label: '待发货', idx: 4 },
  { key: 'DELIVERED', label: '已发货', idx: 5 },
  { key: 'CLOSED', label: '已结算', idx: 6 },
];
const BILLING_LABEL = { SPECIAL_VAT: '专票', NORMAL: '普票', CASH: '现金' };
const DELIVERY_LABEL = { PENDING: '待发货', PENDING_DELIVERY: '待发货', DELIVERED: '已发货' };

const OrdersPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('receipt',22)"></div>
        <div>
          <div class="ph-title">销售订单</div>
          <div class="ph-sub">来料加工入库 · 订单驱动主线 · 草稿→生效→生产→发货→结算</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建订单</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="订单号/客户" style="width:220px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)"></span></template>
      </el-input>
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in ORDER_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.order_no}}</span>
            <span class="pill" :class="row.status">{{ORDER_STATUS[row.status]||row.status}}</span>
            <span class="doc-cust" v-if="row.customer_name">{{row.customer_name}}</span>
            <span class="pill warn" v-if="row.return_count">退单{{row.return_count}}次</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">开票主体</span><span class="df-value">{{compName(row.company_id)}}</span></div>
            <div class="doc-field"><span class="df-label">开票类型</span><span class="df-value">{{BILLING_LABEL[row.billing_type]||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">预收款</span><span class="df-value">¥{{fmt(row.prepayment_amount)}}</span></div>
            <div class="doc-field"><span class="df-label">发货状态</span><span class="df-value">{{DELIVERY_LABEL[row.delivery_status]||row.delivery_status||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">生效时间</span><span class="df-value">{{fmtDateShort(row.effective_at)}}</span></div>
            <div class="doc-field"><span class="df-label">创建时间</span><span class="df-value">{{fmtDateShort(row.created_at)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='DRAFT'||row.status==='RETURNED'" size="small" type="primary" @click="act(row,'/api/orders/'+row.id+'/submit','提交')">提交</el-button>
          <el-button v-if="row.status==='SUBMITTED'" size="small" type="success" @click="act(row,'/api/orders/'+row.id+'/effect','生效')">生效</el-button>
          <el-button v-if="row.status==='SUBMITTED'" size="small" type="warning" @click="actInput(row,'/api/orders/'+row.id+'/return','退单原因','退单')">退单</el-button>
          <el-button v-if="row.status==='EFFECTIVE'" size="small" type="primary" @click="goWorkOrder(row)">下加工单</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无订单</div>
        <div class="de-desc">点击右上方"新建订单"创建第一条来料加工入库单</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="dialog.visible" title="新建订单 · 来料加工入库单" width="820px" top="6vh">
      <el-form :model="form" label-width="100px">
        <div class="form-grid">
          <el-form-item label="客户">
            <el-select v-model="form.customer_id" placeholder="选择客户" style="width:280px" filterable>
              <el-option v-for="c in custs" :key="c.id" :label="c.code+' '+c.name" :value="c.id"/>
            </el-select>
          </el-form-item>
          <el-form-item label="开票主体">
            <el-select v-model="form.company_id" placeholder="公司主体" style="width:240px">
              <el-option v-for="c in comps" :key="c.id" :label="c.short_name||c.name" :value="c.id"/>
            </el-select>
          </el-form-item>
          <el-form-item label="开票类型">
            <el-select v-model="form.billing_type" placeholder="款项流向" style="width:200px">
              <el-option label="增值税专用发票" value="SPECIAL_VAT"/><el-option label="增值税普通发票" value="NORMAL"/><el-option label="现金(无票)" value="CASH"/>
            </el-select>
          </el-form-item>
          <el-form-item label="预收款">
            <el-input-number v-model="form.prepayment_amount" :min="0" :precision="2" style="width:180px"/>
          </el-form-item>
        </div>
        <div class="form-tip">订单生效时自动建收款单核销应收</div>
        <el-divider>订单明细(喷涂工件)</el-divider>
        <div v-for="(it,i) in form.items" :key="i" class="item-row">
          <el-input v-model="it.part_name" placeholder="工件名" style="width:130px"/>
          <el-input v-model="it.part_spec" placeholder="规格" style="width:130px"/>
          <el-select v-model="it.price_type" placeholder="计价" style="width:100px">
            <el-option label="按件" value="BY_PIECE"/><el-option label="按面积" value="BY_AREA"/><el-option label="按重量" value="BY_WEIGHT"/>
          </el-select>
          <el-input-number v-model="it.quantity" :min="0" placeholder="数量" style="width:110px"/>
          <el-input v-model="it.unit" placeholder="单位" style="width:64px"/>
          <el-input-number v-model="it.unit_price" :min="0" :precision="2" placeholder="单价" style="width:110px"/>
          <el-select v-model="it.material_mode" placeholder="料属" style="width:90px">
            <el-option label="自营料" value="SELF"/><el-option label="客供料" value="CUSTOMER"/>
          </el-select>
          <el-input v-model="it.paint_spec" placeholder="涂料规格" style="width:130px"/>
          <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
        </div>
        <el-button size="small" @click="form.items.push({seq:form.items.length+1,price_type:'BY_AREA',unit:'m²',material_mode:'SELF'})"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加明细</el-button>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">保存草稿</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="订单详情" size="640px">
      <template v-if="detail.data.id">
        <div class="flow-steps">
          <div v-for="(s,i) in ORDER_FLOW" :key="s.key" :class="['flow-step', flowClass(detail.data, s)]">
            <div class="fs-node">{{i+1}}</div>
            <div class="fs-label">{{s.label}}</div>
          </div>
        </div>

        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.order_no}}</span>
            <span class="pill" :class="detail.data.status">{{ORDER_STATUS[detail.data.status]||detail.data.status}}</span>
            <span class="pill warn" v-if="detail.data.return_count">退单{{detail.data.return_count}}次</span>
            <span class="dh-amount">¥{{fmt(detail.data.total_amount)}}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">
            {{detail.data.customer_name}} · {{DELIVERY_LABEL[detail.data.delivery_status]||detail.data.delivery_status||'-'}}
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">基本信息</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">客户</div><div class="ig-value">{{detail.data.customer_name||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">开票主体</div><div class="ig-value">{{compName(detail.data.company_id)}}</div></div>
            <div class="ig-item"><div class="ig-label">开票类型</div><div class="ig-value">{{BILLING_LABEL[detail.data.billing_type]||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">总金额</div><div class="ig-value big">¥{{fmt(detail.data.total_amount)}}</div></div>
            <div class="ig-item"><div class="ig-label">预收款</div><div class="ig-value pos">¥{{fmt(detail.data.prepayment_amount)}}</div></div>
            <div class="ig-item"><div class="ig-label">退单次数</div><div class="ig-value" :class="detail.data.return_count?'neg':''">{{detail.data.return_count||0}}</div></div>
            <div class="ig-item"><div class="ig-label">生效时间</div><div class="ig-value">{{fmtDate(detail.data.effective_at)}}</div></div>
            <div class="ig-item"><div class="ig-label">创建时间</div><div class="ig-value">{{fmtDate(detail.data.created_at)}}</div></div>
          </div>
        </div>

        <div class="detail-section" v-if="detail.data.return_reason">
          <div class="ds-title">退单原因</div>
          <div class="info-grid"><div class="ig-item" style="grid-column:1/-1"><div class="ig-value neg">{{detail.data.return_reason}}</div></div></div>
        </div>

        <div class="detail-section">
          <div class="ds-title">订单明细</div>
          <el-table :data="detail.data.items||[]" border size="small">
            <el-table-column type="index" label="#" width="44"/>
            <el-table-column prop="part_name" label="工件" min-width="120"/>
            <el-table-column label="规格" prop="part_spec" width="120"/>
            <el-table-column label="计价" width="80"><template #default="{row}">{{ {BY_PIECE:'按件',BY_AREA:'按面积',BY_WEIGHT:'按重量'}[row.price_type]||row.price_type }}</template></el-table-column>
            <el-table-column label="数量" width="100"><template #default="{row}">{{row.quantity}}{{row.unit}}</template></el-table-column>
            <el-table-column label="单价" width="90"><template #default="{row}">¥{{fmt(row.unit_price)}}</template></el-table-column>
            <el-table-column label="金额" width="110"><template #default="{row}">¥{{fmt(row.amount)}}</template></el-table-column>
            <el-table-column label="料属" width="80"><template #default="{row}">{{row.material_mode==='CUSTOMER'?'客供料':'自营料'}}</template></el-table-column>
            <el-table-column label="工艺" prop="paint_spec" min-width="120"/>
          </el-table>
        </div>

        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='DRAFT'||detail.data.status==='RETURNED'" type="primary" @click="act(detail.data,'/api/orders/'+detail.data.id+'/submit','提交')">提交生效</el-button>
            <el-button v-if="detail.data.status==='SUBMITTED'" type="success" @click="act(detail.data,'/api/orders/'+detail.data.id+'/effect','生效')">确认生效</el-button>
            <el-button v-if="detail.data.status==='SUBMITTED'" type="warning" @click="actInput(detail.data,'/api/orders/'+detail.data.id+'/return','退单原因','退单')">退单</el-button>
            <el-button v-if="detail.data.status==='EFFECTIVE'" type="primary" @click="goWorkOrder(detail.data)">下加工单</el-button>
            <el-button type="info" plain @click="showProfit(detail.data)">利润分析</el-button>
            <el-button type="warning" plain @click="printOrder(detail.data)"><span v-html="Icon.icon('printer',14)" style="vertical-align:middle;margin-right:4px"></span>打印入库单</el-button>
          </div>
        </div>
      </template>
    </el-drawer>

    <el-dialog v-model="profit.visible" title="订单利润分析" width="600px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="营收">¥{{fmt(profit.data.revenue)}}</el-descriptions-item>
        <el-descriptions-item label="总成本">¥{{fmt(profit.data.cost)}}</el-descriptions-item>
        <el-descriptions-item label="成本分解">
          <span v-for="(v,k) in profit.data.cost_breakdown||{}" :key="k" style="margin-right:12px">{{costLabel(k)}}:¥{{fmt(v)}}</span>
        </el-descriptions-item>
        <el-descriptions-item label="利润"><span :style="'color:'+(profit.data.profit>=0?'#10b981':'#ef4444')+';font-weight:600'">¥{{fmt(profit.data.profit)}}</span></el-descriptions-item>
        <el-descriptions-item label="毛利率"><span :style="'color:'+(profit.data.gross_margin_pct>=15?'#10b981':'#ef4444')+';font-weight:600'">{{profit.data.gross_margin_pct}}%</span></el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ keyword: '', status: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const profit = reactive({ visible: false, data: {} });
    const custs = ref([]);
    const comps = ref([]);
    const form = reactive({ customer_id: null, company_id: null, billing_type: null, prepayment_amount: 0, items: [{ seq: 1, price_type: 'BY_AREA', unit: 'm²', material_mode: 'SELF' }] });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    const costLabel = k => ({ MATERIAL: '材料', LABOR: '人工', OVERHEAD: '制造费用', OUTSOURCE: '委外', REWORK: '返工' }[k] || k);
    const flowClass = (row, s) => {
      const order = ['DRAFT', 'RETURNED', 'SUBMITTED', 'EFFECTIVE', 'PROCESSING', 'PENDING_DELIVERY', 'DELIVERED', 'CLOSED'];
      const cur = order.indexOf(row.status);
      const target = s.idx;
      const realIdx = { DRAFT: 0, SUBMITTED: 1, EFFECTIVE: 2, PROCESSING: 3, PENDING_DELIVERY: 4, DELIVERED: 5, CLOSED: 6 }[row.status] ?? -1;
      if (realIdx < 0) return '';
      if (target < realIdx) return 'done';
      if (target === realIdx) return 'current';
      return '';
    };

    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v)) }).toString();
        const r = await api.get('/api/orders?' + qs);
        rows.value = r.data; total.value = r.total;
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.keyword = ''; query.status = ''; search(); }
    async function openCreate() {
      try {
        const [rc, rp] = await Promise.all([api.get('/api/customers'), api.get('/api/companies')]);
        custs.value = rc.data; comps.value = rp.data || [];
      } catch {}
      Object.assign(form, { customer_id: null, company_id: null, billing_type: null, prepayment_amount: 0, items: [{ seq: 1, price_type: 'BY_AREA', unit: 'm²', material_mode: 'SELF' }] });
      dialog.visible = true;
    }
    async function submit() {
      try { await api.post('/api/orders', form); ElMessage.success('订单已创建(草稿)'); dialog.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    async function openDetail(row) {
      try { const r = await api.get('/api/orders/' + row.id); detail.data = r.data || {}; detail.visible = true; } catch (e) { ElMessage.error(e.message); }
    }
    async function act(row, url, label) {
      try {
        await ElMessageBox.confirm(`确认${label}订单 ${row.order_no}?`, '提示', { type: 'warning' });
        await api.post(url, {});
        ElMessage.success(label + '成功');
        if (detail.visible) { const r = await api.get('/api/orders/' + row.id); detail.data = r.data || {}; }
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    async function actInput(row, url, label, btnLabel) {
      try {
        const { value } = await ElMessageBox.prompt(label, btnLabel, {});
        await api.post(url, { reason: value });
        ElMessage.success(btnLabel + '成功');
        if (detail.visible) { const r = await api.get('/api/orders/' + row.id); detail.data = r.data || {}; }
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    function goWorkOrder(row) { location.hash = '#/work-orders?order_id=' + row.id; }
    async function showProfit(row) {
      try { const r = await api.get('/api/finance/profit/order/' + row.id); profit.data = r.data; profit.visible = true; }
      catch (e) { ElMessage.error(e.message); }
    }
    const compName = id => { const c = comps.value.find(x => x.id === id); return c ? (c.short_name || c.name) : '-'; };
    function printOrder(d) {
      const d2 = d => d < 10 ? '0' + d : '' + d;
      const now = new Date();
      const dateStr = now.getFullYear() + '-' + d2(now.getMonth() + 1) + '-' + d2(now.getDate());
      const itemsHtml = (d.items || []).map((it, i) =>
        `<tr><td>${i + 1}</td><td>${d.customer_name || ''}</td><td>${it.material_mode === 'CUSTOMER' ? '客供料' : '自营料'}</td><td>${it.part_spec || ''}</td><td style="text-align:right">${fmt(it.quantity)}${it.unit || ''}</td><td>${it.process_requirement || it.paint_spec || ''}</td><td></td><td style="text-align:right">¥${fmt(it.amount)}</td></tr>`
      ).join('');
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>来料加工入库单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:20mm 15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:20px;margin-bottom:4px;letter-spacing:4px}
        .sub{text-align:center;font-size:13px;color:#666;margin-bottom:20px}
        .info{display:flex;justify-content:space-between;font-size:13px;margin-bottom:12px}
        table{width:100%;border-collapse:collapse;font-size:12px}
        th,td{border:1px solid #333;padding:6px 8px;text-align:left}
        th{background:#f0f0f0;font-weight:600}
        .total{text-align:right;font-weight:600;margin-top:8px;font-size:14px}
        .sign{margin-top:40px;display:flex;justify-content:space-between;font-size:13px}
        .sign div{text-align:center}
        .sign .line{display:inline-block;width:120px;border-bottom:1px solid #333;margin-top:28px}
        @media print{body{margin:0;padding:15mm}@page{margin:10mm}}
      </style></head><body>
        <h1>东莞市峰业精密机械有限公司</h1>
        <div class="sub">来料加工入库单</div>
        <div class="info"><span>单号：${d.order_no || ''}</span><span>日期：${dateStr}</span><span>经手人：______________</span></div>
        <table><thead><tr><th style="width:40px">序号</th><th>客户名称</th><th style="width:70px">来料类型</th><th>尺寸</th><th style="width:80px">数量</th><th>工艺</th><th style="width:70px">交期</th><th style="width:100px">金额</th></tr></thead>
        <tbody>${itemsHtml || '<tr><td colspan="8" style="text-align:center;color:#999">无明细</td></tr>'}</tbody></table>
        <div class="total">合计金额：¥${fmt(d.total_amount)}</div>
        <div class="sign"><div>客户签字：<span class="line"></span></div><div>经手人：<span class="line"></span></div><div>日期：<span class="line"></span></div></div>
        <script>window.onload=function(){setTimeout(function(){window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, total, page, loading, query, dialog, detail, profit, custs, comps, form, ORDER_STATUS, ORDER_FLOW, BILLING_LABEL, DELIVERY_LABEL, fmt, fmtDate, fmtDateShort, costLabel, compName, flowClass, load, search, reset, openCreate, submit, openDetail, act, actInput, goWorkOrder, showProfit, printOrder, Icon };
  }
};

// ============ 加工单 ============
const WO_STATUS = { CREATED: '待下达', RELEASED: '已下达', PROCESSING: '生产中', COMPLETED: '已完成', CONFIRMED: '已确认' };
const WO_FLOW = [
  { key: 'CREATED', label: '创建', idx: 0 },
  { key: 'RELEASED', label: '下达', idx: 1 },
  { key: 'PROCESSING', label: '生产', idx: 2 },
  { key: 'COMPLETED', label: '完工', idx: 3 },
  { key: 'CONFIRMED', label: '确认', idx: 4 },
];

const WorkOrdersPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('wrench',22)"></div>
        <div>
          <div class="ph-title">加工单(工艺单)</div>
          <div class="ph-sub">由生效订单派单 · 下达→生产→完工→确认</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建加工单</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in WO_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="加工单号/批次号" style="width:220px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)"></span></template>
      </el-input>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.work_order_no}}</span>
            <span class="pill" :class="row.status">{{WO_STATUS[row.status]||row.status}}</span>
            <span class="pill warn" v-if="row.workshop">{{ {A:'A车间',B:'B车间'}[row.workshop]||row.workshop+'车间' }}</span>
            <span class="doc-cust" v-if="row.order_no">来源: {{row.order_no}}</span>
            <span class="doc-amount" v-if="row.total_cost">¥{{fmt(row.total_cost)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">批次号</span><span class="df-value">{{row.batch_no||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">计划数量</span><span class="df-value">{{fmt(row.plan_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">实际数量</span><span class="df-value" :class="row.actual_qty<row.plan_qty?'neg':''">{{fmt(row.actual_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">计划交期</span><span class="df-value">{{fmtDateShort(row.plan_finish_date)}}</span></div>
            <div class="doc-field"><span class="df-label">完工单号</span><span class="df-value">{{row.completion_no||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">创建时间</span><span class="df-value">{{fmtDateShort(row.created_at)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='CREATED'" size="small" type="primary" @click="act(row,'/api/work-orders/'+row.id+'/release','下达')">下达</el-button>
          <el-button v-if="row.status==='RELEASED'||row.status==='PROCESSING'" size="small" type="success" @click="goCompletion(row)">填完工</el-button>
          <el-button size="small" @click="showCost(row)">成本</el-button>
          <el-button size="small" type="warning" @click="printWO(row)">工艺单</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无加工单</div>
        <div class="de-desc">从生效订单派生第一条加工单</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="dialog.visible" title="新建加工单" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="来源订单">
          <el-select v-model="form.order_id" filterable placeholder="选择已生效订单" style="width:340px">
            <el-option v-for="o in orders" :key="o.id" :label="o.order_no+' '+o.customer_name" :value="o.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="批次号"><el-input v-model="form.batch_no" style="width:240px"/></el-form-item>
        <el-form-item label="车间"><el-select v-model="form.workshop" style="width:140px"><el-option label="A车间" value="A"/><el-option label="B车间" value="B"/></el-select></el-form-item>
        <el-form-item label="计划数量"><el-input-number v-model="form.plan_qty" :min="0" style="width:200px"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">创建</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="加工单详情" size="600px">
      <template v-if="detail.data.id">
        <div class="flow-steps">
          <div v-for="(s,i) in WO_FLOW" :key="s.key" :class="['flow-step', woFlowClass(detail.data, s)]">
            <div class="fs-node">{{i+1}}</div>
            <div class="fs-label">{{s.label}}</div>
          </div>
        </div>

        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.work_order_no}}</span>
            <span class="pill" :class="detail.data.status">{{WO_STATUS[detail.data.status]||detail.data.status}}</span>
            <span class="pill warn">{{ {A:'A车间',B:'B车间'}[detail.data.workshop]||detail.data.workshop+'车间' }}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">
            来源订单: {{detail.data.order_no||'-'}} · 批次: {{detail.data.batch_no||'-'}}
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">计划与执行</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">计划数量</div><div class="ig-value big">{{fmt(detail.data.plan_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">实际数量</div><div class="ig-value big" :class="detail.data.actual_qty<detail.data.plan_qty?'neg':'pos'">{{fmt(detail.data.actual_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">计划交期</div><div class="ig-value">{{fmtDate(detail.data.plan_finish_date)}}</div></div>
            <div class="ig-item"><div class="ig-label">完工单号</div><div class="ig-value">{{detail.data.completion_no||'-'}}</div></div>
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='CREATED'" type="primary" @click="act(detail.data,'/api/work-orders/'+detail.data.id+'/release','下达')">下达生产</el-button>
            <el-button v-if="detail.data.status==='RELEASED'||detail.data.status==='PROCESSING'" type="success" @click="goCompletion(detail.data)">填完工单</el-button>
            <el-button type="info" plain @click="showCost(detail.data)">成本归集</el-button>
            <el-button type="warning" plain @click="printWO(detail.data)"><span v-html="Icon.icon('printer',14)" style="vertical-align:middle;margin-right:4px"></span>打印工艺单</el-button>
          </div>
        </div>
      </template>
    </el-drawer>

    <el-dialog v-model="cost.visible" title="工单成本归集" width="640px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="总成本"><span style="color:#ef4444;font-weight:600">¥{{fmt(cost.data.total_cost)}}</span></el-descriptions-item>
        <el-descriptions-item label="成本分解"><span v-for="(v,k) in cost.data.breakdown||{}" :key="k" style="margin-right:14px">{{costLabel(k)}}:¥{{fmt(v)}}</span></el-descriptions-item>
      </el-descriptions>
      <el-table :data="cost.data.details||[]" border size="small" style="margin-top:12px">
        <el-table-column prop="cost_type" label="类型" width="100"><template #default="{row}">{{costLabel(row.cost_type)}}</template></el-table-column>
        <el-table-column prop="amount" label="金额" width="100"><template #default="{row}">¥{{fmt(row.amount)}}</template></el-table-column>
        <el-table-column prop="source_doc_type" label="来源"/>
        <el-table-column prop="occurred_at" label="时间"><template #default="{row}">{{fmtDate(row.occurred_at)}}</template></el-table-column>
      </el-table>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ status: '', keyword: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const cost = reactive({ visible: false, data: {} });
    const orders = ref([]);
    const form = reactive({ order_id: null, batch_no: '', workshop: 'A', plan_qty: 100 });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    const costLabel = k => ({ MATERIAL: '材料', LABOR: '人工', OVERHEAD: '制造费用', OUTSOURCE: '委外', REWORK: '返工' }[k] || k);
    const woFlowClass = (row, s) => {
      const idx = { CREATED: 0, RELEASED: 1, PROCESSING: 2, COMPLETED: 3, CONFIRMED: 4 }[row.status];
      if (idx == null) return '';
      if (s.idx < idx) return 'done';
      if (s.idx === idx) return 'current';
      return '';
    };

    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v)) }).toString();
        const r = await api.get('/api/work-orders?' + qs);
        rows.value = r.data; total.value = r.total;
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.status = ''; query.keyword = ''; search(); }
    async function openCreate() {
      try { const r = await api.get('/api/orders?status=EFFECTIVE'); orders.value = r.data; } catch {}
      form.batch_no = 'BATCH-' + Date.now().toString().slice(-6);
      dialog.visible = true;
    }
    async function submit() {
      try { await api.post('/api/work-orders', form); ElMessage.success('加工单已创建'); dialog.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    async function act(row, url, label) {
      try {
        await ElMessageBox.confirm(`确认${label}?`, '提示', { type: 'warning' });
        await api.post(url, {});
        ElMessage.success(label + '成功');
        if (detail.visible) detail.data = { ...detail.data, status: 'RELEASED' };
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    function goCompletion(row) { location.hash = '#/completions?work_order_id=' + row.id; }
    async function showCost(row) {
      try { const r = await api.get('/api/finance/work-order-costs/' + row.id); cost.data = r.data; cost.visible = true; }
      catch (e) { ElMessage.error(e.message); }
    }
    function printWO(row) {
      const workshopLabel = { A: '氧乙炔喷涂房', B: '等离子喷涂房' }[row.workshop] || row.workshop + '车间';
      const processes = ['清洗', '喷砂', '喷涂', '抛光', '包装', '验收'];
      const procHtml = processes.map(p =>
        `<tr><td style="text-align:center">${p}</td><td>______________</td><td>______________</td><td>______________</td><td>______________</td></tr>`
      ).join('');
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>生产工艺单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:18px;margin-bottom:2px;letter-spacing:2px}
        .sub{text-align:center;font-size:14px;color:#333;margin-bottom:6px;font-weight:600}
        .info{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:13px;margin-bottom:10px;border:1px solid #ccc;padding:8px 12px;border-radius:4px;background:#fafafa}
        .info span{margin-right:16px}
        table{width:100%;border-collapse:collapse;font-size:12px}
        th,td{border:1px solid #333;padding:5px 6px;text-align:left}
        th{background:#f0f0f0}
        .sign{margin-top:30px;display:flex;justify-content:space-between;font-size:12px}
        .sign .line{display:inline-block;width:100px;border-bottom:1px solid #333;margin-top:24px}
        @media print{body{margin:0;padding:12mm}@page{margin:8mm}}
      </style></head><body>
        <h1>东莞市峰业精密机械有限公司</h1>
        <div class="sub">${workshopLabel} — 生产工艺单</div>
        <div class="sub" style="font-size:13px;color:#666">单号：${row.work_order_no || ''}</div>
        <div class="info">
          <span>来源订单：${row.order_no || ''}</span>
          <span>批次号：${row.batch_no || ''}</span>
          <span>计划数量：${fmt(row.plan_qty)}</span>
          <span>交期：${row.plan_finish_date ? fmtDate(row.plan_finish_date) : '______________'}</span>
        </div>
        <table><thead><tr><th style="width:60px">工序</th><th style="width:100px">操作人</th><th style="width:100px">开始时间</th><th style="width:100px">完成时间</th><th>质检签字</th></tr></thead>
        <tbody>${procHtml}</tbody></table>
        <div style="margin-top:6px;font-size:11px;color:#888">说明：每道工序完成后由操作人填写时间，质检签字确认。</div>
        <div class="sign"><div>业务员：<span class="line"></span></div><div>生产厂长：<span class="line"></span></div><div>技术负责人：<span class="line"></span></div></div>
        <script>window.onload=function(){setTimeout(function(){window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, total, page, loading, query, dialog, detail, cost, orders, form, WO_STATUS, WO_FLOW, fmt, fmtDate, fmtDateShort, costLabel, woFlowClass, load, search, reset, openCreate, submit, openDetail, act, goCompletion, showCost, printWO, Icon };
  }
};

// ============ 完工单 ============
const CP_STATUS = { DRAFT: '草稿', CONFIRMED: '已确认' };
const CP_FLOW = [
  { key: 'DRAFT', label: '草稿', idx: 0 },
  { key: 'CONFIRMED', label: '已确认', idx: 1 },
];

const CompletionsPage = {
  components: { FlowTrack, FlowMini },
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('clipboard-check',22)"></div>
        <div>
          <div class="ph-title">完工单</div>
          <div class="ph-sub">质检·成本结转·成品入库·退料·利用率 · 确认后自动业财联动</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>录入完工单</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in CP_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="完工单号/加工单号" style="width:240px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)"></span></template>
      </el-input>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.completion_no}}</span>
            <span class="pill" :class="row.status">{{CP_STATUS[row.status]||row.status}}</span>
            <span class="doc-cust" v-if="row.work_order_no">加工: {{row.work_order_no}}</span>
            <span class="doc-amount">¥{{fmt(row.total_cost)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">完工数</span><span class="df-value">{{fmt(row.finished_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">合格数</span><span class="df-value pos">{{fmt(row.qualified_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">返工数</span><span class="df-value" :class="row.rework_qty?'neg':''">{{fmt(row.rework_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">废品数</span><span class="df-value" :class="row.scrap_qty?'neg':''">{{fmt(row.scrap_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">工时</span><span class="df-value">{{row.labor_hours||0}}h</span></div>
            <div class="doc-field"><span class="df-label">人工/制造</span><span class="df-value">¥{{fmt(row.labor_cost)}} / ¥{{fmt(row.overhead_cost)}}</span></div>
          </div>
          <flow-mini biz-type="COMPLETION" :biz-id="row.id"/>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='DRAFT'" size="small" type="success" @click="confirm(row)">确认完工</el-button>
          <el-button size="small" type="warning" @click="printCP(row)">质检单</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无完工单</div>
        <div class="de-desc">从已下达加工单录入第一条完工质检</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="dialog.visible" title="录入完工单" width="800px" top="6vh">
      <el-form :model="form" label-width="110px">
        <el-form-item label="加工单">
          <el-select v-model="form.work_order_id" filterable placeholder="选择已下达加工单" style="width:360px">
            <el-option v-for="w in wos" :key="w.id" :label="w.work_order_no+' ('+w.workshop+')'" :value="w.id"/>
          </el-select>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="完工数"><el-input-number v-model="form.finished_qty" :min="0" style="width:140px"/></el-form-item>
          <el-form-item label="合格数"><el-input-number v-model="form.qualified_qty" :min="0" style="width:140px"/></el-form-item>
          <el-form-item label="返工数"><el-input-number v-model="form.rework_qty" :min="0" style="width:140px"/></el-form-item>
          <el-form-item label="废品数"><el-input-number v-model="form.scrap_qty" :min="0" style="width:140px"/></el-form-item>
          <el-form-item label="工时"><el-input-number v-model="form.labor_hours" :min="0" style="width:140px"/></el-form-item>
          <el-form-item label="人工费"><el-input-number v-model="form.labor_cost" :min="0" :precision="2" style="width:140px"/></el-form-item>
          <el-form-item label="制造费"><el-input-number v-model="form.overhead_cost" :min="0" :precision="2" style="width:140px"/></el-form-item>
        </div>
        <el-divider>涂料/粉末实际用量(用于利用率计算)</el-divider>
        <div v-for="(it,i) in form.items" :key="i" class="item-row">
          <el-select v-model="it.item_id" placeholder="物料" style="width:200px" filterable>
            <el-option v-for="m in paints" :key="m.id" :label="m.name" :value="m.id"/>
          </el-select>
          <el-input-number v-model="it.theoretical_qty" :min="0" :precision="3" placeholder="理论" style="width:120px"/>
          <el-input-number v-model="it.actual_qty" :min="0" :precision="3" placeholder="实际" style="width:120px"/>
          <el-input-number v-model="it.return_qty" :min="0" :precision="3" placeholder="退料" style="width:120px"/>
          <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
        </div>
        <el-button size="small" @click="form.items.push({item_id:null,theoretical_qty:0,actual_qty:0,return_qty:0})"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加物料</el-button>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">保存(草稿)</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="完工单详情" size="620px">
      <template v-if="detail.data.id">
        <div class="detail-section">
          <div class="ds-title">🔄 流转轨迹</div>
          <flow-track biz-type="COMPLETION" :biz-id="detail.data.id"/>
        </div>

        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.completion_no}}</span>
            <span class="pill" :class="detail.data.status">{{CP_STATUS[detail.data.status]||detail.data.status}}</span>
            <span class="dh-amount">¥{{fmt(detail.data.total_cost)}}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">
            加工单: {{detail.data.work_order_no||'-'}}
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">完工数量</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">完工数</div><div class="ig-value big">{{fmt(detail.data.finished_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">合格数</div><div class="ig-value big pos">{{fmt(detail.data.qualified_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">返工数</div><div class="ig-value big" :class="detail.data.rework_qty?'neg':''">{{fmt(detail.data.rework_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">废品数</div><div class="ig-value big" :class="detail.data.scrap_qty?'neg':''">{{fmt(detail.data.scrap_qty)}}</div></div>
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">成本与工时</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">工时</div><div class="ig-value">{{detail.data.labor_hours||0}}h</div></div>
            <div class="ig-item"><div class="ig-label">人工费</div><div class="ig-value">¥{{fmt(detail.data.labor_cost)}}</div></div>
            <div class="ig-item"><div class="ig-label">制造费</div><div class="ig-value">¥{{fmt(detail.data.overhead_cost)}}</div></div>
            <div class="ig-item"><div class="ig-label">总成本</div><div class="ig-value big neg">¥{{fmt(detail.data.total_cost)}}</div></div>
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">涂料用量与利用率</div>
          <el-table :data="detail.data.items||[]" border size="small">
            <el-table-column prop="item_name" label="物料" min-width="140"/>
            <el-table-column label="理论用量" width="100"><template #default="{row}">{{fmt(detail.data.theoretical_qty||row.theoretical_qty)}}</template></el-table-column>
            <el-table-column label="实际用量" width="100"><template #default="{row}">{{fmt(row.actual_qty)}}</template></el-table-column>
            <el-table-column label="退料" width="80"><template #default="{row}">{{fmt(row.return_qty)}}</template></el-table-column>
            <el-table-column label="利用率" width="100"><template #default="{row}"><span :style="'color:'+(row.utilization_rate>=70?'#10b981':'#ef4444')+';font-weight:600'">{{row.utilization_rate||0}}%</span></template></el-table-column>
          </el-table>
        </div>

        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='DRAFT'" type="success" @click="confirm(detail.data)">确认完工</el-button>
            <el-button type="warning" plain @click="printCP(detail.data)"><span v-html="Icon.icon('printer',14)" style="vertical-align:middle;margin-right:4px"></span>打印质检单</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ status: '', keyword: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const wos = ref([]); const paints = ref([]);
    const form = reactive({ work_order_id: null, finished_qty: 0, qualified_qty: 0, rework_qty: 0, scrap_qty: 0, labor_hours: 0, labor_cost: 0, overhead_cost: 0, items: [] });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    const cpFlowClass = (row, s) => {
      const idx = { DRAFT: 0, CONFIRMED: 1 }[row.status];
      if (idx == null) return '';
      if (s.idx < idx) return 'done';
      if (s.idx === idx) return 'current';
      return '';
    };
    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v)) }).toString();
        const r = await api.get('/api/completions?' + qs);
        rows.value = r.data; total.value = r.total;
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.status = ''; query.keyword = ''; search(); }
    async function openCreate() {
      try {
        const r1 = await api.get('/api/work-orders?status=RELEASED'); wos.value = r1.data;
        const r2 = await api.get('/api/inventory/items?category=PAINT_POWDER'); paints.value = r2.data;
      } catch {}
      Object.assign(form, { work_order_id: null, finished_qty: 100, qualified_qty: 95, rework_qty: 3, scrap_qty: 2, labor_hours: 8, labor_cost: 400, overhead_cost: 200, items: [{ item_id: null, theoretical_qty: 15, actual_qty: 18, return_qty: 2 }] });
      dialog.visible = true;
    }
    async function submit() {
      try { await api.post('/api/completions', form); ElMessage.success('完工单已录入(草稿)'); dialog.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    async function confirm(row) {
      try {
        await ElMessageBox.confirm(`确认完工 ${row.completion_no}?将触发业财联动`, '提示', { type: 'warning' });
        await api.post('/api/completions/' + row.id + '/confirm', {});
        ElMessage.success('完工已确认,业财联动完成');
        if (detail.visible) detail.data = { ...detail.data, status: 'CONFIRMED' };
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    function printCP(row) {
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>完工质检单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:18px;margin-bottom:2px;letter-spacing:2px}
        .sub{text-align:center;font-size:14px;color:#333;margin-bottom:10px;font-weight:600}
        .info{font-size:13px;margin-bottom:8px;border:1px solid #ccc;padding:8px 12px;background:#fafafa}
        .info span{margin-right:24px}
        table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
        th,td{border:1px solid #333;padding:5px 8px;text-align:center}
        th{background:#f0f0f0;font-weight:600}
        .qty{display:flex;justify-content:space-around;margin:10px 0;font-size:14px;font-weight:600}
        .qty span{padding:6px 16px;border-radius:4px}
        .qty .ok{background:#d1fae5;color:#065f46}
        .qty .re{background:#fef3c7;color:#92400e}
        .qty .bad{background:#fce4ec;color:#c62828}
        .sign{margin-top:30px;display:flex;justify-content:space-between;font-size:12px}
        .sign .line{display:inline-block;width:100px;border-bottom:1px solid #333;margin-top:24px}
        @media print{body{margin:0;padding:12mm}@page{margin:8mm}}
      </style></head><body>
        <h1>东莞市峰业精密机械有限公司</h1>
        <div class="sub">完工质检单</div>
        <div class="info"><span>加工单号：${row.work_order_no||''}</span><span>完工单号：${row.completion_no||''}</span><span>日期：${new Date().toLocaleDateString('zh-CN')}</span></div>
        <div class="qty">
          <span class="ok">✅ 合格：${fmt(row.qualified_qty)}</span>
          <span class="re">🔁 返工：${fmt(row.rework_qty)}</span>
          <span class="bad">❌ 废品：${fmt(row.scrap_qty)}</span>
        </div>
        <div style="text-align:center;font-size:13px;margin:6px 0">完工总数：${fmt(row.finished_qty)}   |   工时：${row.labor_hours||0}h   |   总成本：¥${fmt(row.total_cost)}</div>
        <table><thead><tr><th>物料名称</th><th>理论用量</th><th>实际用量</th><th>退料</th><th>利用率</th><th>成本</th></tr></thead>
        <tbody>${(row.items||[]).map(it=>'<tr><td>'+(it.item_name||'')+'</td><td>'+fmt(it.theoretical_qty||0)+'</td><td>'+fmt(it.actual_qty||0)+'</td><td>'+fmt(it.return_qty||0)+'</td><td>'+(it.utilization_rate||0)+'%</td><td>¥'+fmt(it.cost_amount||0)+'</td></tr>').join('')||'<tr><td colspan="6" style="text-align:center;color:#999">无物料明细</td></tr>'}</tbody></table>
        <div class="sign"><div>质检员：<span class="line"></span></div><div>生产厂长：<span class="line"></span></div><div>运营确认：<span class="line"></span></div></div>
        <script>window.onload=function(){setTimeout(()=>{window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, total, page, loading, query, dialog, detail, wos, paints, form, CP_STATUS, CP_FLOW, fmt, fmtDate, cpFlowClass, load, search, reset, openCreate, submit, openDetail, confirm, printCP, Icon };
  }
};

// ============ 领料 ============
const RQ_STATUS = { PENDING: '待出库', CONFIRMED: '已出库', REJECTED: '已拒领' };

const RequisitionsPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('package-export',22)"></div>
        <div>
          <div class="ph-title">领料单</div>
          <div class="ph-sub">系统按加工单自动生成 · 仓库确认出库</div>
        </div>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in RQ_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.req_no}}</span>
            <span class="pill" :class="row.status">{{RQ_STATUS[row.status]||row.status}}</span>
            <span class="doc-cust">加工单ID: {{row.work_order_id}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field" style="grid-column:1/-1"><span class="df-label">物料明细</span>
              <span class="df-value" style="white-space:normal">
                <span v-for="it in (row.items||[])" :key="it.item_id" class="pill" style="margin-right:6px;background:rgba(0,212,255,.1);color:var(--primary)">{{it.item_name}} ×{{it.qty}}{{it.unit}}</span>
              </span>
            </div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='PENDING'" size="small" type="success" @click="act(row,'/api/requisitions/'+row.id+'/confirm','确认出库')">确认出库</el-button>
          <el-button v-if="row.status==='PENDING'" size="small" type="danger" @click="act(row,'/api/requisitions/'+row.id+'/reject','拒领')">拒领</el-button>
          <el-button size="small" type="warning" @click="printRQ(row)"><span v-html="Icon.icon('printer',14)" style="vertical-align:middle;margin-right:4px"></span>打印</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无领料单</div>
        <div class="de-desc">领料单由加工单自动派生</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-drawer v-model="detail.visible" title="领料单详情" size="560px">
      <template v-if="detail.data.id">
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.req_no}}</span>
            <span class="pill" :class="detail.data.status">{{RQ_STATUS[detail.data.status]||detail.data.status}}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">加工单ID: {{detail.data.work_order_id}}</div>
        </div>
        <div class="detail-section">
          <div class="ds-title">物料明细</div>
          <el-table :data="detail.data.items||[]" border size="small">
            <el-table-column type="index" label="#" width="44"/>
            <el-table-column prop="item_name" label="物料" min-width="160"/>
            <el-table-column prop="spec" label="规格" width="120"/>
            <el-table-column label="数量" width="80"><template #default="{row}">{{row.qty}}{{row.unit}}</template></el-table-column>
          </el-table>
        </div>
        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='PENDING'" type="success" @click="act(detail.data,'/api/requisitions/'+detail.data.id+'/confirm','确认出库')">确认出库</el-button>
            <el-button v-if="detail.data.status==='PENDING'" type="danger" @click="act(detail.data,'/api/requisitions/'+detail.data.id+'/reject','拒领')">拒领</el-button>
            <el-button type="warning" plain @click="printRQ(detail.data)"><span v-html="Icon.icon('printer',14)" style="vertical-align:middle;margin-right:4px"></span>打印</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ status: '' });
    const detail = reactive({ visible: false, data: {} });
    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v)) }).toString();
        const r = await api.get('/api/requisitions?' + qs);
        rows.value = r.data; total.value = r.total;
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.status = ''; search(); }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    async function act(row, url, label) {
      try {
        await ElMessageBox.confirm(`确认${label}?`, '提示', { type: 'warning' });
        await api.post(url, {});
        ElMessage.success(label + '成功');
        if (detail.visible) detail.data = { ...detail.data, status: label === '确认出库' ? 'CONFIRMED' : 'REJECTED' };
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    function printRQ(row) {
      const itemsHtml = (row.items||[]).map((it,i) =>
        `<tr><td>${i+1}</td><td>${it.item_name||''}</td><td>${it.spec||''}</td><td style="text-align:right">${it.qty}</td><td>${it.unit||''}</td><td></td></tr>`
      ).join('');
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>领料单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:18px;margin-bottom:2px;letter-spacing:2px}
        .sub{text-align:center;font-size:14px;color:#333;margin-bottom:10px;font-weight:600}
        .info{font-size:13px;margin-bottom:8px;border:1px solid #ccc;padding:8px 12px;background:#fafafa}
        .info span{margin-right:24px}
        table{width:100%;border-collapse:collapse;font-size:12px}
        th,td{border:1px solid #333;padding:5px 8px;text-align:left}
        th{background:#f0f0f0}
        .sign{margin-top:30px;display:flex;justify-content:space-between;font-size:12px}
        .sign .line{display:inline-block;width:100px;border-bottom:1px solid #333;margin-top:24px}
        @media print{body{margin:0;padding:12mm}@page{margin:8mm}}
      </style></head><body>
        <h1>东莞市峰业精密机械有限公司</h1>
        <div class="sub">领 料 单</div>
        <div class="info"><span>领料单号：${row.req_no||''}</span><span>加工单ID：${row.work_order_id||''}</span><span>日期：${new Date().toLocaleDateString('zh-CN')}</span></div>
        <table><thead><tr><th style="width:36px">序号</th><th>物料名称</th><th>规格</th><th style="width:70px">数量</th><th style="width:50px">单位</th><th>备注</th></tr></thead>
        <tbody>${itemsHtml||'<tr><td colspan="6" style="text-align:center;color:#999">无明细</td></tr>'}</tbody></table>
        <div class="sign"><div>领料人：<span class="line"></span></div><div>仓管员：<span class="line"></span></div><div>日期：<span class="line"></span></div></div>
        <script>window.onload=function(){setTimeout(()=>{window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, total, page, loading, query, detail, RQ_STATUS, load, search, reset, openDetail, act, printRQ, Icon };
  }
};

// ============ 库存 ============
const INV_CAT = { PAINT_POWDER: '涂料粉末', CONSUMABLE: '耗材', RAW_MATERIAL: '原材料', FINISHED_GOOD: '成品' };

const InventoryPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('package',22)"></div>
        <div>
          <div class="ph-title">物料库存</div>
          <div class="ph-sub">涂料·耗材·原料·成品 · 安全库存预警</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新增物料</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.category" placeholder="全部分类" style="width:160px" clearable @change="search">
        <el-option v-for="(l,v) in INV_CAT" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="物料名/编码" style="width:220px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)"></span></template>
      </el-input>
      <el-button @click="search">查询</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div :class="'doc-bar '+(row.stock_qty<row.safety_qty?'OVERDUE':'COMPLETED')"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.code}}</span>
            <span class="pill warn">{{INV_CAT[row.category]||row.category}}</span>
            <span class="doc-cust">{{row.name}}</span>
            <span class="doc-amount" v-if="row.unit_cost">¥{{fmt(row.stock_qty*row.unit_cost)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">规格</span><span class="df-value">{{row.spec||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">库存量</span><span class="df-value" :class="row.stock_qty<row.safety_qty?'neg':'pos'">{{row.stock_qty}}{{row.unit}}</span></div>
            <div class="doc-field"><span class="df-label">安全库存</span><span class="df-value">{{row.safety_qty}}{{row.unit}}</span></div>
            <div class="doc-field"><span class="df-label">单价</span><span class="df-value">¥{{fmt(row.unit_cost)}}</span></div>
            <div class="doc-field"><span class="df-label">库位</span><span class="df-value">{{row.location||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">状态</span><span class="df-value" :class="row.stock_qty<row.safety_qty?'neg':''">{{row.stock_qty<row.safety_qty?'库存不足':'充足'}}</span></div>
          </div>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无物料</div>
        <div class="de-desc">点击右上方按钮新增第一条物料</div>
      </div>
    </div>

    <el-dialog v-model="dialog.visible" title="新增物料" width="560px">
      <el-form :model="form" label-width="90px">
        <div class="form-grid">
          <el-form-item label="编码"><el-input v-model="form.code" style="width:200px"/></el-form-item>
          <el-form-item label="名称"><el-input v-model="form.name" style="width:240px"/></el-form-item>
          <el-form-item label="规格"><el-input v-model="form.spec" style="width:200px"/></el-form-item>
          <el-form-item label="单位"><el-input v-model="form.unit" style="width:100px"/></el-form-item>
          <el-form-item label="分类"><el-select v-model="form.category" style="width:180px"><el-option v-for="(l,v) in INV_CAT" :key="v" :label="l" :value="v"/></el-select></el-form-item>
          <el-form-item label="库位"><el-input v-model="form.location" style="width:160px"/></el-form-item>
          <el-form-item label="期初库存"><el-input-number v-model="form.stock_qty" :min="0" style="width:180px"/></el-form-item>
          <el-form-item label="安全库存"><el-input-number v-model="form.safety_qty" :min="0" style="width:180px"/></el-form-item>
          <el-form-item label="单价"><el-input-number v-model="form.unit_cost" :min="0" :precision="4" style="width:180px"/></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">保存</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false);
    const query = reactive({ category: '', keyword: '' });
    const dialog = reactive({ visible: false });
    const form = reactive({ code: '', name: '', spec: '', unit: 'kg', category: 'PAINT_POWDER', stock_qty: 0, safety_qty: 0, unit_cost: 0, location: '' });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    async function load() {
      loading.value = true;
      try { const r = await api.get('/api/inventory/items?' + new URLSearchParams(Object.fromEntries(Object.entries(query).filter(([_, v]) => v))).toString()); rows.value = r.data; }
      catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { load(); }
    function openCreate() { Object.assign(form, { code: '', name: '', spec: '', unit: 'kg', category: 'PAINT_POWDER', stock_qty: 0, safety_qty: 0, unit_cost: 0, location: '' }); dialog.visible = true; }
    async function submit() { try { await api.post('/api/inventory/items', form); ElMessage.success('已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    onMounted(load);
    return { rows, loading, query, dialog, form, INV_CAT, fmt, load, search, openCreate, submit, Icon };
  }
};

// ============ 财务 ============
const FIN_TYPE = { RECEIVABLE: '应收', PAYABLE: '应付', RECEIPT: '收款', PAYMENT: '付款' };
const FIN_STATUS = { DRAFT: '草稿', OPEN: '未核销', SETTLED: '已结算', CANCELLED: '已取消' };

const FinancePage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('banknote',22)"></div>
        <div>
          <div class="ph-title">财务单据</div>
          <div class="ph-sub">应收/应付/收款/付款 · 双公司主体核算</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="success" @click="openRcpt"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>登记收款</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.doc_type" placeholder="全部类型" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in FIN_TYPE" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in FIN_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.doc_no}}</span>
            <span class="pill" :class="row.status">{{FIN_STATUS[row.status]||row.status}}</span>
            <span class="pill" :class="row.doc_type==='RECEIVABLE'||row.doc_type==='RECEIPT'?'EFFECTIVE':'CLOSED'" style="background:rgba(0,212,255,.15);color:var(--primary)">{{FIN_TYPE[row.doc_type]||row.doc_type}}</span>
            <span class="doc-cust" v-if="row.counterparty_name">{{row.counterparty_name}}</span>
            <span class="doc-amount">{{row.doc_type==='PAYABLE'||row.doc_type==='PAYMENT'?'-':''}}¥{{fmt(row.amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">已结算</span><span class="df-value pos">¥{{fmt(row.settled_amount)}}</span></div>
            <div class="doc-field"><span class="df-label">未核销</span><span class="df-value" :class="(row.amount-row.settled_amount)>0?'neg':''">¥{{fmt(row.amount-row.settled_amount)}}</span></div>
            <div class="doc-field"><span class="df-label">来源事件</span><span class="df-value">{{row.source_event||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">记账日</span><span class="df-value">{{fmtDateShort(row.account_date)}}</span></div>
          </div>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无财务单据</div>
        <div class="de-desc">订单生效/完工确认/采购入库等事件自动生成单据</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="rcpt.visible" title="登记收款" width="500px">
      <el-form :model="rcpt.form" label-width="100px">
        <el-form-item label="订单">
          <el-select v-model="rcpt.form.order_id" filterable placeholder="选择订单" style="width:320px">
            <el-option v-for="o in orders" :key="o.id" :label="o.order_no+' '+o.customer_name+' (¥'+o.total_amount+')'" :value="o.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="收款金额"><el-input-number v-model="rcpt.form.amount" :min="0" :precision="2" style="width:220px"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="rcpt.visible=false">取消</el-button><el-button type="primary" @click="submitRcpt">确认收款</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ doc_type: '', status: '' });
    const rcpt = reactive({ visible: false, form: { order_id: null, amount: 0 } });
    const orders = ref([]);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    async function load() {
      loading.value = true;
      try { const r = await api.get('/api/finance/docs?' + new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v)) }).toString()); rows.value = r.data; total.value = r.total; }
      catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.doc_type = ''; query.status = ''; search(); }
    async function openRcpt() {
      try { const r = await api.get('/api/orders?status=EFFECTIVE'); orders.value = r.data; } catch {}
      rcpt.form = { order_id: null, amount: 0 }; rcpt.visible = true;
    }
    async function submitRcpt() {
      try { await api.post('/api/finance/receipts', rcpt.form); ElMessage.success('收款已登记,应收已核销'); rcpt.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, total, page, loading, query, rcpt, orders, FIN_TYPE, FIN_STATUS, fmt, fmtDateShort, load, search, reset, openRcpt, submitRcpt, Icon };
  }
};

// ============ 采购 ============
const PO_STATUS = { DRAFT: '草稿', ORDERED: '已下单', RECEIVED: '已入库', CANCELLED: '已取消' };
const PO_FLOW = [
  { key: 'DRAFT', label: '草稿', idx: 0 },
  { key: 'ORDERED', label: '下单', idx: 1 },
  { key: 'RECEIVED', label: '入库', idx: 2 },
];

const PurchasesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('shopping-cart',22)"></div>
        <div>
          <div class="ph-title">采购单</div>
          <div class="ph-sub">下单→入库 · 自动生成应付与库存流水</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建采购单</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in PO_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.po_no}}</span>
            <span class="pill" :class="row.status">{{PO_STATUS[row.status]||row.status}}</span>
            <span class="doc-cust" v-if="row.supplier_name">{{row.supplier_name}}</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">明细数</span><span class="df-value">{{(row.items||[]).length}}项</span></div>
            <div class="doc-field"><span class="df-label">创建时间</span><span class="df-value">{{fmtDateShort(row.created_at)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='DRAFT'" size="small" type="primary" @click="act(row,'/api/purchases/'+row.id+'/order','下单')">下单</el-button>
          <el-button v-if="row.status==='ORDERED'" size="small" type="success" @click="act(row,'/api/purchases/'+row.id+'/receive','入库')">入库</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无采购单</div>
        <div class="de-desc">从采购申请转化或直接新建</div>
      </div>
    </div>

    <el-dialog v-model="dialog.visible" title="新建采购单" width="720px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="供应商"><el-select v-model="form.supplier_id" filterable style="width:320px"><el-option v-for="s in sups" :key="s.id" :label="s.name" :value="s.id"/></el-select></el-form-item>
        <el-divider>采购明细</el-divider>
        <div v-for="(it,i) in form.items" :key="i" class="item-row">
          <el-select v-model="it.item_id" placeholder="物料" filterable style="width:200px"><el-option v-for="m in items" :key="m.id" :label="m.name" :value="m.id"/></el-select>
          <el-input v-model="it.item_name" placeholder="物料名" style="width:140px" v-if="!it.item_id"/>
          <el-input-number v-model="it.qty" :min="0" placeholder="数量" style="width:120px"/>
          <el-input v-model="it.unit" placeholder="单位" style="width:70px"/>
          <el-input-number v-model="it.unit_price" :min="0" :precision="2" placeholder="单价" style="width:120px"/>
          <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
        </div>
        <el-button size="small" @click="form.items.push({item_id:null,item_name:'',qty:1,unit:'kg',unit_price:0})"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加明细</el-button>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit">创建</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="采购单详情" size="580px">
      <template v-if="detail.data.id">
        <div class="flow-steps">
          <div v-for="(s,i) in PO_FLOW" :key="s.key" :class="['flow-step', poFlowClass(detail.data, s)]">
            <div class="fs-node">{{i+1}}</div>
            <div class="fs-label">{{s.label}}</div>
          </div>
        </div>
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.po_no}}</span>
            <span class="pill" :class="detail.data.status">{{PO_STATUS[detail.data.status]||detail.data.status}}</span>
            <span class="dh-amount">¥{{fmt(detail.data.total_amount)}}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">供应商: {{detail.data.supplier_name||'-'}}</div>
        </div>
        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='DRAFT'" type="primary" @click="act(detail.data,'/api/purchases/'+detail.data.id+'/order','下单')">下单</el-button>
            <el-button v-if="detail.data.status==='ORDERED'" type="success" @click="act(detail.data,'/api/purchases/'+detail.data.id+'/receive','入库')">入库</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false);
    const query = reactive({ status: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const sups = ref([]); const items = ref([]);
    const form = reactive({ supplier_id: null, items: [{ item_id: null, item_name: '', qty: 1, unit: 'kg', unit_price: 0 }] });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    const poFlowClass = (row, s) => {
      const idx = { DRAFT: 0, ORDERED: 1, RECEIVED: 2, CANCELLED: 0 }[row.status];
      if (idx == null) return '';
      if (s.idx < idx) return 'done';
      if (s.idx === idx) return 'current';
      return '';
    };
    async function load() { loading.value = true; try { const r = await api.get('/api/purchases?' + new URLSearchParams(Object.fromEntries(Object.entries(query).filter(([_, v]) => v))).toString()); rows.value = r.data; } catch (e) { ElMessage.error(e.message); } loading.value = false; }
    function search() { load(); }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    async function openCreate() {
      try { const r1 = await api.get('/api/purchases/suppliers'); sups.value = r1.data; const r2 = await api.get('/api/inventory/items'); items.value = r2.data; } catch {}
      Object.assign(form, { supplier_id: null, items: [{ item_id: null, item_name: '', qty: 1, unit: 'kg', unit_price: 0 }] });
      dialog.visible = true;
    }
    async function submit() { try { await api.post('/api/purchases', form); ElMessage.success('采购单已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    async function act(row, url, label) {
      try { await ElMessageBox.confirm(`确认${label}?`, '提示', { type: 'warning' }); await api.post(url, {}); ElMessage.success(label + '成功'); if (detail.visible) detail.data = { ...detail.data, status: label === '下单' ? 'ORDERED' : 'RECEIVED' }; load(); }
      catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, loading, query, dialog, detail, sups, items, form, PO_STATUS, PO_FLOW, fmt, fmtDateShort, poFlowClass, load, search, openDetail, openCreate, submit, act, Icon };
  }
};

// ============ 采购申请 ============
const PR_STATUS = { DRAFT: '草稿', SUBMITTED: '审批中', APPROVED: '已批准', REJECTED: '已驳回' };

const PRPage = {
  components: { FlowMini },
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('file-text',22)"></div>
        <div>
          <div class="ph-title">采购申请</div>
          <div class="ph-sub">提交后自动启动审批流 · 5000元以上需总经理审批</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>采购申请</el-button>
      </div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.req_no}}</span>
            <span class="pill" :class="row.status">{{PR_STATUS[row.status]||row.status}}</span>
            <span class="pill warn" v-if="row.total_amount>=5000">大额</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field" style="grid-column:1/-1"><span class="df-label">事由</span><span class="df-value">{{row.reason||'-'}}</span></div>
            <div class="doc-field" style="grid-column:1/-1"><span class="df-label">明细</span>
              <span class="df-value" style="white-space:normal">
                <span v-for="it in (row.items||[])" :key="it.name" class="pill" style="margin-right:6px;background:rgba(0,212,255,.1);color:var(--primary)">{{it.name}} ×{{it.qty}}{{it.unit}}</span>
              </span>
            </div>
          </div>
          <flow-mini biz-type="PURCHASE_REQUEST" :biz-id="row.id"/>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='DRAFT'" size="small" type="primary" @click="submit(row)">提交审批</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无采购申请</div>
        <div class="de-desc">点击右上方按钮发起第一条采购申请</div>
      </div>
    </div>

    <el-dialog v-model="dialog.visible" title="采购申请" width="620px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="事由"><el-input v-model="form.reason" style="width:400px"/></el-form-item>
        <el-divider>采购明细</el-divider>
        <div v-for="(it,i) in form.items" :key="i" class="item-row">
          <el-input v-model="it.name" placeholder="物料名" style="width:160px"/>
          <el-input-number v-model="it.qty" :min="0" style="width:110px"/>
          <el-input v-model="it.unit" placeholder="单位" style="width:70px"/>
          <el-input-number v-model="it.est_price" :min="0" :precision="2" placeholder="估价" style="width:130px"/>
          <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
        </div>
        <el-button size="small" @click="form.items.push({name:'',qty:1,unit:'kg',est_price:0})"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加</el-button>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="create">创建申请</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false); const dialog = reactive({ visible: false });
    const form = reactive({ reason: '', items: [{ name: '', qty: 1, unit: 'kg', est_price: 0 }] });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    async function load() { loading.value = true; try { const r = await api.get('/api/purchase-requests'); rows.value = r.data; } catch (e) { ElMessage.error(e.message); } loading.value = false; }
    function openCreate() { Object.assign(form, { reason: '', items: [{ name: '', qty: 1, unit: 'kg', est_price: 0 }] }); dialog.visible = true; }
    async function create() { try { await api.post('/api/purchase-requests', form); ElMessage.success('申请已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    async function submit(row) { try { await api.post('/api/purchase-requests/' + row.id + '/submit', {}); ElMessage.success('已提交审批'); load(); } catch (e) { ElMessage.error(e.message); } }
    onMounted(load);
    return { rows, loading, dialog, form, PR_STATUS, fmt, load, openCreate, create, submit, Icon };
  }
};

// ============ 工资 ============
const PR_STATUS_PAY = { DRAFT: '草稿', CONFIRMED: '已发放' };
const PAY_FLOW = [
  { key: 'DRAFT', label: '草稿', idx: 0 },
  { key: 'CONFIRMED', label: '发放', idx: 1 },
];

const PayrollPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('wallet',22)"></div>
        <div>
          <div class="ph-title">工资发放</div>
          <div class="ph-sub">确认后自动生成付款单 · 双公司主体</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建工资单</el-button>
      </div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.run_no}}</span>
            <span class="pill" :class="row.status">{{PR_STATUS_PAY[row.status]||row.status}}</span>
            <span class="pill warn">{{row.period}}</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">人数</span><span class="df-value">{{row.item_count||0}}人</span></div>
            <div class="doc-field"><span class="df-label">人均</span><span class="df-value">¥{{fmt(row.item_count?row.total_amount/row.item_count:0)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button v-if="row.status==='DRAFT'" size="small" type="success" @click="confirm(row)">确认发放</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无工资单</div>
        <div class="de-desc">按月创建工资单,确认后自动生成付款单</div>
      </div>
    </div>

    <el-dialog v-model="dialog.visible" title="新建工资单" width="720px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="期间"><el-input v-model="form.period" placeholder="2026-07" style="width:160px"/></el-form-item>
        <el-divider>员工工资明细</el-divider>
        <div v-for="(it,i) in form.items" :key="i" class="item-row">
          <el-input v-model="it.employee_id" placeholder="员工ID" style="width:100px"/>
          <el-input v-model="it.name" placeholder="姓名" style="width:140px"/>
          <el-input v-model="it.position" placeholder="岗位" style="width:140px"/>
          <el-input-number v-model="it.amount" :min="0" :precision="2" style="width:150px"/>
          <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
        </div>
        <el-button size="small" @click="form.items.push({employee_id:'',name:'',position:'',amount:0})"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加员工</el-button>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="create">创建</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="工资单详情" size="560px">
      <template v-if="detail.data.id">
        <div class="flow-steps">
          <div v-for="(s,i) in PAY_FLOW" :key="s.key" :class="['flow-step', payFlowClass(detail.data, s)]">
            <div class="fs-node">{{i+1}}</div>
            <div class="fs-label">{{s.label}}</div>
          </div>
        </div>
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.run_no}}</span>
            <span class="pill" :class="detail.data.status">{{PR_STATUS_PAY[detail.data.status]||detail.data.status}}</span>
            <span class="pill warn">{{detail.data.period}}</span>
            <span class="dh-amount">¥{{fmt(detail.data.total_amount)}}</span>
          </div>
        </div>
        <div class="detail-section">
          <div class="ds-title">操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button v-if="detail.data.status==='DRAFT'" type="success" @click="confirm(detail.data)">确认发放</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false); const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const form = reactive({ period: '', items: [{ employee_id: '', name: '', position: '', amount: 0 }] });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const payFlowClass = (row, s) => {
      const idx = { DRAFT: 0, CONFIRMED: 1 }[row.status];
      if (idx == null) return '';
      if (s.idx < idx) return 'done';
      if (s.idx === idx) return 'current';
      return '';
    };
    async function load() { loading.value = true; try { const r = await api.get('/api/payroll'); rows.value = r.data; } catch (e) { ElMessage.error(e.message); } loading.value = false; }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    function openCreate() { Object.assign(form, { period: new Date().toISOString().slice(0, 7), items: [{ employee_id: '', name: '', position: '', amount: 0 }] }); dialog.visible = true; }
    async function create() { try { await api.post('/api/payroll', form); ElMessage.success('工资单已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    async function confirm(row) {
      try {
        await ElMessageBox.confirm('确认发放?将自动生成付款单', '提示', { type: 'warning' });
        await api.post('/api/payroll/' + row.id + '/confirm', {});
        ElMessage.success('已确认发放');
        if (detail.visible) detail.data = { ...detail.data, status: 'CONFIRMED' };
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, loading, dialog, detail, form, PR_STATUS_PAY, PAY_FLOW, fmt, payFlowClass, load, openDetail, openCreate, create, confirm, Icon };
  }
};

// ============ 审批 (真实工作流:FlowTrack可视化 + 转交催办) ============
const BIZ_LABEL = {PURCHASE_REQUEST:'采购申请',RECEIVING:'来货登记',COMPLETION:'完工单',EXPENSE:'费用报销',SALES_ADJUSTMENT:'调价申请'};
const ApprovalsPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('check-circle',22)"></div>
        <div>
          <div class="ph-title">待办审批</div>
          <div class="ph-sub">工作流引擎自动派发 · 流转轨迹可视化 · 支持转交催办</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button @click="load"><span v-html="Icon.icon('refresh',14)" style="vertical-align:middle;margin-right:4px"></span>刷新</el-button>
      </div>
    </div>

    <div class="stat-strip">
      <div class="ss-item"><div class="ss-label">待处理任务</div><div class="ss-value">{{rows.length}}</div></div>
      <div class="ss-item green"><div class="ss-label">涉及业务</div><div class="ss-value pos">{{bizTypeCount}}</div></div>
      <div class="ss-item orange"><div class="ss-label">审批节点</div><div class="ss-value">{{nodeCount}}</div></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div class="doc-bar PENDING"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.biz_no||('任务#'+row.id)}}</span>
            <span class="pill PENDING">{{bizLabel(row.biz_type)}}</span>
            <span class="doc-cust">{{row.biz_title||row.node_name||''}}</span>
            <span class="doc-amount" style="font-size:12px;color:var(--text2)">已停留 {{row.duration||'-'}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">当前节点</span><span class="df-value">{{row.node_name||'-'}} · 第{{row.node_seq}}步</span></div>
            <div class="doc-field"><span class="df-label">业务类型</span><span class="df-value">{{bizLabel(row.biz_type)}}</span></div>
            <div class="doc-field"><span class="df-label">创建时间</span><span class="df-value">{{fmtDate(row.created_at)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button size="small" type="success" @click="handle(row,'approve')">通过</el-button>
          <el-button size="small" type="danger" @click="handle(row,'reject')">拒绝</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('check-circle',56)"></div>
        <div class="de-title">暂无待办审批</div>
        <div class="de-desc">所有审批任务已处理完毕</div>
      </div>
    </div>

    <el-drawer v-model="detail.visible" title="审批任务详情" size="640px">
      <template v-if="detail.data.id">
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.biz_no||('任务#'+detail.data.id)}}</span>
            <span class="pill PENDING">{{bizLabel(detail.data.biz_type)}}</span>
          </div>
          <div class="dh-row" style="margin:0;color:var(--text2);font-size:12px">
            {{detail.data.biz_title||''}} · 节点: {{detail.data.node_name||'-'}} · 已停留 {{detail.data.duration||'-'}}
          </div>
        </div>
        <div class="detail-section">
          <div class="ds-title">流转轨迹</div>
          <flow-track v-if="detail.data.biz_id" :biz-type="detail.data.biz_type" :biz-id="detail.data.biz_id"/>
        </div>
        <div class="detail-section">
          <div class="ds-title">审批操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <el-button type="success" @click="handle(detail.data,'approve')">通过</el-button>
            <el-button type="danger" @click="handle(detail.data,'reject')">拒绝</el-button>
            <el-button @click="urge(detail.data)">催办</el-button>
            <el-button @click="transfer(detail.data)">转交</el-button>
          </div>
          <div class="muted tiny" style="margin-top:10px;line-height:1.7">流程节点可在「流程设计器」可视化拖拽编排,支持审批(approve)与流转(process)混合,表驱动可配</div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  components: { FlowTrack },
  setup() {
    const rows = ref([]); const loading = ref(false);
    const detail = reactive({ visible: false, data: {} });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    const bizLabel = t => BIZ_LABEL[t] || t || '-';
    const bizTypeCount = computed(() => new Set(rows.value.map(r => r.biz_type)).size);
    const nodeCount = computed(() => new Set(rows.value.map(r => r.node_name)).size);
    async function load() { loading.value = true; try { const r = await api.get('/api/approvals/tasks/pending'); rows.value = r.data || []; } catch (e) { ElMessage.error(e.message); } loading.value = false; }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    async function handle(row, action) {
      try {
        const { value } = await ElMessageBox.prompt('审批意见', action === 'approve' ? '通过' : '拒绝', { inputType: 'textarea' });
        await api.post('/api/approvals/tasks/' + row.id + '/handle', { action, comment: value });
        ElMessage.success('已处理'); detail.visible = false; load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    async function urge(row) {
      try { await api.post('/api/approvals/tasks/' + row.id + '/urge'); ElMessage.success('已催办'); }
      catch (e) { ElMessage.error(e.message); }
    }
    async function transfer(row) {
      try {
        const { value } = await ElMessageBox.prompt('输入目标用户ID', '转交任务', { inputType: 'number' });
        await api.post('/api/approvals/tasks/' + row.id + '/transfer', { to_user_id: Number(value) });
        ElMessage.success('已转交'); detail.visible = false; load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, loading, detail, bizTypeCount, nodeCount, bizLabel, fmtDate, load, openDetail, handle, urge, transfer, Icon };
  }
};

const FlowDesignPage = {
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
        <el-select v-model="curBizType" placeholder="选择业务类型" style="width:160px" @change="onBizTypeChange">
          <el-option v-for="o in bizTypes" :key="o.v" :label="o.l" :value="o.v"/>
        </el-select>
        <el-select v-model="loadedDefId" placeholder="加载已有流程" clearable style="width:260px" @change="onLoadDef" v-if="flowDefs.length">
          <el-option v-for="d in flowDefs" :key="d.id" :label="(bizTypes.find(b=>b.v===d.biz_type)?.l||d.biz_type) + ' - ' + d.name + ' v' + (d.version||1)" :value="d.id"/>
        </el-select>
        <el-button @click="openMgmt"><span v-html="Icon.icon('list',14)" style="vertical-align:middle;margin-right:4px"></span>管理</el-button>
        <el-button @click="doClear"><span v-html="Icon.icon('trash',14)" style="vertical-align:middle;margin-right:4px"></span>清空</el-button>
        <el-button v-if="loadedDefId" @click="doDelete"><span v-html="Icon.icon('close',14)" style="vertical-align:middle;margin-right:4px"></span>删除</el-button>
        <el-button @click="doSaveAs"><span v-html="Icon.icon('copy',14)" style="vertical-align:middle;margin-right:4px"></span>另存为</el-button>
        <el-button type="primary" @click="doSave"><span v-html="Icon.icon('save',14)" style="vertical-align:middle;margin-right:4px"></span>{{loadedDefId?'更新':'保存'}}</el-button>
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
      <div class="lf-container" ref="lfContainer">
        <div class="save-flash-overlay" v-if="saveFlash">
          <div class="save-flash-content">
            <div class="save-flash-icon">✓</div>
            <div class="save-flash-text">保存成功</div>
          </div>
        </div>
      </div>
    </div>
    <el-dialog v-model="dlg.vis" :title="dlg.isNew?'添加节点':'编辑节点'" width="460px" :close-on-click-modal="false">
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item label="节点类型">
          <el-tag :type="dlg.type==='approve'?'':(dlg.type==='flow'?'success':'warning')" effect="dark" style="margin-right:8px">
            {{typeMeta(dlg.type).label}}
          </el-tag>
          <span class="tiny muted">{{typeMeta(dlg.type).desc}}</span>
        </el-form-item>
        <el-form-item label="节点名称">
          <el-input v-model="dlg.name" placeholder="如:主管审批、财务入账" maxlength="20" show-word-limit/>
        </el-form-item>
        <el-form-item label="处理角色" v-if="dlg.type==='approve'">
          <el-select v-model="dlg.role" style="width:100%" @change="onRoleChange">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v"/>
          </el-select>
          <div class="tiny muted" style="margin-top:4px">选择后该角色将收到待办任务</div>
        </el-form-item>
        <el-form-item label="流转方式" v-if="dlg.type==='flow'">
          <el-select v-model="dlg.flowAction" style="width:100%">
            <el-option label="自动推进到下一节点" value="auto_advance"/>
            <el-option label="通知下一节点处理人" value="notify_next"/>
            <el-option label="记录日志后推进" value="log_and_advance"/>
          </el-select>
          <div class="tiny muted" style="margin-top:4px">流转节点由系统自动处理,无需人工干预</div>
        </el-form-item>
        <el-form-item label="条件表达式" v-if="dlg.type==='branch'">
          <el-input v-model="dlg.condition" type="textarea" :rows="2" placeholder="如: amount > 10000"/>
          <div class="tiny muted" style="margin-top:4px">条件为真走上方分支,为假走下方分支</div>
        </el-form-item>
        <el-form-item label="抄送角色" v-if="dlg.type==='cc'">
          <el-select v-model="dlg.ccRoles" multiple style="width:100%" placeholder="可多选抄送角色" @change="onCcRoleChange">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v"/>
          </el-select>
          <div class="tiny muted" style="margin-top:4px">选择后流程到达此节点时自动通知所选角色</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" type="danger" @click="delFromDlg" v-if="!dlg.isNew && dlg.type!=='start' && dlg.type!=='end'" style="float:left">删除节点</el-button>
        <el-button @click="dlg.vis=false">取消</el-button>
        <el-button type="primary" @click="saveDlg">确定</el-button>
      </template>
    </el-dialog>

    <!-- 工作流管理对话框 -->
    <el-dialog v-model="mgmtVis" title="流程管理" width="700px">
      <el-table :data="flowDefs" style="width:100%" size="small" max-height="400">
        <el-table-column prop="id" label="ID" width="60"/>
        <el-table-column label="业务类型" width="120">
          <template #default="s">
            {{bizTypes.find(b=>b.v===s.row.biz_type)?.l||s.row.biz_type}}
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="140"/>
        <el-table-column label="节点数" width="80">
          <template #default="s">{{(s.row.nodes||[]).length}}</template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="60"/>
        <el-table-column label="操作" width="100">
          <template #default="s">
            <el-button size="small" type="danger" plain @click="doMgmtDelete(s.row.id,s.row.name)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="mgmtVis=false">关闭</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ref, reactive, onMounted, onBeforeUnmount, nextTick, watch } = Vue;
    const lfContainer = ref(null);
    let lf = null;
    const curBizType = ref('core_production');
    const loadedDefId = ref(null);
    const flowDefs = ref([]);
    const saveFlash = ref(false);
    const mgmtVis = ref(false);
    const dlg = reactive({
      vis: false, isNew: true, id: null,
      type: 'approve', name: '', role: 'DEPARTMENT_HEAD',
      flowAction: 'auto_advance', condition: '', ccRoles: []
    });
    let dragType = '';
    var _lastClickNode = null;

    const bizTypes = [
      {v:'core_production',l:'核心生产流'},
      {v:'procurement',l:'采购审批流'},
      {v:'expense',l:'费用报销流'},
      {v:'price_adjust',l:'调价审批流'},
      {v:'RECEIVING',l:'来货登记流程'},
      {v:'COMPLETION',l:'完工单确认'},
      {v:'PURCHASE_REQUEST',l:'采购请求审批'},
      {v:'SALES_ADJUSTMENT',l:'调价申请审批'},
    ];
    const roles = [
      {v:'DEPARTMENT_HEAD',l:'部门主管'},
      {v:'FINANCE',l:'财务'},
      {v:'OPERATION',l:'运营助理'},
      {v:'MANAGER',l:'厂长'},
      {v:'GM',l:'总经理'},
      {v:'WAREHOUSE',l:'仓管'},
      {v:'SALES',l:'销售'},
    ];
    const palTypes = [
      {type:'start', label:'开始节点', icon:'play', desc:'流程起点', ntype:'circle', color:'#10b981'},
      {type:'approve', label:'审批节点', icon:'check', desc:'指定角色审批', ntype:'rect', color:'#8b5cf6'},
      {type:'flow', label:'流转节点', icon:'arrow-right', desc:'自动流转', ntype:'rect', color:'#06b6d4'},
      {type:'branch', label:'分支节点', icon:'fork', desc:'条件判断', ntype:'diamond', color:'#f59e0b'},
      {type:'cc', label:'抄送节点', icon:'mail', desc:'抄送给指定角色', ntype:'rect', color:'#6366f1'},
      {type:'end', label:'结束节点', icon:'stop', desc:'流程终点', ntype:'circle', color:'#ef4444'},
    ];

    function typeMeta(type) { return palTypes.find(p=>p.type===type) || palTypes[1]; }

    function initLF() {
      var LF = (window.Core && window.Core.LogicFlow) || window.LogicFlow;
      if (!LF) {
        var tries = (initLF._tries = (initLF._tries || 0) + 1);
        if (tries > 50) {
          console.error('[LogicFlow] 加载失败,重试超时');
          var c = lfContainer.value;
          if (c && !c.querySelector('.lf-error')) {
            var d = document.createElement('div');
            d.className = 'lf-error';
            d.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#ff6b6b;font-size:14px;background:rgba(15,23,42,0.9);z-index:10;';
            d.textContent = 'LogicFlow 库加载失败,请刷新页面或检查网络';
            c.appendChild(d);
          }
          return;
        }
        setTimeout(initLF, 200);
        return;
      }
      try {
        initLF._tries = 0;
        lf = new LF({
          container: lfContainer.value,
          grid: { size: 20, type: 'dot', config: { color: '#334155' } },
          background: { color: '#0f172a' },
          edgeType: 'bezier',
          keyboard: { enabled: true },
        });

        lf.setTheme({
          rect: { radius: 12, width: 160, height: 56, fill: '#8b5cf6', stroke: '#ffffff', strokeWidth: 2 },
          circle: { r: 32, fill: '#10b981', stroke: '#ffffff', strokeWidth: 2 },
          diamond: { fill: '#f59e0b', stroke: '#ffffff', strokeWidth: 2 },
          edge: { stroke: '#00d4ff', strokeWidth: 2.5 },
          anchor: { stroke: '#00d4ff', fill: '#0f172a', r: 5, hoverStroke: '#22d3ee', hoverFill: '#fff' },
          anchorLine: { stroke: '#00d4ff', strokeWidth: 2, strokeDasharray: '4 4' },
          text: { color: '#ffffff', fontSize: 14, fontWeight: 700 },
        });

        lf.on('node:dblclick', function(ev) {
          var nid = null;
          try {
            if (ev.data && ev.data.id) {
              nid = ev.data.id;
            }
            if (!nid && ev.e) {
              var container = lfContainer.value;
              if (container) {
                var rect = container.getBoundingClientRect();
                var tf = lf.getTransform ? lf.getTransform() : {scale:1,translateX:0,translateY:0};
                var s = tf.scale || 1, tx = tf.translateX || 0, ty = tf.translateY || 0;
                var lx = (ev.e.clientX - rect.left - tx) / s;
                var ly = (ev.e.clientY - rect.top - ty) / s;
                var data = lf.getGraphData();
                var best = null, bestDist = Infinity;
                for (var i = 0; i < data.nodes.length; i++) {
                  var n = data.nodes[i];
                  var d = Math.abs(n.x - lx) + Math.abs(n.y - ly);
                  if (d < bestDist) { bestDist = d; best = n.id; }
                }
                if (best && bestDist < 80) nid = best;
              }
            }
          } catch(e) { console.warn('node:dblclick err:', e); }
          if (nid) openEditByNodeId(nid);
        });

        document.addEventListener('keydown', onKey);

        // 右键菜单 - 节点
        lf.on('node:contextmenu', function(ev) {
          ev.e.preventDefault();
          var nodeId = ev.data?.id;
          if (!nodeId) return;
          showContextMenu(ev.e.clientX, ev.e.clientY, 'node', nodeId);
        });

        // 右键菜单 - 边
        lf.on('edge:contextmenu', function(ev) {
          ev.e.preventDefault();
          var edgeId = ev.data?.id;
          if (!edgeId) return;
          showContextMenu(ev.e.clientX, ev.e.clientY, 'edge', edgeId);
        });

        // 画布空白处右键 - 隐藏菜单
        lf.on('blank:contextmenu', function(ev) {
          ev.e.preventDefault();
          hideContextMenu();
        });

        lf.on('graph:rendered', function() {
          bindNodeClicks();
        });
        lf.render({ nodes: [], edges: [] });

        loadFlowDefs();
      } catch(e) {
        console.error('LogicFlow init error:', e);
      }
    }

    // 右键菜单
    var _contextMenu = null;
    function showContextMenu(x, y, type, targetId) {
      hideContextMenu();
      var menu = document.createElement('div');
      menu.className = 'wf-context-menu';
      menu.style.left = x + 'px';
      menu.style.top = y + 'px';

      var items = [];
      if (type === 'node') {
        items.push({ label: '编辑节点', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>', action: 'edit' });
        items.push({ label: '复制节点', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>', action: 'copy' });
        items.push({ divider: true });
        items.push({ label: '删除节点', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>', action: 'delete', danger: true });
      } else if (type === 'edge') {
        items.push({ label: '删除连线', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>', action: 'delete', danger: true });
      }

      items.forEach(function(item) {
        if (item.divider) {
          var div = document.createElement('div');
          div.className = 'wf-context-menu-divider';
          menu.appendChild(div);
        } else {
          var el = document.createElement('div');
          el.className = 'wf-context-menu-item' + (item.danger ? ' danger' : '');
          el.innerHTML = item.icon + '<span>' + item.label + '</span>';
          el.onclick = function() {
            handleContextAction(item.action, type, targetId);
            hideContextMenu();
          };
          menu.appendChild(el);
        }
      });

      document.body.appendChild(menu);

      // 调整位置防止超出屏幕
      var rect = menu.getBoundingClientRect();
      if (rect.right > window.innerWidth) {
        menu.style.left = (x - rect.width) + 'px';
      }
      if (rect.bottom > window.innerHeight) {
        menu.style.top = (y - rect.height) + 'px';
      }

      _contextMenu = menu;

      // 点击其他地方关闭
      setTimeout(function() {
        document.addEventListener('click', hideContextMenu, { once: true });
        document.addEventListener('contextmenu', function(e) {
          if (!menu.contains(e.target)) hideContextMenu();
        }, { once: true });
      }, 0);
    }

    function hideContextMenu() {
      if (_contextMenu) {
        _contextMenu.remove();
        _contextMenu = null;
      }
    }

    function handleContextAction(action, type, targetId) {
      if (!lf) return;
      if (action === 'edit' && type === 'node') {
        openEditByNodeId(targetId);
      } else if (action === 'copy' && type === 'node') {
        try {
          var model = lf.getNodeModelById(targetId);
          if (model) {
            var data = model.getData();
            var newX = data.x + 80;
            var newY = data.y + 40;
            var newNode = lf.addNode({
              type: model.type,
              x: newX, y: newY,
              text: (typeof model.text === 'string') ? model.text : (model.text?.value || ''),
              properties: model.properties,
              fill: model.style?.fill,
              stroke: model.style?.stroke,
              strokeWidth: model.style?.strokeWidth
            });
            if (newNode && newNode.id) {
              setTimeout(function() {
                try {
                  var nm = lf.getNodeModelById(newNode.id);
                  if (nm) nm.setProperties(model.properties);
                  colorizeNodeById(newNode.id);
                  bindNodeClicks();
                } catch(_) {}
              }, 50);
            }
          }
        } catch(e) { console.error('copy node error:', e); }
      } else if (action === 'delete') {
        ElementPlus.ElMessageBox.confirm(
          type === 'node' ? '确定删除此节点？相关连线也会被删除。' : '确定删除此连线？',
          '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
        ).then(function() {
          try {
            if (type === 'node') {
              lf.removeNode(targetId);
            } else {
              lf.removeEdge(targetId);
            }
            ElementPlus.ElMessage.success('已删除');
          } catch(e) {
            ElementPlus.ElMessage.error('删除失败');
          }
        }).catch(function() {});
      }
    }

    function openEditByNodeId(nodeId) {
      if (!nodeId || !lf) return;
      var model = lf.getNodeModelById(nodeId);
      if (!model) return;
      var props = model.properties || {};
      var rawType = props.bizNodeType;
      if (!rawType && model.type) {
        var t = model.type;
        if (t === 'circle') rawType = 'start';
        else if (t === 'diamond') rawType = 'branch';
        else rawType = 'approve';
      }
      if (!rawType) rawType = 'approve';
      var txt = (typeof model.text === 'string') ? model.text : ((model.text && model.text.value) || '');
      dlg.id = nodeId;
      dlg.type = rawType;
      dlg.name = txt;
      dlg.role = props.role || 'DEPARTMENT_HEAD';
      dlg.flowAction = props.flowAction || 'auto_advance';
      dlg.condition = props.condition || '';
      dlg.ccRoles = props.ccRoles || [];
      dlg.isNew = false;
      dlg.vis = true;
    }

    function bindNodeClicks() {
      if (!lfContainer.value || !lf) return;
      var container = lfContainer.value;
      if (container._nodeDblClickBound) return;
      container._nodeDblClickBound = true;
      container.addEventListener('dblclick', function(ev) {
        if (!lf) return;
        try {
          var rect = container.getBoundingClientRect();
          var tf = lf.getTransform ? lf.getTransform() : {scale:1,translateX:0,translateY:0};
          var s = tf.scale || 1, tx = tf.translateX || 0, ty = tf.translateY || 0;
          var lx = (ev.clientX - rect.left - tx) / s;
          var ly = (ev.clientY - rect.top - ty) / s;
          var data = lf.getGraphData();
          var best = null, bestDist = Infinity;
          for (var i = 0; i < data.nodes.length; i++) {
            var n = data.nodes[i];
            var d = Math.abs(n.x - lx) + Math.abs(n.y - ly);
            if (d < bestDist) { bestDist = d; best = n.id; }
          }
          if (best && bestDist < 80) {
            openEditByNodeId(best);
          }
        } catch(e) {}
      });
    }

    function addNode(type, name, x, y, role) {
      if (!lf) return null;
      const meta = typeMeta(type);
      const text = name || meta.label;
      const props = { bizNodeType: type, nodeColor: meta.color };
      if (role) props.role = role;
      if (type === 'flow') props.flowAction = 'auto_advance';
      if (type === 'branch') props.condition = '';
      if (type === 'cc' && Array.isArray(role)) props.ccRoles = role;
      const cfg = { type: meta.ntype, x: x, y: y, text: text, properties: props,
                   fill: meta.color, stroke: '#ffffff', strokeWidth: 2 };
      if (meta.ntype === 'rect') { cfg.width = 160; cfg.height = 56; }
      if (meta.ntype === 'circle') { cfg.r = 32; }
      if (meta.ntype === 'diamond') { cfg.fill = meta.color; }
      try {
        const result = lf.addNode(cfg);
        const id = (result && result.id) ? result.id : null;
        if (id) {
          setTimeout(function() {
            try {
              var nodeModel = lf.getNodeModelById(id);
              if (nodeModel) {
                nodeModel.setProperties(props);
              }
              colorizeNodeById(id);
              triggerNodeAddAnim(id);
              bindNodeClicks();
            } catch(_) {}
          }, 50);
        }
        return result;
      } catch(e) {
        console.error('addNode error:', e, cfg);
        return null;
      }
    }

    function applyNodeColorById(nodeId, color) {
      try {
        var container = lfContainer.value;
        if (!container || !lf) return;
        var model = lf.getNodeModelById(nodeId);
        if (!model) return;
        var mx = model.x, my = model.y;
        var all = container.querySelectorAll('.lf-node');
        var best = null, bestDist = Infinity;
        for (var i = 0; i < all.length; i++) {
          var el = all[i];
          var shape = el.querySelector('circle, rect, polygon, ellipse');
          if (!shape) continue;
          var tx = parseFloat(shape.getAttribute('cx')) || parseFloat(shape.getAttribute('x'));
          var ty = parseFloat(shape.getAttribute('cy')) || parseFloat(shape.getAttribute('y'));
          if (isNaN(tx) || isNaN(ty)) continue;
          var d = Math.abs(tx - mx) + Math.abs(ty - my);
          if (d < bestDist) { bestDist = d; best = el; }
        }
        if (!best || bestDist > 30) return;
        var targetShape = best.querySelector('rect, circle, polygon, ellipse');
        if (targetShape) {
          targetShape.setAttribute('fill', color);
          targetShape.setAttribute('stroke', '#ffffff');
          targetShape.setAttribute('stroke-width', '2');
        }
      } catch(_) {}
    }

    function colorizeNodeById(nodeId) {
      try {
        var m = lf.getNodeModelById(nodeId);
        if (!m) return;
        var props = m.properties || {};
        var type = props.bizNodeType || props.flowNodeType;
        if (!type) return;
        var meta = typeMeta(type);
        applyNodeColorById(nodeId, meta.color);
      } catch(_) {}
    }

    function triggerNodeAddAnim(nodeId) {
      try {
        var container = lfContainer.value;
        if (!container || !lf) return;
        var model = lf.getNodeModelById(nodeId);
        if (!model) return;
        var mx = model.x, my = model.y;
        var all = container.querySelectorAll('.lf-node');
        var best = null, bestDist = Infinity;
        for (var i = 0; i < all.length; i++) {
          var el = all[i];
          var shape = el.querySelector('circle, rect, polygon, ellipse');
          if (!shape) continue;
          var tx = parseFloat(shape.getAttribute('cx')) || parseFloat(shape.getAttribute('x'));
          var ty = parseFloat(shape.getAttribute('cy')) || parseFloat(shape.getAttribute('y'));
          if (isNaN(tx) || isNaN(ty)) continue;
          var d = Math.abs(tx - mx) + Math.abs(ty - my);
          if (d < bestDist) { bestDist = d; best = el; }
        }
        if (best && bestDist <= 30) {
          best.classList.add('lf-node-enter');
          setTimeout(function() { best.classList.remove('lf-node-enter'); }, 400);
        }
      } catch(_) {}
    }

    function onPalDragStart(e, type) {
      dragType = type;
      e.dataTransfer.effectAllowed = 'copy';
      try { e.dataTransfer.setData('text/plain', type); } catch(_) {}
    }

    function onCanvasDrop(e) {
      e.preventDefault();
      if (!dragType || !lf) return;
      let pos = null;
      try { pos = lf.getPointByClient(e.clientX, e.clientY); } catch(_) { pos = null; }
      let x, y;
      if (pos && typeof pos.x === 'number' && !isNaN(pos.x)) {
        x = pos.x; y = pos.y;
      } else {
        const rect = lfContainer.value.getBoundingClientRect();
        let s = 1, tx = 0, ty = 0;
        try {
          const t = lf.getTransform();
          if (t) { s = t.scale || 1; tx = t.translateX || 0; ty = t.translateY || 0; }
        } catch(_) {}
        x = (e.clientX - rect.left - tx) / s;
        y = (e.clientY - rect.top - ty) / s;
      }
      const meta = typeMeta(dragType);
      const isSE = dragType === 'start' || dragType === 'end';
      addNode(dragType, meta.label, x, y, isSE ? null : 'DEPARTMENT_HEAD');
      dragType = '';
    }

    function onRoleChange(roleCode) {
      const roleCN = {DEPARTMENT_HEAD:'部门主管',FINANCE:'财务',OPERATION:'运营助理',FACTORY_MANAGER:'厂长',MANAGER:'厂长',GM:'总经理',WAREHOUSE:'仓管',SALES:'销售'};
      if (!dlg.name || dlg.name === '审批' || dlg.name === '人工审批') {
        dlg.name = (roleCN[roleCode] || '') + '审批';
      }
    }
    function onCcRoleChange(roles) {
      if (!dlg.name || dlg.name === '抄送' || dlg.name === '抄送节点') {
        dlg.name = '抄送';
      }
    }

    function saveDlg() {
      if (!dlg.name) { ElementPlus.ElMessage.warning('请输入节点名称'); return; }
      if (dlg.type === 'approve' && !dlg.role) { ElementPlus.ElMessage.warning('请选择处理角色'); return; }
      if (dlg.type === 'cc' && (!dlg.ccRoles || !dlg.ccRoles.length)) { ElementPlus.ElMessage.warning('请选择至少一个抄送角色'); return; }
      if (dlg.isNew) {
        addNode(dlg.type, dlg.name, lfContainer.value.clientWidth / 2, lfContainer.value.clientHeight / 2, dlg.type === 'cc' ? dlg.ccRoles : dlg.role);
      } else {
        const model = lf.getNodeModelById(dlg.id);
        const oldType = model ? (model.properties && (model.properties.bizNodeType || model.properties.flowNodeType)) : null;
        if (oldType && oldType !== dlg.type) {
          const ox = (model && model.x) || (lfContainer.value.clientWidth / 2);
          const oy = (model && model.y) || (lfContainer.value.clientHeight / 2);
          try { lf.deleteNode(dlg.id); } catch(_) {}
          addNode(dlg.type, dlg.name, ox, oy, dlg.type === 'cc' ? dlg.ccRoles : dlg.role);
        } else {
          try {
            if (model) {
              var newProps = { bizNodeType: dlg.type };
              if (dlg.type === 'approve') newProps.role = dlg.role;
              if (dlg.type === 'flow') newProps.flowAction = dlg.flowAction;
              if (dlg.type === 'branch') newProps.condition = dlg.condition;
              if (dlg.type === 'cc') newProps.ccRoles = dlg.ccRoles;
              model.setProperties(newProps);
              try { lf.setNodeText(dlg.id, dlg.name); } catch(_) {}
              try { model.text = { value: dlg.name }; } catch(_) {}
              colorizeNodeById(dlg.id);
              triggerNodeAddAnim(dlg.id);
            }
          } catch(_) {}
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
        try { ElementPlus.ElMessageBox.close(); } catch(_) {}
        if (lf) { lf.clearData(); }
        loadedDefId.value = null;
      }).catch(()=>{});
    }

    async function loadFlowDefs() {
      try {
        const r = await api.get('/api/approvals/definitions');
        // 显示所有流程定义,不按业务类型过滤
        flowDefs.value = r.data || [];
      } catch(_) { flowDefs.value = []; }
    }

    function onBizTypeChange() {
      loadedDefId.value = null;
      loadFlowDefs();
    }

    function onLoadDef(defId, silent) {
      if (!defId) return;
      const def = flowDefs.value.find(d => d.id === defId);
      if (!def || !lf) return;

      const doLoad = function() {
        lf.clearData();
        let nodes = def.nodes || [];
        
        // 检测数据格式:如果节点的name字段是对象,说明是LogicFlow序列化格式
        const isLogicFlowFormat = nodes.length > 0 && typeof nodes[0].name === 'object' && nodes[0].name !== null;
        
        // 将LogicFlow格式转换为简单节点列表格式
        if (isLogicFlowFormat) {
          nodes = nodes.map(function(node) {
            // LogicFlow格式: name是对象 {x, y, value}
            let name = node.name;
            if (typeof name === 'object' && name !== null) {
              name = name.value || '';
            } else if (typeof name === 'string') {
              // 已是字符串,保持原样
            }
            // 过滤掉start和end节点(LogicFlow内置类型)
            if (node.type === 'start' || node.type === 'end') {
              return null;
            }
            return {
              seq: node.seq || 0,
              name: name || '',
              type: node.type === 'item' ? 'approve' : (node.type || 'approve'),
              approver_role: node.approver_role || '',
              flow_action: node.flow_action || '',
              condition: node.condition || '',
              cc_roles: node.cc_roles || [],
              _x: node._x,
              _y: node._y
            };
          }).filter(function(n) { return n !== null; });
        } else {
          // 简单节点列表格式:也过滤掉start和end类型的节点
          nodes = nodes.filter(function(node) {
            return node.type !== 'start' && node.type !== 'end';
          });
        }
        
        // 按seq排序确保顺序正确
        nodes = nodes.slice().sort(function(a, b) { return (a.seq || 0) - (b.seq || 0); });
        
        const startX = 120, startY = 250, gapX = 260, gapY = 120;
        let hasBranch = false;
        
        // 先检测是否有分支节点,并设置默认类型
        nodes.forEach(function(node) {
          // 向后兼容:如果没有type字段,根据approver_role判断,有role则为approve,否则为flow
          if (!node.type || node.type === 'start' || node.type === 'end' || node.type === 'item') {
            node.type = node.approver_role ? 'approve' : 'flow';
          }
          // 兼容老数据:process类型映射为flow
          if (node.type === 'process') node.type = 'flow';
          if (node.type === 'branch') hasBranch = true;
        });

        nodes.forEach(function(node, idx) {
          let type = node.type;
          if (!type) type = node.approver_role ? 'approve' : 'flow';
          
          const meta = typeMeta(type);
          // 分支节点放在下方，其他节点放在上方
          let x, y;
          if (type === 'branch') {
            x = startX + idx * gapX;
            y = startY + (hasBranch ? gapY : 0);
          } else {
            x = startX + idx * gapX;
            y = startY;
          }
          
          // 处理节点名称:如果是乱码(???),根据角色或类型生成默认名称
          let nodeName = node.name;
          const isGarbled = !nodeName || /^\?+$/.test(nodeName) || nodeName.length < 1;
          if (isGarbled) {
            const roleCN = {DEPARTMENT_HEAD:'部门主管',FINANCE:'财务',OPERATION:'运营助理',FACTORY_MANAGER:'厂长',MANAGER:'厂长',GM:'总经理',WAREHOUSE:'仓管',SALES:'销售'};
            if (type === 'approve' && node.approver_role) {
              nodeName = (roleCN[node.approver_role] || node.approver_role) + '审批';
            } else if (type === 'flow') {
              nodeName = '流转节点';
            } else if (type === 'branch') {
              nodeName = '分支节点';
            } else if (type === 'cc') {
              nodeName = '抄送节点';
            } else {
              nodeName = '节点' + (idx + 1);
            }
          }
          
          const props = { bizNodeType: type, nodeColor: meta.color };
          if (node.approver_role) props.role = node.approver_role;
          if (type === 'flow') props.flowAction = node.flow_action || 'auto_advance';
          if (type === 'branch') props.condition = node.condition || '';
          if (type === 'cc' && node.cc_roles) props.ccRoles = node.cc_roles;

          const cfg = { type: meta.ntype, x: x, y: y, text: nodeName, properties: props,
                       fill: meta.color, stroke: '#ffffff', strokeWidth: 2 };
          if (meta.ntype === 'rect') { cfg.width = 160; cfg.height = 56; }
          if (meta.ntype === 'circle') { cfg.r = 32; }
          if (meta.ntype === 'diamond') { cfg.width = 140; cfg.height = 80; }

          try {
            lf.addNode(cfg);
          } catch(e) { console.error('load node err:', e); }
        });

        setTimeout(function() {
          try {
            const allNodes = lf.getGraphData().nodes;
            if (allNodes.length >= 2) {
              for (var i = 0; i < allNodes.length - 1; i++) {
                try {
                  lf.addEdge({ sourceNodeId: allNodes[i].id, targetNodeId: allNodes[i+1].id, type: 'bezier' });
                } catch(_) {}
              }
            }
            allNodes.forEach(function(n) { colorizeNodeById(n.id); });
            bindNodeClicks();
          } catch(_) {}
        }, 300);

        if (!silent) ElementPlus.ElMessage.success('已加载: ' + def.name);
      };

      if (silent) {
        doLoad();
      } else {
        ElementPlus.ElMessageBox.confirm(
          '加载「' + def.name + '」？当前画布内容将被替换。',
          '加载流程', { type: 'warning' }
        ).then(() => {
          try { ElementPlus.ElMessageBox.close(); } catch(_) {}
          doLoad();
        }).catch(() => {});
      }
    }

    function playSaveAnimation() {
      saveFlash.value = true;
      setTimeout(function() { saveFlash.value = false; }, 1500);
    }

    function _buildNodeList() {
      const data = lf.getGraphData();
      const bizNodes = data.nodes.filter(n => {
        const props = n.properties || {};
        const bt = props.bizNodeType || props.flowNodeType || '';
        return bt && bt !== 'start' && bt !== 'end';
      });
      return bizNodes.map((n, idx) => {
        const props = n.properties || {};
        var nodeText = '';
        try {
          var model = lf.getNodeModelById(n.id);
          if (model) {
            var mt = model.text;
            nodeText = (typeof mt === 'string') ? mt : ((mt && mt.value) || '');
          }
        } catch(_) {}
        if (!nodeText) {
          var rawText = (typeof n.text === 'string') ? n.text : ((n.text && n.text.value) || '');
          if (rawText && typeof rawText === 'string') {
            try {
              var decoded = decodeURIComponent(escape(rawText));
              nodeText = decoded;
            } catch(_) {
              nodeText = rawText;
            }
          }
        }
        if (!nodeText) nodeText = '节点' + (idx + 1);
        const rawType = props.bizNodeType || props.flowNodeType || 'approve';
        const type = rawType === 'flow' ? 'process' : rawType;
        return {
          seq: idx + 1,
          name: nodeText,
          type: type,
          approver_role: props.role || '',
          flow_action: props.flowAction || '',
          condition: props.condition || null,
          cc_roles: props.ccRoles || [],
        };
      });
    }

    async function doSave() {
      if (!curBizType.value) { ElementPlus.ElMessage.warning('请选择业务类型'); return; }
      const data = lf.getGraphData();
      if (!data.nodes || !data.nodes.length) { ElementPlus.ElMessage.warning('请至少添加一个节点'); return; }

      const nodeList = _buildNodeList();
      const bizName = (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程';

      try {
        if (loadedDefId.value) {
          // 已有加载的流程 → 更新
          await api.put('/api/approvals/definitions/' + loadedDefId.value, {
            biz_type: curBizType.value,
            name: bizName,
            nodes: nodeList,
          });
        } else {
          // 新建
          const r = await api.post('/api/approvals/definitions', {
            biz_type: curBizType.value,
            name: bizName,
            nodes: nodeList,
          });
          loadedDefId.value = r.data && r.data.id;
        }
        playSaveAnimation();
        await loadFlowDefs();
        ElementPlus.ElMessage.success('保存成功');
      } catch(e) {
        console.error('保存流程失败:', e);
        ElementPlus.ElMessage.error(e.message || '保存失败');
      }
    }

    async function doSaveAs() {
      if (!curBizType.value) { ElementPlus.ElMessage.warning('请选择业务类型'); return; }
      const data = lf.getGraphData();
      if (!data.nodes || !data.nodes.length) { ElementPlus.ElMessage.warning('请至少添加一个节点'); return; }

      const nodeList = _buildNodeList();
      const bizName = (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程';

      try {
        const r = await api.post('/api/approvals/definitions', {
          biz_type: curBizType.value,
          name: bizName,
          nodes: nodeList,
        });
        loadedDefId.value = r.data && r.data.id;
        playSaveAnimation();
        await loadFlowDefs();
        ElementPlus.ElMessage.success('另存为成功');
      } catch(e) {
        console.error('另存为失败:', e);
        ElementPlus.ElMessage.error(e.message || '另存为失败');
      }
    }

    async function doDelete() {
      if (!loadedDefId.value) return;
      const def = flowDefs.value.find(d => d.id === loadedDefId.value);
      const name = def ? def.name : '该流程';
      ElementPlus.ElMessageBox.confirm(
        '确定删除「' + name + '」？删除后不可恢复。',
        '删除流程', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      ).then(async () => {
        try {
          await api.del('/api/approvals/definitions/' + loadedDefId.value);
          if (lf) lf.clearData();
          loadedDefId.value = null;
          await loadFlowDefs();
          ElementPlus.ElMessage.success('已删除');
        } catch(e) {
          ElementPlus.ElMessage.error(e.message || '删除失败');
        }
      }).catch(() => {});
    }

    function openMgmt() {
      mgmtVis.value = true;
    }

    async function doMgmtDelete(fid, fname) {
      ElementPlus.ElMessageBox.confirm(
        '确定删除「' + fname + '」？删除后不可恢复。',
        '删除流程', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      ).then(async () => {
        try {
          await api.del('/api/approvals/definitions/' + fid);
          if (loadedDefId.value === fid) {
            if (lf) lf.clearData();
            loadedDefId.value = null;
          }
          await loadFlowDefs();
          ElementPlus.ElMessage.success('已删除');
        } catch(e) {
          ElementPlus.ElMessage.error(e.message || '删除失败');
        }
      }).catch(() => {});
    }

    onMounted(() => {
      nextTick(() => {
        const container = lfContainer.value;
        if (container) {
          container.addEventListener('drop', onCanvasDrop);
          container.addEventListener('dragover', e => e.preventDefault());
          container.addEventListener('dragleave', e => e.preventDefault());
          // 双击兜底,确保双击节点能触发编辑
          container.addEventListener('dblclick', function(ev) {
            if (!lf) return;
            try {
              var rect = container.getBoundingClientRect();
              var tf = lf.getTransform ? lf.getTransform() : {scale:1,translateX:0,translateY:0};
              var s = tf.scale || 1, tx = tf.translateX || 0, ty = tf.translateY || 0;
              var lx = (ev.clientX - rect.left - tx) / s;
              var ly = (ev.clientY - rect.top - ty) / s;
              var data = lf.getGraphData();
              var best = null, bestDist = Infinity;
              for (var i = 0; i < data.nodes.length; i++) {
                var n = data.nodes[i];
                var d = Math.abs(n.x - lx) + Math.abs(n.y - ly);
                if (d < bestDist) { bestDist = d; best = n.id; }
              }
              if (best && bestDist < 80) {
                openEditByNodeId(best);
              }
            } catch(e) { console.warn('dblclick fallback err:', e); }
          });
        }
        setTimeout(() => {
          const palItems = document.querySelectorAll('.fd-pal-item');
          palItems.forEach(item => {
            const clsArr = item.className.split(' ');
            const typeCls = clsArr.find(c => c.startsWith('pal-'));
            if (!typeCls) return;
            const type = typeCls.substring(4);
            item.addEventListener('dragstart', function(e) {
              dragType = type;
              e.dataTransfer.effectAllowed = 'copy';
              try { e.dataTransfer.setData('text/plain', type); } catch(_) {}
            });
            item.addEventListener('dblclick', function() {
              if (!lf) return;
              const meta = typeMeta(type);
              const cx = lfContainer.value.clientWidth / 2;
              const cy = lfContainer.value.clientHeight / 2;
              const isSE = type === 'start' || type === 'end';
              addNode(type, meta.label, cx, cy, isSE ? null : 'DEPARTMENT_HEAD');
            });
          });
        }, 200);
        setTimeout(initLF, 100);

        // 从工作台跳转带过来的definition_id:加载完成后自动加载该流程
        const autoLoadId = window.__flowDesignerLoadId;
        if (autoLoadId) {
          delete window.__flowDesignerLoadId;
          let tries = 0;
          const tryLoad = setInterval(async () => {
            tries++;
            if (tries > 50) { clearInterval(tryLoad); return; }
            if (!lf || !flowDefs.value.length) return;
            clearInterval(tryLoad);
            await loadFlowDefs();
            const fid = Number(autoLoadId);
            // 静默加载,不弹确认,不触发select再次@change
            const def = flowDefs.value.find(d => d.id === fid);
            if (def && def.biz_type) curBizType.value = def.biz_type;
            loadedDefId.value = fid;
            // 手动调用silent=true加载
            const def2 = flowDefs.value.find(d => d.id === fid);
            if (def2) {
              onLoadDef._silent = true;
              onLoadDef(fid, true);
            }
          }, 100);
        }
      });
    });

    onBeforeUnmount(() => {
      document.removeEventListener('keydown', onKey);
    });

    return {
      lfContainer, curBizType, loadedDefId, flowDefs, saveFlash,
      mgmtVis, dlg, bizTypes, roles, palTypes, Icon,
      onPalDragStart, onRoleChange, onCcRoleChange, saveDlg, delFromDlg,
      doClear, doSave, doSaveAs, doDelete, openMgmt, doMgmtDelete, onBizTypeChange, onLoadDef, typeMeta
    };
  }
};

const App = {
  template: `
  <template v-if="!user">
    <LoginPage/>
  </template>
  <div v-else class="main" style="display:flex;flex-direction:column;height:100vh">
    <div class="topbar">
      <div class="brand" @click="go('dashboard')">
        <div class="logo" v-html="Icon.icon('cube',16)"></div>
        峰业精密 <span>SURFACE COATING</span>
      </div>
      <div class="spacer"></div>
      <div class="user-info">
        <span class="role-tag">{{roleLabel}}</span>
        <span>{{user.name}}</span>
        <el-button size="small" @click="logout">退出</el-button>
      </div>
    </div>
    <div class="body">
      <div class="icon-rail">
        <div v-for="n in navItems" :key="n.key" :class="['rail-item',{active:active===n.key}]" @click="go(n.key)" :title="n.label">
          <span v-html="Icon.icon(n.icon,20)"></span>
          <span class="rail-badge" v-if="badges[n.key]">{{badges[n.key]}}</span>
        </div>
        <div class="rail-spacer"></div>
        <div :class="['rail-item',{active:active==='flow-design'}]" @click="go('flow-design')" title="流程设计">
          <span v-html="Icon.icon('workflow',20)"></span>
        </div>
      </div>
      <div class="content">
        <component :is="pageComp"/>
      </div>
    </div>
  </div>`,
  setup() {
    // 首屏登录校验短路: 无token或无user缓存 -> 强制进Login(不发401请求), 解决新域名/隧道下"登录已过期"误报
    const rawTk = localStorage.getItem(TOKEN_KEY);
    const rawUsr = localStorage.getItem(USER_KEY);
    if (!rawTk || !rawUsr) {
      localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY);
      if (location.hash && !location.hash.startsWith('#/login')) location.hash = '#/login';
    }
    const user = ref(rawUsr ? JSON.parse(rawUsr) : null);
    const active = ref('dashboard');
    const badges = ref({});
    const roleLabel = computed(() => ({ADMIN:'管理员',GM:'总经理',SALES:'销售',FINANCE:'财务',MANAGER:'厂长',WAREHOUSE:'仓管',PURCHASE:'采购',OPERATION:'运营',DEPARTMENT_HEAD:'部门主管'}[user.value?.role]||user.value?.role||'用户'));
    const navItems = [
      {key:'dashboard',label:'工作台',icon:'dashboard'},
      {key:'orders',label:'订单',icon:'shopping-cart'},
      {key:'work-orders',label:'工单',icon:'wrench'},
      {key:'completions',label:'完工',icon:'check-circle'},
      {key:'requisitions',label:'领料',icon:'cube'},
      {key:'purchases',label:'采购',icon:'truck'},
      {key:'inventory',label:'库存',icon:'package'},
      {key:'finance',label:'财务',icon:'cash'},
      {key:'payroll',label:'工资',icon:'users'},
      {key:'approvals',label:'审批',icon:'check'},
      {key:'customers',label:'客户',icon:'users'},
      {key:'pr',label:'申请',icon:'file-text'},
      {key:'my-todos',label:'待办',icon:'bell'},
      {key:'my-done',label:'已办',icon:'check-circle'},
    ];
    const pageMap = {
      'dashboard': DashboardPage, 'my-todos': MyTodosPage, 'my-done': MyDonePage,
      'customers': CustomersPage, 'orders': OrdersPage, 'work-orders': WorkOrdersPage,
      'completions': CompletionsPage, 'requisitions': RequisitionsPage,
      'inventory': InventoryPage, 'finance': FinancePage, 'purchases': PurchasesPage,
      'pr': PRPage, 'payroll': PayrollPage, 'approvals': ApprovalsPage,
      'approval-flows': FlowDesignPage, 'flow-design': FlowDesignPage,
      'users': UsersPage, 'roles': RolesPage,
    };
    const pageComp = computed(() => pageMap[active.value] || DashboardPage);
    function go(key) { active.value = key; window.location.hash = '#/' + key; if (window.__go) window.__go(key); }
    function logout() {
      localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY);
      // 退出也不reload, 直接切去登录页组件, 避免整页刷新闪烁
      location.hash = '#/login';
      user.value = null;
    }
    function handleHash() {
      // 没登录时任何hash都不跳转组件渲染, 保持LoginPage显示
      if (!user.value) return;
      const h = location.hash; const m = h.match(/^#\/([\w-]+)/);
      if (m && pageMap[m[1]]) go(m[1]);
    }
    async function loadBadges() {
      try { const r = await api.get('/api/workbench'); const d = r.data||{};
        const b = {}; (d.todos||[]).forEach(t => { b[t.route] = (b[t.route]||0)+(t.count||0); }); badges.value = b;
      } catch(e) {}
    }
    onMounted(() => { handleHash(); if (user.value) loadBadges(); });
    window.addEventListener('hashchange', handleHash);
    // 监听登录成功后user变了: 重新同步ref + 拉badges + 跳默认首页
    window.addEventListener('storage', (e) => {
      if ((e.key === USER_KEY || !e.key) && localStorage.getItem(USER_KEY)) {
        try { user.value = JSON.parse(localStorage.getItem(USER_KEY)); handleHash(); loadBadges(); } catch {}
      }
    });
    // 暴露给LoginPage登录成功后调用
    window.__onLoginOk = function(u) {
      user.value = u;
      active.value = 'dashboard';   // 强制回工作台, 避免仍停在上个页面的空白
      location.hash = '#/dashboard';
      nextTick(() => { handleHash(); loadBadges(); });
    };
    // 401凭证失效时由api.req调用: 同步清空App的user.value, 让v-if="!user"切回LoginPage
    window.__forceLogout = function() {
      user.value = null;
      active.value = 'dashboard';
    };
    return { user, active, pageComp, navItems, badges, roleLabel, go, logout, Icon };
  }
};

const app = createApp(App);
app.use(ElementPlus);
// 全局注册所有页面组件
app.component('LoginPage', LoginPage);
app.mount('#app');
