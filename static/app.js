/* 喷涂加工ERP 前端 - Vue3 + Element Plus */
const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch, nextTick, h } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

// 模块级 Icon: 确保所有 setup 中模板引用的 Icon 都可解析(icons.js 已先行加载)
const Icon = window.Icon || { icon: (n,s) => `<svg viewBox="0 0 24 24" width="${s||20}" height="${s||20}" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="9"/></svg>`, has: () => false };

// ============ API ============
const TOKEN_KEY = 'erp_token';
const USER_KEY = 'erp_user';
const api = {
  _lastErrorTime: 0,
  _errorCooldown: 2000, // 错误消息冷却时间(ms)
  async req(method, url, body) {
    const opt = { method, headers: {} };
    const tk = localStorage.getItem(TOKEN_KEY);
    if (tk) opt.headers['Authorization'] = 'Bearer ' + tk;
    if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let r;
    try {
      r = await fetch(url, opt);
    } catch (e) {
      const now = Date.now();
      if (now - this._lastErrorTime > this._errorCooldown) {
        ElMessage.error('网络连接失败, 请检查服务是否正常运行');
        this._lastErrorTime = now;
      }
      throw e;
    }
    if (r.status === 401) {
      if (url.includes('/api/auth/login')) {
        throw new Error('账号或密码错误');
      }
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
      const now = Date.now();
      if (now - this._lastErrorTime > this._errorCooldown) {
        if (r.status === 403) {
          // 权限不足: 静默处理,仅控制台提示,不弹窗打断用户
          console.warn('权限不足:', msg);
        } else {
          ElMessage.error(msg);
        }
        this._lastErrorTime = now;
      }
      throw new Error(msg);
    }
    return j;
  },
  get(u) { return this.req('GET', u); },
  post(u, b) { return this.req('POST', u, b); },
  put(u, b) { return this.req('PUT', u, b); },
  del(u) { return this.req('DELETE', u); },
};

// ============ 关联数据源注册表(宜搭"关联表单"模式): 选中记录后按fillMap自动带出字段,带出后仍可改 ============
// source定义: api列表接口 / label显示主信息 / sub次要信息 / id取值字段
const REF_SOURCES = {
  orders:       { api: '/api/orders?size=200',        label: r => r.order_no,            sub: r => ((r.customer_name || '') + ' ¥' + (r.total_amount || 0)), id: 'id' },
  customers:    { api: '/api/customers?size=200',     label: r => r.name,                sub: r => (r.code || r.short_code || ''), id: 'id' },
  opportunities:{ api: '/api/opportunities?size=200', label: r => r.title,               sub: r => ('¥' + (r.expected_amount || 0)), id: 'id' },
  products:     { api: '/api/inventory/items?size=200', label: r => r.name,              sub: r => (r.sku || r.code || ''), id: 'id' },
  suppliers:    { api: '/api/purchases/suppliers',    label: r => r.name,                sub: r => (r.contact || ''), id: 'id' },
  work_orders:  { api: '/api/work-orders?size=200',   label: r => r.work_order_no,       sub: r => ((r.customer_name || '') + ' ' + (r.product_spec || '')), id: 'id' },
  employees:    { api: '/api/admin/users?page=1&size=200', label: r => (r.name || r.username), sub: r => (r.username || ''), id: 'id' },
};

// ============ NodeFormView 动态表单渲染组件 ============
const NodeFormView = {
  props: ['formConfig', 'bizData', 'mode', 'taskFormData'],
  template: `
  <div class="node-form-view" v-if="formConfig && formConfig.fields && formConfig.fields.length">
    <div class="nfv-header" v-if="formConfig.showHeader !== false && formConfig.title">
      <div class="nfv-title">{{formConfig.title}}</div>
    </div>
    <div class="nfv-body">
      <div 
        v-for="(field, index) in formConfig.fields" 
        :key="field.key" 
        :class="['nfv-field', 'col-' + (field.columnWidth || 1)]">
        <div class="nfv-field-label">
          {{field.label}}<span v-if="field.required && mode !== 'view'" class="required">*</span>
          <span v-if="getFieldError(field.key)" class="nfv-error-msg">{{getFieldError(field.key)}}</span>
        </div>
        <div class="nfv-field-value">
          <!-- 只读显示模式 -->
          <template v-if="mode === 'view'">
            <div v-if="field.type === 'display'" class="nfv-display">
              {{getDisplayValue(field.key)}}
            </div>
            <div v-else-if="field.type === 'detail_table'" class="nfv-table">
              <el-table
                :data="getTableRows(field.key)"
                size="small"
                border
                max-height="240">
                <el-table-column type="index" label="#" width="40"/>
                <el-table-column
                  v-for="col in (field.config && field.config.columns || [])"
                  :key="col.key"
                  :prop="col.key"
                  :label="col.label"
                  :width="col.width"/>
              </el-table>
            </div>
            <div v-else-if="field.type === 'section'" class="nfv-section-title">
              {{field.label}}
            </div>
            <div v-else-if="field.type === 'approval_info'" class="nfv-approval-info">
              <div v-if="taskFormData && taskFormData.approver">
                <div>审批人：{{taskFormData.approver || '-'}}</div>
                <div>审批时间：{{taskFormData.approved_at ? formatTime(taskFormData.approved_at) : '-'}}</div>
                <div>审批意见：{{taskFormData.comment || '同意'}}</div>
              </div>
              <div v-else-if="bizData && bizData.approver">
                <div>审批人：{{bizData.approver.name || '-'}}</div>
                <div>审批时间：{{formatTime(bizData.approved_at)}}</div>
                <div>审批意见：{{bizData.comment || '同意'}}</div>
              </div>
              <div v-else class="muted">待审批</div>
            </div>
            <div v-else-if="field.type === 'print_button'" class="nfv-print">
              <el-button size="small" type="primary" @click="$emit('print')">
                打印{{field.label || '单据'}}
              </el-button>
            </div>
            <div v-else class="nfv-display">
              {{getFieldDisplayValue(field)}}
            </div>
          </template>
          
          <!-- 编辑模式（create/edit） -->
          <template v-else>
            <el-input 
              v-if="field.type === 'input'" 
              v-model="fieldValues[field.key]"
              :placeholder="field.placeholder || ''"
              :readonly="field.readonly"
              size="small"/>
            
            <el-input 
              v-else-if="field.type === 'textarea'" 
              v-model="fieldValues[field.key]"
              type="textarea"
              :placeholder="field.placeholder || ''"
              :readonly="field.readonly"
              :rows="3"
              size="small"/>
            
            <el-input-number 
              v-else-if="field.type === 'number'" 
              v-model="fieldValues[field.key]"
              :disabled="field.readonly"
              size="small"
              style="width: 100%"/>
            
            <el-date-picker 
              v-else-if="field.type === 'date'" 
              v-model="fieldValues[field.key]"
              type="date"
              :placeholder="field.placeholder || '选择日期'"
              :disabled="field.readonly"
              size="small"
              style="width: 100%"/>
            
            <el-select 
              v-else-if="field.type === 'select'" 
              v-model="fieldValues[field.key]"
              :placeholder="field.placeholder || '请选择'"
              :disabled="field.readonly"
              size="small"
              style="width: 100%">
              <el-option 
                v-for="opt in field.options" 
                :key="opt.value" 
                :label="opt.label" 
                :value="opt.value"/>
            </el-select>
            
            <div v-else-if="field.type === 'display'" class="nfv-display">
              {{getDisplayValue(field.key)}}
            </div>
            
            <div v-else-if="field.type === 'ref_picker'" class="nfv-picker">
              <el-select
                v-if="!refFailed[(field.config && field.config.source) || '']"
                v-model="fieldValues[field.key]"
                :placeholder="field.placeholder || '选择关联数据，或直接跳过手动填写'"
                :disabled="field.readonly"
                filterable
                clearable
                size="small"
                style="width: 100%"
                @change="val => onRefPick(field, val)">
                <el-option
                  v-for="rec in refOptions[(field.config && field.config.source) || ''] || []"
                  :key="rec.id"
                  :label="refLabel(field, rec)"
                  :value="rec.id">
                  <span style="float:left">{{refLabel(field, rec)}}</span>
                  <span style="float:right;color:var(--text2);font-size:12px">{{refSub(field, rec)}}</span>
                </el-option>
              </el-select>
              <el-input v-else v-model="fieldValues[field.key]" :placeholder="field.placeholder || '手动填写'" :readonly="field.readonly" size="small"/>
            </div>

            <div v-else-if="field.type === 'customer_picker'" class="nfv-picker">
              <el-select
                v-model="fieldValues[field.key]"
                :placeholder="field.placeholder || '选择客户'"
                :disabled="field.readonly"
                filterable
                clearable
                size="small"
                style="width: 100%">
                <el-option v-for="c in pickerOptions.customers" :key="c.id" :label="c.name" :value="c.name"/>
              </el-select>
            </div>

            <div v-else-if="field.type === 'product_picker'" class="nfv-picker">
              <el-select
                v-model="fieldValues[field.key]"
                :placeholder="field.placeholder || '选择产品'"
                :disabled="field.readonly"
                filterable
                clearable
                size="small"
                style="width: 100%">
                <el-option v-for="p in pickerOptions.products" :key="p.id" :label="p.name" :value="p.name"/>
              </el-select>
            </div>

            <div v-else-if="field.type === 'employee_picker'" class="nfv-picker">
              <el-select
                v-if="pickerOptions.employees.length"
                v-model="fieldValues[field.key]"
                :placeholder="field.placeholder || '选择员工'"
                :disabled="field.readonly"
                filterable
                clearable
                size="small"
                style="width: 100%">
                <el-option v-for="u in pickerOptions.employees" :key="u.id" :label="u.name || u.username" :value="u.name || u.username"/>
              </el-select>
              <el-input v-else v-model="fieldValues[field.key]" :placeholder="field.placeholder || '输入员工姓名'" :readonly="field.readonly" size="small"/>
            </div>

            <div v-else-if="field.type === 'dept_picker'" class="nfv-picker">
              <el-select
                v-model="fieldValues[field.key]"
                :placeholder="field.placeholder || '选择部门'"
                :disabled="field.readonly"
                clearable
                size="small"
                style="width: 100%">
                <el-option v-for="d in (field.options && field.options.length ? field.options : defaultDepts)" :key="d.value || d" :label="d.label || d" :value="d.value || d"/>
              </el-select>
            </div>
            
            <div v-else-if="field.type === 'approval_info'" class="nfv-approval-info">
              <div v-if="bizData && bizData.approver">
                <div>审批人：{{bizData.approver.name || '-'}}</div>
                <div>审批时间：{{formatTime(bizData.approved_at)}}</div>
                <div>审批意见：{{bizData.comment || '同意'}}</div>
              </div>
              <div v-else class="muted">待审批</div>
            </div>
            
            <div v-else-if="field.type === 'print_button'" class="nfv-print">
              <el-button size="small" type="primary" @click="$emit('print')">
                打印{{field.label || '单据'}}
              </el-button>
            </div>
            
            <div v-else-if="field.type === 'detail_table'" class="nfv-table">
              <el-table
                :data="fieldValues[field.key] || []"
                size="small"
                border
                max-height="240">
                <el-table-column type="index" label="#" width="40"/>
                <el-table-column
                  v-for="col in (field.config && field.config.columns || [])"
                  :key="col.key"
                  :label="col.label"
                  :width="col.width">
                  <template #default="{ row }">
                    <el-input-number v-if="col.type === 'number'" v-model="row[col.key]" :min="0" size="small" style="width:100%"/>
                    <el-input v-else v-model="row[col.key]" :placeholder="col.label" size="small"/>
                  </template>
                </el-table-column>
                <el-table-column label="" width="52">
                  <template #default="{ $index }">
                    <el-button size="small" type="danger" link @click="removeTableRow(field.key, $index)">删</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button size="small" style="margin-top:6px" @click="addTableRow(field)">+ 加一行</el-button>
            </div>
            
            <div v-else-if="field.type === 'section'" class="nfv-section-title">
              {{field.label}}
            </div>
            
            <div v-else class="muted">未知组件类型: {{field.type}}</div>
          </template>
        </div>
      </div>
    </div>
  </div>`,
  setup(props, { emit, expose }) {
    const fieldValues = ref({});
    const errors = ref({});
    const pickerOptions = ref({ customers: [], products: [], employees: [] });
    const defaultDepts = ['销售部', '生产部', '采购部', '仓储部', '财务部', '总经办'];

    // 加载选择器数据源（客户/产品/员工），失败静默降级
    (async function loadPickerSources() {
      try {
        const rc = await api.get('/api/customers?size=200');
        pickerOptions.value.customers = rc.data || [];
      } catch(_) {}
      try {
        const rp = await api.get('/api/inventory/items?size=200');
        pickerOptions.value.products = rp.data || [];
      } catch(_) {}
      try {
        const ru = await api.get('/api/admin/users?page=1&size=200');
        pickerOptions.value.employees = ru.data || [];
      } catch(_) {}
    })();

    function addTableRow(field) {
      const cur = fieldValues.value[field.key];
      const rows = Array.isArray(cur) ? cur.slice() : [];
      const empty = {};
      ((field.config && field.config.columns) || []).forEach(c => { empty[c.key] = c.type === 'number' ? 0 : ''; });
      rows.push(empty);
      fieldValues.value[field.key] = rows;
    }

    function removeTableRow(key, idx) {
      const cur = fieldValues.value[key];
      if (!Array.isArray(cur)) return;
      const rows = cur.slice();
      rows.splice(idx, 1);
      fieldValues.value[key] = rows;
    }

    // view模式明细表行数据: 兼容数组/JSON字符串
    function getTableRows(key) {
      let v = null;
      if (props.taskFormData && props.taskFormData[key] !== undefined) v = props.taskFormData[key];
      else if (props.bizData && props.bizData[key] !== undefined) v = props.bizData[key];
      if (Array.isArray(v)) return v;
      if (typeof v === 'string' && v) {
        try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch(_) { return []; }
      }
      return [];
    }

    // ===== 关联选择器(ref_picker): 数据源懒加载 + 选中按fillMap带出(带出后仍可手动改) =====
    const refOptions = ref({});   // {source: [记录...]}
    const refFailed = ref({});    // {source: true} 加载失败(如403)降级为输入框

    function _srcOf(field) { return (field.config && field.config.source) || ''; }
    function refLabel(field, rec) {
      const def = REF_SOURCES[_srcOf(field)];
      return def ? def.label(rec) : (rec.name || rec.title || rec.id);
    }
    function refSub(field, rec) {
      const def = REF_SOURCES[_srcOf(field)];
      return def ? def.sub(rec) : '';
    }
    async function loadRefSource(source) {
      if (!source || !REF_SOURCES[source] || refOptions.value[source] || refFailed.value[source]) return;
      try {
        const r = await api.get(REF_SOURCES[source].api);
        refOptions.value = { ...refOptions.value, [source]: r.data || [] };
      } catch(_) {
        refFailed.value = { ...refFailed.value, [source]: true };
      }
    }
    // 选中关联记录 → 按fillMap自动填充本表单字段(宜搭"数据填充")
    function onRefPick(field, val) {
      const source = _srcOf(field);
      const fillMap = (field.config && field.config.fillMap) || {};
      const recs = refOptions.value[source] || [];
      const rec = recs.find(r => r.id === val);
      if (!rec) return;
      Object.keys(fillMap).forEach(targetKey => {
        const srcKey = fillMap[targetKey];
        if (rec[srcKey] !== undefined) fieldValues.value[targetKey] = rec[srcKey];
      });
    }
    // view模式: ref_picker显示label而非id
    function refDisplay(field) {
      const v = props.taskFormData && props.taskFormData[field.key] !== undefined
        ? props.taskFormData[field.key]
        : (props.bizData ? props.bizData[field.key] : null);
      if (v === null || v === undefined || v === '') return '-';
      const recs = refOptions.value[_srcOf(field)] || [];
      const rec = recs.find(r => r.id === v);
      return rec ? refLabel(field, rec) : v;
    }
    
    function getDisplayValue(key) {
      if (props.bizData && props.bizData[key]) {
        return props.bizData[key];
      }
      return '-';
    }
    
    function getFieldDisplayValue(field) {
      // ref_picker显示关联记录label而非id
      if (field.type === 'ref_picker') return refDisplay(field);
      const key = field.key;
      // 优先从taskFormData读取（已完成节点的数据）
      if (props.taskFormData && props.taskFormData[key] !== undefined) {
        const val = props.taskFormData[key];
        if (Array.isArray(val)) return JSON.stringify(val);
        if (typeof val === 'object') return JSON.stringify(val);
        return val;
      }
      // 从bizData读取
      if (props.bizData && props.bizData[key] !== undefined) {
        const val = props.bizData[key];
        if (Array.isArray(val)) return JSON.stringify(val);
        if (typeof val === 'object') return JSON.stringify(val);
        return val;
      }
      return '-';
    }
    
    function getFieldError(key) {
      return errors.value[key] || '';
    }
    
    function formatTime(s) {
      return s ? new Date(s).toLocaleString('zh-CN') : '-';
    }
    
    // 验证表单
    function validate() {
      errors.value = {};
      if (!props.formConfig || !props.formConfig.fields) return true;
      let valid = true;
      props.formConfig.fields.forEach(field => {
        if (field.required && !field.readonly) {
          const val = fieldValues.value[field.key];
          if (val === '' || val === null || val === undefined) {
            errors.value[field.key] = field.label + '不能为空';
            valid = false;
          }
        }
      });
      return valid;
    }
    
    // 获取表单数据
    function getFormData() {
      const raw = JSON.parse(JSON.stringify(fieldValues.value));
      // number字段归一化(空串→0), 避免提交空字符串被后端number校验拒绝
      (props.formConfig?.fields || []).forEach(f => {
        if (f.type === 'number') raw[f.key] = Number(raw[f.key]) || 0;
      });
      return raw;
    }
    
    // 设置表单数据
    function setFormData(data) {
      if (data) {
        fieldValues.value = { ...fieldValues.value, ...data };
      }
    }
    
    // 初始化字段值
    watch(() => [props.formConfig, props.bizData, props.taskFormData, props.mode], () => {
      if (!props.formConfig || !props.formConfig.fields) return;
      const values = {};
      props.formConfig.fields.forEach(field => {
        const key = field.key;
        // 优先级: taskFormData > bizData > 默认值
        if (props.taskFormData && props.taskFormData[key] !== undefined) {
          values[key] = props.taskFormData[key];
        } else if (props.bizData && props.bizData[key] !== undefined) {
          values[key] = props.bizData[key];
        } else if (props.mode === 'view') {
          values[key] = '';
        } else {
          values[key] = '';
        }
      });
      fieldValues.value = values;
      errors.value = {};
      // 懒加载关联选择器数据源
      props.formConfig.fields.forEach(f => {
        if (f.type === 'ref_picker') loadRefSource(_srcOf(f));
      });
    }, { immediate: true, deep: true });

    // 暴露方法给父组件
    expose({ validate, getFormData, setFormData, fieldValues });

    return { fieldValues, getDisplayValue, getFieldDisplayValue, getFieldError, formatTime, Icon,
             pickerOptions, defaultDepts, addTableRow, removeTableRow, getTableRows,
             refOptions, refFailed, refLabel, refSub, onRefPick };
  }
};

// ============ FlowTrack 流转轨迹(真实时间轴,调instances接口) ============
const FlowTrack = {
  props: ['bizType', 'bizId'],
  components: { NodeFormView },
  template: `
  <div class="flow-track" v-loading="loading">
    <div v-if="!loading && !instance" class="ft-empty">该单据暂未接入流程</div>
    <template v-else-if="instance">
      
      <!-- 单据完整表单（只读展示） -->
      <div class="ft-full-form" v-if="firstNodeFormConfig">
        <div class="ft-section-title">📋 单据表单详情</div>
        <div class="ft-form-wrapper">
          <NodeFormView 
            :form-config="firstNodeFormConfig" 
            :biz-data="instance.biz_data"
            :mode="'view'"
          />
        </div>
      </div>
      
      <!-- 流程状态 -->
      <div class="ft-head" style="margin-top:16px">
        <span class="ft-pill" :class="instance.status">{{statusLabel}}</span>
        <span class="muted tiny">共 {{nodes.length}} 个环节</span>
      </div>

      <!-- 已驳回: 发起人/管理员可重新发起(历史留痕保留) -->
      <div v-if="instance.status==='REJECTED' && canReopen" class="ft-reopen-bar">
        <span class="ft-reopen-tip">该流程已被驳回，可修改业务数据后重新发起，历史审批记录将保留</span>
        <el-button type="warning" size="small" @click="reopenInstance">↻ 重新发起</el-button>
      </div>

      <!-- 审批时序轨迹 -->
      <div class="ft-section-title">⏱ 审批流转记录</div>
      <div class="ft-timeline">
        <div v-for="(n,i) in nodes" :key="n.seq" :class="['ft-timeline-item', n.status]">
          <div class="ft-timeline-dot">
            <span v-if="n.status==='done'" v-html="Icon.icon('check',16)"></span>
            <span v-else-if="n.status==='rejected'" v-html="Icon.icon('alert-circle',16)"></span>
            <span v-else-if="n.status==='current'" v-html="Icon.icon('clock',16)"></span>
            <span v-else>{{i+1}}</span>
          </div>
          <div class="ft-timeline-content">
            <div class="ft-timeline-header">
              <span class="ft-timeline-name">{{n.name}}</span>
              <el-tag size="small" :type="n.type==='process'?'success':(n.type==='cc'?'info':'')" style="margin-left:8px">
                {{n.type==='process'?'流转':(n.type==='cc'?'抄送':'审批')}}
              </el-tag>
              <el-tag v-if="n.status==='done'" size="small" type="success" style="margin-left:4px">已通过</el-tag>
              <el-tag v-else-if="n.status==='rejected'" size="small" type="danger" style="margin-left:4px">已驳回</el-tag>
              <el-tag v-else-if="n.status==='current'" size="small" type="warning" style="margin-left:4px">进行中</el-tag>
              <el-tag v-else size="small" style="margin-left:4px">待处理</el-tag>
            </div>
            
            <!-- 已完成节点：展示审批详情 -->
            <div class="ft-timeline-detail" v-if="n.status==='done' || n.status==='rejected'">
              <div class="ft-detail-item">
                <span class="ft-detail-label">处理人：</span>
                <span class="ft-detail-value">{{n.assignee_name || '系统自动'}}</span>
              </div>
              <div class="ft-detail-item" v-if="n.handled_at">
                <span class="ft-detail-label">处理时间：</span>
                <span class="ft-detail-value">{{fmtTime(n.handled_at)}}</span>
              </div>
              <div class="ft-detail-item" v-if="n.comment && n.comment!=='流转自动推进'">
                <span class="ft-detail-label">审批意见：</span>
                <span class="ft-detail-value ft-comment">{{n.comment}}</span>
              </div>
              <div class="ft-detail-item" v-if="n.form_config && n.form_config.fields && n.form_config.fields.length && n.form_data">
                <el-button size="small" link type="primary" @click="toggleNodeForm(n)">
                  {{expandedNodeSeq === n.seq ? '收起表单变更' : '查看表单变更'}}
                </el-button>
                <div v-if="expandedNodeSeq === n.seq" class="ft-form-change">
                  <div class="muted tiny" style="margin-bottom:4px">该节点修改的字段：</div>
                  <div v-for="(val, key) in n.form_data" :key="key" class="ft-form-change-item">
                    <span class="ft-change-key">{{key}}:</span>
                    <span class="ft-change-val">{{val}}</span>
                  </div>
                </div>
              </div>
            </div>
            
            <!-- 当前节点：展示待办信息 + 审批操作 -->
            <div class="ft-timeline-detail" v-else-if="n.status==='current' && n.type==='approve'">
              <div class="ft-detail-item">
                <span class="ft-detail-label">待处理人：</span>
                <span class="ft-detail-value">{{n.assignee_name || '待分配'}}</span>
                <span class="muted tiny" style="margin-left:8px">已停留 {{n.duration||'-'}}</span>
              </div>
              
              <!-- 节点独立表单（可编辑） -->
              <div class="ft-node-form" v-if="n.form_config && n.form_config.fields && n.form_config.fields.length">
                <div class="muted tiny" style="margin:8px 0 4px">📋 本节点表单（可编辑）</div>
                <NodeFormView 
                  ref="currentNodeFormRef"
                  :form-config="n.form_config"
                  :biz-data="instance.biz_data"
                  :task-form-data="n.form_data"
                  :mode="'edit'"
                />
              </div>
              
              <!-- 审批操作 -->
              <div class="ft-approval-actions">
                <div class="ft-quick-comments">
                  <span class="ft-qc-label">快捷意见：</span>
                  <el-tag
                    v-for="q in quickComments"
                    :key="q"
                    size="small"
                    class="ft-qc-chip"
                    @click="approvalComment = q"
                  >{{q}}</el-tag>
                </div>
                <el-input
                  v-model="approvalComment"
                  type="textarea"
                  :rows="2"
                  placeholder="点上方快捷意见或手动输入（通过可不填，驳回必填）"
                  style="margin-bottom:8px"
                />
                <div class="ft-action-buttons">
                  <el-button type="primary" size="small" @click="handleApprove(n)">✓ 审批通过</el-button>
                  <el-button type="danger" size="small" @click="handleReject(n)">✕ 驳回</el-button>
                </div>
              </div>
            </div>
            
            <!-- 当前节点：流转类型 -->
            <div class="ft-timeline-detail" v-else-if="n.status==='current' && n.type==='process'">
              <div class="muted tiny">系统自动流转中...</div>
            </div>
            
            <!-- 未来节点 -->
            <div class="ft-timeline-detail" v-else-if="n.status==='pending'">
              <div class="muted tiny">
                预计角色：{{roleLabel(n.role)}}
                <span v-if="n.type==='cc'" style="margin-left:8px">抄送：{{ccRoleLabel(n.cc_roles)}}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>`,
  setup(props) {
    const instance = ref(null);
    const nodes = ref([]);
    const loading = ref(false);
    const expandedNodeSeq = ref(null);
    const currentNodeFormRef = ref(null);
    const approvalComment = ref('');
    
    const statusLabel = computed(() => ({RUNNING:'进行中',APPROVED:'已完成',REJECTED:'已驳回',CANCELLED:'已取消'}[instance.value?.status]||''));
    
    // 获取第一个节点的表单配置（完整表单展示）
    const firstNodeFormConfig = computed(() => {
      if (nodes.value.length === 0) return null;
      return nodes.value[0]?.form_config || null;
    });
    
    const ROLE_CN = {OPS:'运营',FINANCE:'财务',GM:'总经理',SALES:'销售',PRODUCTION:'厂长',ADMIN:'管理员',DEPARTMENT_HEAD:'部门主管',OPERATION:'运营',FACTORY_MANAGER:'厂长',MANAGER:'厂长'};
    const roleLabel = c => ROLE_CN[c] || c || '-';
    const ccRoleLabel = roles => (roles||[]).map(r => ROLE_CN[r]||r).join('、') || '-';
    
    function toggleNodeForm(node) {
      expandedNodeSeq.value = expandedNodeSeq.value === node.seq ? null : node.seq;
    }
    
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
    
    async function handleApprove(node) {
      if (!instance.value) return;
      
      // 验证节点表单（如果有）
      if (node.form_config && node.form_config.fields && currentNodeFormRef.value) {
        const valid = currentNodeFormRef.value.validate();
        if (!valid) {
          ElementPlus.ElMessage.warning('请完善必填项');
          return;
        }
      }
      
      try {
        let formData = {};
        if (currentNodeFormRef.value) {
          formData = currentNodeFormRef.value.getFormData();
        }
        
        await api.post(`/api/approvals/instances/${instance.value.id}/approve`, {
          comment: approvalComment.value || '审批通过',
          form_data: formData
        });
        ElementPlus.ElMessage.success('审批成功');
        approvalComment.value = '';
        load();
      } catch(e) {
        ElementPlus.ElMessage.error(e.message || '审批失败');
      }
    }
    
    async function handleReject(node) {
      if (!instance.value) return;
      if (!approvalComment.value || !approvalComment.value.trim()) {
        ElementPlus.ElMessage.warning('驳回必须填写审批意见');
        return;
      }
      try {
        await api.post(`/api/approvals/instances/${instance.value.id}/reject`, {
          comment: approvalComment.value
        });
        ElementPlus.ElMessage.success('已驳回');
        approvalComment.value = '';
        load();
      } catch(e) {
        ElementPlus.ElMessage.error(e.message || '驳回失败');
      }
    }

    // 快捷审批意见(免打字, 对标钉钉/飞书常用语)
    const quickComments = ['同意', '同意，按计划执行', '同意，注意成本控制', '信息不全，请补充', '金额有误，请修改后重新提交'];

    // 驳回重启: 仅发起人/ADMIN/GM可见
    const curUser = JSON.parse(localStorage.getItem(USER_KEY) || '{}');
    const canReopen = computed(() => {
      if (!instance.value) return false;
      const isInitiator = instance.value.initiator_id === curUser.id;
      const isAdmin = ['ADMIN', 'GM'].includes(curUser.role);
      return isInitiator || isAdmin;
    });

    async function reopenInstance() {
      if (!instance.value) return;
      try {
        await ElementPlus.ElMessageBox.confirm(
          '重新发起后流程将从第一环节重新流转，历史审批记录保留。确认重新发起？',
          '重新发起', { type: 'warning', confirmButtonText: '重新发起', cancelButtonText: '取消' }
        );
      } catch(_) { return; }
      try {
        await api.post(`/api/approvals/instances/${props.bizType}/${props.bizId}/reopen`, {});
        ElementPlus.ElMessage.success('已重新发起');
        load();
      } catch(e) {
        ElementPlus.ElMessage.error(e.message || '重新发起失败');
      }
    }
    
    function fmtTime(s){ return s ? new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }
    
    watch(() => [props.bizType, props.bizId], load);
    onMounted(load);
    
    return {
      instance, nodes, loading, statusLabel, fmtTime, roleLabel, ccRoleLabel, Icon,
      firstNodeFormConfig, expandedNodeSeq, approvalComment, currentNodeFormRef,
      toggleNodeForm, handleApprove, handleReject, quickComments, canReopen, reopenInstance
    };
  }
};

// ============ FlowMini 卡片内嵌微型流程条(一眼看到当前位置) ============
const FlowMini = {
  props: ['bizType', 'bizId', 'instanceId'],
  template: `
  <div class="flow-mini" v-if="nodes.length">
    <div class="fm-label">
      <span class="fm-cur">{{curName}}</span>
      <span class="fm-rest">· 剩余{{rest}}步</span>
    </div>
    <div class="fm-bar">
      <div v-for="(n,i) in nodes" :key="n.seq" :class="['fm-node',n.status]">
        <div class="fm-dot"><span v-if="n.status==='done'" v-html="ic('check',10)"></span><span v-else-if="n.status==='rejected'" v-html="ic('alert-circle',10)"></span></div>
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
    const ic = (name, size) => (window.Icon||{}).icon ? window.Icon.icon(name, size) : '<span style="font-size:'+(size||12)+'px">●</span>';
    async function load() {
      if (!props.bizType && !props.instanceId) return;
      try {
        const id = props.instanceId || `${props.bizType}/${props.bizId}`;
        const r = await api.get(`/api/approvals/instances/${id}`);
        nodes.value = r.data?.nodes || [];
      } catch(e) { nodes.value = []; }
    }
    watch(() => [props.bizType, props.bizId, props.instanceId], load);
    onMounted(load);
    return { nodes, curName, rest, ic };
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
        <template v-if="cfg.formConfigBlType && formConfig && formConfig.fields && formConfig.fields.length">
          <NodeFormView ref="formViewRef" :formConfig="formConfig" mode="create"/>
        </template>
        <template v-else>
        <template v-for="f in (cfg.formFields||[])" :key="f.key">
          <el-form-item :label="f.label" :required="f.required">
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
        </template>
        ${cfg.extraCreateSection || ''}
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
      ${cfg.extraDetailSection || ''}
    </el-drawer>
  </div>`;

  // 自动补全: apiUrl -> createUrl/listUrl
  if (cfg.apiUrl) {
    if (!cfg.createUrl) cfg.createUrl = cfg.apiUrl;
    if (!cfg.listUrl) cfg.listUrl = cfg.apiUrl;
  }

  return {
    template: cfg.template || cardTemplate,
    components: { FlowTrack, FlowMini, NodeFormView },
    setup() {
      const rows = ref([]);
      const total = ref(0);
      const loading = ref(false);
      const page = reactive({ page: 1, size: 15 });
      const query = reactive({ ...(cfg.query ? Object.fromEntries(Object.keys(cfg.query).map(k=>[k,''])) : {}) });
      const dialog = reactive({ visible: false, title: '', data: {} });
      const detail = reactive({ visible: false, data: {} });
      // 画布动态表单(可选): 当cfg.formConfigBlType存在时,创建表单改用流程定义form_config渲染,零硬编码
      const formConfig = ref(null);
      const formViewRef = ref(null);
      const formLoading = ref(false);
      async function loadFormConfig() {
        const bt = cfg.formConfigBlType;
        if (!bt) return;
        formLoading.value = true;
        try {
          const r = await api.get('/api/approvals/definitions?biz_type=' + bt);
          formConfig.value = (r.data && r.data.length && r.data[0].nodes && r.data[0].nodes.length)
            ? (r.data[0].nodes[0].form_config || null) : null;
        } catch (e) { console.warn('[动态表单] 加载流程定义失败', e.message || e); formConfig.value = null; }
        finally { formLoading.value = false; }
      }

      async function load() {
        loading.value = true;
        try {
          const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_, v]) => v !== '' && v !== null && v !== undefined)) }).toString();
          const res = await api.get((cfg.listUrl || cfg.apiUrl || '') + '?' + qs);
          rows.value = res.data || [];
          total.value = res.total ?? rows.value.length;
        } catch (e) { ElMessage.error(e.message); }
        loading.value = false;
      }
      function search() { page.page = 1; load(); }
      function reset() { Object.keys(query).forEach(k => query[k] = ''); search(); }
      function openCreate() { dialog.visible = true; dialog.title = cfg.createLabel || '新增'; dialog.data = cfg.emptyForm ? cfg.emptyForm() : {}; if (cfg.formConfigBlType) loadFormConfig(); }
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
          if (cfg.formConfigBlType && formViewRef.value) {
            if (formViewRef.value.validate && !formViewRef.value.validate()) { ElMessage.warning('请完善画布表单必填项'); return; }
          }
          let body = dialog.data;
          if (cfg.formConfigBlType && formViewRef.value && formViewRef.value.getFormData) {
            body = { form_data: formViewRef.value.getFormData() };
          }
          // 注入 extraSetupCreate 提供的附加字段(如附件)
          if (extra.beforeSubmit) body = await extra.beforeSubmit(body);
          let res;
          if (dialog.data.id && cfg.updateUrl) res = await api.put(cfg.updateUrl(dialog.data), dialog.data);
          else res = await api.post(cfg.createUrl, body);
          ElMessage.success('保存成功');
          dialog.visible = false; load();
          if (extra.afterSubmit) extra.afterSubmit();
          // 创建成功后自动打开详情(用于附件上传/查看流转等)
          if (!dialog.data.id && res && res.data && res.data.id) {
            setTimeout(() => openDetail({ id: res.data.id }), 300);
          }
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
      return { rows, total, page, loading, query, dialog, detail, cfg, card, load, search, reset, openCreate, openEdit, openDetail, submit, action: doAction, fmt: FMT, fmtDate: FMT_DATE, pNo, pCust, pSt, pStLabel, pAmt, pAmtNeg, pFv, pShowAct, pDoAct, Icon, formConfig, formViewRef, formLoading, loadFormConfig, ...extra };
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
      f.username = f.username.trim();  // 去除前后空格
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
      <div class="wb-greeting">{{greeting}}<span class="role">{{user?.name}} · {{userRoleLabel}}</span></div>
      <div class="wb-date">{{today}}</div>
    </div>

    <!-- 工作流指引带 (全宽顶部) - 仅业务角色可见，管理员通过流程设计器管理 -->
    <div class="wf-pipeline wf-pipeline--hero" v-if="showWorkflow && workflowSteps.length">
      <div class="wf-title">
        🔀 业务流程 <span class="wf-count" v-if="workflowSteps.length">{{workflowSteps.length}}个进行中</span>
        <span class="wf-more" v-if="workflowSteps.length > 3" @click="openWorkflowList">显示全部 ›</span>
      </div>
      <div class="wf-rows" v-if="workflowSteps.length">
        <div v-for="(wf, wi) in displayWorkflows" :key="wi" class="wf-row" :class="{ 'wf-row-running': wf.status === 'RUNNING' }">
          <div class="wf-row-head">
            <div class="wf-row-title">{{wf.title}}</div>
            <div class="wf-row-bizno">{{wf.biz_no}}</div>
            <div class="wf-row-status" :class="wf.status">
              <span v-if="wf.status === 'RUNNING'">进行中</span>
              <span v-else-if="wf.status === 'APPROVED'">已通过</span>
              <span v-else-if="wf.status === 'REJECTED'">已驳回</span>
              <span v-else>{{wf.status}}</span>
            </div>
          </div>
          <div class="wf-flow">
            <div v-for="(n, i) in wf.nodes" :key="i"
                 :class="['wf-step', n.status, { 'wf-clickable': true }]"
                 @click="showNodeDetail(wf, n)">
              <div class="wf-connector" v-if="i>0">
                <div :class="['wf-line', wfLineClass(i, wf.nodes)]"></div>
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
        <div class="wf-more-row" v-if="workflowSteps.length > 3" @click="openWorkflowList">
          <span class="wf-more-icon">
            <span v-html="Icon.icon('chevron-down', 16)"></span>
          </span>
          <span>还有 {{workflowSteps.length - 3}} 个流程进行中，点击查看全部</span>
        </div>
      </div>
      <div class="wf-empty" v-else>
        <span>暂无进行中的流程</span>
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
      <!-- 右列第2行:我的待办(6条明细大卡片,和老版一致) -->
      <div class="right-block rb-2">
        <div class="wb-section-title">
          <span>📋 我的待办</span>
          <span class="cc-more" @click="go('my-todos')">查看全部 ›</span>
        </div>
        <div class="content-card todo-body">
          <div class="todo-list" v-if="todos.length">
            <div v-for="t in todos" :key="t.id" :class="'todo-row todo-'+t.color" @click="openTodo(t)">
              <span class="todo-prio">{{t.prio || (t.color==='red'?'紧急':t.color==='orange'?'重要':'普通')}}</span>
              <span class="todo-text">{{t.title || t.sub || t.text || ('待办 #'+t.id)}}</span>
              <span class="todo-arrow" v-html="Icon.icon('chevron-right', 16)"></span>
            </div>
          </div>
          <div class="cc-empty" v-else>
            <span v-html="Icon.icon('check-circle', 28)"></span>
            <p>暂无待办，一切顺利 🎉</p>
          </div>
        </div>
      </div>
      <!-- 右列第3-N行:工作台(已办+动态,跨左栏第3组起所有剩余行) -->
      <div class="right-block rb-34">
        <div class="wb-section-title"><span>📊 工作台</span></div>
        <div class="content-row">
          <div class="content-card">
            <div class="cc-head"><h3>✅ 最近已办</h3><span class="cc-more" @click="go('my-done')">更多 ›</span></div>
            <div class="timeline-list" v-if="doneItems.length">
              <div v-for="d in doneItems" :key="d.id" class="tl-item clickable" @click="openDone(d)">
                <span :class="'tl-dot tl-'+d.color"></span>
                <div class="tl-body">
                  <div class="tl-text">{{d.text}}</div>
                  <div class="tl-sub" v-if="d.sub">{{d.sub}}</div>
                  <div class="tl-time">{{d.time}}</div>
                </div>
              </div>
            </div>
            <div v-else class="empty-hint">暂无已办记录</div>
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

    <!-- 节点详情对话框 -->
    <el-dialog v-model="nodeDetailVis" title="节点详情" width="560px" :close-on-click-modal="true">
      <div class="node-detail" v-if="selectedNode">
        <div class="nd-header">
          <div :class="['nd-icon', selectedNode.status]">
            <span v-html="Icon.icon(selectedNode.icon || 'circle', 24)"></span>
          </div>
          <div class="nd-title">
            <div class="nd-name">{{selectedNode.name}}</div>
            <div :class="['nd-status', selectedNode.status]">
              {{selectedNode.status_text || '未知状态'}}
            </div>
          </div>
        </div>

        <!-- 当前节点信息 -->
        <div class="nd-section">
          <div class="nd-section-title">当前节点信息</div>
          <div class="nd-body">
            <div class="nd-row">
              <span class="nd-label">节点类型</span>
              <span class="nd-value">{{nodeTypeLabel(selectedNode.ntype)}}</span>
            </div>
            <div class="nd-row">
              <span class="nd-label">审批角色</span>
              <span class="nd-value nd-role">{{roleLabel(selectedNode.assignee_role)}}</span>
            </div>
            <div class="nd-row" v-if="selectedNode.assignee">
              <span class="nd-label">当前处理人</span>
              <span class="nd-value nd-assignee">
                <span class="nd-avatar">{{selectedNode.assignee.charAt(0)}}</span>
                {{selectedNode.assignee}}
              </span>
            </div>
            <div class="nd-row" v-if="selectedNode.count">
              <span class="nd-label">待办数量</span>
              <span class="nd-value">{{selectedNode.count}} 条</span>
            </div>
            <div class="nd-row" v-if="selectedNode.approved_at">
              <span class="nd-label">审批时间</span>
              <span class="nd-value">{{fmtTime(selectedNode.approved_at)}}</span>
            </div>
            <div class="nd-row" v-if="selectedNode.comment">
              <span class="nd-label">审批意见</span>
              <span class="nd-value nd-comment">{{selectedNode.comment}}</span>
            </div>
            <div class="nd-row" v-if="isMyNode(selectedNode)">
              <el-button type="primary" size="small" @click="go('approvals')">前往处理 ›</el-button>
            </div>
          </div>
        </div>

        <!-- 审批历史（前面所有节点的审批结果） -->
        <div class="nd-section" v-if="selectedNode.approve_history && selectedNode.approve_history.length">
          <div class="nd-section-title">审批历史 <span class="nd-count">({{selectedNode.approve_history.length}})</span></div>
          <div class="nd-timeline">
            <div v-for="(h, idx) in selectedNode.approve_history" :key="idx" class="nd-tl-item">
              <div :class="['nd-tl-dot', h.status === '通过' ? 'approved' : 'rejected']"></div>
              <div class="nd-tl-body">
                <div class="nd-tl-header">
                  <span class="nd-tl-name">{{h.name}}</span>
                  <span :class="['nd-tl-status', h.status === '通过' ? 'approved' : 'rejected']">{{h.status}}</span>
                </div>
                <div class="nd-tl-meta">
                  <span v-if="h.assignee">{{h.assignee}} 处理</span>
                  <span v-if="h.approved_at">· {{fmtTime(h.approved_at)}}</span>
                </div>
                <div v-if="h.comment" class="nd-tl-comment">{{h.comment}}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="nodeDetailVis = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'));
    const todos = ref([]);
    const workflowSteps = ref([]);
    const appGroups = ref({});
    const kpis = ref([]);
    const groupList = computed(() => Object.entries(appGroups.value).map(([name, apps]) => ({ name, apps })));
    const roleCode = user.value?.role || '';
    const isAdmin = roleCode === 'ADMIN';
    const showWorkflow = roleCode !== 'ADMIN';
    const userRoleLabel = { ADMIN: '管理员', GM: '总经理', SALES: '销售', FINANCE: '财务', MANAGER: '厂长', OPERATION: '运营', DEPARTMENT_HEAD: '部门主管' }[roleCode] || roleCode || '用户';

    const hour = new Date().getHours();
    const greeting = hour < 6 ? '凌晨好' : hour < 12 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
    const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });

    // 工作台只显示最新3条工作流
    const displayWorkflows = computed(() => workflowSteps.value.slice(0, 3));
    
    // 打开工作流列表页
    function openWorkflowList() {
      window.__go && window.__go('workflow-list');
    }

    // 工作流节点样式判定
    function wfLineClass(i, steps) {
      if (!steps || !steps.length) return '';
      const prev = steps[i - 1];
      if (!prev) return '';
      // 已完成节点 -> 绿色连接线（表示已走通）
      if (prev.status === 'done') return 'wf-line-done';
      // 当前进行中节点 -> 蓝色连接线（表示流程进行中）
      if (prev.status === 'active' || prev.status === 'current') return 'wf-line-active';
      // 驳回节点 -> 红色连接线
      if (prev.status === 'rejected') return 'wf-line-rejected';
      return '';
    }

    // 已办 - 初始为空，从API加载
    const doneItems = ref([]);

    // 团队动态
    const news = ref([
      { id: 1, who: '张销售', action: '提交了', target: '订单 SO-20260803-012', time: '5 分钟前', color: 'blue' },
      { id: 2, who: '李厂长', action: '下达了', target: '加工单 WO-089', time: '18 分钟前', color: 'purple' },
      { id: 3, who: '王财务', action: '审批通过', target: '报销单 EX-203 ¥1,280', time: '40 分钟前', color: 'green' },
      { id: 4, who: '陈仓管', action: '完成了', target: '采购入库 PR-45', time: '1 小时前', color: 'orange' },
      { id: 5, who: 'AI 助手', action: '生成了', target: '本月销售分析报告', time: '2 小时前', color: 'cyan' },
    ]);

    // 快捷入口候选库（仅作为展示池，最终由权限严格过滤；不再按角色硬编码分配 — 杜绝权限泄露）
    const QUICK_POOL = [
      { key: 'opportunities', label: '商机管理', icon: 'target', color: 'blue' },
      { key: 'orders', label: '销售订单', icon: 'plus', color: 'blue' },
      { key: 'customers', label: '客户档案', icon: 'users', color: 'orange' },
      { key: 'sample-request', label: '打样申请', icon: 'beaker', color: 'green' },
      { key: 'finance', label: '财务单据', icon: 'cash', color: 'green' },
      { key: 'receivables', label: '应收管理', icon: 'credit-card', color: 'orange' },
      { key: 'payroll', label: '工资管理', icon: 'users', color: 'purple' },
      { key: 'expense', label: '费用报销', icon: 'receipt', color: 'red' },
      { key: 'work-orders', label: '工单管理', icon: 'wrench', color: 'purple' },
      { key: 'inventory', label: '库存管理', icon: 'package', color: 'blue' },
      { key: 'purchases', label: '采购管理', icon: 'cart', color: 'green' },
      { key: 'purchase-requests', label: '采购申请', icon: 'file-text', color: 'blue' },
      { key: 'completions', label: '完工确认', icon: 'check-circle', color: 'blue' },
      { key: 'requisitions', label: '领料出库', icon: 'box', color: 'orange' },
      { key: 'approvals', label: '审批中心', icon: 'check', color: 'orange' },
      { key: 'my-todos', label: '我的待办', icon: 'bell', color: 'purple' },
      { key: 'my-done', label: '我的已办', icon: 'clipboard-check', color: 'green' },
      { key: 'analysis', label: 'AI经营分析', icon: 'chart-bar', color: 'green' },
      { key: 'ai-finance', label: '财务AI助手', icon: 'cpu-chip', color: 'green' },
      { key: 'sample-request', label: '打样申请', icon: 'beaker', color: 'green' },
      { key: 'workflow-list', label: '业务流程', icon: 'workflow', color: 'green' },
      { key: 'vouchers', label: '凭证管理', icon: 'file', color: 'purple' },
      { key: 'reports', label: '财务报表', icon: 'bar-chart', color: 'orange' },
      { key: 'accounts', label: '会计科目', icon: 'book', color: 'cyan' },
      { key: 'stock-moves', label: '出入库流水', icon: 'swap', color: 'blue' },
      { key: 'screen', label: '车间大屏', icon: 'tv', color: 'cyan' },
    ];
    // 按权限过滤: 仅展示已授权的入口; ADMIN/GM由 __hasPage 直接放行; 未知角色仅最小集合
    function _filterQuick(arr) {
      const fn = window.__hasPage || (() => false);
      return arr.filter(a => fn(a.key)).slice(0, 6);
    }
    // 默认先用最小安全集初始化，等权限加载完后会更新 (组件内部watch)
    const quickEntries = ref(_filterQuick(QUICK_POOL));
    // 定时同步最新权限状态 (rolePages加载是异步的)
    const _refreshQuick = () => { quickEntries.value = _filterQuick(QUICK_POOL); };
    // 挂载时立刻尝试一次刷新
    setTimeout(_refreshQuick, 200);
    // 提供对外hook: 登录成功回调/App权限加载完毕后刷新
    const _origLoginOk = window.__onLoginOk;
    // 也可以通过事件通知: 暴露一个刷新专用方法
    window.__reloadQuickEntries = _refreshQuick;

    async function load() {
      try {
        const r = await api.get('/api/workbench');
        const d = r.data || {};
        // todos: 强制转数组 + 过滤title空的脏数据(不显示空壳横条)
        const rawTodos = Array.isArray(d.todos) ? d.todos : [];
        todos.value = rawTodos.filter(x => x && (x.title || '').toString().trim().length > 0);
        // apps: 非null/非数组时回退空对象,避免v-for渲染崩溃
        appGroups.value = (d.apps && typeof d.apps === 'object' && !Array.isArray(d.apps)) ? d.apps : {};
        workflowSteps.value = d.workflow_steps || [];
        if (Array.isArray(d.kpis) && d.kpis.length) kpis.value = d.kpis;
      } catch (e) {
        todos.value = [];
        appGroups.value = {};
        workflowSteps.value = [];
        if (e.message && !e.message.includes('登录已过期') && !e.message.includes('网络连接失败')) {
          ElMessage.error('加载工作台数据失败: ' + e.message);
        }
      }
      // 加载已办数据
      try {
        const dr = await api.get('/api/workbench/done?page=1&size=4');
        const data = dr.data || {};
        const items = data.items || [];
        if (Array.isArray(items) && items.length) {
          doneItems.value = items.map(x => ({
            id: x.id,
            text: x.title,
            sub: x.sub || '',
            time: x.time,
            color: x.color || 'blue',
            route: x.route || 'my-done',
            instance_id: x.instance_id || null,
            biz_no: x.biz_no || '',
            type: x.type || '',
          }));
        } else {
          doneItems.value = [];
        }
      } catch(e) {
        doneItems.value = [];
      }
    }
    function go(key) {
      window.location.hash = '#/' + key;
      if (window.__go) window.__go(key);
    }
    // 打开待办卡片详情 - 跳审批中心并携带instance+task定位
    function openTodo(t) {
      if (!t) return;
      const route = t.route || 'approvals';
      const inst = t.instance_id ? ('?instance=' + t.instance_id) : '';
      const task = t.task_id ? ((inst ? '&' : '?') + 'task=' + t.task_id) : '';
      if (window.__go) window.__go(route);
      else go(route);
    }
    // 打开已办条目 - 跳转对应详情页面(我的已办页按instance定位)
    function openDone(d) {
      if (!d) return;
      const route = d.route || 'my-done';
      const params = [];
      if (d.instance_id) params.push('instance=' + d.instance_id);
      if (d.biz_no) params.push('no=' + encodeURIComponent(d.biz_no));
      const qs = params.length ? ('?' + params.join('&')) : '';
      if (window.__go) window.__go(route);
      else go(route);
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

    // 节点详情
    const nodeDetailVis = ref(false);
    const selectedNode = ref(null);
    function showNodeDetail(wf, n) {
      selectedNode.value = { ...n, _wf: wf };
      nodeDetailVis.value = true;
    }
    function nodeTypeLabel(ntype) {
      return { start: '开始节点', end: '结束节点', approve: '审批节点', item: '审批节点', 
               process: '流程节点', cc: '抄送节点', branch: '分支节点' }[ntype] || ntype || '未知';
    }
    function roleLabel(role) {
      const map = {
        ADMIN: '管理员', SALES: '销售', FINANCE: '财务', GM: '总经理',
        OPERATION: '运营', MANAGER: '经理',
      };
      return map[role] || role || '-';
    }
    function isMyNode(n) {
      return n && (n.status === 'active');
    }
    function fmtTime(s) {
      if (!s) return '';
      const d = new Date(s);
      return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    }

    onMounted(load);
    return { user, todos, workflowSteps, displayWorkflows, appGroups, groupList, userRoleLabel, roleLabel, isAdmin, showWorkflow, greeting, today, go, badge, Icon,
      kpis, doneItems, news, quickEntries, wfLineClass, deleteFlow, editFlow, openTodo, openDone,
      nodeDetailVis, selectedNode, showNodeDetail, nodeTypeLabel, isMyNode, fmtTime, openWorkflowList };
  }
};

// ============ 工作流列表页 (显示所有流程实例) ============
const WorkflowListPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('workflow', 22)"></div>
        <div>
          <div class="ph-title">业务流程</div>
          <div class="ph-sub">所有进行中的流程 <span class="muted" v-if="total"> · 共 {{total}} 条</span></div>
        </div>
      </div>
    </div>
    <div class="filter-bar">
      <el-input v-model="kw" placeholder="搜索订单号/标题" style="width:260px" clearable @keyup.enter="search" @clear="reset"/>
      <el-select v-model="statusFilter" placeholder="状态筛选" style="width:140px" clearable>
        <el-option label="进行中" value="RUNNING"/>
        <el-option label="已通过" value="APPROVED"/>
        <el-option label="已驳回" value="REJECTED"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>
    <div class="wf-list" v-loading="loading">
      <div v-for="(wf, wi) in rows" :key="wi" class="wf-list-card">
        <div class="wf-list-head">
          <div class="wf-list-title">{{wf.title}}</div>
          <div class="wf-list-bizno">{{wf.biz_no}}</div>
          <div class="wf-list-status" :class="wf.status">
            <span v-if="wf.status === 'RUNNING'">进行中</span>
            <span v-else-if="wf.status === 'APPROVED'">已通过</span>
            <span v-else-if="wf.status === 'REJECTED'">已驳回</span>
          </div>
        </div>
        <div class="wf-list-flow">
          <div v-for="(n, i) in wf.nodes" :key="i"
               :class="['wf-step', n.status, { 'wf-clickable': true }]"
               @click="showNodeDetail(wf, n)">
            <div class="wf-connector" v-if="i>0">
              <div :class="['wf-line', wfLineClass(i, wf.nodes)]"></div>
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
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('workflow', 44)"></div>
        <div class="title">暂无流程</div>
        <div class="desc">当前没有进行中的流程</div>
      </div>
    </div>
    <el-pagination v-if="total>size" style="margin-top:14px;justify-content:flex-end;display:flex"
      v-model:current-page="page" :page-size="size" :total="total" background layout="total, prev, pager, next"
      @current-change="load"/>

    <!-- 节点详情对话框 -->
    <el-dialog v-model="nodeDetailVis" title="节点详情" width="560px" :close-on-click-modal="true">
      <div class="node-detail" v-if="selectedNode">
        <div class="nd-header">
          <div :class="['nd-icon', selectedNode.status]">
            <span v-html="Icon.icon(selectedNode.icon || 'circle', 24)"></span>
          </div>
          <div class="nd-title">
            <div class="nd-name">{{selectedNode.name}}</div>
            <div :class="['nd-status', selectedNode.status]">
              {{selectedNode.status_text || '未知状态'}}
            </div>
          </div>
        </div>
        <div class="nd-section">
          <div class="nd-section-title">当前节点信息</div>
          <div class="nd-body">
            <div class="nd-row"><span class="nd-label">节点类型</span><span class="nd-value">{{nodeTypeLabel(selectedNode.ntype)}}</span></div>
            <div class="nd-row"><span class="nd-label">审批角色</span><span class="nd-value nd-role">{{roleLabel(selectedNode.assignee_role)}}</span></div>
            <div class="nd-row" v-if="selectedNode.assignee"><span class="nd-label">当前处理人</span><span class="nd-value nd-assignee"><span class="nd-avatar">{{selectedNode.assignee.charAt(0)}}</span>{{selectedNode.assignee}}</span></div>
            <div class="nd-row" v-if="selectedNode.count"><span class="nd-label">待办数量</span><span class="nd-value">{{selectedNode.count}} 条</span></div>
          </div>
        </div>
        <div class="nd-section" v-if="selectedNode.approve_history && selectedNode.approve_history.length">
          <div class="nd-section-title">审批历史 <span class="nd-count">({{selectedNode.approve_history.length}})</span></div>
          <div class="nd-timeline">
            <div v-for="(h, idx) in selectedNode.approve_history" :key="idx" class="nd-tl-item">
              <div :class="['nd-tl-dot', h.status === '通过' ? 'approved' : 'rejected']"></div>
              <div class="nd-tl-body">
                <div class="nd-tl-header">
                  <span class="nd-tl-name">{{h.name}}</span>
                  <span :class="['nd-tl-status', h.status === '通过' ? 'approved' : 'rejected']">{{h.status}}</span>
                </div>
                <div class="nd-tl-meta">
                  <span v-if="h.assignee">{{h.assignee}} 处理</span>
                  <span v-if="h.approved_at">· {{fmtTime(h.approved_at)}}</span>
                </div>
                <div v-if="h.comment" class="nd-tl-comment">{{h.comment}}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="nodeDetailVis = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'));
    const rows = ref([]);
    const total = ref(0);
    const page = ref(1);
    const size = ref(10);
    const kw = ref('');
    const statusFilter = ref('');
    const loading = ref(false);

    // 节点详情
    const nodeDetailVis = ref(false);
    const selectedNode = ref(null);
    
    function showNodeDetail(wf, n) {
      selectedNode.value = { ...n, _wf: wf };
      nodeDetailVis.value = true;
    }
    
    function nodeTypeLabel(ntype) {
      return { start: '开始节点', end: '结束节点', approve: '审批节点', item: '审批节点', 
               process: '流程节点', cc: '抄送节点', branch: '分支节点' }[ntype] || ntype || '未知';
    }
    
    function roleLabel(role) {
      const map = {
        ADMIN: '管理员', SALES: '销售', FINANCE: '财务', GM: '总经理',
        OPERATION: '运营', MANAGER: '经理',
      };
      return map[role] || role || '-';
    }
    
    function fmtTime(s) {
      if (!s) return '';
      const d = new Date(s);
      return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    }
    
    function wfLineClass(i, steps) {
      if (!steps || !steps.length) return '';
      const prev = steps[i - 1];
      if (!prev) return '';
      if (prev.status === 'done') return 'wf-line-done';
      if (prev.status === 'active') return 'wf-line-active';
      return '';
    }

    async function load() {
      loading.value = true;
      try {
        const params = new URLSearchParams();
        params.append('page', page.value);
        params.append('size', size.value);
        if (kw.value) params.append('keyword', kw.value);
        if (statusFilter.value) params.append('status', statusFilter.value);
        
        const r = await api.get('/api/workbench/workflow-steps?' + params.toString());
        const data = r.data || {};
        rows.value = data.items || [];
        total.value = data.total || 0;
      } catch (e) {
        rows.value = [];
        total.value = 0;
      } finally {
        loading.value = false;
      }
    }

    function search() { page.value = 1; load(); }
    function reset() { kw.value = ''; statusFilter.value = ''; page.value = 1; load(); }

    onMounted(load);
    return { Icon, rows, total, page, size, kw, statusFilter, loading, load, search, reset,
      nodeDetailVis, selectedNode, showNodeDetail, nodeTypeLabel, roleLabel, fmtTime, wfLineClass };
  }
};

// ============ 我的待办 / 我的已办 (直接跳转详情) ============
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
        <el-button @click="load" :loading="loading">刷新</el-button>
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
        <div v-for="r in rows" :key="r.id" class="doc-card" @click="jumpToDetail(r)">
          <div :class="'doc-bar '+ (r.color||'blue')"></div>
          <div class="doc-main">
            <div class="doc-top">
              <span class="doc-no">{{r.type_label||r.type}}</span>
              <span class="pill" :class="r.color||'blue'" v-if="r.tag">{{r.tag}}</span>
              <span class="doc-cust" v-if="r.biz_no">{{r.biz_no}}</span>
              <span class="doc-cust" v-else-if="r.title">{{r.title}}</span>
              <span class="doc-time muted" v-if="r.time">{{r.time}}</span>
              <span class="doc-arrow" style="margin-left:auto;color:var(--text3)">→</span>
            </div>
            <div class="doc-fields">
              <div class="doc-field" style="grid-column: span 4;" v-if="r.title && r.biz_no">
                <span class="df-label">标题</span>
                <span class="df-value">{{r.title}}</span>
              </div>
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

      async function load() {
        loading.value = true;
        try {
          let url = `${apiPath}?page=${page.value}&size=${size.value}`;
          if (kw.value) url += '&keyword=' + encodeURIComponent(kw.value);
          if (tagFilter.value) url += '&tag=' + encodeURIComponent(tagFilter.value);
          const r = await api.get(url);
          const data = r.data || {};
          rows.value = data.items || [];
          total.value = data.total || 0;
          tagTypes.value = data.tag_types || [];
        } catch(e) { console.error(e); rows.value = []; total.value = 0; }
        finally { loading.value = false; }
      }
      function search() { page.value=1; load(); }
      function reset() { kw.value=''; tagFilter.value=''; page.value=1; load(); }
      
      function jumpToDetail(r) {
        // 直接跳转到对应业务详情页
        if (r.route) {
          window.location.hash = '#/' + r.route;
          if (window.__go) window.__go(r.route);
        } else if (r.biz_type && r.biz_id) {
          // 跳转到审批中心，打开详情
          window.location.hash = '#/approvals';
          if (window.__go) window.__go('approvals');
        }
      }
      
      onMounted(load);
      return { rows, total, page, size, loading, kw, tagFilter, tagTypes, load, search, reset, jumpToDetail, Icon };
    }
  };
}
const MyTodosPage = makeMyListPage({
  kind: 'todos', title: '我的待办', sub: '个人相关的待处理事项(按紧急度排序)',
  icon: 'check', apiPath: '/api/workbench/todos', emptyText: '暂无待办事项'
});
const MyDonePage = makeMyListPage({
  kind: 'done', title: '我的已办', sub: '所有历史已完成/确认的业务单据(按时间倒序)',
  icon: 'clipboard-check', apiPath: '/api/workbench/done', emptyText: '暂无已办记录'
});

// ============ 调价申请列表页 ============
const SalesAdjustmentPage = {
  components: { NodeFormView, FlowTrack },
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('tag', 22)"></div>
        <div>
          <div class="ph-title">调价申请</div>
          <div class="ph-sub">销售发起的实收调价审批记录<span class="muted" v-if="rows.length"> · 共 {{rows.length}} 条</span></div>
        </div>
      </div>
      <div>
        <el-button type="primary" v-if="canCreate" @click="openCreate">+ 新建调价申请</el-button>
      </div>
    </div>
    <div class="doc-list" v-loading="loading">
      <div v-for="r in rows" :key="r.id" class="doc-card">
        <div :class="'doc-bar ' + (r.status==='APPROVED'?'green':r.status==='REJECTED'?'red':'blue')"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{r.adj_no}}</span>
            <span class="pill" :class="r.status==='APPROVED'?'green':r.status==='REJECTED'?'red':'blue'">{{r.status==='APPROVED'?'已通过':r.status==='REJECTED'?'已驳回':'审批中'}}</span>
            <span class="doc-cust">订单 {{r.order_no}}</span>
            <span class="doc-time muted">{{r.created_at}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">原应收</span><span class="df-value">¥{{r.original_amount}}</span></div>
            <div class="doc-field"><span class="df-label">调整后</span><span class="df-value">¥{{r.adjusted_amount}}</span></div>
            <div class="doc-field"><span class="df-label">差额</span><span class="df-value" :style="{color: r.diff_amount<0?'#e74c3c':'#27ae60'}">{{r.diff_amount<0?'':''}}¥{{r.diff_amount}}</span></div>
            <div class="doc-field"><span class="df-label">发起人</span><span class="df-value">{{r.initiator}}</span></div>
            <div class="doc-field" style="grid-column: span 4;"><span class="df-label">调价原因</span><span class="df-value">{{r.reason||'-'}}</span></div>
          </div>
          <div style="margin-top:8px;text-align:right">
            <el-button size="small" link type="primary" @click="openTrack(r)">流转轨迹 / 审批操作</el-button>
          </div>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('tag', 44)"></div>
        <div class="title">暂无调价申请</div>
        <div class="desc">点击右上角"新建调价申请"发起审批</div>
      </div>
    </div>

    <!-- 新建调价申请对话框 - 基础业务字段 + 流程动态表单 -->
    <el-dialog v-model="createVis" title="新建调价申请" width="720px" :close-on-click-modal="false" :close-on-press-escape="false">
      <el-form label-width="90px" style="margin-bottom:8px">
        <el-form-item label="选择订单" required>
          <el-select v-model="adjForm.order_id" filterable placeholder="选择生效订单" style="width:100%" @change="onOrderChange">
            <el-option v-for="o in orderOptions" :key="o.id" :label="o.order_no + ' / ' + (o.customer_name||'') + ' / ¥' + o.total_amount" :value="o.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="原应收" v-if="selectedOrder">
          <span style="font-weight:700">¥{{selectedOrder.total_amount}}</span>
        </el-form-item>
        <el-form-item label="调整后" required>
          <el-input-number v-model="adjForm.new_amount" :min="0" :precision="2" style="width:220px"/>
        </el-form-item>
        <el-form-item label="调价原因" required>
          <el-input v-model="adjForm.reason" type="textarea" :rows="2" placeholder="请说明调价原因"/>
        </el-form-item>
      </el-form>
      <NodeFormView
        v-if="formConfig && formConfig.fields && formConfig.fields.length"
        ref="formViewRef"
        :formConfig="formConfig"
        :bizData="selectedOrder"
        mode="create"
      />
      <template #footer>
        <el-button @click="createVis = false">取消</el-button>
        <el-button type="primary" @click="submitAdj" :loading="submitting">提交审批</el-button>
      </template>
    </el-dialog>

    <!-- 流转轨迹抽屉: 查看审批进度/驳回后重新发起 -->
    <el-drawer v-model="trackVis" title="流转轨迹" size="620px">
      <flow-track v-if="trackRow" :biz-type="'SALES_ADJUSTMENT'" :biz-id="trackRow.id" :key="trackRow.id + '_' + trackTick"/>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]);
    const loading = ref(false);
    const createVis = ref(false);
    const submitting = ref(false);
    const formViewRef = ref(null);
    const orderOptions = ref([]);
    const formConfig = ref(null);
    const trackVis = ref(false);
    const trackRow = ref(null);
    const trackTick = ref(0);

    function openTrack(r) {
      trackRow.value = r;
      trackTick.value += 1;
      trackVis.value = true;
    }

    const userRole = JSON.parse(localStorage.getItem(USER_KEY) || '{}').role || '';
    const canCreate = ['SALES', 'ADMIN'].includes(userRole);

    const adjForm = reactive({
      order_id: null, new_amount: 0, reason: ''
    });

    const selectedOrder = computed(() => {
      return orderOptions.value.find(o => o.id === adjForm.order_id);
    });

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/approvals/price-adjustments');
        rows.value = r.data || [];
      } catch(e) { console.error(e); rows.value = []; }
      finally { loading.value = false; }
    }

    async function loadOrders() {
      try {
        const r = await api.get('/api/orders?status=EFFECTIVE&size=50');
        orderOptions.value = r.data || [];
      } catch(e) { console.error(e); orderOptions.value = []; }
    }

    async function loadFormConfig() {
      try {
        const r = await api.get('/api/approvals/definitions?biz_type=SALES_ADJUSTMENT');
        if (r.data && r.data.length > 0) {
          const fd = r.data[0];
          if (fd.nodes && fd.nodes.length > 0) {
            formConfig.value = fd.nodes[0].form_config || null;
          }
        }
      } catch(e) {
        console.error(e);
        formConfig.value = null;
      }
    }

    function openCreate() {
      Object.assign(adjForm, { order_id: null, new_amount: 0, reason: '' });
      loadOrders();
      loadFormConfig();
      createVis.value = true;
    }

    function onOrderChange() {
      if (selectedOrder.value) {
        adjForm.new_amount = selectedOrder.value.total_amount;
      }
    }

    async function submitAdj() {
      try {
        if (!adjForm.order_id) { ElMessage.warning('请选择订单'); return; }
        if (!adjForm.reason) { ElMessage.warning('请填写调价原因'); return; }
        if (formViewRef.value && !formViewRef.value.validate()) {
          ElMessage.warning('请完善表单必填项');
          return;
        }
        submitting.value = true;
        
        // 收集表单数据
        let formData = {};
        if (formViewRef.value) {
          formData = formViewRef.value.getFormData();
        }
        
        const newAmt = adjForm.new_amount || formData.order_amount || 0;
        const order = selectedOrder.value;
        const original = order ? order.total_amount : newAmt;
        const diff = newAmt - original;
        const type = diff < 0 ? 'DECREASE' : 'INCREASE';
        
        await api.post('/api/approvals/price-adjustment', {
          order_id: adjForm.order_id || formData.order_id,
          type: type,
          method: 'FIXED',
          amount: Math.abs(diff),
          percent: 0,
          new_amount: newAmt,
          reason: formData.remark || formData.reason || adjForm.reason || '调价申请',
        });
        ElMessage.success('调价申请已提交');
        createVis.value = false;
        load();
      } catch(e) {
        if (e.message) ElMessage.error(e.message);
      } finally {
        submitting.value = false;
      }
    }

    onMounted(load);
    return { rows, loading, Icon, createVis, submitting, adjForm,
             orderOptions, selectedOrder, canCreate, formConfig, formViewRef,
             openCreate, onOrderChange, submitAdj,
             trackVis, trackRow, trackTick, openTrack };
  }
};

// ============ 编号规则管理 (仅ADMIN) ============
const NumberRulesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('hash', 22)"></div>
        <div>
          <div class="ph-title">单据编号状态</div>
          <div class="ph-sub">系统自动生成编号，序号单调递增永不重置，确保唯一性</div>
        </div>
      </div>
      <div>
        <el-button @click="load" :loading="loading">刷新</el-button>
      </div>
    </div>
    <el-table :data="rules" v-loading="loading" stripe>
      <el-table-column prop="biz_type_label" label="业务类型" width="120"/>
      <el-table-column prop="prefix" label="编号前缀" width="100">
        <template #default="r">
          <span style="font-family:monospace;font-weight:bold;color:#3b82f6">{{r.row.prefix}}</span>
        </template>
      </el-table-column>
      <el-table-column label="编号格式预览" width="280">
        <template #default="r">
          <span style="font-family:monospace;background:#f8fafc;padding:4px 8px;border-radius:4px">
            {{r.row.prefix}}-YYYYMMDD-{{'X'.repeat(r.row.seq_length)}}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="current_seq" label="已生成数量" width="100" align="right"/>
      <el-table-column prop="next_number" label="下一个编号" width="200">
        <template #default="r">
          <span style="font-family:monospace;font-weight:bold;color:#10b981">{{r.row.next_number}}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="muted" style="margin-top:16px;padding:12px;background:#f8fafc;border-radius:6px">
      💡 编号规则由系统预设，业务流程自动匹配对应编号头。序号单调递增永不重置，确保全局唯一性。<br>
      如需新增业务类型，请联系系统管理员修改代码中的编号映射表。
    </div>
  </div>`,
  setup() {
    const rules = ref([]);
    const loading = ref(false);

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/approvals/number-rules');
        rules.value = r.data || [];
      } catch(e) { console.error(e); rules.value = []; }
      finally { loading.value = false; }
    }

    onMounted(load);
    return { rules, loading, Icon, load };
  }
};

// ============ 用户管理 / 角色管理 (仅ADMIN) ============
const UsersPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('users', 22)"></div>
        <div>
          <div class="ph-title">用户管理</div>
          <div class="ph-sub">维护系统登录用户、角色分配、密码重置、页面权限<span class="muted" v-if="total!=null"> · 共 {{total}} 个用户</span></div>
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
      <el-button v-if="selectedRows.length" type="success" size="small" @click="exportSelected">导出选中({{selectedRows.length}})</el-button>
      <el-button type="success" size="small" @click="exportAll">导出全部</el-button>
    </div>
    <el-table ref="tableRef" :data="rows" v-loading="loading" stripe style="width:100%;--el-table-bg-color:transparent;--el-table-tr-bg-color:transparent;" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="42"/>
      <el-table-column label="ID" width="60" prop="id"/>
      <el-table-column label="账号" width="160" prop="username"/>
      <el-table-column label="姓名" width="140" prop="real_name"/>
      <el-table-column label="角色" width="160">
        <template #default="s"><el-tag size="small">{{s.row.role?.name||'-'}}</el-tag></template>
      </el-table-column>
      <el-table-column label="页面权限" width="120">
        <template #default="s">
          <el-tag v-if="s.row.pages && s.row.pages.length" type="warning" size="small">{{s.row.pages.length}} 项(自定义)</el-tag>
          <el-tag v-else type="info" size="small">继承角色</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="s">
          <el-tag size="small" :type="s.row.status==='ACTIVE'?'success':'danger'">{{s.row.status==='ACTIVE'?'启用':'停用'}}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="350">
        <template #default="s">
          <el-button size="small" type="primary" is-plain @click="openEdit(s.row)">编辑</el-button>
          <el-button size="small" type="warning" is-plain @click="openUserPerm(s.row)">页面权限</el-button>
          <el-button size="small" type="info" is-plain @click="resetPwd(s.row)">重置密码</el-button>
          <el-button size="small" type="danger" is-plain @click="remove(s.row)">停用</el-button>
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
            <el-radio label="ACTIVE">启用</el-radio>
            <el-radio label="DISABLED">停用</el-radio>
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

    <el-dialog v-model="permDlg.show" :title="'页面权限 - ' + (permDlg.user?.real_name||permDlg.user?.username||'')" width="720px" :close-on-click-modal="false">
      <el-alert style="margin-bottom:12px" type="info" :closable="false" show-icon>
        为空时继承角色权限。勾选后覆盖角色权限，仅对该用户生效。
      </el-alert>
      <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
        <el-button size="small" @click="clearUserPerm">清空(继承角色)</el-button>
        <el-button size="small" @click="copyRolePerm">复制角色权限</el-button>
        <el-button size="small" @click="selectAllUserPerm">全选</el-button>
        <span class="muted" style="margin-left:auto">已选 {{permDlg.selected.length}} 项</span>
      </div>
      <el-checkbox-group v-model="permDlg.selected">
        <el-row :gutter="16" v-for="group in groupedCatalog" :key="group.name">
          <el-col :span="24" style="margin-bottom:8px">
            <div style="font-weight:600;font-size:13px;color:var(--text-secondary);border-bottom:1px solid var(--border);padding-bottom:4px;margin-bottom:8px">{{group.name}}</div>
          </el-col>
          <el-col :span="6" v-for="p in group.items" :key="p.key" style="margin-bottom:6px">
            <el-checkbox :label="p.key">{{p.label}}</el-checkbox>
          </el-col>
        </el-row>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="permDlg.show=false">取消</el-button>
        <el-button type="primary" @click="saveUserPerm">保存权限</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ElMessage, ElMessageBox } = ElementPlus;
    const tableRef = ref(null);
    const rows = ref([]), total = ref(0), page = ref(1), size = ref(20), loading = ref(false);
    const kw = ref(''), roleFilter = ref(null), roles = ref([]);
    const selectedRows = ref([]);
    const catalog = ref([]);
    const dlg = reactive({ show:false, id:null, username:'', real_name:'', role_id:null, status:'ACTIVE', password:'123456' });
    const permDlg = reactive({ show:false, user:null, selected: [] });
    
    const groupedCatalog = computed(() => {
      const g = {};
      for (const p of catalog.value) {
        if (!g[p.group]) g[p.group] = [];
        g[p.group].push(p);
      }
      return Object.entries(g).map(([name, items]) => ({ name, items }));
    });
    
    function onSelectionChange(sel) { selectedRows.value = sel; }
    function exportSelected() {
      if (!selectedRows.value.length) { ElMessage.warning('请先勾选要导出的行'); return; }
      const headers = ['ID', '账号', '姓名', '角色', '状态', '创建时间'];
      const data = selectedRows.value.map(r => [r.id, r.username, r.real_name || r.name, r.role?.name || '-', r.status === 'ACTIVE' ? '启用' : '停用', r.created_at || '']);
      exportToExcel(headers, data, `用户管理_${new Date().toISOString().slice(0,10)}`, '用户列表');
      ElMessage.success(`已导出 ${data.length} 条记录`);
    }
    async function exportAll() {
      const r = await api.get('/api/admin/users?page=1&size=500');
      const allRows = r.data || [];
      if (!allRows.length) { ElMessage.warning('暂无数据'); return; }
      const headers = ['ID', '账号', '姓名', '角色', '状态', '创建时间'];
      const data = allRows.map(r => [r.id, r.username, r.real_name || r.name, r.role?.name || '-', r.status === 'ACTIVE' ? '启用' : '停用', r.created_at || '']);
      exportToExcel(headers, data, `用户管理_全部_${new Date().toISOString().slice(0,10)}`, '用户列表');
      ElMessage.success(`已导出 ${data.length} 条记录`);
    }
    async function loadRoles() {
      try { roles.value = (await api.get('/api/admin/roles')).data || []; } catch(e){}
    }
    async function loadCatalog() {
      try { catalog.value = (await api.get('/api/admin/page-catalog')).data || []; } catch(e){}
    }
    async function load(p) {
      if (p) page.value = p;
      loading.value = true;
      try {
        const params = new URLSearchParams();
        params.set('page', page.value); params.set('size', size.value);
        if (kw.value) params.set('keyword', kw.value.trim());
        if (roleFilter.value) params.set('role_id', roleFilter.value);
        const r = await api.get('/api/admin/users?' + params.toString());
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
    function openUserPerm(r) {
      permDlg.user = r;
      permDlg.selected = [...(r.pages || [])];
      permDlg.show = true;
    }
    function clearUserPerm() { permDlg.selected = []; }
    function copyRolePerm() {
      const role = roles.value.find(r2 => r2.id === permDlg.user.role?.id);
      permDlg.selected = [...(role?.pages || [])];
    }
    function selectAllUserPerm() { permDlg.selected = catalog.value.map(p => p.key); }
    async function saveUserPerm() {
      try {
        await api.put('/api/admin/users/' + permDlg.user.id, { pages: permDlg.selected });
        ElMessage.success('页面权限已保存');
        permDlg.show = false; load();
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
        await ElMessageBox.confirm(
          `确定停用用户「${r.username}」？停用后不可登录，历史单据与审计记录保留。若其名下有待办任务，需先转交后才能停用。`,
          '停用用户', { type:'error' }
        );
        await api.del('/api/admin/users/' + r.id);
        ElMessage.success('已停用'); load();
      } catch(e) { if (e !== 'cancel' && e?.message) ElMessage.error(e.message); }
    }
    onMounted(async () => { await loadRoles(); loadCatalog(); load(); });
    return { rows, total, page, size, loading, kw, roleFilter, roles, dlg, permDlg, groupedCatalog, load, openCreate, openEdit, submit, openUserPerm, clearUserPerm, copyRolePerm, selectAllUserPerm, saveUserPerm, resetPwd, remove, tableRef, selectedRows, onSelectionChange, exportSelected, exportAll, Icon };
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
          <div class="ph-sub">维护业务角色编码与名称 · 配置角色可访问的页面权限<span class="muted" v-if="roles.length"> · 共 {{roles.length}} 个角色</span></div>
        </div>
      </div>
      <div>
        <el-button type="primary" @click="openCreate">+ 新增角色</el-button>
      </div>
    </div>
    <div class="filter-bar">
      <div class="grow"></div>
      <el-button v-if="selectedRows.length" type="success" size="small" @click="exportSelected">导出选中({{selectedRows.length}})</el-button>
      <el-button type="success" size="small" @click="exportAll">导出全部</el-button>
    </div>
    <el-table ref="tableRef" :data="roles" stripe style="width:100%" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="42"/>
      <el-table-column label="ID" width="80" prop="id"/>
      <el-table-column label="编码" width="180">
        <template #default="s"><code style="color:var(--primary2)">{{s.row.code}}</code></template>
      </el-table-column>
      <el-table-column label="名称" width="180" prop="name"/>
      <el-table-column label="页面权限" width="120">
        <template #default="s">
          <el-tag v-if="s.row.pages && s.row.pages.length" type="success" size="small">{{s.row.pages.length}} 项</el-tag>
          <el-tag v-else type="info" size="small">未配置</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="说明" prop="description"/>
      <el-table-column label="操作" width="320">
        <template #default="s">
          <el-button size="small" type="warning" is-plain @click="openPerm(s.row)">权限</el-button>
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

    <el-dialog v-model="permDlg.show" :title="'配置权限 - ' + (permDlg.role?.name||'')" width="720px" :close-on-click-modal="false">
      <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
        <el-button size="small" @click="selectAllPerm">全选</el-button>
        <el-button size="small" @click="clearAllPerm">清空</el-button>
        <el-button size="small" @click="selectGroup('核心')">核心模块</el-button>
        <el-button size="small" @click="selectGroup('销售')">销售</el-button>
        <el-button size="small" @click="selectGroup('仓储')">仓储</el-button>
        <el-button size="small" @click="selectGroup('采购')">采购</el-button>
        <el-button size="small" @click="selectGroup('生产')">生产</el-button>
        <el-button size="small" @click="selectGroup('财务')">财务</el-button>
        <span class="muted" style="margin-left:auto">已选 {{permDlg.selected.length}} 项</span>
      </div>
      <el-checkbox-group v-model="permDlg.selected">
        <el-row :gutter="16" v-for="group in groupedPages" :key="group.name">
          <el-col :span="24" style="margin-bottom:8px">
            <div style="font-weight:600;font-size:13px;color:var(--text-secondary);border-bottom:1px solid var(--border);padding-bottom:4px;margin-bottom:8px">{{group.name}}</div>
          </el-col>
          <el-col :span="6" v-for="p in group.items" :key="p.key" style="margin-bottom:6px">
            <el-checkbox :label="p.key">{{p.label}}</el-checkbox>
          </el-col>
        </el-row>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="permDlg.show=false">取消</el-button>
        <el-button type="primary" @click="savePerm">保存权限</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const { ElMessage, ElMessageBox } = ElementPlus;
    const tableRef = ref(null);
    const roles = ref([]);
    const catalog = ref([]);
    const selectedRows = ref([]);
    const dlg = reactive({ show:false, id:null, code:'', name:'', description:'' });
    const permDlg = reactive({ show:false, role:null, selected: [] });

    const groupedPages = computed(() => {
      const g = {};
      for (const p of catalog.value) {
        if (!g[p.group]) g[p.group] = [];
        g[p.group].push(p);
      }
      return Object.entries(g).map(([name, items]) => ({ name, items }));
    });

    function onSelectionChange(sel) { selectedRows.value = sel; }
    function exportSelected() {
      if (!selectedRows.value.length) { ElMessage.warning('请先勾选要导出的行'); return; }
      const headers = ['ID', '编码', '名称', '页面权限数', '说明'];
      const data = selectedRows.value.map(r => [r.id, r.code, r.name, (r.pages||[]).length, r.description || '']);
      exportToExcel(headers, data, `角色管理_${new Date().toISOString().slice(0,10)}`, '角色列表');
      ElMessage.success(`已导出 ${data.length} 条记录`);
    }
    async function exportAll() {
      const headers = ['ID', '编码', '名称', '页面权限数', '说明'];
      const data = roles.value.map(r => [r.id, r.code, r.name, (r.pages||[]).length, r.description || '']);
      if (!data.length) { ElMessage.warning('暂无数据'); return; }
      exportToExcel(headers, data, `角色管理_全部_${new Date().toISOString().slice(0,10)}`, '角色列表');
      ElMessage.success(`已导出 ${data.length} 条记录`);
    }

    async function load() {
      try { roles.value = (await api.get('/api/admin/roles')).data || []; }
      catch(e) { ElMessage.error(e.message||'加载失败'); }
    }
    async function loadCatalog() {
      try { catalog.value = (await api.get('/api/admin/page-catalog')).data || []; } catch(e) {}
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
        // 先查引用: 有引用则必须合并到目标角色, 无引用可直接删
        const refs = (await api.get('/api/admin/roles/' + r.id + '/refs')).data || { users:0, pending_tasks:0, flow_defs:0, total:0 };
        if (!refs.total) {
          await ElMessageBox.confirm(`确定删除角色「${r.name}」？`, '删除角色', { type:'error' });
          await api.del('/api/admin/roles/' + r.id);
          ElMessage.success('已删除'); load(); return;
        }
        await ElMessageBox.confirm(
          `角色「${r.name}」仍有 ${refs.total} 处关联（用户${refs.users}/待办${refs.pending_tasks}/流程${refs.flow_defs}），删除将把这些关联一并迁移到目标角色。`,
          '删除角色', { type:'warning' }
        );
        const { value } = await ElMessageBox.prompt('输入合并目标角色编码(如 OPERATION)。用户/待办/流程节点将迁移到该角色，历史已完成数据保留原角色。', '选择合并目标角色', {
          inputPlaceholder: '目标角色编码',
        });
        const t = value ? value.trim().toUpperCase() : '';
        if (!t) { ElMessage.warning('目标角色不能为空'); return; }
        await api.del('/api/admin/roles/' + r.id + '?merge_to=' + encodeURIComponent(t));
        ElMessage.success('已删除并合并到 ' + t); load();
      } catch(e) { if (e !== 'cancel' && e?.message) ElMessage.error(e.message); }
    }
    function openPerm(r) {
      permDlg.role = r;
      permDlg.selected = [...(r.pages || [])];
      permDlg.show = true;
    }
    function selectAllPerm() { permDlg.selected = catalog.value.map(p => p.key); }
    function clearAllPerm() { permDlg.selected = []; }
    function selectGroup(groupName) {
      const keys = catalog.value.filter(p => p.group === groupName).map(p => p.key);
      const set = new Set([...permDlg.selected, ...keys]);
      permDlg.selected = [...set];
    }
    async function savePerm() {
      try {
        await api.put('/api/admin/roles/' + permDlg.role.id, { pages: permDlg.selected });
        ElMessage.success('权限已保存');
        permDlg.show = false; load();
      } catch(e) { ElMessage.error(e.message||'保存失败'); }
    }
    onMounted(() => { load(); loadCatalog(); });
    return { roles, catalog, dlg, permDlg, groupedPages, load, openCreate, openEdit, submit, remove, openPerm, selectAllPerm, clearAllPerm, selectGroup, savePerm, tableRef, selectedRows, onSelectionChange, exportSelected, exportAll, Icon };
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
    { key: 'name', label: '名称', type: 'text', w: 300, required: true },
    { key: 'tax_no', label: '税号', type: 'text', w: 260 },
    { key: 'address', label: '地址', type: 'text', w: 360, required: true },
    { key: 'contact_name', label: '联系人', type: 'text', w: 160, required: true },
    { key: 'contact_phone', label: '电话', type: 'text', w: 180, required: true },
    { key: 'industry', label: '行业', type: 'select', w: 160, required: true, options: [
      {v: '汽配', l: '汽配'}, {v: '家电', l: '家电'}, {v: '五金', l: '五金'},
      {v: '化工', l: '化工'}, {v: '其他', l: '其他'},
    ]},
    { key: 'settlement_cycle', label: '结算周期', type: 'select', w: 200, required: true, options: [
      {v: '月结30天', l: '月结30天'}, {v: '月结60天', l: '月结60天'},
      {v: '月结90天', l: '月结90天'}, {v: '款到发货', l: '款到发货'},
    ]},
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
  components: { NodeFormView, FlowTrack, FlowMini },
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
        <el-button v-if="canCreateOrder" type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建订单</el-button>
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

    <el-dialog v-model="dialog.visible" title="新建订单 · 来料加工入库单" width="960px" top="4vh" class="order-create-dialog">
      <el-form :model="form" label-width="100px">
        <!-- ===== 核心业务字段(固定 + 美观 Grid) ===== -->
        <div class="order-form-core">
          <div class="ofc-grid">
            <el-form-item label="客户" required>
              <el-select v-model="form.customer_id" placeholder="选择客户" filterable style="width:100%">
                <el-option v-for="c in custs" :key="c.id" :label="c.code+' '+c.name" :value="c.id"/>
              </el-select>
            </el-form-item>
            <el-form-item label="开票主体">
              <el-select v-model="form.company_id" placeholder="公司主体" style="width:100%">
                <el-option v-for="c in comps" :key="c.id" :label="c.short_name||c.name" :value="c.id"/>
              </el-select>
            </el-form-item>
            <el-form-item label="开票类型">
              <el-select v-model="form.billing_type" placeholder="款项流向" style="width:100%">
                <el-option label="增值税专用发票" value="SPECIAL_VAT"/>
                <el-option label="增值税普通发票" value="NORMAL"/>
                <el-option label="现金(无票)" value="CASH"/>
              </el-select>
            </el-form-item>
            <el-form-item label="预收款">
              <el-input-number v-model="form.prepayment_amount" :min="0" :precision="2" style="width:100%"/>
            </el-form-item>
          </div>
          <div class="ofc-tip">订单生效时自动建收款单核销应收</div>
        </div>

        <!-- ===== 动态表单: 从 CORE_PRODUCTION 流程定义第1节点 form_config 读取(画布配置→DB→此处渲染,彻底杜绝硬编码) ===== -->
        <NodeFormView
          v-if="orderFormConfig && orderFormConfig.fields && orderFormConfig.fields.length"
          ref="orderFormViewRef"
          :formConfig="orderFormConfig"
          mode="create"
        />

        <!-- ===== 订单明细表格(喷涂工件) ===== -->
        <el-divider class="ofc-divider"><span class="ofc-divider-text">订单明细 · 喷涂工件</span></el-divider>
        <div class="order-items-wrap">
          <div class="items-grid items-grid-head">
            <div>工件名</div><div>规格</div><div>计价</div><div>数量</div><div>单位</div><div>单价</div><div>料属</div><div>材料种类</div><div>工艺类型</div><div>材料厚度</div><div style="width:44px"></div>
          </div>
          <div v-for="(it,i) in form.items" :key="i" class="items-grid items-grid-row">
            <el-input v-model="it.part_name" placeholder="工件名"/>
            <el-input v-model="it.part_spec" placeholder="规格"/>
            <el-select v-model="it.price_type" placeholder="计价">
              <el-option label="按件" value="BY_PIECE"/>
              <el-option label="按面积" value="BY_AREA"/>
              <el-option label="按重量" value="BY_WEIGHT"/>
            </el-select>
            <el-input-number v-model="it.quantity" :min="0" placeholder="数量" controls-position="right"/>
            <el-input v-model="it.unit" placeholder="单位"/>
            <el-input-number v-model="it.unit_price" :min="0" :precision="2" placeholder="单价" controls-position="right"/>
            <el-select v-model="it.material_mode" placeholder="料属">
              <el-option label="自营料" value="SELF"/>
              <el-option label="客供料" value="CUSTOMER"/>
            </el-select>
            <el-input v-model="it.paint_spec" placeholder="材料种类"/>
            <el-select v-model="it.craft_type" placeholder="选择工艺" filterable>
              <el-option label="超音速" value="超音速"/>
              <el-option label="等离子" value="等离子"/>
              <el-option label="氧乙炔火焰陶瓷棒" value="氧乙炔火焰陶瓷棒"/>
              <el-option label="碳化钨防粘" value="碳化钨防粘"/>
              <el-option label="碳纤维防粘" value="碳纤维防粘"/>
            </el-select>
            <el-input v-model="it.material_thickness" placeholder="如 0.3mm"/>
            <el-button link type="danger" @click="form.items.splice(i,1)"><span v-html="Icon.icon('trash',14)"></span></el-button>
          </div>
          <el-button size="small" class="add-item-btn" @click="form.items.push({seq:form.items.length+1,price_type:'BY_AREA',unit:'m²',material_mode:'SELF'})">
            <span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:4px"></span>添加工件明细
          </el-button>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible=false">取消</el-button>
        <el-button type="primary" @click="submit">保存草稿</el-button>
      </template>
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

    <!-- 调价申请对话框 -->
    <el-dialog v-model="priceAdj.visible" title="调价申请" width="600px">
      <div v-if="priceAdj.order" style="margin-bottom:16px;padding:12px;background:var(--panel2);border-radius:8px">
        <div style="font-weight:600;margin-bottom:8px">订单信息</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div>订单号: {{priceAdj.order.order_no}}</div>
          <div>客户: {{priceAdj.order.customer_name}}</div>
          <div>原金额: ¥{{fmt(priceAdj.order.total_amount)}}</div>
          <div>状态: {{ORDER_STATUS[priceAdj.order.status]}}</div>
        </div>
      </div>
      <el-form :model="priceAdj.form" label-width="100px">
        <el-form-item label="调价类型">
          <el-select v-model="priceAdj.form.type" style="width:100%">
            <el-option label="降价" value="DECREASE"/>
            <el-option label="涨价" value="INCREASE"/>
          </el-select>
        </el-form-item>
        <el-form-item label="调价方式">
          <el-radio-group v-model="priceAdj.form.method">
            <el-radio label="FIXED">固定金额</el-radio>
            <el-radio label="PERCENT">百分比</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="调价金额" v-if="priceAdj.form.method==='FIXED'">
          <el-input-number v-model="priceAdj.form.amount" :min="0" :precision="2" style="width:200px"/>
        </el-form-item>
        <el-form-item label="调价比例" v-else>
          <el-input-number v-model="priceAdj.form.percent" :min="0" :max="100" :precision="2" style="width:200px"/>
          <span style="margin-left:8px">%</span>
        </el-form-item>
        <el-form-item label="原金额">
          <span>¥{{fmt(priceAdj.order?.total_amount)}}</span>
        </el-form-item>
        <el-form-item label="新金额">
          <span style="color:var(--primary);font-weight:600;font-size:18px">¥{{fmt(calcNewAmount())}}</span>
        </el-form-item>
        <el-form-item label="调价原因" required>
          <el-input v-model="priceAdj.form.reason" type="textarea" :rows="3" placeholder="请详细说明调价原因..."/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="priceAdj.visible=false">取消</el-button>
        <el-button type="primary" @click="submitPriceAdj">提交申请</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const currentUser = JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    const userRole = currentUser?.role || '';
    const canCreateOrder = ['SALES', 'ADMIN', 'GM'].includes(userRole);
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ keyword: '', status: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const profit = reactive({ visible: false, data: {} });
    const priceAdj = reactive({ 
      visible: false, 
      order: null, 
      form: { type: 'DECREASE', method: 'FIXED', amount: 0, percent: 0, reason: '' } 
    });
    const custs = ref([]);
    const comps = ref([]);
    const form = reactive({ customer_id: null, company_id: null, billing_type: null, prepayment_amount: 0, items: [{ seq: 1, price_type: 'BY_AREA', unit: 'm²', material_mode: 'SELF' }] });
    // ===== 动态表单: 从流程定义DB读取form_config,彻底杜绝硬编码扩展字段 =====
    const orderFormConfig = ref(null);
    const orderFormViewRef = ref(null);
    async function loadOrderFormConfig() {
      try {
        // 销售订单仅加载ORDER类型流程表单，禁止加载CORE_PRODUCTION(生产申请)表单导致字段重复
        const r = await api.get('/api/approvals/definitions?biz_type=ORDER');
        if (r.data && r.data.length > 0) {
          const fd = r.data[0];
          if (fd.nodes && fd.nodes.length > 0) {
            orderFormConfig.value = fd.nodes[0].form_config || null;
            return;
          }
        }
        orderFormConfig.value = null;
      } catch (e) {
        console.warn('[订单表单] 加载流程定义form_config失败，使用纯核心字段模式', e.message || e);
        orderFormConfig.value = null;
      }
    }
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
        const [rc, rp] = await Promise.all([api.get('/api/customers'), api.get('/api/companies'), loadOrderFormConfig()]);
        custs.value = rc.data; comps.value = rp.data || [];
      } catch {}
      Object.assign(form, { customer_id: null, company_id: null, billing_type: null, prepayment_amount: 0, items: [{ seq: 1, price_type: 'BY_AREA', unit: 'm²', material_mode: 'SELF' }] });
      dialog.visible = true;
    }
    async function submit() {
      // 校验动态表单必填
      if (orderFormViewRef.value && typeof orderFormViewRef.value.validate === 'function') {
        if (!orderFormViewRef.value.validate()) {
          ElMessage.warning('请完善表单必填项');
          return;
        }
      }
      let formData = {};
      if (orderFormViewRef.value && typeof orderFormViewRef.value.getFormData === 'function') {
        formData = orderFormViewRef.value.getFormData() || {};
      }
      const payload = { ...form, form_data: Object.keys(formData).length ? formData : null };
      try { await api.post('/api/orders', payload); ElMessage.success('订单已创建(草稿)'); dialog.visible = false; load(); }
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
    function openPriceAdj(row) {
      priceAdj.order = row;
      priceAdj.form = { type: 'DECREASE', method: 'FIXED', amount: 0, percent: 0, reason: '' };
      priceAdj.visible = true;
    }
    function calcNewAmount() {
      if (!priceAdj.order) return 0;
      const orig = priceAdj.order.total_amount || 0;
      const { type, method, amount, percent } = priceAdj.form;
      let adj = 0;
      if (method === 'FIXED') {
        adj = amount || 0;
      } else {
        adj = orig * (percent || 0) / 100;
      }
      return type === 'DECREASE' ? Math.max(0, orig - adj) : orig + adj;
    }
    async function submitPriceAdj() {
      if (!priceAdj.form.reason) {
        ElMessage.warning('请填写调价原因');
        return;
      }
      try {
        await api.post('/api/approvals/price-adjustment', {
          order_id: priceAdj.order.id,
          ...priceAdj.form,
          new_amount: calcNewAmount(),
        });
        ElMessage.success('调价申请已提交');
        priceAdj.visible = false;
        go('sales-adjustments');
      } catch (e) {
        ElMessage.error(e.message || '提交失败');
      }
    }
    const compName = id => { const c = comps.value.find(x => x.id === id); return c ? (c.short_name || c.name) : '-'; };
    function printOrder(d) {
      const d2 = d => d < 10 ? '0' + d : '' + d;
      const now = new Date();
      const dateStr = now.getFullYear() + '-' + d2(now.getMonth() + 1) + '-' + d2(now.getDate());
      const itemsHtml = (d.items || []).map((it, i) =>
        `<tr><td>${i + 1}</td><td>${d.customer_name || ''}</td><td>${it.material_mode === 'CUSTOMER' ? '客供料' : '自营料'}</td><td>${it.part_spec || ''}</td><td style="text-align:right">${fmt(it.quantity)}${it.unit || ''}</td><td>${it.craft_type || it.process_requirement || it.paint_spec || ''}</td><td>${it.material_thickness || ''}</td><td></td></tr>`
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
        .sign{margin-top:40px;display:flex;justify-content:space-between;font-size:13px}
        .sign div{text-align:center}
        .sign .line{display:inline-block;width:120px;border-bottom:1px solid #333;margin-top:28px}
        @media print{body{margin:0;padding:15mm}@page{margin:10mm}}
      </style></head><body>
        <h1>东莞市峰业精密机械有限公司</h1>
        <div class="sub">来料加工入库单</div>
        <div class="info"><span>单号：${d.order_no || ''}</span><span>日期：${dateStr}</span><span>经手人：______________</span></div>
        <table><thead><tr><th style="width:40px">序号</th><th>客户名称</th><th style="width:70px">来料类型</th><th>尺寸</th><th style="width:80px">数量</th><th>工艺类型</th><th style="width:70px">厚度</th><th style="width:70px">交期</th></tr></thead>
        <tbody>${itemsHtml || '<tr><td colspan="8" style="text-align:center;color:#999">无明细</td></tr>'}</tbody></table>
        <div class="sign"><div>客户签字：<span class="line"></span></div><div>经手人：<span class="line"></span></div><div>日期：<span class="line"></span></div></div>
        <script>window.onload=function(){setTimeout(function(){window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { canCreateOrder, rows, total, page, loading, query, dialog, detail, profit, priceAdj, custs, comps, form, orderFormConfig, orderFormViewRef, ORDER_STATUS, ORDER_FLOW, BILLING_LABEL, DELIVERY_LABEL, fmt, fmtDate, fmtDateShort, costLabel, compName, flowClass, load, search, reset, openCreate, submit, openDetail, act, actInput, goWorkOrder, showProfit, openPriceAdj, calcNewAmount, submitPriceAdj, printOrder, Icon };
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
            <span class="doc-cust" v-if="row.customer_name">{{row.customer_name}}</span>
            <span class="doc-cust" v-if="row.order_no">· {{row.order_no}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">规格</span><span class="df-value">{{row.product_spec||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">工艺</span><span class="df-value" style="color:#6366f1;font-weight:500">{{row.process||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">数量</span><span class="df-value">{{fmt(row.plan_qty)}}</span></div>
            <div class="doc-field"><span class="df-label">交期</span><span class="df-value">{{fmtDateShort(row.delivery_date)}}</span></div>
            <div class="doc-field"><span class="df-label">状态</span><span class="df-value">{{fmtDateShort(row.released_at)}}</span></div>
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

    <el-dialog v-model="dialog.visible" title="新建加工单" width="620px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="客户"><el-input v-model="form.customer_name" placeholder="直接输入客户名称,或选择客户" style="width:340px"/></el-form-item>
        <el-form-item label="关联订单"><el-select v-model="form.order_id" filterable clearable placeholder="选择已生效订单(可选)" style="width:340px">
            <el-option v-for="o in orders" :key="o.id" :label="o.order_no+' '+o.customer_name" :value="o.id"/>
        </el-select></el-form-item>
        <el-form-item label="产品规格"><el-input v-model="form.product_spec" placeholder="如: Φ85-A*8.7 轮" style="width:340px"/></el-form-item>
        <el-form-item label="工艺"><el-select v-model="form.process" placeholder="选择工艺" clearable style="width:240px">
            <el-option label="镜面喷漆" value="镜面喷漆"/>
            <el-option label="加厚喷漆0.3MM" value="加厚喷漆0.3MM"/>
            <el-option label="喷瓷" value="喷瓷"/>
            <el-option label="喷砂" value="喷砂"/>
            <el-option label="抛光" value="抛光"/>
        </el-select></el-form-item>
        <el-form-item label="计划数量"><el-input-number v-model="form.plan_qty" :min="1" style="width:200px"/></el-form-item>
        <el-form-item label="车间"><el-select v-model="form.workshop" style="width:140px"><el-option label="A车间" value="A"/><el-option label="B车间" value="B"/></el-select></el-form-item>
        <el-form-item label="发货日期"><el-date-picker v-model="form.delivery_date" type="date" placeholder="选择交期" style="width:240px"/></el-form-item>
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
            {{detail.data.customer_name||'-'}} {{detail.data.order_no?'· '+detail.data.order_no:''}}
          </div>
        </div>

        <div class="detail-section">
          <div class="ds-title">产品信息</div>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="规格">{{detail.data.product_spec||'-'}}</el-descriptions-item>
            <el-descriptions-item label="工艺"><span style="color:#6366f1;font-weight:500">{{detail.data.process||'-'}}</span></el-descriptions-item>
            <el-descriptions-item label="计划数量">{{fmt(detail.data.plan_qty)}}</el-descriptions-item>
            <el-descriptions-item label="发货日期">{{fmtDate(detail.data.delivery_date)}}</el-descriptions-item>
          </el-descriptions>
        </div>

        <div class="detail-section">
          <div class="ds-title">执行情况</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">计划数量</div><div class="ig-value big">{{fmt(detail.data.plan_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">实际数量</div><div class="ig-value big" :class="detail.data.actual_qty<detail.data.plan_qty?'neg':'pos'">{{fmt(detail.data.actual_qty)}}</div></div>
            <div class="ig-item"><div class="ig-label">批次号</div><div class="ig-value">{{detail.data.batch_no||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">状态</div><div class="ig-value">{{WO_STATUS[detail.data.status]||detail.data.status}}</div></div>
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
    const form = reactive({ order_id: null, customer_id: null, customer_name: '', product_spec: '', process: '', batch_no: '', workshop: 'A', plan_qty: 100, delivery_date: null });
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
      form.customer_name = '';
      form.product_spec = '';
      form.process = '';
      form.batch_no = 'BATCH-' + Date.now().toString().slice(-6);
      form.delivery_date = null;
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
          <el-button v-if="row.status==='CONFIRMED'" size="small" type="primary" @click="shipOrder(row)">确认出货</el-button>
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
            <el-button v-if="detail.data.status==='CONFIRMED'" type="primary" @click="shipOrder(detail.data)">确认出货</el-button>
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
    async function shipOrder(row) {
      try {
        const r = await api.post('/api/shipments?order_id=' + row.order_id + '&completion_id=' + row.id, {});
        ElMessage.success('出货单已生成:' + (r.data.ship_no || ''));
        printShipment(r.data);
        load();
      } catch (e) { if (e.message) ElMessage.error(e.message); }
    }
    function printShipment(s) {
      const d2 = d => d < 10 ? '0' + d : '' + d;
      const now = s.ship_date ? new Date(s.ship_date) : new Date();
      const dateStr = now.getFullYear() + '-' + d2(now.getMonth() + 1) + '-' + d2(now.getDate());
      const copies = ['存根联', '客户联', '财务联', '仓库联'];
      const itemsHtml = (s.items || []).map((it, i) =>
        `<tr><td style="text-align:center">${i + 1}</td><td>${it.part_name || ''}</td><td>${it.spec || it.part_spec || ''}</td><td style="text-align:right">${fmt(it.qty)}</td><td>${it.unit || ''}</td><td>${it.craft_type || ''}</td><td>${it.material_thickness || ''}</td></tr>`
      ).join('');
      const copyBlock = label => `
        <div class="copy">
          <h1>东莞市峰业精密机械有限公司</h1>
          <div class="sub">${label} — 出货单</div>
          <div class="info"><span>出货单号：${s.ship_no || ''}</span><span>日期：${dateStr}</span><span>客户：${s.customer_name || ''}</span></div>
          <table><thead><tr><th style="width:40px">序号</th><th>工件名</th><th>规格</th><th style="width:70px">数量</th><th style="width:50px">单位</th><th>工艺类型</th><th style="width:70px">厚度</th></tr></thead>
          <tbody>${itemsHtml || '<tr><td colspan="7" style="text-align:center;color:#999">无明细</td></tr>'}</tbody></table>
          <div class="sign"><div>客户签字：<span class="line"></span></div><div>经手人：<span class="line"></span></div><div>日期：<span class="line"></span></div></div>
        </div>`;
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>出货四联单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:10mm 15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:18px;margin-bottom:2px;letter-spacing:2px}
        .sub{text-align:center;font-size:13px;color:#666;margin-bottom:8px;font-weight:600}
        .info{display:flex;justify-content:space-between;font-size:12px;margin-bottom:8px;border:1px solid #ccc;padding:6px 10px;background:#fafafa}
        table{width:100%;border-collapse:collapse;font-size:11px}
        th,td{border:1px solid #333;padding:4px 6px;text-align:left}
        th{background:#f0f0f0;font-weight:600}
        .sign{margin-top:24px;display:flex;justify-content:space-between;font-size:12px}
        .sign div{text-align:center}
        .sign .line{display:inline-block;width:100px;border-bottom:1px solid #333;margin-top:20px}
        .cut{border-top:2px dashed #999;margin:12px 0}
        @media print{body{margin:0;padding:8mm 12mm}@page{margin:6mm}}
      </style></head><body>
        ${copies.map((c, i) => copyBlock(c) + (i < copies.length - 1 ? '<div class="cut"></div>' : '')).join('')}
        <script>window.onload=function(){setTimeout(function(){window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, total, page, loading, query, dialog, detail, wos, paints, form, CP_STATUS, CP_FLOW, fmt, fmtDate, cpFlowClass, load, search, reset, openCreate, submit, openDetail, confirm, shipOrder, printCP, printShipment, Icon };
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

    <div v-if="lowStockList.length" class="remind-block remind-danger" style="margin:16px 24px 0">
      <div class="rb-head">
        <span v-html="Icon.icon('exclamation-triangle',16)"></span>
        低库存预警 · 共 {{lowStockList.length}} 项低于安全库存
      </div>
      <el-table :data="lowStockList" size="small" border style="margin-top:10px">
        <el-table-column label="编码" prop="code" width="140"/>
        <el-table-column label="名称" prop="name" min-width="180"/>
        <el-table-column label="分类" width="100">
          <template #default="{row}">{{INV_CAT[row.category]||row.category}}</template>
        </el-table-column>
        <el-table-column label="当前库存" width="120" align="right">
          <template #default="{row}"><span class="neg">{{fmt(row.stock_qty)}} {{row.unit}}</span></template>
        </el-table-column>
        <el-table-column label="安全库存" width="120" align="right">
          <template #default="{row}">{{fmt(row.safety_qty)}} {{row.unit}}</template>
        </el-table-column>
        <el-table-column label="缺口" width="100" align="right">
          <template #default="{row}"><span class="neg">{{fmt(row.safety_qty - row.stock_qty)}} {{row.unit}}</span></template>
        </el-table-column>
        <el-table-column label="库位" prop="location" width="100"/>
      </el-table>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.category" placeholder="全部分类" style="width:160px" clearable @change="search">
        <el-option v-for="(l,v) in INV_CAT" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-select v-model="query.warn_only" placeholder="全部状态" style="width:140px" @change="search">
        <el-option label="全部物料" :value="false"/>
        <el-option label="仅看库存不足" :value="true"/>
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
    const query = reactive({ category: '', keyword: '', warn_only: false });
    const dialog = reactive({ visible: false });
    const form = reactive({ code: '', name: '', spec: '', unit: 'kg', category: 'PAINT_POWDER', stock_qty: 0, safety_qty: 0, unit_cost: 0, location: '' });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const lowStockList = computed(() => (rows.value || []).filter(r => parseFloat(r.stock_qty||0) < parseFloat(r.safety_qty||0)));
    async function load() {
      loading.value = true;
      try {
        const params = Object.fromEntries(Object.entries(query).filter(([k, v]) => v && v !== 'false' && v !== false && k !== 'warn_only'));
        const r = await api.get('/api/inventory/items?' + new URLSearchParams(params).toString());
        let all = r.data || [];
        if (query.warn_only) all = all.filter(x => parseFloat(x.stock_qty||0) < parseFloat(x.safety_qty||0));
        rows.value = all;
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { load(); }
    function openCreate() { Object.assign(form, { code: '', name: '', spec: '', unit: 'kg', category: 'PAINT_POWDER', stock_qty: 0, safety_qty: 0, unit_cost: 0, location: '' }); dialog.visible = true; }
    async function submit() { try { await api.post('/api/inventory/items', form); ElMessage.success('已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    onMounted(load);
    return { rows, loading, query, dialog, form, INV_CAT, fmt, lowStockList, load, search, openCreate, submit, Icon };
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
        <span v-if="isFin" class="ph-tip" style="font-size:12px;color:var(--muted);margin-right:10px">在单据上操作相应功能</span>
      </div>
    </div>

    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="单据号/客户/摘要模糊搜索" style="width:200px" clearable @keyup.enter="search" @clear="search">
        <template #prefix><span v-html="Icon.icon('search',14)" style="color:#94a3b8;margin-right:4px"></span></template>
      </el-input>
      <el-select v-model="query.customer_id" placeholder="全部客户" style="width:150px" clearable filterable @change="search">
        <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id"/>
      </el-select>
      <el-date-picker v-model="query.daterange" type="daterange" value-format="YYYY-MM-DD" range-separator="至"
        start-placeholder="开始日期" end-placeholder="结束日期" style="width:240px" @change="search"/>
      <el-select v-model="query.doc_type" placeholder="全部类型" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in FIN_TYPE" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in FIN_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
      <el-button v-if="isFin" type="warning" plain @click="openTransfer">
        <span v-html="Icon.icon('arrow-path',14)" style="vertical-align:-2px;margin-right:4px"></span>账户转账
      </el-button>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.doc_no}}</span>
            <span class="pill" :class="row.status">{{FIN_STATUS[row.status]||row.status}}</span>
            <span class="pill" :class="row.doc_type==='RECEIVABLE'||row.doc_type==='RECEIPT'?'EFFECTIVE':'CLOSED'" style="background:rgba(0,212,255,.15);color:var(--primary)">{{FIN_TYPE[row.doc_type]||row.doc_type}}</span>
            <span class="doc-cust" v-if="row.counterparty_name">{{row.counterparty_name}}</span>
            <span class="doc-amount">{{row.doc_type==='PAYABLE'||row.doc_type==='PAYMENT'?'-':''}}¥{{fmt(row.amount)}}</span>
            <span class="doc-ops" v-if="(isFin || isSales) && (row.order_id || row.doc_type==='PAYABLE')" @click.stop>
              <!-- 收款按钮:仅财务可见,且在真正的应收单(RECEIVABLE)且订单还有未核销时显示 -->
              <template v-if="isFin && row.doc_type==='RECEIVABLE' && row.order_ar_unsettled>0">
                <el-button link type="success" @click.stop="openRcpt(row)"><span v-html="Icon.icon('arrow-down-tray',13)" style="vertical-align:-2px;margin-right:2px"></span>收款</el-button>
              </template>
              <span v-else-if="isFin && row.order_ar_unsettled===0 && row.doc_type==='RECEIVABLE'" class="settled-tag"><span v-html="Icon.icon('check-circle',13)" style="vertical-align:-2px;margin-right:2px"></span>已收款</span>
              <!-- 付款按钮:仅财务可见,应付单(PAYABLE)有未核销余额时显示 -->
              <template v-if="isFin && row.doc_type==='PAYABLE' && (row.amount-row.settled_amount)>0.005">
                <el-button link type="danger" @click.stop="openPay(row)"><span v-html="Icon.icon('arrow-up-tray',13)" style="vertical-align:-2px;margin-right:2px"></span>付款</el-button>
              </template>
              <span v-else-if="isFin && row.doc_type==='PAYABLE' && row.status==='SETTLED'" class="settled-tag"><span v-html="Icon.icon('check-circle',13)" style="vertical-align:-2px;margin-right:2px"></span>已付清</span>
              <!-- 调价/返工/退货: 仅销售线可见 -->
              <template v-if="isSales">
                <el-button link type="primary" @click.stop="openAdj(row)"><span v-html="Icon.icon('tag',13)" style="vertical-align:-2px;margin-right:2px"></span>调价</el-button>
                <el-button link type="warning" @click.stop="openRet(row)"><span v-html="Icon.icon('arrow-path',13)" style="vertical-align:-2px;margin-right:2px"></span>返工</el-button>
                <el-button link type="danger" @click.stop="openRtn(row)"><span v-html="Icon.icon('arrow-uturn-left',13)" style="vertical-align:-2px;margin-right:2px"></span>退货</el-button>
              </template>
            </span>
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

    <el-dialog v-model="rcpt.visible" title="收款登记" width="520px">
      <el-form @submit.prevent>
        <el-form-item label="收款日期"><el-date-picker v-model="rcpt.form.receipt_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="客户"><span style="font-weight:600">{{curLabel(rcpt.cur)}}</span></el-form-item>
        <el-form-item label="应收金额"><span style="font-weight:700;color:var(--primary)">¥{{fmt(rcpt.unsettled)}}</span></el-form-item>
        <el-form-item label="本次收款"><el-input-number v-model="rcpt.form.amount" :min="0" :max="rcpt.unsettled" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="余额核销后">
          <span class="pill" :class="(rcpt.unsettled-rcpt.form.amount)<=0?'SETTLED':'OPEN'" style="background:rgba(0,212,255,.12);color:var(--primary)">
            {{(rcpt.unsettled-rcpt.form.amount)<=0?'全部结清':'剩余 ¥'+fmt(rcpt.unsettled-rcpt.form.amount)}}
          </span>
        </el-form-item>
        <el-form-item label="收款方式">
          <el-radio-group v-model="rcpt.form.pay_method" @change="rcpt.form.company_id=null">
            <el-radio label="TELEGRAPHIC">电汇</el-radio>
            <el-radio label="CASH">现金</el-radio>
            <el-radio label="ACCEPTANCE">承兑</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="收款主体" v-if="rcpt.form.pay_method!=='CASH'">
          <el-select v-model="rcpt.form.company_id" placeholder="选择主体(峰业精密机械/东莞加工厂)" style="width:100%">
            <el-option v-for="c in companies" :key="c.id" :label="c.short_name" :value="c.id"/>
          </el-select>
          <div class="df-tip">电汇收款记入 <b>银行存款</b>，承兑记入 <b>应收票据</b></div>
        </el-form-item>
        <el-form-item label="收款主体" v-else>
          <span style="font-weight:600">东莞加工厂(小规模纳税人)</span>
          <div class="df-tip">现金收款记入 <b>库存现金</b></div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="rcpt.form.remark" placeholder="备注(可选)" style="width:100%"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="rcpt.visible=false">取消</el-button><el-button type="primary" @click="submitRcpt">确认收款</el-button></template>
    </el-dialog>

    <!-- 付款登记(对称收款, 素人化: 只填金额和账户) -->
    <el-dialog v-model="pay.visible" title="付款登记" width="520px">
      <el-form @submit.prevent label-width="90px">
        <el-form-item label="付款日期"><el-date-picker v-model="pay.form.pay_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="供应商"><span style="font-weight:600">{{pay.cur && pay.cur.counterparty_name || '-'}}</span></el-form-item>
        <el-form-item label="应付余额"><span style="font-weight:700;color:var(--danger)">¥{{fmt(pay.unsettled)}}</span></el-form-item>
        <el-form-item label="本次付款"><el-input-number v-model="pay.form.amount" :min="0" :max="pay.unsettled" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="付款后">
          <span class="pill" :class="(pay.unsettled-pay.form.amount)<=0?'SETTLED':'OPEN'" style="background:rgba(0,212,255,.12);color:var(--primary)">
            {{(pay.unsettled-pay.form.amount)<=0?'全部付清':'剩余 ¥'+fmt(pay.unsettled-pay.form.amount)}}
          </span>
        </el-form-item>
        <el-form-item label="付款账户">
          <el-select v-model="pay.form.fund_account_id" placeholder="选择付款账户" style="width:100%">
            <el-option v-for="a in fundAccounts" :key="a.id" :label="a.name+' (余额 ¥'+fmt(a.balance)+')'" :value="a.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="pay.form.remark" placeholder="备注(可选)" style="width:100%"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="pay.visible=false">取消</el-button><el-button type="primary" @click="submitPay">确认付款</el-button></template>
    </el-dialog>

    <!-- 账户转账(素人化: 从哪转/转到哪/多少钱) -->
    <el-dialog v-model="trf.visible" title="账户转账" width="520px">
      <el-form @submit.prevent label-width="90px">
        <el-form-item label="转账日期"><el-date-picker v-model="trf.form.occur_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="转出账户">
          <el-select v-model="trf.form.from_account_id" placeholder="从哪个账户转出" style="width:100%">
            <el-option v-for="a in fundAccounts" :key="a.id" :label="a.name+' (余额 ¥'+fmt(a.balance)+')'" :value="a.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="转入账户">
          <el-select v-model="trf.form.to_account_id" placeholder="转到哪个账户" style="width:100%">
            <el-option v-for="a in fundAccounts.filter(x=>x.id!==trf.form.from_account_id)" :key="a.id" :label="a.name+' (余额 ¥'+fmt(a.balance)+')'" :value="a.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="金额"><el-input-number v-model="trf.form.amount" :min="0" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="摘要"><el-input v-model="trf.form.summary" placeholder="如: 公账提现备用金(可选)" style="width:100%"/></el-form-item>
        <div class="df-tip">转账只影响两个账户的余额，不会计入收入或费用，系统自动生成记账凭证。</div>
      </el-form>
      <template #footer><el-button @click="trf.visible=false">取消</el-button><el-button type="primary" @click="submitTransfer">确认转账</el-button></template>
    </el-dialog>

    <!-- 申请调价 -->
    <el-dialog v-model="adj.visible" title="申请调价" width="520px">
      <el-form :model="adj.form" label-width="110px">
        <el-form-item label="订单"><span style="font-weight:600">{{curLabel(adj.cur)}}</span></el-form-item>
        <el-form-item label="原应收" v-if="adj.cur"><span style="font-weight:700">¥{{fmt(adj.cur.amount)}}</span></el-form-item>
        <el-form-item label="调整后"><el-input-number v-model="adj.form.new_amount" :min="0" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="调价原因"><el-input v-model="adj.form.reason" type="textarea" :rows="2" placeholder="请说明调价原因"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="adj.visible=false">取消</el-button><el-button type="primary" @click="submitAdj">提交审批</el-button></template>
    </el-dialog>

    <!-- 返工申请 -->
    <el-dialog v-model="ret.visible" title="返工申请" width="520px">
      <el-form :model="ret.form" label-width="110px">
        <el-form-item label="订单"><span style="font-weight:600">{{curLabel(ret.cur)}}</span></el-form-item>
        <el-form-item label="返工成本"><el-input-number v-model="ret.form.amount" :min="0" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="返工原因"><el-input v-model="ret.form.reason" type="textarea" :rows="2" placeholder="请说明返工原因"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="ret.visible=false">取消</el-button><el-button type="primary" @click="submitRet">提交审批</el-button></template>
    </el-dialog>

    <!-- 退货申请 -->
    <el-dialog v-model="rtn.visible" title="退货申请" width="520px">
      <el-form :model="rtn.form" label-width="110px">
        <el-form-item label="订单"><span style="font-weight:600">{{curLabel(rtn.cur)}}</span></el-form-item>
        <el-form-item label="退货金额"><el-input-number v-model="rtn.form.amount" :min="0" :precision="2" style="width:220px"/></el-form-item>
        <el-form-item label="退货原因"><el-input v-model="rtn.form.reason" type="textarea" :rows="2" placeholder="请说明退货原因"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="rtn.visible=false">取消</el-button><el-button type="primary" @click="submitRtn">提交审批</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="财务单据详情" size="520px">
      <template v-if="detail.data.id">
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.doc_no}}</span>
            <span class="pill" :class="detail.data.status">{{FIN_STATUS[detail.data.status]||detail.data.status}}</span>
            <span class="pill" :class="detail.data.doc_type==='RECEIVABLE'||detail.data.doc_type==='RECEIPT'?'EFFECTIVE':'CLOSED'" style="background:rgba(0,212,255,.15);color:var(--primary)">{{FIN_TYPE[detail.data.doc_type]||detail.data.doc_type}}</span>
            <span class="dh-amount" :class="(detail.data.doc_type==='PAYABLE'||detail.data.doc_type==='PAYMENT')?'neg':'pos'">
              {{(detail.data.doc_type==='PAYABLE'||detail.data.doc_type==='PAYMENT')?'-':''}}¥{{fmt(detail.data.amount)}}
            </span>
          </div>
          <div class="dh-row" style="margin:8px 0 0;color:var(--text2);font-size:12px">
            客户/对手方: {{detail.data.customer_name||detail.data.counterparty_name||'-'}}
          </div>
        </div>
        <div class="detail-section">
          <div class="ds-title">基础信息</div>
          <div class="ds-grid">
            <div><span class="ds-label">关联订单</span><span class="ds-val">{{detail.data.order_no||'-'}}</span></div>
            <div><span class="ds-label">来源事件</span><span class="ds-val">{{detail.data.source_event||'-'}}</span></div>
            <div><span class="ds-label">记账日</span><span class="ds-val">{{fmtDateShort(detail.data.account_date)}}</span></div>
            <div><span class="ds-label">收款方式</span><span class="ds-val">{{({ACCEPTANCE:'承兑',TELEGRAPHIC:'电汇',CASH:'现金'})[detail.data.pay_method]||'-'}}</span></div>
            <div><span class="ds-label">应收金额</span><span class="ds-val">¥{{fmt(detail.data.amount)}}</span></div>
            <div><span class="ds-label">已结算</span><span class="ds-val pos">¥{{fmt(detail.data.settled_amount)}}</span></div>
            <div><span class="ds-label">未核销</span><span class="ds-val" :class="(detail.data.amount-detail.data.settled_amount)>0?'neg':''">¥{{fmt(detail.data.amount-detail.data.settled_amount)}}</span></div>
            <div><span class="ds-label">订单应收余额</span><span class="ds-val">{{detail.data.order_ar_unsettled!=null?'¥'+fmt(detail.data.order_ar_unsettled):'-'}}</span></div>
          </div>
        </div>
        <div class="detail-section" v-if="detail.data.remark">
          <div class="ds-title">备注</div>
          <div style="color:var(--text2);font-size:13px;line-height:1.6">{{detail.data.remark}}</div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ keyword: '', customer_id: null, daterange: null, doc_type: '', status: '' });
    const customers = ref([]);
    async function loadCustomers() { try { const r = await api.get('/api/customers?size=500'); customers.value = r.data || []; } catch {} }
    const rcpt = reactive({ visible: false, unsettled: 0, form: { order_id: null, amount: 0, pay_method: 'TELEGRAPHIC', company_id: null, receipt_date: null } });
    const orders = ref([]);
    const companies = ref([]);
    const adj = reactive({ visible: false, cur: null, form: { order_id: null, new_amount: 0, reason: '' } });
    const ret = reactive({ visible: false, cur: null, form: { order_id: null, amount: 0, reason: '' } });
    const rtn = reactive({ visible: false, cur: null, form: { order_id: null, amount: 0, reason: '' } });
    const detail = reactive({ visible: false, data: {} });
    const role = JSON.parse(localStorage.getItem(USER_KEY) || '{}').role || '';
    const isFin = ['FINANCE'].includes(role);
    const isSales = ['SALES', 'SALES_VICE_MANAGER', 'SALES_MANAGER'].includes(role);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    async function loadOrders() { try { const r = await api.get('/api/orders?status=EFFECTIVE'); orders.value = r.data; } catch {} }
    async function loadCompanies() { try { const r = await api.get('/api/companies'); companies.value = r.data; } catch {} }
    async function load() {
      loading.value = true;
      try {
        const params = { page: page.page, size: page.size };
        Object.entries(query).forEach(([k, v]) => { if (v === '' || v == null || (Array.isArray(v) && !v.length)) return; params[k] = Array.isArray(v) ? v : v; });
        if (params.daterange) { const [from, to] = params.daterange; delete params.daterange; params.date_from = from; params.date_to = to; }
        const r = await api.get('/api/finance/docs?' + new URLSearchParams(params).toString());
        rows.value = r.data; total.value = r.total;
      }
      catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.keyword = ''; query.customer_id = null; query.daterange = null; query.doc_type = ''; query.status = ''; search(); }
    const curLabel = r => r ? (r.order_no || r.doc_no || '') + ' ' + (r.customer_name || r.counterparty_name || '') : '-';
    async function openRcpt(row) {
      await loadCompanies();
      rcpt.cur = row;
      const unsettled = Number(row.order_ar_unsettled != null ? row.order_ar_unsettled : (row.amount || 0) - (row.settled_amount || 0));
      rcpt.unsettled = unsettled;
      const today = new Date().toISOString().slice(0, 10);
      rcpt.form = { order_id: row.order_id, amount: unsettled, pay_method: 'TELEGRAPHIC', company_id: null, receipt_date: today }; rcpt.visible = true;
    }
    async function submitRcpt() {
      if (!rcpt.form.amount || rcpt.form.amount <= 0) { ElMessage.warning('请填写收款金额'); return; }
      if (rcpt.form.pay_method !== 'CASH' && !rcpt.form.company_id) { ElMessage.warning('请选择收款主体'); return; }
      try { await api.post('/api/finance/receipts', rcpt.form); ElMessage.success('收款已登记,应收已核销'); rcpt.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    // ---- 付款登记 + 账户转账(素人化) ----
    const fundAccounts = ref([]);
    const pay = reactive({ visible: false, cur: null, unsettled: 0, form: { doc_id: null, fund_account_id: null, amount: 0, pay_date: null, remark: '' } });
    const trf = reactive({ visible: false, form: { from_account_id: null, to_account_id: null, amount: 0, occur_date: null, summary: '' } });
    async function loadFundAccounts() { try { const r = await api.get('/api/finance/fund-accounts'); fundAccounts.value = r.data || []; } catch {} }
    async function openPay(row) {
      await loadFundAccounts();
      pay.cur = row;
      pay.unsettled = Number((row.amount || 0) - (row.settled_amount || 0));
      pay.form = { doc_id: row.id, fund_account_id: null, amount: pay.unsettled, pay_date: new Date().toISOString().slice(0, 10), remark: '' };
      pay.visible = true;
    }
    async function submitPay() {
      if (!pay.form.amount || pay.form.amount <= 0) { ElMessage.warning('请填写付款金额'); return; }
      if (!pay.form.fund_account_id) { ElMessage.warning('请选择付款账户'); return; }
      try { await api.post('/api/finance/payments', pay.form); ElMessage.success('付款已登记,应付已核销'); pay.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    async function openTransfer() {
      await loadFundAccounts();
      trf.form = { from_account_id: null, to_account_id: null, amount: 0, occur_date: new Date().toISOString().slice(0, 10), summary: '' };
      trf.visible = true;
    }
    async function submitTransfer() {
      if (!trf.form.from_account_id || !trf.form.to_account_id) { ElMessage.warning('请选择转出和转入账户'); return; }
      if (!trf.form.amount || trf.form.amount <= 0) { ElMessage.warning('请填写转账金额'); return; }
      try { await api.post('/api/finance/transfer', trf.form); ElMessage.success('转账完成,已自动生成凭证'); trf.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openAdj(row) { adj.cur = row; adj.form = { order_id: row.order_id, new_amount: Number(row.amount || 0), reason: '' }; adj.visible = true; }
    async function submitAdj() {
      try { await api.post('/api/approvals/price-adjustment', adj.form); ElMessage.success('调价申请已提交审批'); adj.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openRet(row) { ret.cur = row; ret.form = { order_id: row.order_id, amount: Number((row.amount || 0) - (row.settled_amount || 0)), reason: '' }; ret.visible = true; }
    async function submitRet() {
      try { await api.post('/api/finance/reworks', ret.form); ElMessage.success('返工申请已提交审批'); ret.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openRtn(row) { rtn.cur = row; rtn.form = { order_id: row.order_id, amount: Number((row.order_ar_unsettled != null ? row.order_ar_unsettled : (row.amount || 0) - (row.settled_amount || 0))), reason: '' }; rtn.visible = true; }
    async function submitRtn() {
      try { await api.post('/api/finance/returns', rtn.form); ElMessage.success('退货申请已提交审批'); rtn.visible = false; load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    onMounted(() => { loadCustomers(); load(); });
    return { rows, total, page, loading, query, customers, rcpt, orders, companies, adj, ret, rtn, detail, isFin, isSales, curLabel, FIN_TYPE, FIN_STATUS, fmt, fmtDateShort, load, search, reset, openRcpt, submitRcpt, openAdj, submitAdj, openRet, submitRet, openRtn, submitRtn, openDetail, fundAccounts, pay, trf, openPay, submitPay, openTransfer, submitTransfer, Icon };
  }
};

// ============ 凭证管理 ============
const VOUCHER_STATUS = { DRAFT: '草稿', POSTED: '已过账', REVERSED: '已冲销' };

const VouchersPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('file-text',22)"></div>
        <div>
          <div class="ph-title">凭证管理</div>
          <div class="ph-sub">标准凭证录入 · 自动过账 · 红冲处理</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button @click="openReviewDlg"><span v-html="Icon.icon('shield-check',14)" style="vertical-align:middle;margin-right:4px"></span>AI审凭证</el-button>
        <el-badge :value="mc.badge" :hidden="!mc.badge" type="danger">
          <el-button @click="openMc"><span v-html="Icon.icon('clipboard-check',14)" style="vertical-align:middle;margin-right:4px"></span>月结</el-button>
        </el-badge>
        <el-button type="success" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建凭证</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.period" placeholder="会计期间" style="width:160px" clearable @change="search">
        <el-option v-for="p in periods" :key="p" :label="p" :value="p"/>
      </el-select>
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in VOUCHER_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="凭证号" style="width:180px" clearable @keyup.enter="search"/>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
      <div class="grow"></div>
    </div>

    <!-- 封账状态条(素人化: 自动封账结果一目了然) -->
    <div class="period-bar" v-if="query.period && periodMap[query.period]">
      <span v-if="periodMap[query.period]==='CLOSED'" class="pb-tag closed">🔒 {{query.period}} 已封账·数据已锁定</span>
      <span v-else class="pb-tag open">📖 {{query.period}} 账期开放中</span>
      <span class="pb-tip">到期账期每月10日后由系统自动封存</span>
      <el-button v-if="canClosePeriod && periodMap[query.period]!=='CLOSED'" size="small" type="warning" plain @click="closePeriodNow">立即封账</el-button>
      <el-button v-if="canClosePeriod && periodMap[query.period]==='CLOSED'" size="small" type="info" plain @click="reopenPeriodNow">解封</el-button>
    </div>
    <div class="period-bar warn" v-if="pendingClose.length">
      <span class="pb-tag pending">⏳ 待封账提醒</span>
      <span class="pb-tip" v-for="p in pendingClose" :key="p.period">{{p.period}} {{p.reason}}</span>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.voucher_no}}</span>
            <span class="pill" :class="row.status">{{VOUCHER_STATUS[row.status]||row.status}}</span>
            <span v-if="row.reviewed" class="rv-stamp">✓已复核</span>
            <span class="doc-no">{{row.period}}</span>
            <span class="doc-cust">{{row.summary}}</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">日期</span><span class="df-value">{{fmtDateShort(row.voucher_date)}}</span></div>
            <div class="doc-field"><span class="df-label">分录</span><span class="df-value">{{row.entry_count}}条</span></div>
          </div>
        </div>
        <div class="doc-actions">
          <el-button size="small" @click="openDetail(row)">查看</el-button>
          <el-button v-if="row.status==='POSTED' && !row.reviewed" size="small" type="primary" plain @click="reviewVoucher(row)">复核</el-button>
          <el-button v-if="row.status==='DRAFT'" size="small" type="success" @click="postVoucher(row)">过账</el-button>
          <el-button v-if="row.status==='POSTED'" size="small" type="warning" @click="reverseVoucher(row)">红冲</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无凭证</div>
        <div class="de-desc">点击「新建凭证」创建第一张凭证</div>
      </div>
    </div>

    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <!-- 新建凭证对话框 -->
    <el-dialog v-model="createDlg.visible" title="新建凭证" width="800px" :close-on-click-modal="false">
      <el-form :model="createDlg.form" label-width="100px">
        <el-form-item label="会计期间">
          <el-date-picker v-model="createDlg.form.voucher_date" type="month" format="YYYY-MM" value-format="YYYY-MM" placeholder="选择月份" style="width:200px"/>
        </el-form-item>
        <el-form-item label="凭证日期">
          <el-date-picker v-model="createDlg.form.voucher_date" type="date" format="YYYY-MM-DD" value-format="YYYY-MM-DD" placeholder="选择日期" style="width:200px"/>
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="createDlg.form.summary" placeholder="请输入凭证摘要" style="width:400px"/>
        </el-form-item>
        <el-form-item label="分录明细">
          <div style="width:100%">
            <el-table :data="createDlg.form.entries" border size="small" style="width:100%">
              <el-table-column type="index" label="#" width="40"/>
              <el-table-column label="科目" width="200">
                <template #default="{ row }">
                  <el-select v-model="row.account_id" filterable placeholder="选择科目" style="width:180px" @change="onAccountChange(row)">
                    <el-option v-for="a in accounts" :key="a.id" :label="a.code+' '+a.name" :value="a.id"/>
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column prop="account_code" label="编码" width="80"/>
              <el-table-column prop="account_name" label="科目名称" width="120"/>
              <el-table-column label="摘要" width="200">
                <template #default="{ row }">
                  <el-input v-model="row.summary" placeholder="明细摘要"/>
                </template>
              </el-table-column>
              <el-table-column label="借方金额" width="120">
                <template #default="{ row }">
                  <el-input-number v-model="row.debit" :min="0" :precision="2" :step="100" style="width:110px" @change="checkBalance"/>
                </template>
              </el-table-column>
              <el-table-column label="贷方金额" width="120">
                <template #default="{ row }">
                  <el-input-number v-model="row.credit" :min="0" :precision="2" :step="100" style="width:110px" @change="checkBalance"/>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="60" align="center">
                <template #default="{ $index }">
                  <el-button type="danger" size="small" circle @click="removeEntry($index)">×</el-button>
                </template>
              </el-table-column>
              <el-table-column label="小计" width="80" align="right">
                <template #default="">
                  <div style="font-weight:bold;color:#67c23a">{{ totalDebit }}</div>
                  <div style="font-weight:bold;color:#f56c6c;margin-top:2px">{{ totalCredit }}</div>
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top:8px">
              <el-button type="primary" size="small" @click="addEntry">+ 添加分录</el-button>
              <span style="margin-left:12px;color:{{ isBalanced ? '#67c23a' : '#f56c6c' }}">
                {{ isBalanced ? '✓ 借贷平衡' : '⚠ 借贷不平衡' }}
                (借: ¥{{ totalDebit }} / 贷: ¥{{ totalCredit }})
              </span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg.visible=false">取消</el-button>
        <el-button type="primary" :disabled="!isBalanced || totalDebit === 0" @click="submitCreate">保存并过账</el-button>
      </template>
    </el-dialog>

    <!-- 凭证详情 -->
    <el-dialog v-model="detailDlg.visible" title="凭证详情" width="700px">
      <div v-if="detailDlg.data">
        <div style="display:flex;gap:20px;margin-bottom:16px">
          <div><span class="df-label">凭证号:</span> <strong>{{detailDlg.data.voucher_no}}</strong></div>
          <div><span class="df-label">期间:</span> {{detailDlg.data.period}}</div>
          <div><span class="df-label">日期:</span> {{fmtDateShort(detailDlg.data.voucher_date)}}</div>
          <div><span class="df-label">状态:</span> <span class="pill" :class="detailDlg.data.status">{{VOUCHER_STATUS[detailDlg.data.status]}}</span></div>
        </div>
        <div style="margin-bottom:12px"><span class="df-label">摘要:</span> {{detailDlg.data.summary}}</div>
        <el-table :data="detailDlg.data.entries" border size="small">
          <el-table-column prop="account_code" label="编码" width="80"/>
          <el-table-column prop="account_name" label="科目" width="150"/>
          <el-table-column prop="summary" label="明细摘要"/>
          <el-table-column prop="debit" label="借方" width="120" align="right">
            <template #default="{ row }">
              <span v-if="row.debit > 0" style="color:#67c23a">¥{{fmt(row.debit)}}</span>
            </template>
          </el-table-column>
          <el-table-column prop="credit" label="贷方" width="120" align="right">
            <template #default="{ row }">
              <span v-if="row.credit > 0" style="color:#f56c6c">¥{{fmt(row.credit)}}</span>
            </template>
          </el-table-column>
        </el-table>
        <div style="margin-top:8px;text-align:right;font-weight:bold">
          合计: <span style="color:#67c23a">借 ¥{{fmt(detailDlg.data.total_amount)}}</span> / 
          <span style="color:#f56c6c">贷 ¥{{fmt(detailDlg.data.total_amount)}}</span>
        </div>
      </div>
      <template #footer>
        <el-button v-if="detailDlg.data && detailDlg.data.status==='DRAFT'" type="success" @click="postVoucher(detailDlg.data)">过账</el-button>
        <el-button v-if="detailDlg.data && detailDlg.data.status==='POSTED'" type="warning" @click="reverseVoucher(detailDlg.data)">红冲</el-button>
        <el-button @click="detailDlg.visible=false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 月结检查面板 -->
    <el-drawer v-model="mc.visible" title="月结体检" size="520px" @open="loadMc">
      <div v-loading="mc.loading" style="padding:0 4px">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
          <el-date-picker v-model="mc.period" type="month" value-format="YYYY-MM" placeholder="期间" style="width:140px" @change="loadMc"/>
          <el-tag v-if="mc.is_closed" type="info" size="small">已封账</el-tag>
          <el-tag v-else-if="mc.all_clear" type="success" size="small">全部通过</el-tag>
          <el-tag v-else type="danger" size="small">{{ mc.badge }} 项待处理</el-tag>
        </div>
        <div v-if="!mc.loading && !mc.items.length && !mc.is_closed" class="doc-empty" style="padding:30px 0">
          <div v-html="Icon.icon('check-circle',48)"></div>
          <div class="de-title">全部通过</div>
          <div class="de-desc">当期无待处理事项, 可直接封账</div>
        </div>
        <div v-for="it in mc.items" :key="it.key" style="margin-bottom:12px;border:1px solid var(--border);border-radius:8px;padding:12px">
          <div style="display:flex;align-items:flex-start;gap:8px">
            <span :class="['mc-dot',it.level]"></span>
            <div style="flex:1">
              <div style="font-weight:600;font-size:14px">{{it.title}}</div>
              <div style="font-size:12px;color:var(--text2);margin-top:2px">{{it.detail}}</div>
            </div>
            <el-button text size="small" @click="askAI(it)" :loading="mc.aiLoading===it.key"><span v-html="Icon.icon('question-mark-circle',16)" style="vertical-align:middle"></span></el-button>
          </div>
          <div v-if="it.action" style="margin-top:8px">
            <el-button v-if="it.action==='post_all'" size="small" type="primary" :loading="mc.actionLoading===it.key" @click="doPostAllDrafts(it)">批量过账</el-button>
            <el-button v-if="it.action==='accrue_payroll'" size="small" type="warning" :loading="mc.actionLoading===it.key" @click="doAccruePayroll(it)">生成计提凭证</el-button>
          </div>
          <div v-if="mc.aiAnswer[it.key]" style="margin-top:8px;padding:8px 10px;background:var(--bg-alt);border-radius:6px;font-size:12px;line-height:1.6;color:var(--text2)">{{mc.aiAnswer[it.key]}}</div>
        </div>
        <div style="margin-top:20px;border-top:1px solid var(--border);padding-top:16px;display:flex;gap:8px">
          <el-button v-if="!mc.is_closed" type="danger" :disabled="mc.badge>0" @click="closePeriodNow">一键封账</el-button>
          <el-button v-if="mc.is_closed" type="info" @click="reopenPeriodNow">解封</el-button>
          <el-button @click="mc.visible=false">关闭</el-button>
        </div>
      </div>
    </el-drawer>

    <!-- AI审凭证弹窗 -->
    <el-dialog v-model="reviewDlg.visible" title="AI审凭证" width="720px">
      <div v-loading="reviewDlg.loading">
        <div v-if="!reviewDlg.results && !reviewDlg.loading" style="padding:20px 0;text-align:center">
          <div v-html="Icon.icon('shield-check',48)" style="color:var(--text3);margin-bottom:12px"></div>
          <div style="font-size:14px;color:var(--text2);margin-bottom:8px">AI逐张审核当期凭证</div>
          <div style="font-size:12px;color:var(--text3);margin-bottom:16px">检查科目用错 · 金额异常 · 摘要不规范 · 业务逻辑不通 · 税务风险</div>
          <div style="display:flex;gap:8px;justify-content:center;align-items:center">
            <el-date-picker v-model="reviewDlg.period" type="month" value-format="YYYY-MM" placeholder="期间" size="default" style="width:140px"/>
            <el-button type="primary" @click="runVoucherReview">开始审核</el-button>
          </div>
          <div style="font-size:11px;color:var(--text3);margin-top:12px">💡 仅在你主动点击时调用AI，不自动跑，不浪费token</div>
        </div>
        <div v-if="reviewDlg.results">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div>
              <el-tag type="success" size="small" v-if="reviewDlg.total_issues===0">✓ 全部通过</el-tag>
              <el-tag type="danger" size="small" v-else>⚠ {{reviewDlg.total_issues}} 个问题</el-tag>
              <span style="margin-left:8px;font-size:12px;color:var(--text3)">共审核 {{reviewDlg.total}} 张凭证</span>
            </div>
            <div style="display:flex;gap:6px">
              <el-date-picker v-model="reviewDlg.period" type="month" value-format="YYYY-MM" size="small" style="width:120px" :disabled="reviewDlg.loading"/>
              <el-button size="small" @click="runVoucherReview" :loading="reviewDlg.loading">重新审核</el-button>
            </div>
          </div>
          <div v-if="reviewDlg.summary" style="padding:8px 12px;background:var(--bg-alt);border-radius:6px;font-size:13px;margin-bottom:12px">{{reviewDlg.summary}}</div>
              <div v-if="reviewDlg.truncated" style="padding:6px 10px;background:#fdf6ec;border-radius:4px;font-size:12px;color:#e6a23c;margin-bottom:10px">⚠ 凭证较多，仅审核了最近60张</div>
          <div v-if="!reviewDlg.results.length && reviewDlg.total_issues===0" style="padding:24px 0;text-align:center">
            <div v-html="Icon.icon('check-circle',40)" style="color:#67c23a"></div>
            <div style="color:#67c23a;font-weight:600;margin-top:8px">凭证全部正常</div>
            <div style="font-size:12px;color:var(--text3);margin-top:4px">AI未发现科目/金额/摘要/逻辑问题</div>
          </div>
          <div v-for="rv in reviewDlg.results" :key="rv.voucher_no" style="margin-bottom:10px;border:1px solid #fde2e2;border-radius:8px;padding:10px">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
              <span style="font-weight:600;font-size:13px">{{rv.voucher_no}}</span>
              <el-button text size="small" type="primary" @click="openVoucherByNo(rv.voucher_no)">查看凭证 →</el-button>
            </div>
            <div v-for="iss in rv.issues" :key="iss.type+iss.msg" style="margin-bottom:4px;padding-left:14px;position:relative">
              <span style="position:absolute;left:0;top:5px;width:8px;height:8px;border-radius:50%;display:inline-block" :style="{background:iss.level==='danger'?'#f56c6c':iss.level==='warning'?'#e6a23c':'#909399'}"></span>
              <span style="font-size:12px" :style="{color:iss.level==='danger'?'#f56c6c':iss.level==='warning'?'#e6a23c':'#909399'}">
                <strong>{{iss.msg}}</strong>
                <span v-if="iss.suggestion" style="color:var(--text2)"> → {{iss.suggestion}}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ period: '', status: '', keyword: '' });
    const periods = ref([]);
    const accounts = ref([]);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    // 月结面板
    const mc = reactive({ visible: false, loading: false, period: '', items: [], is_closed: false, all_clear: false, badge: 0, actionLoading: null, aiLoading: null, aiAnswer: {} });
    // AI审凭证弹窗
    const reviewDlg = reactive({
      visible: false, loading: false, period: '', results: null, total: 0, total_issues: 0, summary: ''
    });
    
    const createDlg = reactive({ visible: false, form: { voucher_date: null, summary: '', entries: [] } });
    const detailDlg = reactive({ visible: false, data: null });
    
    const totalDebit = computed(() => createDlg.form.entries.reduce((s, e) => s + Number(e.debit || 0), 0).toFixed(2));
    const totalCredit = computed(() => createDlg.form.entries.reduce((s, e) => s + Number(e.credit || 0), 0).toFixed(2));
    const isBalanced = computed(() => Math.abs(Number(totalDebit.value) - Number(totalCredit.value)) < 0.01 && Number(totalDebit.value) > 0);
    
    async function loadAccounts() {
      try { const r = await api.get('/api/finance/accounts'); accounts.value = r.data || []; } catch {}
    }
    async function loadPeriods() {
      // 生成最近12个期间
      const now = new Date();
      const result = [];
      for (let i = 0; i < 12; i++) {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        result.push(`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`);
      }
      periods.value = result;
    }
    async function load() {
      loading.value = true;
      try { 
        const params = { page: page.page, page_size: page.size };
        if (query.period) params.period = query.period;
        if (query.status) params.status = query.status;
        if (query.keyword) params.keyword = query.keyword;
        const r = await api.get('/api/vouchers?' + new URLSearchParams(params).toString()); 
        rows.value = r.items || []; total.value = r.total || 0; 
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function reset() { query.period = ''; query.status = ''; query.keyword = ''; search(); }
    
    function addEntry() {
      createDlg.form.entries.push({ account_id: null, account_code: '', account_name: '', summary: '', debit: 0, credit: 0 });
    }
    function removeEntry(index) {
      createDlg.form.entries.splice(index, 1);
      checkBalance();
    }
    function onAccountChange(row) {
      const acc = accounts.value.find(a => a.id === row.account_id);
      if (acc) { row.account_code = acc.code; row.account_name = acc.name; }
    }
    function checkBalance() { /* 由 computed 自动计算 */ }
    
    function openCreate() {
      createDlg.form = { 
        voucher_date: new Date().toISOString().split('T')[0], 
        summary: '', 
        entries: [{ account_id: null, account_code: '', account_name: '', summary: '', debit: 0, credit: 0 }] 
      };
      createDlg.visible = true;
    }
    
    async function submitCreate() {
      if (!isBalanced.value) { ElMessage.warning('借贷不平衡'); return; }
      try {
        const data = {
          period: createDlg.form.voucher_date ? createDlg.form.voucher_date.substring(0, 7) : '',
          voucher_date: createDlg.form.voucher_date,
          summary: createDlg.form.summary,
          entries: createDlg.form.entries.map(e => ({
            account_id: e.account_id,
            summary: e.summary,
            debit: Number(e.debit || 0),
            credit: Number(e.credit || 0)
          }))
        };
        await api.post('/api/vouchers', data);
        ElMessage.success('凭证已创建并过账');
        createDlg.visible = false;
        load();
      } catch (e) { ElMessage.error(e.message); }
    }
    
    async function postVoucher(row) {
      try { await api.post(`/api/vouchers/${row.id}/post`); ElMessage.success('凭证已过账'); load(); }
      catch (e) { ElMessage.error(e.message); }
    }

    // ---- 复核盖章 + 封账状态(素人化) ----
    const periodMap = ref({});      // { '2026-08': 'OPEN'|'CLOSED' }
    const pendingClose = ref([]);   // 到期但未能自动封的期间+原因
    const userInfo = reactive(JSON.parse(localStorage.getItem('user') || '{}'));
    const canClosePeriod = computed(() => ['ADMIN','GM','FINANCE'].includes(userInfo.role_code || userInfo.role || ''));

    async function loadPeriodStatus() {
      try {
        const r = await api.get('/api/vouchers/periods');
        const m = {};
        (r.items || []).forEach(p => { m[p.period] = p.status; });
        periodMap.value = m;
        pendingClose.value = r.pending_close || [];
        if ((r.auto_closed || []).length) {
          ElMessage.success(`已自动封账 ${r.auto_closed.length} 个到期账期`);
        }
      } catch {}
    }
    async function reviewVoucher(row) {
      try { await api.post(`/api/vouchers/${row.id}/review`); ElMessage.success('已复核盖章'); load(); }
      catch (e) { ElMessage.error(e.message); }
    }
    async function closePeriodNow() {
      try {
        await ElMessageBox.confirm(
          `封账后 ${query.period} 将锁定：不能再录入/修改该月凭证，系统自动结转本月利润。确定封账？`,
          '一键封账', { type: 'warning', confirmButtonText: '确定封账', cancelButtonText: '再想想' });
        const r = await api.post(`/api/vouchers/periods/${query.period}/close`);
        ElMessage.success(r.message || '已封账');
        loadPeriodStatus(); load();
      } catch (e) { if (e !== 'cancel') ElMessage.error(e.message); }
    }
    async function reopenPeriodNow() {
      try {
        await ElMessageBox.confirm(
          `解封 ${query.period} 将自动红冲封账时的结转凭证。仅用于改错账，确定解封？`,
          '解封确认', { type: 'warning', confirmButtonText: '确定解封', cancelButtonText: '取消' });
        const r = await api.post(`/api/vouchers/periods/${query.period}/reopen`);
        ElMessage.success(r.message || '已解封');
        loadPeriodStatus(); load();
      } catch (e) { if (e !== 'cancel') ElMessage.error(e.message); }
    }

    async function reverseVoucher(row) {
      try {
        await ElMessageBox.confirm('确定要红冲此凭证吗？将生成一张冲销凭证。', '红冲确认', { type: 'warning' });
        await api.post(`/api/vouchers/${row.id}/reverse`);
        ElMessage.success('红冲凭证已生成');
        load();
      } catch (e) { if (e !== 'cancel') ElMessage.error(e.message); }
    }
    
    async function openDetail(row) {
      try {
        const r = await api.get(`/api/vouchers/${row.id}`);
        detailDlg.data = r.data || r;
        detailDlg.visible = true;
      } catch (e) { ElMessage.error(e.message); }
    }
    
    // ---- 月结面板 ----
    async function loadMcBadge() {
      // 惰性加载红点: 页面打开时查当前期间
      try {
        const p = query.period || (new Date().getFullYear() + '-' + String(new Date().getMonth()+1).padStart(2,'0'));
        const r = await api.get('/api/vouchers/month-close/check?period=' + p);
        mc.badge = r.badge || 0;
      } catch {}
    }
    function openMc() {
      mc.visible = true;
      if (!mc.period) { const n = new Date(); mc.period = n.getFullYear() + '-' + String(n.getMonth()+1).padStart(2,'0'); }
      loadMc();
    }
    async function loadMc() {
      if (!mc.period) return;
      mc.loading = true; mc.aiAnswer = {};
      try {
        const r = await api.get('/api/vouchers/month-close/check?period=' + mc.period);
        mc.items = r.items || []; mc.is_closed = r.is_closed; mc.all_clear = r.all_clear; mc.badge = r.badge || 0;
      } catch (e) { ElMessage.error(e.message); }
      mc.loading = false;
    }
    async function doPostAllDrafts(it) {
      mc.actionLoading = it.key;
      try {
        const r = await api.post('/api/vouchers/month-close/post-all-drafts?period=' + mc.period);
        ElMessage.success(`已过账 ${r.posted.length} 张` + (r.failed.length ? `, ${r.failed.length} 张失败` : ''));
        loadMc(); load();
      } catch (e) { ElMessage.error(e.message); }
      mc.actionLoading = null;
    }
    async function doAccruePayroll(it) {
      mc.actionLoading = it.key;
      try {
        const r = await api.post('/api/vouchers/month-close/accrue-payroll?period=' + mc.period);
        ElMessage.success(`计提完成: ¥${fmt(r.amount)} → ${r.voucher_no}`);
        loadMc(); load();
      } catch (e) { ElMessage.error(e.message); }
      mc.actionLoading = null;
    }
    async function askAI(it) {
      mc.aiLoading = it.key;
      const q = `月结检查发现"${it.title}", 详情: ${it.detail}。请简要解释这是什么问题、为什么要处理、怎么处理。控制在3句话内。`;
      try {
        const tk = localStorage.getItem(TOKEN_KEY);
        const resp = await fetch('/api/ai-finance/stream', {
          method: 'POST', headers: {'Content-Type':'application/json', 'Authorization': tk ? 'Bearer '+tk : ''},
          body: JSON.stringify({ query: q })
        });
        if (!resp.ok) { mc.aiAnswer[it.key] = '解释获取失败'; mc.aiLoading = null; return; }
        const reader = resp.body.getReader(); const dec = new TextDecoder('utf-8');
        let buf = '', answer = '';
        while (true) {
          const { done, value } = await reader.read(); if (done) break;
          buf += dec.decode(value, {stream:true});
          let i;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, i); buf = buf.slice(i+2);
            let ev = '', data = '';
            block.split('\n').forEach(line => {
              if (line.startsWith('event:')) ev = line.slice(6).trim();
              else if (line.startsWith('data:')) data = line.slice(5).trim();
            });
            if (ev === 'answer' && data) { try { const j = JSON.parse(data); answer += (j.delta||''); } catch {} }
          }
        }
        mc.aiAnswer[it.key] = answer || '暂无解释';
      } catch (e) { mc.aiAnswer[it.key] = '解释获取失败: ' + e.message; }
      mc.aiLoading = null;
    }

    // ---- AI审凭证 ----
    function openReviewDlg() {
      if (!reviewDlg.period) {
        const n = new Date();
        reviewDlg.period = n.getFullYear() + '-' + String(n.getMonth()+1).padStart(2,'0');
      }
      reviewDlg.results = null;
      reviewDlg.visible = true;
    }
    async function runVoucherReview() {
      if (!reviewDlg.period) {
        const n = new Date();
        reviewDlg.period = n.getFullYear() + '-' + String(n.getMonth()+1).padStart(2,'0');
      }
      reviewDlg.loading = true;
      reviewDlg.results = null;
      try {
        const r = await api.post('/api/ai-ops/voucher-review', { period: reviewDlg.period });
        const d = r.data || r;
        reviewDlg.results = d.results || [];
        reviewDlg.total = d.total || 0;
        reviewDlg.total_issues = d.total_issues || 0;
        reviewDlg.summary = d.summary || '';
        if (d.total_issues === 0) ElMessage.success('审核完成，凭证全部正常');
        else ElMessage.warning(`审核完成，发现 ${d.total_issues} 个问题`);
      } catch (e) { ElMessage.error(e.message); }
      reviewDlg.loading = false;
    }
    function openVoucherByNo(no) {
      query.keyword = no;
      page.page = 1;
      reviewDlg.visible = false;
      load().then(() => {
        const found = rows.value.find(v => v.voucher_no === no);
        if (found) openDetail(found);
        else ElMessage.info(`未找到凭证 ${no}`);
      });
    }

    onMounted(async () => {
      await loadAccounts();
      loadPeriods();
      loadPeriodStatus();
      loadMcBadge();
      load();
    });

    return { rows, total, page, loading, query, periods, accounts,
             VOUCHER_STATUS, fmt, fmtDateShort, load, search, reset,
             createDlg, detailDlg, totalDebit, totalCredit, isBalanced,
             addEntry, removeEntry, onAccountChange, checkBalance,
             openCreate, submitCreate, postVoucher, reverseVoucher, openDetail,
             periodMap, pendingClose, canClosePeriod, reviewVoucher, closePeriodNow, reopenPeriodNow,
             mc, openMc, loadMc, doPostAllDrafts, doAccruePayroll, askAI,
             reviewDlg, openReviewDlg, runVoucherReview, openVoucherByNo,
             Icon, ElMessageBox };
  }
};

// ============ 财务报表 ============
const ReportsPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('chart-bar',22)"></div>
        <div>
          <div class="ph-title">财务报表</div>
          <div class="ph-sub">利润表 · 试算平衡表 · 资产负债表</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-select v-model="period" style="width:160px" @change="loadAll">
          <el-option v-for="p in periods" :key="p" :label="p+' 期'" :value="p"/>
        </el-select>
        <el-button type="primary" @click="exportExcel">导出Excel</el-button>
      </div>
    </div>

    <div class="report-body">
    <div class="report-main">
    <!-- Tab切换 -->
    <div class="report-tabs">
      <div :class="['report-tab', {active: activeTab==='profit'}]" @click="switchTab('profit')">利润表</div>
      <div :class="['report-tab', {active: activeTab==='trial'}]" @click="switchTab('trial')">试算平衡表</div>
      <div :class="['report-tab', {active: activeTab==='balance'}]" @click="switchTab('balance')">资产负债表</div>
    </div>

    <!-- 利润表 -->
    <div v-if="activeTab==='profit'" class="report-content" v-loading="loading">
      <div class="report-header">
        <h2>利润表</h2>
        <span class="report-period">会计期间: {{ period }}</span>
      </div>
      <div v-if="profitData" class="profit-statement">
        <div class="ps-section revenue">
          <div class="ps-title">一、营业收入</div>
          <div v-if="profitData.revenue_details.length" class="ps-details">
            <template v-for="d in profitData.revenue_details" :key="d.account_code">
              <div class="ps-row ps-expandable" @click="toggleRow('rev-'+d.account_code, d.account_code)">
                <span class="ps-toggle">{{ expandedRows['rev-'+d.account_code] ? '−' : '+' }}</span>
                <span class="ps-item">{{ d.account_name }}</span>
                <span class="ps-amount pos">¥{{ fmt(d.amount) }}</span>
              </div>
              <div v-if="expandedRows['rev-'+d.account_code]" class="ps-detail-panel">
                <div v-if="detailLoading['rev-'+d.account_code]" class="ps-detail-loading">加载中...</div>
                <div v-else-if="detailCache['rev-'+d.account_code] && detailCache['rev-'+d.account_code].length">
                  <div v-for="e in detailCache['rev-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                    <span class="pd-date">{{ e.voucher_date }}</span>
                    <span class="pd-no">{{ e.voucher_no }}</span>
                    <span class="pd-summary">{{ e.summary }}</span>
                    <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                    <span class="pd-credit">贷 ¥{{ fmt(e.credit) }}</span>
                  </div>
                </div>
                <div v-else class="ps-detail-empty">无明细</div>
              </div>
            </template>
          </div>
          <div class="ps-total">
            <span>营业收入合计</span>
            <span class="ps-amount pos">¥{{ fmt(profitData.total_revenue) }}</span>
          </div>
        </div>

        <div class="ps-section expense">
          <div class="ps-title">减：营业成本</div>
          <template v-for="d in (profitData.expense_details||[]).filter(x=>x.category==='COGS')" :key="'cogs-'+d.account_code">
            <div class="ps-row ps-expandable" @click="toggleRow('cogs-'+d.account_code, d.account_code)">
              <span class="ps-toggle">{{ expandedRows['cogs-'+d.account_code] ? '−' : '+' }}</span>
              <span class="ps-item">{{ d.account_name }}</span>
              <span class="ps-amount neg">-¥{{ fmt(d.amount) }}</span>
            </div>
            <div v-if="expandedRows['cogs-'+d.account_code]" class="ps-detail-panel">
              <div v-if="detailLoading['cogs-'+d.account_code]" class="ps-detail-loading">加载中...</div>
              <div v-else-if="detailCache['cogs-'+d.account_code] && detailCache['cogs-'+d.account_code].length">
                <div v-for="e in detailCache['cogs-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                  <span class="pd-date">{{ e.voucher_date }}</span>
                  <span class="pd-no">{{ e.voucher_no }}</span>
                  <span class="pd-summary">{{ e.summary }}</span>
                  <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                  <span class="pd-debit">借 ¥{{ fmt(e.debit) }}</span>
                </div>
              </div>
              <div v-else class="ps-detail-empty">无明细</div>
            </div>
          </template>
          <div class="ps-total">
            <span>主营业务成本</span>
            <span class="ps-amount neg">-¥{{ fmt(profitData.total_cogs) }}</span>
          </div>
        </div>

        <div class="ps-total gross-profit">
          <span>二、毛利润</span>
          <span class="ps-amount" :class="profitData.gross_profit >= 0 ? 'pos' : 'neg'">¥{{ fmt(profitData.gross_profit) }}</span>
        </div>

        <div class="ps-section expense">
          <div class="ps-title">减：期间费用</div>
          <template v-for="d in (profitData.expense_details||[]).filter(x=>x.category!=='COGS')" :key="'exp-'+d.account_code">
            <div class="ps-row ps-expandable" @click="toggleRow('exp-'+d.account_code, d.account_code)">
              <span class="ps-toggle">{{ expandedRows['exp-'+d.account_code] ? '−' : '+' }}</span>
              <span class="ps-item">{{ d.account_name }}</span>
              <span class="ps-amount neg">-¥{{ fmt(d.amount) }}</span>
            </div>
            <div v-if="expandedRows['exp-'+d.account_code]" class="ps-detail-panel">
              <div v-if="detailLoading['exp-'+d.account_code]" class="ps-detail-loading">加载中...</div>
              <div v-else-if="detailCache['exp-'+d.account_code] && detailCache['exp-'+d.account_code].length">
                <div v-for="e in detailCache['exp-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                  <span class="pd-date">{{ e.voucher_date }}</span>
                  <span class="pd-no">{{ e.voucher_no }}</span>
                  <span class="pd-summary">{{ e.summary }}</span>
                  <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                  <span class="pd-debit">借 ¥{{ fmt(e.debit) }}</span>
                </div>
              </div>
              <div v-else class="ps-detail-empty">无明细</div>
            </div>
          </template>
          <div class="ps-total">
            <span>期间费用合计</span>
            <span class="ps-amount neg">-¥{{ fmt(profitData.total_selling_expense + profitData.total_admin_expense + profitData.total_finance_expense + profitData.total_other_expense) }}</span>
          </div>
        </div>

        <div class="ps-total operating-profit">
          <span>三、营业利润</span>
          <span class="ps-amount" :class="profitData.operating_profit >= 0 ? 'pos' : 'neg'">¥{{ fmt(profitData.operating_profit) }}</span>
        </div>

        <div class="ps-total net-profit final">
          <span>四、净利润</span>
          <span class="ps-amount" :class="profitData.net_profit >= 0 ? 'pos' : 'neg'">¥{{ fmt(profitData.net_profit) }}</span>
        </div>
      </div>
      <div v-else-if="!loading" class="report-empty">
        <div>暂无数据</div>
        <div class="hint">请先录入并过账凭证</div>
      </div>
    </div>

    <!-- 试算平衡表 -->
    <div v-if="activeTab==='trial'" class="report-content" v-loading="loading">
      <div class="report-header">
        <h2>试算平衡表</h2>
        <span class="report-period">会计期间: {{ period }}</span>
        <el-tag v-if="trialData && trialData.is_balanced" type="success" size="small">✓ 已平衡</el-tag>
        <el-tag v-else-if="trialData && !trialData.is_balanced" type="danger" size="small">✗ 不平衡</el-tag>
      </div>
      <div v-if="trialData && trialData.balances.length" class="trial-balance">
        <el-table :data="trialData.balances" border size="small" stripe
                  row-key="account_code"
                  :expand-row-keys="Object.keys(expandedRows).filter(k=>expandedRows[k]&&k.startsWith('trial-'))"
                  @expand-change="(row, expanded) => { const key='trial-'+row.account_code; if(expanded.length) toggleRow(key, row.account_code); }">
          <el-table-column type="expand" width="30">
            <template #default="{ row }">
              <div class="trial-detail-wrap">
                <div v-if="detailLoading['trial-'+row.account_code]" style="padding:8px;color:#999">加载中...</div>
                <div v-else-if="detailCache['trial-'+row.account_code] && detailCache['trial-'+row.account_code].length">
                  <div v-for="e in detailCache['trial-'+row.account_code]" :key="e.voucher_id" class="ps-detail-row">
                    <span class="pd-date">{{ e.voucher_date }}</span>
                    <span class="pd-no">{{ e.voucher_no }}</span>
                    <span class="pd-summary">{{ e.summary }}</span>
                    <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                    <span class="pd-debit" v-if="e.debit">借 ¥{{ fmt(e.debit) }}</span>
                    <span class="pd-credit" v-if="e.credit">贷 ¥{{ fmt(e.credit) }}</span>
                  </div>
                </div>
                <div v-else style="padding:8px;color:#999">无明细凭证</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="account_code" label="科目编码" width="100"/>
          <el-table-column prop="account_name" label="科目名称" width="150"/>
          <el-table-column prop="account_type" label="类型" width="80">
            <template #default="{ row }">
              <span :class="['type-tag', row.account_type]">{{ {ASSET:'资产', LIABILITY:'负债', REVENUE:'收入', EXPENSE:'费用', EQUITY:'权益'}[row.account_type] || row.account_type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="期初余额" width="180">
            <el-table-column prop="opening_debit" label="借方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.opening_debit">¥{{ fmt(row.opening_debit) }}</span></template>
            </el-table-column>
            <el-table-column prop="opening_credit" label="贷方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.opening_credit">¥{{ fmt(row.opening_credit) }}</span></template>
            </el-table-column>
          </el-table-column>
          <el-table-column label="本期发生额" width="180">
            <el-table-column prop="debit_amount" label="借方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.debit_amount">¥{{ fmt(row.debit_amount) }}</span></template>
            </el-table-column>
            <el-table-column prop="credit_amount" label="贷方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.credit_amount">¥{{ fmt(row.credit_amount) }}</span></template>
            </el-table-column>
          </el-table-column>
          <el-table-column label="期末余额" width="180">
            <el-table-column prop="closing_debit" label="借方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.closing_debit">¥{{ fmt(row.closing_debit) }}</span></template>
            </el-table-column>
            <el-table-column prop="closing_credit" label="贷方" width="90" align="right">
              <template #default="{ row }"><span v-if="row.closing_credit">¥{{ fmt(row.closing_credit) }}</span></template>
            </el-table-column>
          </el-table-column>
        </el-table>
        <div class="trial-totals">
          <div>合计: 期初借 ¥{{ fmt(trialData.totals.opening_debit) }} / 贷 ¥{{ fmt(trialData.totals.opening_credit) }}</div>
          <div>合计: 本期借 ¥{{ fmt(trialData.totals.debit_amount) }} / 贷 ¥{{ fmt(trialData.totals.credit_amount) }}</div>
          <div>合计: 期末借 ¥{{ fmt(trialData.totals.closing_debit) }} / 贷 ¥{{ fmt(trialData.totals.closing_credit) }}</div>
        </div>
      </div>
      <div v-else-if="!loading" class="report-empty">
        <div>暂无数据</div>
        <div class="hint">请先录入并过账凭证</div>
      </div>
    </div>

    <!-- 资产负债表 -->
    <div v-if="activeTab==='balance'" class="report-content" v-loading="loading">
      <div class="report-header">
        <h2>资产负债表</h2>
        <span class="report-period">会计期间: {{ period }}</span>
        <el-tag v-if="balanceData && balanceData.is_balanced" type="success" size="small">✓ 平衡</el-tag>
        <el-tag v-else-if="balanceData && !balanceData.is_balanced" type="danger" size="small">✗ 不平衡</el-tag>
      </div>
      <div v-if="balanceData" class="balance-sheet">
        <div class="bs-section">
          <h3>资产</h3>
          <div v-if="balanceData.asset_details.length" class="bs-list">
            <template v-for="d in balanceData.asset_details" :key="d.account_code">
              <div class="bs-row ps-expandable" @click="toggleRow('bs-asset-'+d.account_code, d.account_code)">
                <span class="ps-toggle">{{ expandedRows['bs-asset-'+d.account_code] ? '−' : '+' }}</span>
                <span>{{ d.account_name }}</span>
                <span class="bs-amount">¥{{ fmt(d.amount) }}</span>
              </div>
              <div v-if="expandedRows['bs-asset-'+d.account_code]" class="ps-detail-panel">
                <div v-if="detailLoading['bs-asset-'+d.account_code]" class="ps-detail-loading">加载中...</div>
                <div v-else-if="detailCache['bs-asset-'+d.account_code] && detailCache['bs-asset-'+d.account_code].length">
                  <div v-for="e in detailCache['bs-asset-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                    <span class="pd-date">{{ e.voucher_date }}</span>
                    <span class="pd-no">{{ e.voucher_no }}</span>
                    <span class="pd-summary">{{ e.summary }}</span>
                    <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                    <span class="pd-debit" v-if="e.debit">借 ¥{{ fmt(e.debit) }}</span>
                    <span class="pd-credit" v-if="e.credit">贷 ¥{{ fmt(e.credit) }}</span>
                  </div>
                </div>
                <div v-else class="ps-detail-empty">无明细</div>
              </div>
            </template>
          </div>
          <div class="bs-total">
            <span>资产合计</span>
            <span class="bs-amount total">¥{{ fmt(balanceData.total_assets) }}</span>
          </div>
        </div>
        <div class="bs-section">
          <h3>负债</h3>
          <div v-if="balanceData.liability_details.length" class="bs-list">
            <template v-for="d in balanceData.liability_details" :key="d.account_code">
              <div class="bs-row ps-expandable" @click="toggleRow('bs-liab-'+d.account_code, d.account_code)">
                <span class="ps-toggle">{{ expandedRows['bs-liab-'+d.account_code] ? '−' : '+' }}</span>
                <span>{{ d.account_name }}</span>
                <span class="bs-amount">¥{{ fmt(d.amount) }}</span>
              </div>
              <div v-if="expandedRows['bs-liab-'+d.account_code]" class="ps-detail-panel">
                <div v-if="detailLoading['bs-liab-'+d.account_code]" class="ps-detail-loading">加载中...</div>
                <div v-else-if="detailCache['bs-liab-'+d.account_code] && detailCache['bs-liab-'+d.account_code].length">
                  <div v-for="e in detailCache['bs-liab-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                    <span class="pd-date">{{ e.voucher_date }}</span>
                    <span class="pd-no">{{ e.voucher_no }}</span>
                    <span class="pd-summary">{{ e.summary }}</span>
                    <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                    <span class="pd-debit" v-if="e.debit">借 ¥{{ fmt(e.debit) }}</span>
                    <span class="pd-credit" v-if="e.credit">贷 ¥{{ fmt(e.credit) }}</span>
                  </div>
                </div>
                <div v-else class="ps-detail-empty">无明细</div>
              </div>
            </template>
          </div>
          <div class="bs-total">
            <span>负债合计</span>
            <span class="bs-amount">¥{{ fmt(balanceData.total_liabilities) }}</span>
          </div>
        </div>
        <div class="bs-section">
          <h3>所有者权益</h3>
          <div v-if="balanceData.equity_details.length" class="bs-list">
            <template v-for="d in balanceData.equity_details" :key="d.account_code">
              <div class="bs-row ps-expandable" @click="toggleRow('bs-eq-'+d.account_code, d.account_code)">
                <span class="ps-toggle">{{ expandedRows['bs-eq-'+d.account_code] ? '−' : '+' }}</span>
                <span>{{ d.account_name }}</span>
                <span class="bs-amount">¥{{ fmt(d.amount) }}</span>
              </div>
              <div v-if="expandedRows['bs-eq-'+d.account_code]" class="ps-detail-panel">
                <div v-if="detailLoading['bs-eq-'+d.account_code]" class="ps-detail-loading">加载中...</div>
                <div v-else-if="detailCache['bs-eq-'+d.account_code] && detailCache['bs-eq-'+d.account_code].length">
                  <div v-for="e in detailCache['bs-eq-'+d.account_code]" :key="e.voucher_id" class="ps-detail-row">
                    <span class="pd-date">{{ e.voucher_date }}</span>
                    <span class="pd-no">{{ e.voucher_no }}</span>
                    <span class="pd-summary">{{ e.summary }}</span>
                    <span class="pd-aux" v-if="e.aux_name">[{{ e.aux_name }}]</span>
                    <span class="pd-debit" v-if="e.debit">借 ¥{{ fmt(e.debit) }}</span>
                    <span class="pd-credit" v-if="e.credit">贷 ¥{{ fmt(e.credit) }}</span>
                  </div>
                </div>
                <div v-else class="ps-detail-empty">无明细</div>
              </div>
            </template>
          </div>
          <div class="bs-row">
            <span>未分配利润</span>
            <span class="bs-amount">¥{{ fmt(balanceData.net_profit) }}</span>
          </div>
          <div class="bs-total">
            <span>所有者权益合计</span>
            <span class="bs-amount">¥{{ fmt(balanceData.total_equity) }}</span>
          </div>
        </div>
        <div class="bs-final">
          <div>资产 = 负债 + 所有者权益</div>
          <div class="bs-equation">
            <span>¥{{ fmt(balanceData.total_assets) }}</span>
            <span>=</span>
            <span>¥{{ fmt(balanceData.total_liabilities) }}</span>
            <span>+</span>
            <span>¥{{ fmt(balanceData.total_equity) }}</span>
          </div>
        </div>
      </div>
      <div v-else-if="!loading" class="report-empty">
        <div>暂无数据</div>
        <div class="hint">请先录入并过账凭证</div>
      </div>
    </div>

    </div><!-- /report-main -->

    <!-- 右侧老板白话卡（随Tab切换） -->
    <div class="boss-panel">
      <div class="bp-head">
        <span class="bp-icon" v-html="Icon.icon('user',16)"></span>
        <span>老板一眼懂</span>
      </div>

      <!-- 大字标题 -->
      <div class="bp-profit" :class="bossView.headStatus">
        <div class="bp-profit-label">{{ bossView.headline }}</div>
        <div class="bp-profit-num" v-if="bossView.headVal !== null">¥{{ fmt(bossView.headVal) }}</div>
      </div>

      <!-- 一句话总结 -->
      <div class="bp-summary">{{ bossView.summary }}</div>

      <!-- 数据条目（随Tab变化） -->
      <div class="bp-section">
        <div class="bp-sec-title">{{ activeTab === 'profit' ? '💰 钱怎么赚怎么花的' : activeTab === 'trial' ? '⚖️ 三行合计人话解读' : '🏠 家当怎么构成的' }}</div>
        <div v-for="(it, i) in bossView.items" :key="i" :class="['bp-row', it.bold ? 'bp-row-bold' : '', it.label.startsWith('    ') ? 'bp-row-child' : '']">
          <span>{{ it.label }}</span>
          <b :class="it.cls" v-if="it.isText">{{ it.text }}</b>
          <b :class="it.cls" v-else>{{ it.val >= 0 ? '¥' + fmt(it.val) : '-¥' + fmt(Math.abs(it.val)) }}</b>
        </div>
      </div>

      <!-- 附加指标 -->
      <div v-if="bossView.extra?.length" class="bp-section">
        <div class="bp-sec-title">📊 关键指标</div>
        <div v-for="(e, i) in bossView.extra" :key="i" class="bp-row">
          <span>{{ e.label }}</span><b>{{ e.val }}</b>
        </div>
      </div>

      <!-- 异常预警 -->
      <div v-if="bossView.warnings?.length" class="bp-section bp-warn">
        <div class="bp-sec-title">⚠️ 需要注意</div>
        <div v-for="(w,i) in bossView.warnings" :key="i" :class="['bp-warn-item', w.type]">
          <span :class="['bp-warn-dot', w.type]"></span>{{ w.text }}
        </div>
      </div>

      <!-- 会计黑话翻译（随Tab切换） -->
      <div class="bp-section bp-glossary">
        <div class="bp-sec-title">📖 会计黑话翻译</div>
        <div v-for="g in glossary()" :key="g.t" class="bp-gloss-item">
          <div class="bp-gloss-t">{{ g.t }}</div>
          <div class="bp-gloss-d">{{ g.d }}</div>
        </div>
      </div>
    </div>

    </div><!-- /report-body -->
  </div>`,
  setup() {
    const activeTab = ref('profit');
    const loading = ref(false);
    const period = ref('');
    const periods = ref([]);
    const profitData = ref(null);
    const trialData = ref(null);
    const balanceData = ref(null);
    const expandedRows = reactive({});
    const detailCache = reactive({});
    const detailLoading = ref({});
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });

    // 黑话翻译——全部说人话，消灭会计术语
    const GLOSSARY = {
      profit: [
        { t: '收入（卖了多少钱）', d: '这个月给客户加工/卖货，一共应该收多少钱（不管收没收到钱）' },
        { t: '成本（做货花了多少）', d: '为了做这批活直接花掉的钱：买材料、付外协加工费，卖一单算一单' },
        { t: '毛利润', d: '卖货收到的钱 - 做货直接花的钱 = 这单活本身赚不赚钱' },
        { t: '费用（运营开销）', d: '不管有没有生意都要花的钱：房租、工资、跑业务请客、银行手续费' },
        { t: '· 销售费用', d: '跑业务花的：差旅费、请客户吃饭、业务员提成' },
        { t: '· 管理费用', d: '办公室花的：房租、办公用品、员工工资、设备折旧' },
        { t: '· 财务费用', d: '银行扣的手续费、贷款利息、承兑汇票贴现亏的钱' },
        { t: '净利润（最后落袋）', d: '毛利扣掉所有开销，真正赚到手的钱' },
        { t: '毛利率', d: '每做100块钱的活，扣掉直接成本后能赚多少块，越高越好' },
      ],
      trial: [
        { t: '借（借方）', d: '打个比方：你钱包里多了100块钱，这100块"去了哪里"——是在你钱包里（现金增加），还是买了东西（费用增加），还是别人借走了（应收增加）。钱的"去向"就叫借。资产增加、费用增加记借方。' },
        { t: '贷（贷方）', d: '接着上面：你这100块是"从哪来的"——是工资发的（收入），是从银行借的（负债），还是自己投的本钱（权益）。钱的"来源"就叫贷。收入增加、负债增加、利润增加记贷方。' },
        { t: '一句话记借贷', d: '借 = 钱花去了哪 / 我们拥有什么 ； 贷 = 钱从哪来 / 我们欠什么。每笔钱有来源必有去向，所以借合计必须=贷合计。' },
        { t: '期初余额', d: '就是月初（上个月最后一天）的"底子"——月初账户里有多少钱、还欠多少钱。相当于上个月的"期末"搬到这个月当"期初"。' },
        { t: '本期发生额', d: '就是这个月之内新发生的进出——这个月一共新收了多少钱、新花了多少钱、新欠了多少钱、新还了多少钱。只看本月变动，不含月初底子。' },
        { t: '期末余额', d: '就是到这个月底的最终结果 = 月初底子 + 本月新进来的 - 本月花出去的。期末 = 期初 + 本期变动。下个月它就变成下个月的"期初"。' },
        { t: '试算平衡（账对没对上）', d: '把所有账户的"借"加起来，所有账户的"贷"加起来，两边必须相等。不等就是某张凭证录错了——金额写错了、方向记反了、或者漏录了。' },
        { t: '为什么不平？怎么查？', d: '差额÷2，如果有某个账户余额正好等于这个数，大概率是方向记反了（借写成贷或贷写成借）。否则按差额金额逐张查凭证——看有没有哪笔金额刚好等于这个差额。' },
        { t: '资产类科目', d: '我们拥有的东西：现金、银行存款、仓库的货、设备、客户欠我们的钱。正常余额在借方（正数），出现在贷方就是异常。' },
        { t: '负债类科目', d: '我们欠别人的：欠供应商、欠银行、欠员工工资、欠税。正常余额在贷方（正数），出现在借方就是异常。' },
        { t: '权益类科目', d: '老板真正拥有的家底：本钱 + 累计赚的利润。资产 - 负债 = 权益。' },
      ],
      balance: [
        { t: '资产（公司家当）', d: '公司拥有的一切值钱东西：银行里的钱、保险柜现金、仓库里的货、设备、客户欠我们的钱' },
        { t: '负债（欠外面的）', d: '公司欠别人的所有钱：欠供应商货款、欠银行贷款、欠员工工资、欠税务局的税' },
        { t: '所有者权益（老板家底）', d: '这是最容易晕的词。打个比方：你有一套100万的房子，还欠银行40万房贷，那100万是资产，40万是负债，剩下60万才真正是你的——这60万就是"所有者权益"。简单说：把公司所有东西卖掉、所有债还清，最后真正落进老板口袋里的钱。' },
        { t: '· 实收资本', d: '老板当初开公司时投进来的本钱' },
        { t: '· 未分配利润', d: '公司从开业到现在，累计赚了但还没分给老板的钱' },
        { t: '资产负债率', d: '欠的钱占总家当的比例。低于50%很安全，50-70%正常，超过70%就比较危险了，说明大部分家当都是借的' },
        { t: '应收/应付账款', d: '应收=客户拿货没给钱（别人欠我）；应付=我们拿货没给钱（我欠别人）' },
        { t: '累计折旧', d: '设备用久了变旧贬值，每年扣一点，不是真金白银花出去的' },
      ],
    };

    function glossary() { return GLOSSARY[activeTab.value] || GLOSSARY.profit; }

    // 老板速览卡（0 token，全大白话）
    const bossView = computed(() => {
      const tab = activeTab.value;
      const p = profitData.value, b = balanceData.value, t = trialData.value;
      const getBal = (code) => {
        if (!t?.balances) return 0;
        const row = t.balances.find(x => x.account_code === code);
        if (!row) return 0;
        return Number(row.closing_debit || 0) - Number(row.closing_credit || 0);
      };

      if (tab === 'profit') {
        const netProfit = p ? Number(p.net_profit || 0) : 0;
        const gross = p ? Number(p.gross_profit || 0) : 0;
        const revenue = p ? Number(p.total_revenue || 0) : 0;
        const cogs = p ? Number(p.total_cogs || 0) : 0;
        const sellExp = p ? Number(p.total_selling_expense || 0) : 0;
        const adminExp = p ? Number(p.total_admin_expense || 0) : 0;
        const finExp = p ? Number(p.total_finance_expense || 0) : 0;
        const otherExp = p ? Number(p.total_other_expense || 0) : 0;
        const totalExp = sellExp + adminExp + finExp + otherExp;
        const margin = revenue > 0 ? (gross / revenue * 100).toFixed(1) : 0;
        const ar = Math.max(0, getBal('1122'));
        const warnings = [];
        if (ar > revenue * 1.5 && revenue > 0) warnings.push({type:'warning', text:'客户欠我们的钱已经是月营收的1.5倍了，赶紧催收！'});
        if (revenue === 0 && totalExp > 0) warnings.push({type:'warning', text:'这个月没开张但花了¥' + fmt(totalExp) + '的费用'});

        // 大白话总结
        let summary = '';
        if (revenue === 0) {
          summary = `这个月还没有做单/开票收入，但已经产生了¥${fmt(totalExp)}的运营开销，所以账面亏¥${fmt(Math.abs(netProfit))}。赶紧找活干。`;
        } else if (netProfit > 0) {
          summary = `这个月做了¥${fmt(revenue)}的活，做货花了¥${fmt(cogs)}，运营开销¥${fmt(totalExp)}，最后净赚¥${fmt(netProfit)}。毛利率${margin}%。`;
          if (warnings.length) summary += ' 但是要注意下面的提醒。';
        } else if (netProfit < 0) {
          const lossReason = cogs > revenue ? '做货成本比卖价还高，卖一单亏一单' : `毛利¥${fmt(gross)}不够覆盖¥${fmt(totalExp)}的运营开销`;
          summary = `这个月做了¥${fmt(revenue)}的活，但${lossReason}，最终亏了¥${fmt(Math.abs(netProfit))}。`;
        } else summary = '本月刚好不赚不亏。';

        return { tab, headline: netProfit > 0 ? '本月净赚' : netProfit < 0 ? '本月净亏' : '本月盈亏',
          headVal: Math.abs(netProfit), headStatus: netProfit > 0.01 ? 'pos' : netProfit < -0.01 ? 'neg' : 'zero',
          summary, warnings,
          items: [
            { label: '① 卖货/加工进账', val: revenue, cls: 'pos' },
            { label: '② 做货直接花掉（材料/外协）', val: -cogs, cls: 'neg' },
            { label: '③ 扣掉成本后毛利', val: gross, cls: gross >= 0 ? 'pos' : 'neg', bold: true },
            { label: '④ 运营开销合计（房租工资业务银行）', val: -totalExp, cls: 'neg' },
            { label: '⑤ 最后真正落袋', val: netProfit, cls: netProfit >= 0 ? 'pos' : 'neg', bold: true },
          ],
          extra: [{ label: '每做100块活，毛利是', val: margin + ' 块' }],
        };
      }

      if (tab === 'trial') {
        const totals = t ? t.totals : null;
        const opDebit = totals ? Number(totals.opening_debit || 0) : 0;
        const opCredit = totals ? Number(totals.opening_credit || 0) : 0;
        const periodDebit = totals ? Number(totals.debit_amount || 0) : 0;
        const periodCredit = totals ? Number(totals.credit_amount || 0) : 0;
        const clDebit = totals ? Number(totals.closing_debit || 0) : 0;
        const clCredit = totals ? Number(totals.closing_credit || 0) : 0;
        const EPS = 0.01;
        const opDiff = Math.abs(opDebit - opCredit);
        const periodDiff = Math.abs(periodDebit - periodCredit);
        const clDiff = Math.abs(clDebit - clCredit);
        const opBalanced = opDiff < EPS;
        const periodBalanced = periodDiff < EPS;
        const clBalanced = clDiff < EPS;
        const balanced = clBalanced; // 期末平衡才算整体平衡
        const warnings = [];
        const badRows = [];
        if (t?.balances) {
          for (const r of t.balances) {
            if (r.account_type === 'ASSET' && Number(r.closing_credit||0) > EPS)
              badRows.push(r.account_name);
            if (r.account_type === 'LIABILITY' && Number(r.closing_debit||0) > EPS)
              badRows.push(r.account_name);
          }
        }
        const inv = getBal('1405');
        if (inv < -EPS) warnings.push({type:'warning', text:'仓库里的货记成负数了（¥' + fmt(Math.abs(inv)) + '），有入库没录或者成本转多了，查"库存商品"'});
        if (badRows.length) warnings.push({type:'warning', text:'这几个科目方向记反了：' + badRows.join('、') + '，让财务查'});

        // 差额诊断
        let diffHint = '';
        if (!balanced) {
          if (opBalanced && !clBalanced) {
            // 期初平，期末不平 = 本月凭证录错
            const halfDiff = clDiff / 2;
            const flipped = t.balances.find(r => Math.abs(Math.abs(Number(r.closing_debit||0)-Number(r.closing_credit||0)) - halfDiff) < 0.1);
            if (flipped) {
              diffHint = '问题出在这个月录的凭证。很可能是"' + flipped.account_name + '"方向记反了（借写成贷或反之），让财务先查这个。';
            } else {
              diffHint = '问题出在这个月录的凭证。差额¥' + fmt(clDiff) + '，常见原因：金额录错、借贷方向反、或漏了一笔。按差额逐张查本月凭证。';
            }
          } else if (!opBalanced && clBalanced) {
            diffHint = '月初账差¥' + fmt(opDiff) + '，本月凭证碰巧冲抵了——不代表账是对的，历史遗留问题还得查。';
          } else if (!opBalanced && !clBalanced) {
            const newDiff = Math.abs(clDiff - opDiff);
            if (newDiff < EPS) {
              diffHint = '差额和月初一样¥' + fmt(clDiff) + '，本月没录错，是以前遗留的问题。';
            } else {
              diffHint = '月初差¥' + fmt(opDiff) + '（历史问题），本月又新增差额¥' + fmt(newDiff) + '。先查本月的错，再处理历史。';
            }
          }
          warnings.push({type:'danger', text: diffHint});
        }

        let summary = '';
        if (balanced) {
          summary = '账对得上。月初底子、本月进出、月底结果三行借贷都相等，账没做错。';
        } else {
          summary = '账没对上！' + diffHint;
        }

        const opStatus = opBalanced ? '✓ 对得上' : '✗ 差¥' + fmt(opDiff) + '（上月遗留）';
        const periodStatus = periodBalanced ? '✓ 对得上' : '✗ 差¥' + fmt(periodDiff) + '（本月录的有问题）';
        const clStatus = clBalanced ? '✓ 对得上' : '✗ 差¥' + fmt(clDiff);

        return { tab, headline: balanced ? '账对上了 ✓' : '账没对上 ✗',
          headVal: null, headStatus: balanced ? 'pos' : 'neg',
          summary, warnings,
          items: [
            { label: '① 月初有多少（钱+货+设备+别人欠我）', val: null, cls: '', text: '¥' + fmt(opDebit), isText: true },
            { label: '    月初欠多少（供应商+银行+工资+税）', val: null, cls: '', text: '¥' + fmt(opCredit), isText: true },
            { label: '    月初老板家底（有-欠）', val: opDebit - opCredit, cls: Math.abs(opDebit - opCredit) < EPS ? 'pos' : (opDebit > opCredit ? 'pos' : 'neg') },
            { label: '② 本月新出的（花出去/新买货/别人新欠我）', val: null, cls: '', text: '¥' + fmt(periodDebit), isText: true },
            { label: '    本月新进的（收回来/新借的/新赚的）', val: null, cls: '', text: '¥' + fmt(periodCredit), isText: true },
            { label: '    本月净进出（出-进）', val: periodDebit - periodCredit, cls: Math.abs(periodDebit - periodCredit) < EPS ? 'pos' : '' },
            { label: '③ 月底有多少（钱+货+设备+别人欠我）', val: null, cls: '', text: '¥' + fmt(clDebit), isText: true, bold: true },
            { label: '    月底欠多少（供应商+银行+工资+税）', val: null, cls: '', text: '¥' + fmt(clCredit), isText: true, bold: true },
            { label: '    月底老板家底（有-欠）', val: clDebit - clCredit, cls: clBalanced ? 'pos' : 'neg', bold: true },
          ],
          extra: [{ label: '共记了多少个账户', val: (t?.balances?.length || 0) + ' 个' }],
        };
      }

      // balance sheet
      const totalAssets = b ? Number(b.total_assets || 0) : 0;
      const totalLiab = b ? Number(b.total_liabilities || 0) : 0;
      const totalEq = b ? Number(b.total_equity || 0) : 0;
      const netProfit = b ? Number(b.net_profit || 0) : 0;
      const balanced = b ? b.is_balanced : false;
      const debtRatio = totalAssets > 0 ? (totalLiab / totalAssets * 100).toFixed(1) : 0;
      const cash = getBal('1001') + getBal('1002');
      const ar = Math.max(0, getBal('1122'));
      const ap = Math.max(0, -getBal('2202'));
      const loan = Math.max(0, -getBal('2001'));
      const accept = Math.max(0, getBal('1121'));
      const inventory = getBal('1401') + getBal('1405') + getBal('1403');
      const fa = getBal('1601') - Math.abs(getBal('1602')); // 固定资产净值
      const warnings = [];
      if (!balanced) warnings.push({type:'danger', text:'账没对上，资产负债表数字不准，先去试算平衡表查问题'});
      let debtStatus = '';
      if (Number(debtRatio) <= 50) debtStatus = '安全';
      else if (Number(debtRatio) <= 70) debtStatus = '正常偏高';
      else { debtStatus = '危险！欠太多了'; warnings.push({type:'danger', text:`资产负债率${debtRatio}%，大部分家当都是借的，一旦收不回款就危险了`}); }
      if (cash < ap + loan && (ap + loan) > 0) warnings.push({type:'danger', text:'手里现金不够还短期要付的钱，资金紧'});
      if (ar > totalAssets * 0.3) warnings.push({type:'warning', text:'客户欠我们的钱占了家当的' + (ar/totalAssets*100).toFixed(0) + '%，压款太多要催收'});

      let summary = balanced
        ? `公司一共有¥${fmt(totalAssets)}的家当（钱+货+设备+别人欠我们的），其中¥${fmt(totalLiab)}是欠外面的（供应商+银行），剩下¥${fmt(totalEq)}是真正属于老板自己的家底。负债率${debtRatio}%，${debtStatus}。`
        : `账还没对上，资产负债表数字不准，先把账做平再看。`;

      return { tab, headline: '公司全部家当', headVal: totalAssets, headStatus: 'pos',
        summary, warnings,
        items: [
          { label: '手里能用的钱（银行+现金+承兑）', val: cash + accept, cls: 'pos' },
          { label: '客户还没给的钱（应收账款）', val: ar, cls: '' },
          { label: '仓库里的货（原材料+库存）', val: inventory, cls: '' },
          { label: '设备（扣掉折旧后净值）', val: fa, cls: '' },
          { label: '① 家当合计', val: totalAssets, cls: 'pos', bold: true },
          { label: '欠供应商的钱（应付账款）', val: -ap, cls: 'neg' },
          ...(loan > 0 ? [{ label: '欠银行贷款', val: -loan, cls: 'neg' }] : []),
          { label: '② 外债合计', val: -totalLiab, cls: 'neg', bold: true },
          { label: '③ 老板真正的家底（①-②）', val: totalEq, cls: 'pos', bold: true },
        ],
        extra: [
          { label: '资产负债率', val: debtRatio + '%（' + debtStatus + '）' },
          { label: '本月经营成果', val: (netProfit >= 0 ? '赚了¥' : '亏了¥') + fmt(Math.abs(netProfit)) },
        ],
      };
    });

    function switchTab(tab) {
      activeTab.value = tab;
    }

    // 导出当前Tab为Excel
    function exportExcel() {
      if (typeof XLSX === 'undefined') { ElMessage.error('XLSX未加载'); return; }
      const tab = activeTab.value, p = period.value;
      let wb = XLSX.utils.book_new();
      if (tab === 'profit' && profitData.value) {
        const d = profitData.value;
        const rows = [
          ['利润表', p + ' 期'],
          ['项目', '金额（元）'],
          ['一、营业收入', Number(d.total_revenue)],
          ...d.revenue_details.map(x => ['  ' + x.account_name, Number(x.amount)]),
          ['减：主营业务成本', -Number(d.total_cogs)],
          ...(d.expense_details||[]).filter(x=>x.category==='COGS').map(x=>['  ' + x.account_name, -Number(x.amount)]),
          ['二、毛利润', Number(d.gross_profit)],
          ['减：期间费用', ''],
          ...(d.expense_details||[]).filter(x=>x.category!=='COGS').map(x=>['  ' + x.account_name, -Number(x.amount)]),
          ['三、营业利润', Number(d.operating_profit)],
          ['四、净利润', Number(d.net_profit)],
        ];
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), '利润表');
      } else if (tab === 'trial' && trialData.value) {
        const d = trialData.value;
        const rows = [['科目编码','科目名称','类型','期初借','期初贷','本期借','本期贷','期末借','期末贷']];
        d.balances.forEach(r => rows.push([r.account_code, r.account_name, r.account_type, Number(r.opening_debit), Number(r.opening_credit), Number(r.debit_amount), Number(r.credit_amount), Number(r.closing_debit), Number(r.closing_credit)]));
        rows.push(['','','合计', Number(d.totals.opening_debit), Number(d.totals.opening_credit), Number(d.totals.debit_amount), Number(d.totals.credit_amount), Number(d.totals.closing_debit), Number(d.totals.closing_credit)]);
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), '试算平衡表');
      } else if (tab === 'balance' && balanceData.value) {
        const d = balanceData.value;
        const rows = [['资产负债表', p + ' 期'],['资产','金额（元）']];
        d.asset_details.forEach(x => rows.push(['  ' + x.account_name, Number(x.amount)]));
        rows.push(['资产合计', Number(d.total_assets)]);
        rows.push(['',''],['负债','金额（元）']);
        d.liability_details.forEach(x => rows.push(['  ' + x.account_name, Number(x.amount)]));
        rows.push(['负债合计', Number(d.total_liabilities)]);
        rows.push(['',''],['所有者权益','金额（元）']);
        d.equity_details.forEach(x => rows.push(['  ' + x.account_name, Number(x.amount)]));
        rows.push(['  未分配利润', Number(d.net_profit)]);
        rows.push(['所有者权益合计', Number(d.total_equity)]);
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), '资产负债表');
      } else { ElMessage.warning('无数据可导出'); return; }
      XLSX.writeFile(wb, `财务报表_${tab}_${p}.xlsx`);
    }

    async function toggleRow(key, accountCode) {
      if (expandedRows[key]) {
        expandedRows[key] = false;
        return;
      }
      expandedRows[key] = true;
      if (detailCache[key]) return;
      detailLoading.value[key] = true;
      try {
        const r = await api.get(`/api/vouchers/reports/account-detail?period=${period.value}&account_code=${accountCode}`);
        detailCache[key] = r.data;
      } catch(e) { ElMessage.error(e.message); }
      detailLoading.value[key] = false;
    }
    
    async function loadPeriods() {
      const now = new Date();
      const result = [];
      for (let i = 0; i < 12; i++) {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        result.push(`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`);
      }
      periods.value = result;
      if (!period.value) period.value = result[0];
    }
    
    async function loadReport() {
      if (!period.value) return;
      loading.value = true;
      try {
        const [rp, rt, rb] = await Promise.all([
          api.get(`/api/vouchers/reports/profit?period=${period.value}`),
          api.get(`/api/vouchers/reports/trial-balance?period=${period.value}`),
          api.get(`/api/vouchers/reports/balance-sheet?period=${period.value}`),
        ]);
        profitData.value = rp.data;
        trialData.value = rt.data;
        balanceData.value = rb.data;
        Object.keys(expandedRows).forEach(k => { expandedRows[k] = false; });
        Object.keys(detailCache).forEach(k => { delete detailCache[k]; });
      } catch (e) { ElMessage.error(e.message); }
      loading.value = false;
    }
    
    async function loadAll() {
      await loadReport();
    }
    
    onMounted(async () => {
      await loadPeriods();
      loadReport();
    });
    
    return { activeTab, loading, period, periods, profitData, trialData, balanceData, expandedRows, detailCache, detailLoading, fmt, switchTab, toggleRow, loadAll, exportExcel, bossView, glossary, Icon };
  }
};

// ============ 科目管理 ============
const ACCOUNT_TYPES = { ASSET: '资产', LIABILITY: '负债', REVENUE: '收入', EXPENSE: '费用', EQUITY: '权益' };
const ACCOUNT_DIRECTIONS = { DEBIT: '借方', CREDIT: '贷方' };

const AccountsPage = {
  template: `<div class="page"><div class="page-head"><div class="ph-left"><div class="ph-icon" v-html="Icon.icon('book-open',22)"></div><div><div class="ph-title">会计科目</div><div class="ph-sub">科目体系 · 类型分组</div></div></div><div class="ph-actions"><el-button @click="openOb">期初建账</el-button><el-button type="success" @click="openCreate">新增科目</el-button></div></div><div class="filter-bar"><el-select v-model="filterType" placeholder="全部类型" style="width:140px" clearable><el-option v-for="(l,v) in ACCOUNT_TYPES" :key="v" :label="l" :value="v"/></el-select><el-input v-model="filterKeyword" placeholder="编码/名称" style="width:200px" clearable/><el-button @click="applyFilter">查询</el-button><div class="grow"></div></div><div v-loading="loading"><div v-for="group in groupedAccounts" :key="group.type" class="ag-section"><div class="ag-header" :style="{borderLeftColor:typeColors[group.type]}"><span class="ag-title">{{ACCOUNT_TYPES[group.type]}}</span><span class="ag-count">{{group.items.length}} 个科目</span></div><el-table :data="group.items" border size="small" stripe><el-table-column prop="code" label="编码" width="120"/><el-table-column prop="name" label="名称" width="180"/><el-table-column prop="type" label="类型" width="100"><template #default="{row}"><span :class="['type-tag',row.type]">{{ACCOUNT_TYPES[row.type]}}</span></template></el-table-column><el-table-column prop="direction" label="方向" width="80"><template #default="{row}">{{row.direction==='DEBIT'?'借方':'贷方'}}</template></el-table-column><el-table-column prop="level" label="级别" width="60"/><el-table-column prop="status" label="状态" width="80"><template #default="{row}"><el-tag v-if="row.status==='ACTIVE'" type="success" size="small">启用</el-tag><el-tag v-else type="info" size="small">停用</el-tag></template></el-table-column><el-table-column label="操作" width="140"><template #default="{row}"><el-button size="small" @click="openEdit(row)">编辑</el-button><el-button size="small" type="danger" @click="doDelete(row)">删除</el-button></template></el-table-column></el-table></div><div v-if="!loading && !allAccounts.length" class="doc-empty"><div v-html="Icon.icon('inbox',56)"></div><div class="de-title">暂无科目</div><div class="de-desc">点击「新增科目」创建第一个会计科目</div></div></div><el-dialog v-model="editDlg.visible" :title="editDlg.isEdit?'编辑科目':'新增科目'" width="500px"><el-form :model="editDlg.form" label-width="90px"><el-form-item label="科目编码"><el-input v-model="editDlg.form.code" :disabled="editDlg.isEdit"/></el-form-item><el-form-item label="科目名称"><el-input v-model="editDlg.form.name"/></el-form-item><el-form-item label="科目类型"><el-select v-model="editDlg.form.type" style="width:100%"><el-option v-for="(l,v) in ACCOUNT_TYPES" :key="v" :label="l" :value="v"/></el-select></el-form-item><el-form-item label="借贷方向"><el-select v-model="editDlg.form.direction" style="width:100%"><el-option label="借方" value="DEBIT"/><el-option label="贷方" value="CREDIT"/></el-select></el-form-item><el-form-item label="级别"><el-input-number v-model="editDlg.form.level" :min="1" :max="5"/></el-form-item></el-form><template #footer><el-button @click="editDlg.visible=false">取消</el-button><el-button type="primary" @click="submitForm">{{editDlg.isEdit?'保存':'创建'}}</el-button></template></el-dialog><el-dialog v-model="ob.visible" title="期初建账" width="740px"><div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap"><el-date-picker v-model="ob.period" type="month" value-format="YYYY-MM" placeholder="建账期间" style="width:150px" @change="loadOb"/><el-tag v-if="ob.hasVouchers" type="danger" size="small">该期已有过账凭证, 期初不可修改</el-tag><div class="grow"></div><span style="font-size:13px;color:var(--text2)">借方合计 <b style="color:var(--text)">{{fmt(obTotals.d)}}</b> · 贷方合计 <b style="color:var(--text)">{{fmt(obTotals.c)}}</b></span><el-tag :type="obBalanced?'success':'danger'" size="small">{{obBalanced?'试算平衡':'差额 '+fmt(Math.abs(obTotals.diff))}}</el-tag></div><el-table :data="ob.items" border size="small" height="380" v-loading="ob.loading"><el-table-column prop="code" label="编码" width="100"/><el-table-column prop="name" label="科目" min-width="150"/><el-table-column label="方向" width="70" align="center"><template #default="{row}">{{row.direction==='DEBIT'?'借':'贷'}}</template></el-table-column><el-table-column label="期初余额" width="170"><template #default="{row}"><el-input-number v-model="row.opening" :min="0" :precision="2" :controls="false" size="small" style="width:100%" :disabled="ob.hasVouchers"/></template></el-table-column></el-table><template #footer><el-button @click="ob.visible=false">取消</el-button><el-button type="primary" :disabled="!obBalanced||ob.hasVouchers||!ob.period" :loading="ob.saving" @click="saveOb">保存期初</el-button></template></el-dialog></div>`,
  setup() {
    const loading = ref(false);
    const allAccounts = ref([]);
    const filterType = ref('');
    const filterKeyword = ref('');
    const editDlg = reactive({ visible: false, isEdit: false, form: {} });
    // 期初建账(一次性操作 → 藏在科目页, 不配菜单)
    const ob = reactive({ visible: false, period: '', items: [], hasVouchers: false, loading: false, saving: false });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const obTotals = computed(() => {
      let d = 0, c = 0;
      for (const it of ob.items) {
        const amt = Number(it.opening) || 0;
        if (it.direction === 'DEBIT') d += amt; else c += amt;
      }
      return { d: Math.round(d * 100) / 100, c: Math.round(c * 100) / 100, diff: Math.round((d - c) * 100) / 100 };
    });
    const obBalanced = computed(() => ob.items.length > 0 && Math.abs(obTotals.value.diff) <= 0.01);
    const typeColors = { ASSET:'#67c23a', LIABILITY:'#e6a23c', REVENUE:'#409eff', EXPENSE:'#f56c6c', EQUITY:'#9b59b6' };
    
    const groupedAccounts = computed(() => {
      const map = {};
      for (const a of allAccounts.value) {
        const t = a.type || 'OTHER';
        if (!map[t]) map[t] = { type: t, items: [] };
        if (!filterType.value || filterType.value === t) {
          if (!filterKeyword.value || a.code.includes(filterKeyword.value) || a.name.includes(filterKeyword.value)) {
            map[t].items.push(a);
          }
        }
      }
      return Object.values(map);
    });

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/finance/accounts');
        allAccounts.value = r.data || [];
      } catch (e) { console.error(e); ElMessage.error(e.message); }
      loading.value = false;
    }
    function applyFilter() { load(); }
    
    function openCreate() {
      editDlg.isEdit = false;
      editDlg.form = { code:'', name:'', type:'ASSET', direction:'DEBIT', level:1 };
      editDlg.visible = true;
    }
    function openEdit(row) {
      editDlg.isEdit = true;
      editDlg.form = { ...row };
      editDlg.visible = true;
    }
    async function submitForm() {
      const f = editDlg.form;
      if (!f.code || !f.name) { ElMessage.warning('请填写完整信息'); return; }
      try {
        if (editDlg.isEdit) {
          await api.put('/api/finance/accounts/' + f.id, f);
          ElMessage.success('更新成功');
        } else {
          await api.post('/api/finance/accounts', f);
          ElMessage.success('创建成功');
        }
        editDlg.visible = false;
        load();
      } catch (e) { ElMessage.error(e.message); }
    }
    async function doDelete(row) {
      try {
        await ElMessageBox.confirm('确定要删除科目 "' + row.name + '" 吗？', '删除确认', { type: 'warning' });
        await api.del('/api/finance/accounts/' + row.id);
        ElMessage.success('删除成功');
        load();
      } catch (e) { /* cancelled */ }
    }

    function openOb() {
      ob.visible = true;
      if (!ob.period) { const n = new Date(); ob.period = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0'); }
      loadOb();
    }
    async function loadOb() {
      if (!ob.period) return;
      ob.loading = true;
      try {
        const r = await api.get('/api/vouchers/opening-balance?period=' + ob.period);
        ob.items = r.items || []; ob.hasVouchers = !!r.has_vouchers;
      } catch (e) { /* 已提示 */ }
      ob.loading = false;
    }
    async function saveOb() {
      ob.saving = true;
      try {
        const items = ob.items.filter(it => (Number(it.opening) || 0) >= 0.005).map(it => ({ account_id: it.account_id, opening: Number(it.opening) || 0 }));
        const r = await api.post('/api/vouchers/opening-balance', { period: ob.period, items });
        ElMessage.success(r.message || '期初建账完成');
        ob.visible = false;
      } catch (e) { /* 已提示 */ }
      ob.saving = false;
    }

    onMounted(load);

    return { loading, allAccounts, filterType, filterKeyword, groupedAccounts, editDlg, typeColors, ACCOUNT_TYPES, openCreate, openEdit, submitForm, doDelete, applyFilter, Icon, ob, obTotals, obBalanced, fmt, openOb, loadOb, saveOb };
  }
};

// ============ 承兑汇票台账(高频日常业务 → 独立入口; 收票/背书/贴现/托收全自动凭证) ============
const BILL_STATUS = { HOLDING: '持有中', ENDORSED: '已背书', DISCOUNTED: '已贴现', SETTLED: '已托收' };
const AcceptancesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('ticket',22)"></div>
        <div>
          <div class="ph-title">承兑汇票</div>
          <div class="ph-sub">收票 → 背书 / 贴现 / 到期托收 · 每步自动生成资金流水+凭证</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>收票登记</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:130px" clearable @change="load">
        <el-option v-for="(l,v) in BILL_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <div class="grow"></div>
      <div v-if="alert.holding_count" style="display:flex;gap:16px;align-items:center;font-size:13px;color:var(--text2)">
        <span>持有中 <b style="color:var(--text)">{{alert.holding_count}}</b> 张 · <b style="color:var(--text)">¥{{fmt(alert.holding_amt)}}</b></span>
        <span v-if="alert.due_soon" style="color:#e6a23c;font-weight:600">30天内到期 {{alert.due_soon}} 张</span>
        <span v-if="alert.overdue" style="color:#f56c6c;font-weight:600">已逾期 {{alert.overdue}} 张</span>
      </div>
    </div>

    <el-table :data="rows" border size="small" stripe v-loading="loading">
      <el-table-column prop="bill_no" label="票号" width="180"/>
      <el-table-column label="票面金额" width="120" align="right"><template #default="{row}">¥{{fmt(row.amount)}}</template></el-table-column>
      <el-table-column label="出票人/前手" min-width="130"><template #default="{row}">{{row.drawer||'-'}}</template></el-table-column>
      <el-table-column prop="receive_date" label="收票日" width="100"/>
      <el-table-column prop="due_date" label="到期日" width="100"/>
      <el-table-column label="剩余" width="90" align="center">
        <template #default="{row}">
          <span v-if="row.status!=='HOLDING'">-</span>
          <span v-else-if="row.days_to_due<0" style="color:#f56c6c;font-weight:600">逾期{{-row.days_to_due}}天</span>
          <span v-else-if="row.days_to_due<=30" style="color:#e6a23c;font-weight:600">{{row.days_to_due}}天</span>
          <span v-else>{{row.days_to_due}}天</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{row}"><el-tag :type="{HOLDING:'primary',ENDORSED:'warning',DISCOUNTED:'success',SETTLED:'info'}[row.status]" size="small">{{row.status_label}}</el-tag></template>
      </el-table-column>
      <el-table-column label="去向/贴息" min-width="130">
        <template #default="{row}">
          <span v-if="row.status==='ENDORSED'">→ {{row.endorse_to}}</span>
          <span v-else-if="row.status==='DISCOUNTED'">贴息 ¥{{fmt(row.discount_fee)}}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{row}">
          <template v-if="row.status==='HOLDING'">
            <el-button size="small" @click="openEndorse(row)">背书</el-button>
            <el-button size="small" type="warning" @click="openDiscount(row)">贴现</el-button>
            <el-button size="small" type="success" @click="doSettle(row)">托收</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <div style="display:flex;justify-content:flex-end;margin-top:10px" v-if="total>query.size">
      <el-pagination layout="prev, pager, next, total" :total="total" :page-size="query.size" :current-page="query.page" @current-change="p=>{query.page=p;load()}"/>
    </div>
    <div v-if="!loading && !rows.length" class="doc-empty">
      <div v-html="Icon.icon('inbox',56)"></div>
      <div class="de-title">暂无承兑汇票</div>
      <div class="de-desc">点击「收票登记」录入第一张票</div>
    </div>

    <el-dialog v-model="createDlg.visible" title="收票登记" width="520px">
      <el-form :model="createDlg.form" label-width="90px">
        <el-form-item label="票号" required><el-input v-model="createDlg.form.bill_no" placeholder="电子承兑票号"/></el-form-item>
        <el-form-item label="票面金额" required><el-input-number v-model="createDlg.form.amount" :min="0.01" :precision="2" style="width:100%"/></el-form-item>
        <el-form-item label="出票人"><el-input v-model="createDlg.form.drawer" placeholder="从谁家收的(客户)"/></el-form-item>
        <el-form-item label="收票日期"><el-date-picker v-model="createDlg.form.receive_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="出票日期"><el-date-picker v-model="createDlg.form.issue_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="到期日" required><el-date-picker v-model="createDlg.form.due_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="备注"><el-input v-model="createDlg.form.remark" type="textarea" :rows="2"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDlg.visible=false">取消</el-button><el-button type="primary" :loading="createDlg.saving" @click="submitCreate">登记</el-button></template>
    </el-dialog>

    <el-dialog v-model="endorseDlg.visible" title="背书转让" width="440px">
      <div style="margin-bottom:12px;font-size:13px;color:var(--text2)">票号 {{endorseDlg.row.bill_no}} · 票面 ¥{{fmt(endorseDlg.row.amount)}}</div>
      <el-form label-width="90px">
        <el-form-item label="背书给" required><el-input v-model="endorseDlg.form.endorse_to" placeholder="供应商名称"/></el-form-item>
        <el-form-item label="背书日期"><el-date-picker v-model="endorseDlg.form.endorse_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="endorseDlg.visible=false">取消</el-button><el-button type="primary" :loading="endorseDlg.saving" @click="submitEndorse">确认背书</el-button></template>
    </el-dialog>

    <el-dialog v-model="discountDlg.visible" title="贴现" width="440px">
      <div style="margin-bottom:12px;font-size:13px;color:var(--text2)">票号 {{discountDlg.row.bill_no}} · 票面 ¥{{fmt(discountDlg.row.amount)}}</div>
      <el-form label-width="90px">
        <el-form-item label="实收金额" required><el-input-number v-model="discountDlg.form.received_amount" :min="0.01" :max="discountDlg.row.amount" :precision="2" style="width:100%"/></el-form-item>
        <el-form-item label="贴息"><span style="color:#f56c6c;font-weight:600">¥{{fmt(discountFee)}}</span><span style="margin-left:8px;font-size:12px;color:var(--text2)">自动计入融资成本</span></el-form-item>
        <el-form-item label="贴现日期"><el-date-picker v-model="discountDlg.form.discount_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="discountDlg.visible=false">取消</el-button><el-button type="warning" :loading="discountDlg.saving" @click="submitDiscount">确认贴现</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const alert = ref({});
    const query = reactive({ status: '', page: 1, size: 20 });
    const createDlg = reactive({ visible: false, saving: false, form: {} });
    const endorseDlg = reactive({ visible: false, saving: false, row: {}, form: {} });
    const discountDlg = reactive({ visible: false, saving: false, row: {}, form: {} });
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const discountFee = computed(() => Math.max(0, Math.round(((discountDlg.row.amount || 0) - (discountDlg.form.received_amount || 0)) * 100) / 100));

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/acceptances?' + new URLSearchParams({ status: query.status || '', page: query.page, size: query.size }));
        rows.value = r.data.items || []; total.value = r.data.total || 0; alert.value = r.data.alert || {};
      } catch (e) { /* 已提示 */ }
      loading.value = false;
    }
    function openCreate() {
      createDlg.form = { bill_no: '', amount: null, drawer: '', receive_date: '', issue_date: '', due_date: '', remark: '' };
      createDlg.visible = true;
    }
    async function submitCreate() {
      const f = createDlg.form;
      if (!f.bill_no || !f.amount || !f.due_date) { ElMessage.warning('票号/金额/到期日必填'); return; }
      createDlg.saving = true;
      try {
        const r = await api.post('/api/acceptances', f);
        ElMessage.success('已登记, 凭证号 ' + r.data.voucher_no);
        createDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      createDlg.saving = false;
    }
    function openEndorse(row) { endorseDlg.row = row; endorseDlg.form = { endorse_to: '', endorse_date: '' }; endorseDlg.visible = true; }
    async function submitEndorse() {
      if (!endorseDlg.form.endorse_to) { ElMessage.warning('请填写背书去向'); return; }
      endorseDlg.saving = true;
      try {
        const r = await api.post(`/api/acceptances/${endorseDlg.row.id}/endorse`, endorseDlg.form);
        ElMessage.success('已背书, 凭证号 ' + r.data.voucher_no + (r.data.settled_doc ? ', 已核减应付单 ' + r.data.settled_doc : ''));
        endorseDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      endorseDlg.saving = false;
    }
    function openDiscount(row) { discountDlg.row = row; discountDlg.form = { received_amount: row.amount, discount_date: '' }; discountDlg.visible = true; }
    async function submitDiscount() {
      discountDlg.saving = true;
      try {
        const r = await api.post(`/api/acceptances/${discountDlg.row.id}/discount`, discountDlg.form);
        ElMessage.success('已贴现, 贴息 ¥' + fmt(r.data.fee) + ', 凭证号 ' + r.data.voucher_no);
        discountDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      discountDlg.saving = false;
    }
    async function doSettle(row) {
      try {
        await ElMessageBox.confirm(`确认到期托收? 票面 ¥${fmt(row.amount)} 将全额入账银行公账`, '托收确认', { type: 'warning' });
        const r = await api.post(`/api/acceptances/${row.id}/settle`);
        ElMessage.success('托收完成, 凭证号 ' + r.data.voucher_no);
        load();
      } catch (e) { /* 取消或已提示 */ }
    }

    onMounted(load);
    return { rows, total, loading, alert, query, BILL_STATUS, createDlg, endorseDlg, discountDlg, discountFee, fmt, Icon, load, openCreate, submitCreate, openEndorse, submitEndorse, openDiscount, submitDiscount, doSettle };
  }
};

// ============ 采购预付 ============
const PREPAY_STATUS = { PAID: '已预付', APPLIED: '已冲抵', CANCELLED: '已作废' };
const PrepaymentPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('arrow-up-circle',22)"></div>
        <div>
          <div class="ph-title">采购预付</div>
          <div class="ph-sub">关联采购单 + 选定出账账户 → 自动生成资金流水 / 应付单(预付冲抵) / 凭证(借预付账款 贷银行存款)</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建预付</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-input v-model="query.supplier_name" placeholder="供应商名称" style="width:180px" clearable @change="load"/>
      <el-input v-model="query.purchase_no" placeholder="采购单号" style="width:160px" clearable @change="load"/>
      <el-select v-model="query.status" placeholder="全部状态" style="width:130px" clearable @change="load">
        <el-option v-for="(l,v) in PREPAY_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="load">查询</el-button>
    </div>

    <el-table :data="rows" border size="small" stripe v-loading="loading">
      <el-table-column prop="prepay_no" label="预付单号" width="170"/>
      <el-table-column prop="purchase_no" label="采购单号" width="170"/>
      <el-table-column label="供应商" min-width="140"><template #default="{row}">{{row.supplier_name||'-'}}</template></el-table-column>
      <el-table-column label="金额" width="120" align="right"><template #default="{row}">¥{{fmt(row.amount)}}</template></el-table-column>
      <el-table-column label="出账账户" width="140"><template #default="{row}">{{row.fund_account_name||'-'}}</template></el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{row}"><el-tag :type="{PAID:'primary',APPLIED:'success',CANCELLED:'info'}[row.status]" size="small">{{row.status_label}}</el-tag></template>
      </el-table-column>
      <el-table-column prop="pay_date" label="支付日" width="110"/>
      <el-table-column label="已冲抵" width="110" align="right"><template #default="{row}">¥{{fmt(row.applied_amount)}}</template></el-table-column>
      <el-table-column label="凭证号" width="110"><template #default="{row}">{{row.voucher_no||'-'}}</template></el-table-column>
    </el-table>
    <div style="display:flex;justify-content:flex-end;margin-top:10px" v-if="total>query.size">
      <el-pagination layout="prev, pager, next, total" :total="total" :page-size="query.size" :current-page="query.page" @current-change="p=>{query.page=p;load()}"/>
    </div>
    <div v-if="!loading && !rows.length" class="doc-empty">
      <div v-html="Icon.icon('inbox',56)"></div>
      <div class="de-title">暂无采购预付</div>
      <div class="de-desc">点击「新建预付」对采购单发起预付</div>
    </div>

    <el-dialog v-model="createDlg.visible" title="新建采购预付" width="540px">
      <el-form :model="createDlg.form" label-width="90px">
        <el-form-item label="采购单" required>
          <el-select v-model="createDlg.form.purchase_id" filterable placeholder="选择采购单" style="width:100%" :loading="poLoading" @visible-change="v=>v&&loadPurchases()">
            <el-option v-for="p in poOptions" :key="p.id" :label="p.po_no + ' · ' + (p.supplier_name||'') + ' · ¥' + fmt(p.total_amount)" :value="p.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="预付金额" required><el-input-number v-model="createDlg.form.amount" :min="0.01" :precision="2" style="width:100%"/></el-form-item>
        <el-form-item label="出账账户" required>
          <el-select v-model="createDlg.form.fund_account_id" filterable placeholder="选择出账账户" style="width:100%" :loading="faLoading" @visible-change="v=>v&&loadFundAccounts()">
            <el-option v-for="a in faOptions" :key="a.id" :label="a.name + ' (余额 ¥' + fmt(a.balance) + ')'" :value="a.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="支付日"><el-date-picker v-model="createDlg.form.pay_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="备注"><el-input v-model="createDlg.form.remark" type="textarea" :rows="2"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDlg.visible=false">取消</el-button><el-button type="primary" :loading="createDlg.saving" @click="submitCreate">确认预付</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const query = reactive({ supplier_name: '', purchase_no: '', status: '', page: 1, size: 20 });
    const createDlg = reactive({ visible: false, saving: false, form: {} });
    const poOptions = ref([]); const poLoading = ref(false);
    const faOptions = ref([]); const faLoading = ref(false);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/prepayments?' + new URLSearchParams({
          page: query.page, size: query.size, status: query.status || '',
        }));
        let items = r.data.items || [];
        if (query.supplier_name) {
          const kw = query.supplier_name.toLowerCase();
          items = items.filter(x => (x.supplier_name || '').toLowerCase().includes(kw));
        }
        if (query.purchase_no) {
          const kw = query.purchase_no.toLowerCase();
          items = items.filter(x => (x.purchase_no || '').toLowerCase().includes(kw));
        }
        rows.value = items; total.value = r.data.total || 0;
      } catch (e) { /* 已提示 */ }
      loading.value = false;
    }
    async function loadPurchases() {
      if (poOptions.value.length) return;
      poLoading.value = true;
      try {
        const r = await api.get('/api/purchases');
        const data = r.data || r.items || [];
        poOptions.value = (Array.isArray(data) ? data : []).filter(p => p.status !== 'CLOSED').slice(0, 80);
      } catch (e) { /* 已提示 */ }
      poLoading.value = false;
    }
    async function loadFundAccounts() {
      if (faOptions.value.length) return;
      faLoading.value = true;
      try {
        const r = await api.get('/api/finance/fund-accounts');
        faOptions.value = r.data || [];
      } catch (e) { /* 已提示 */ }
      faLoading.value = false;
    }
    function openCreate() {
      createDlg.form = { purchase_id: null, amount: null, fund_account_id: null, pay_date: '', remark: '' };
      createDlg.visible = true;
      loadPurchases(); loadFundAccounts();
    }
    async function submitCreate() {
      const f = createDlg.form;
      if (!f.purchase_id || !f.amount || !f.fund_account_id) { ElMessage.warning('采购单/金额/出账账户必填'); return; }
      createDlg.saving = true;
      try {
        const r = await api.post('/api/prepayments', f);
        ElMessage.success('已预付, 应付单 ' + r.data.doc_no + ', 凭证号 ' + r.data.voucher_no);
        createDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      createDlg.saving = false;
    }

    onMounted(load);
    return { rows, total, loading, query, PREPAY_STATUS, createDlg, poOptions, poLoading, faOptions, faLoading, fmt, Icon, load, loadPurchases, loadFundAccounts, openCreate, submitCreate };
  }
};

// ============ 外协单 ============
const OS_STATUS = { SUBMITTED: '待审批', APPROVED: '已通过', REJECTED: '已驳回', PAID: '已付款' };
const PAY_METHODS = [
  { v: 'CASH', l: '现金' }, { v: 'TELEGRAPHIC', l: '电汇' }, { v: 'ACCEPTANCE', l: '承兑' },
];

// ============ 出货单列表(从完工确认出货,已自动产生应收) ============
const ShipmentsPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('truck',22)"></div>
        <div>
          <div class="ph-title">出货单</div>
          <div class="ph-sub">完工确认→出货生效自动产生应收 · 4联单打印</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button @click="load"><span v-html="Icon.icon('arrow-path',14)" style="vertical-align:middle;margin-right:4px"></span>刷新</el-button>
      </div>
    </div>
    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div class="doc-bar CONFIRMED"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.ship_no}}</span>
            <span class="pill CONFIRMED">已出货</span>
            <span class="doc-cust">{{row.customer_name}}</span>
            <span class="doc-amount">¥{{fmt(row.total_amount||0)}} / {{fmt(row.total_qty)}}件</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">出货日期</span><span class="df-value">{{fmtDate(row.ship_date)}}</span></div>
            <div class="doc-field"><span class="df-label">明细数</span><span class="df-value">{{(row.items||[]).length}}项</span></div>
            <div class="doc-field"><span class="df-label">关联订单</span><span class="df-value">#{{row.order_id}}</span></div>
            <div class="doc-field"><span class="df-label">应收单</span><span class="df-value" v-if="row.finance_doc_id">#{{row.finance_doc_id}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button size="small" @click="print(row)"><span v-html="Icon.icon('printer',13)" style="vertical-align:-2px;margin-right:2px"></span>打印4联</el-button>
          <el-button size="small" type="primary" @click="openDetail(row)">详情</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无出货单</div>
        <div class="de-desc">在完工单确认后即可生成出货单</div>
      </div>
    </div>
    <el-drawer v-model="detail.visible" title="出货单详情" size="600px">
      <template v-if="detail.data.id">
        <div class="ig-items" style="margin-bottom:12px">
          <div class="ig-item"><div class="ig-label">单号</div><div class="ig-value">{{detail.data.ship_no}}</div></div>
          <div class="ig-item"><div class="ig-label">客户</div><div class="ig-value">{{detail.data.customer_name}}</div></div>
          <div class="ig-item"><div class="ig-label">出货日期</div><div class="ig-value">{{fmtDate(detail.data.ship_date)}}</div></div>
          <div class="ig-item"><div class="ig-label">总数</div><div class="ig-value big">{{fmt(detail.data.total_qty)}}</div></div>
        </div>
        <el-table :data="detail.data.items||[]" size="small" border>
          <el-table-column label="序号" type="index" width="50"/>
          <el-table-column label="工件名" prop="part_name" min-width="140"/>
          <el-table-column label="规格" prop="part_spec" min-width="120"/>
          <el-table-column label="数量" prop="qty" width="80" align="right"/>
          <el-table-column label="工艺" prop="craft_type" width="100"/>
          <el-table-column label="厚度" prop="material_thickness" width="80"/>
        </el-table>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]);
    const loading = ref(false);
    const detail = reactive({visible:false, data:{}});
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/shipments?page=1&size=100');
        rows.value = r.data || [];
      } catch(e) { if (e.message) ElMessage.error(e.message); }
      finally { loading.value = false; }
    }
    function openDetail(r) { detail.data = r; detail.visible = true; }
    function print(s) {
      const d2 = d => d < 10 ? '0' + d : '' + d;
      const now = s.ship_date ? new Date(s.ship_date) : new Date();
      const dateStr = now.getFullYear() + '-' + d2(now.getMonth() + 1) + '-' + d2(now.getDate());
      const copies = ['存根联', '客户联', '财务联', '仓库联'];
      const itemsHtml = (s.items || []).map((it, i) =>
        `<tr><td style="text-align:center">${i + 1}</td><td>${it.part_name || ''}</td><td>${it.part_spec || ''}</td><td style="text-align:right">${fmt(it.qty)}</td><td>${it.unit || ''}</td><td>${it.craft_type || ''}</td><td>${it.material_thickness || ''}</td></tr>`
      ).join('');
      const copyBlock = label => `
        <div class="copy">
          <h1>东莞市峰业精密机械有限公司</h1>
          <div class="sub">${label} — 出货单</div>
          <div class="info"><span>出货单号：${s.ship_no || ''}</span><span>日期：${dateStr}</span><span>客户：${s.customer_name || ''}</span></div>
          <table><thead><tr><th style="width:40px">序号</th><th>工件名</th><th>规格</th><th style="width:70px">数量</th><th style="width:50px">单位</th><th>工艺类型</th><th style="width:70px">厚度</th></tr></thead>
          <tbody>${itemsHtml || '<tr><td colspan="7" style="text-align:center;color:#999">无明细</td></tr>'}</tbody></table>
          <div class="sign"><div>客户签字：<span class="line"></span></div><div>经手人：<span class="line"></span></div><div>日期：<span class="line"></span></div></div>
        </div>`;
      const w = window.open('', '_blank', 'width=800,height=600');
      w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>出货四联单</title><style>
        body{font-family:"Microsoft YaHei",sans-serif;width:210mm;padding:10mm 15mm;margin:auto;color:#222}
        h1{text-align:center;font-size:18px;margin-bottom:2px;letter-spacing:2px}
        .sub{text-align:center;font-size:13px;color:#666;margin-bottom:8px;font-weight:600}
        .info{display:flex;justify-content:space-between;font-size:12px;margin-bottom:8px;border:1px solid #ccc;padding:6px 10px;background:#fafafa}
        table{width:100%;border-collapse:collapse;font-size:11px}
        th,td{border:1px solid #333;padding:4px 6px;text-align:left}
        th{background:#f0f0f0;font-weight:600}
        .sign{margin-top:24px;display:flex;justify-content:space-between;font-size:12px}
        .sign div{text-align:center}
        .sign .line{display:inline-block;width:100px;border-bottom:1px solid #333;margin-top:20px}
        .cut{border-top:2px dashed #999;margin:12px 0}
        @media print{body{margin:0;padding:8mm 12mm}@page{margin:6mm}}
      </style></head><body>
        ${copies.map((c, i) => copyBlock(c) + (i < copies.length - 1 ? '<div class="cut"></div>' : '')).join('')}
        <script>window.onload=function(){setTimeout(function(){window.print();window.close()},500)}<\/script>
      </body></html>`);
      w.document.close();
    }
    onMounted(load);
    return { rows, loading, detail, fmt, fmtDate, load, openDetail, print, Icon };
  }
};

// ============ 月度盘点 ============
const StockCheckPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('clipboard-document-check',22)"></div>
        <div>
          <div class="ph-title">月度盘点</div>
          <div class="ph-sub">账实差异自动调账 + 凭证生成</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建盘点单</el-button>
      </div>
    </div>
    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.check_no}}</span>
            <span class="pill" :class="row.status">{{SC_STATUS[row.status]||row.status}}</span>
            <span class="doc-cust">{{row.period}}</span>
            <span class="doc-amount" v-if="row.status==='CLOSED'">差异 ¥{{fmt(row.total_diff_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">盘点日</span><span class="df-value">{{fmtDate(row.check_date)}}</span></div>
            <div class="doc-field"><span class="df-label">明细</span><span class="df-value">{{(row.items||[]).length}}项</span></div>
            <div class="doc-field"><span class="df-label">操作员</span><span class="df-value">{{row.operator_name||'-'}}</span></div>
            <div class="doc-field" v-if="row.voucher_no"><span class="df-label">凭证</span><span class="df-value">{{row.voucher_no}}</span></div>
          </div>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无盘点单</div>
        <div class="de-desc">每月一次,封账后自动生成调账凭证</div>
      </div>
    </div>
    <el-drawer v-model="detail.visible" title="盘点单详情" size="720px">
      <template v-if="detail.data.id">
        <div class="ig-items" style="margin-bottom:12px">
          <div class="ig-item"><div class="ig-label">单号</div><div class="ig-value">{{detail.data.check_no}}</div></div>
          <div class="ig-item"><div class="ig-label">期间</div><div class="ig-value">{{detail.data.period}}</div></div>
          <div class="ig-item"><div class="ig-label">状态</div><div class="ig-value big">{{SC_STATUS[detail.data.status]}}</div></div>
          <div class="ig-item" v-if="detail.data.voucher_no"><div class="ig-label">凭证号</div><div class="ig-value">{{detail.data.voucher_no}}</div></div>
        </div>
        <el-table :data="detail.data.items||[]" size="small" border>
          <el-table-column label="物料" prop="item_name" min-width="180"/>
          <el-table-column label="账面数" prop="book_qty" width="100" align="right"/>
          <el-table-column label="实盘数" width="120" align="right">
            <template #default="{row}">
              <el-input-number v-if="detail.data.status==='DRAFT'" v-model="row.actual_qty" :precision="3" :controls="false" size="small" style="width:100%" @change="calcDiff(row)"/>
              <span v-else>{{fmt(row.actual_qty)}}</span>
            </template>
          </el-table-column>
          <el-table-column label="差异" width="100" align="right">
            <template #default="{row}">
              <span :class="row.diff_qty>0?'pos':row.diff_qty<0?'neg':''">{{fmt(row.diff_qty)}}</span>
            </template>
          </el-table-column>
          <el-table-column label="备注" prop="remark" min-width="120"/>
        </el-table>
        <div v-if="detail.data.status==='DRAFT'" style="margin-top:16px;text-align:right">
          <el-button type="primary" @click="closeCheck(detail.data)">封账并生成调账凭证</el-button>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]);
    const loading = ref(false);
    const detail = reactive({visible:false, data:{}});
    const SC_STATUS = {DRAFT:'盘点中', CHECKED:'已盘', CLOSED:'已封账'};
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/stock-check?page=1&size=50');
        rows.value = r.data?.items || r.data || [];
      } catch(e) { if (e.message) ElMessage.error(e.message); }
      finally { loading.value = false; }
    }
    async function openCreate() {
      const period = new Date().toISOString().slice(0,7);
      try {
        const r = await api.post('/api/stock-check', {period});
        ElMessage.success('盘点单已创建');
        load();
        openDetail(r.data);
      } catch(e) { ElMessage.error(e.message); }
    }
    async function openDetail(row) {
      try {
        const r = await api.get('/api/stock-check/' + row.id);
        detail.data = r.data;
        detail.visible = true;
      } catch(e) { ElMessage.error(e.message); }
    }
    function calcDiff(row) {
      const b = parseFloat(row.book_qty||0), a = parseFloat(row.actual_qty||0);
      row.diff_qty = Math.round((a - b) * 1000) / 1000;
    }
    async function closeCheck(d) {
      try {
        // 先逐行回写
        for (const it of (d.items||[])) {
          await api.put('/api/stock-check/' + d.id + '/item', {item_id: it.item_id, actual_qty: it.actual_qty, remark: it.remark || ''});
        }
        await api.post('/api/stock-check/' + d.id + '/close');
        ElMessage.success('已封账,调账凭证已生成');
        detail.visible = false;
        load();
      } catch(e) { ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, loading, detail, SC_STATUS, fmt, fmtDate, load, openCreate, openDetail, calcDiff, closeCheck, Icon };
  }
};

// ============ 收款提醒(提前15天 + 逾期) ============
const ReceivableRemindPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('bell',22)"></div>
        <div>
          <div class="ph-title">收款提醒</div>
          <div class="ph-sub">应收到期日前15天预警 + 已逾期清单</div>
        </div>
      </div>
    </div>
    <div v-if="remind.due_soon?.length" class="remind-block remind-warn">
      <div class="rb-head"><span v-html="Icon.icon('clock',16)"></span> 15天内即将到期 · {{remind.due_soon.length}} 笔 · 合计 ¥{{fmt(remind.total_due_soon)}}</div>
      <el-table :data="remind.due_soon" size="small" border>
        <el-table-column label="单号" prop="doc_no" width="180"/>
        <el-table-column label="客户" prop="counterparty_name" min-width="160"/>
        <el-table-column label="应收" prop="amount" width="120" align="right">
          <template #default="{row}">¥{{fmt(row.amount)}}</template>
        </el-table-column>
        <el-table-column label="已收" prop="settled_amount" width="120" align="right">
          <template #default="{row}">¥{{fmt(row.settled_amount)}}</template>
        </el-table-column>
        <el-table-column label="未收" width="120" align="right">
          <template #default="{row}">¥{{fmt(row.amount - row.settled_amount)}}</template>
        </el-table-column>
        <el-table-column label="到期日" width="110">
          <template #default="{row}">{{fmtDateShort(row.due_date)}}</template>
        </el-table-column>
        <el-table-column label="剩余天数" width="90" align="center">
          <template #default="{row}">
            <span class="pill" :class="row.days<=3?'UNPAID':'PARTIAL'">{{row.days}}天</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div v-if="remind.overdue?.length" class="remind-block remind-danger">
      <div class="rb-head"><span v-html="Icon.icon('exclamation-triangle',16)"></span> 已逾期 · {{remind.overdue.length}} 笔 · 合计 ¥{{fmt(remind.total_overdue)}}</div>
      <el-table :data="remind.overdue" size="small" border>
        <el-table-column label="单号" prop="doc_no" width="180"/>
        <el-table-column label="客户" prop="counterparty_name" min-width="160"/>
        <el-table-column label="应收" prop="amount" width="120" align="right">
          <template #default="{row}">¥{{fmt(row.amount)}}</template>
        </el-table-column>
        <el-table-column label="已收" prop="settled_amount" width="120" align="right">
          <template #default="{row}">¥{{fmt(row.settled_amount)}}</template>
        </el-table-column>
        <el-table-column label="未收" width="120" align="right">
          <template #default="{row}"><span class="neg">¥{{fmt(row.amount - row.settled_amount)}}</span></template>
        </el-table-column>
        <el-table-column label="到期日" width="110">
          <template #default="{row}">{{fmtDateShort(row.due_date)}}</template>
        </el-table-column>
        <el-table-column label="逾期天数" width="90" align="center">
          <template #default="{row}"><span class="pill UNPAID">{{-row.days}}天</span></template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{row}">
            <el-button size="small" type="primary" @click="goCollect(row.id)">去收款</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div v-if="!remind.due_soon?.length && !remind.overdue?.length" class="doc-empty">
      <div v-html="Icon.icon('check-circle',56)"></div>
      <div class="de-title">暂无即将到期的应收</div>
      <div class="de-desc">所有应收均在安全期内</div>
    </div>
  </div>`,
  setup() {
    const remind = ref({});
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    async function load() {
      try {
        const r = await api.get('/api/finance/receivable-remind');
        remind.value = r.data || {};
      } catch(e) { if (e.message) ElMessage.error(e.message); }
    }
    function goCollect(docId) {
      // 跳转应收管理并定位该单据
      window.__go && window.__go('receivables');
    }
    onMounted(load);
    return { remind, fmt, fmtDateShort, goCollect, Icon };
  }
};

const OutsourcePage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('arrow-path',22)"></div>
        <div>
          <div class="ph-title">外协单</div>
          <div class="ph-sub">委托第三方加工 · 必须关联销售订单 · 总经理直审后自动生成应付单+凭证</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建外协单</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:130px" clearable @change="search">
        <el-option v-for="(l,v) in OS_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.supplier_id" placeholder="供应商ID" style="width:130px" clearable @keyup.enter="search"/>
      <el-button @click="search">查询</el-button>
      <div class="grow"></div>
    </div>

    <el-table :data="rows" border size="small" stripe v-loading="loading">
      <el-table-column prop="outsource_no" label="外协单号" width="170"/>
      <el-table-column prop="order_no" label="销售订单" width="150"/>
      <el-table-column prop="customer_name" label="客户" min-width="120"/>
      <el-table-column prop="supplier_name" label="供应商" min-width="120"/>
      <el-table-column prop="process_name" label="外协工序" min-width="120"/>
      <el-table-column label="金额" width="120" align="right"><template #default="{row}">¥{{fmt(row.total_amount)}}</template></el-table-column>
      <el-table-column label="支付方式" width="90" align="center"><template #default="{row}">{{row.pay_method_label||'-'}}</template></el-table-column>
      <el-table-column prop="expected_delivery_date" label="交期" width="110"/>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{row}"><el-tag :type="{SUBMITTED:'warning',APPROVED:'success',REJECTED:'danger',PAID:'info'}[row.status]" size="small">{{row.status_label}}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{row}">
          <template v-if="canApprove && row.status==='SUBMITTED'">
            <el-button size="small" type="success" @click="doApprove(row)">审批通过</el-button>
            <el-button size="small" type="danger" @click="openReject(row)">驳回</el-button>
          </template>
          <span v-else style="color:var(--text2);font-size:12px">{{row.voucher_no ? '凭证:'+row.voucher_no : '-'}}</span>
        </template>
      </el-table-column>
    </el-table>
    <div style="display:flex;justify-content:flex-end;margin-top:10px" v-if="total>query.size">
      <el-pagination layout="prev, pager, next, total" :total="total" :page-size="query.size" :current-page="query.page" @current-change="p=>{query.page=p;load()}"/>
    </div>
    <div v-if="!loading && !rows.length" class="doc-empty">
      <div v-html="Icon.icon('inbox',56)"></div>
      <div class="de-title">暂无外协单</div>
      <div class="de-desc">点击「新建外协单」发起第一笔外协</div>
    </div>

    <el-dialog v-model="createDlg.visible" title="新建外协单" width="620px">
      <el-form :model="createDlg.form" label-width="100px">
        <el-form-item label="销售订单" required>
          <el-select v-model="createDlg.form.order_id" filterable placeholder="选择销售订单(必填)" style="width:100%" @change="onOrderChange">
            <el-option v-for="o in orders" :key="o.id" :label="o.order_no + (o.customer_name?(' · '+o.customer_name):'')" :value="o.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="供应商" required>
          <el-select v-model="createDlg.form.supplier_id" filterable placeholder="选择供应商(必填)" style="width:100%">
            <el-option v-for="s in suppliers" :key="s.id" :label="s.name + (s.code?(' · '+s.code):'')" :value="s.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="外协工序" required><el-input v-model="createDlg.form.process_name" placeholder="如 碳化钨喷涂/精车"/></el-form-item>
        <el-form-item label="数量" required><el-input-number v-model="createDlg.form.qty" :min="0.001" :precision="3" style="width:160px"/></el-form-item>
        <el-form-item label="单位"><el-input v-model="createDlg.form.unit" placeholder="件" style="width:120px"/></el-form-item>
        <el-form-item label="单价" required><el-input-number v-model="createDlg.form.unit_price" :min="0" :precision="2" style="width:160px"/></el-form-item>
        <el-form-item label="合计金额"><span style="font-weight:600;color:var(--text)">¥{{fmt(totalCalc)}}</span></el-form-item>
        <el-form-item label="支付方式">
          <el-select v-model="createDlg.form.pay_method" placeholder="选择支付方式" style="width:160px" clearable>
            <el-option v-for="m in PAY_METHODS" :key="m.v" :label="m.l" :value="m.v"/>
          </el-select>
        </el-form-item>
        <el-form-item label="交期"><el-date-picker v-model="createDlg.form.expected_delivery_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/></el-form-item>
        <el-form-item label="工艺要求"><el-input v-model="createDlg.form.process_spec" type="textarea" :rows="2" placeholder="工艺要求/技术指标"/></el-form-item>
        <el-form-item label="备注"><el-input v-model="createDlg.form.remark" type="textarea" :rows="2"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDlg.visible=false">取消</el-button><el-button type="primary" :loading="createDlg.saving" @click="submitCreate">提交外协</el-button></template>
    </el-dialog>

    <el-dialog v-model="rejectDlg.visible" title="驳回外协单" width="440px">
      <el-form label-width="80px">
        <el-form-item label="驳回原因"><el-input v-model="rejectDlg.form.reason" type="textarea" :rows="3" placeholder="填写驳回原因"/></el-form-item>
      </el-form>
      <template #footer><el-button @click="rejectDlg.visible=false">取消</el-button><el-button type="danger" :loading="rejectDlg.saving" @click="submitReject">确认驳回</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const query = reactive({ status: '', supplier_id: '', page: 1, size: 20 });
    const orders = ref([]); const suppliers = ref([]);
    const createDlg = reactive({ visible: false, saving: false, form: {} });
    const rejectDlg = reactive({ visible: false, saving: false, row: {}, form: {} });
    const userRole = (JSON.parse(localStorage.getItem(USER_KEY) || '{}').role) || '';
    const canApprove = ['GM', 'ADMIN'].includes(userRole);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const totalCalc = computed(() => Math.round((Number(createDlg.form.qty || 0) * Number(createDlg.form.unit_price || 0)) * 100) / 100);

    async function load() {
      loading.value = true;
      try {
        const params = new URLSearchParams({ page: query.page, size: query.size });
        if (query.status) params.set('status', query.status);
        if (query.supplier_id) params.set('supplier_id', query.supplier_id);
        const r = await api.get('/api/outsource?' + params.toString());
        rows.value = r.data || []; total.value = r.total || 0;
      } catch (e) { /* 已提示 */ }
      loading.value = false;
    }
    function search() { query.page = 1; load(); }
    async function loadOrders() {
      try { const r = await api.get('/api/orders?size=999'); orders.value = r.data || []; } catch (e) {}
    }
    async function loadSuppliers() {
      try { const r = await api.get('/api/purchases/suppliers'); suppliers.value = r.data || []; } catch (e) {}
    }
    function onOrderChange(oid) { /* 选订单时客户名由后端校验带出 */ }
    function openCreate() {
      createDlg.form = { order_id: null, supplier_id: null, process_name: '', qty: 1, unit: '件', unit_price: 0, pay_method: '', expected_delivery_date: '', process_spec: '', remark: '' };
      createDlg.visible = true;
      if (!orders.value.length) loadOrders();
      if (!suppliers.value.length) loadSuppliers();
    }
    async function submitCreate() {
      const f = createDlg.form;
      if (!f.order_id) { ElMessage.warning('请选择销售订单'); return; }
      if (!f.supplier_id) { ElMessage.warning('请选择供应商'); return; }
      if (!f.process_name) { ElMessage.warning('请填写外协工序'); return; }
      if (!f.qty || !f.unit_price) { ElMessage.warning('数量/单价必填'); return; }
      createDlg.saving = true;
      try {
        const r = await api.post('/api/outsource', f);
        ElMessage.success('外协单已提交: ' + r.data.outsource_no);
        createDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      createDlg.saving = false;
    }
    async function doApprove(row) {
      try {
        await ElMessageBox.confirm(`确认审批通过? 将自动生成应付单+凭证(借 委外加工费 贷 应付账款), 金额 ¥${fmt(row.total_amount)}`, '外协审批', { type: 'warning' });
        const r = await api.post('/api/outsource/' + row.id + '/approve');
        ElMessage.success('已通过, 应付单 ' + r.data.finance_doc_no + ', 凭证 ' + r.data.voucher_no);
        load();
      } catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    function openReject(row) { rejectDlg.row = row; rejectDlg.form = { reason: '' }; rejectDlg.visible = true; }
    async function submitReject() {
      rejectDlg.saving = true;
      try {
        await api.post('/api/outsource/' + rejectDlg.row.id + '/reject', { reason: rejectDlg.form.reason });
        ElMessage.success('已驳回');
        rejectDlg.visible = false; load();
      } catch (e) { /* 已提示 */ }
      rejectDlg.saving = false;
    }

    onMounted(() => { load(); });
    return { rows, total, loading, query, OS_STATUS, PAY_METHODS, orders, suppliers, createDlg, rejectDlg, canApprove, totalCalc, fmt, Icon, load, search, openCreate, submitCreate, onOrderChange, doApprove, openReject, submitReject };
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

    <el-dialog v-model="dialog.visible" title="新建采购单" width="760px">
      <el-form :model="form" label-width="90px">
        <NodeFormView
          v-if="formConfig && formConfig.fields && formConfig.fields.length"
          ref="formViewRef"
          :formConfig="formConfig"
          mode="create"
        />
        <el-empty v-else description="未配置采购单表单，请到【流程设计】为PROCUREMENT配置表单字段"/>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="submit" :loading="submitting">创建</el-button></template>
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
    const formConfig = ref(null); const formViewRef = ref(null); const formLoading = ref(false);
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
    // 画布动态表单: 以 PROCUREMENT 流程设计为准, 零硬编码
    async function loadFormConfig() {
      formLoading.value = true;
      try {
        const r = await api.get('/api/approvals/definitions?biz_type=PROCUREMENT');
        formConfig.value = (r.data && r.data.length && r.data[0].nodes && r.data[0].nodes.length)
          ? (r.data[0].nodes[0].form_config || null) : null;
      } catch (e) { console.warn('[采购单] 加载画布表单失败', e.message || e); formConfig.value = null; }
      finally { formLoading.value = false; }
    }
    async function openCreate() { loadFormConfig(); dialog.visible = true; }
    async function submit() {
      if (formViewRef.value && formViewRef.value.validate && !formViewRef.value.validate()) { ElMessage.warning('请完善画布表单必填项'); return; }
      const fd = formViewRef.value && formViewRef.value.getFormData ? formViewRef.value.getFormData() : {};
      try { await api.post('/api/purchases', { form_data: fd }); ElMessage.success('采购单已创建'); dialog.visible = false; load(); } catch (e) { ElMessage.error(e.message); } }
    async function act(row, url, label) {
      try { await ElMessageBox.confirm(`确认${label}?`, '提示', { type: 'warning' }); await api.post(url, {}); ElMessage.success(label + '成功'); if (detail.visible) detail.data = { ...detail.data, status: label === '下单' ? 'ORDERED' : 'RECEIVED' }; load(); }
      catch (e) { if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, loading, query, dialog, detail, formConfig, formViewRef, formLoading, PO_STATUS, PO_FLOW, fmt, fmtDateShort, poFlowClass, load, search, openDetail, openCreate, submit, act, Icon };
  }
};

// ============ 采购申请 ============
const PR_STATUS = { DRAFT: '草稿', SUBMITTED: '审批中', APPROVED: '已批准', REJECTED: '已驳回' };

const PRPage = {
  components: { FlowMini, NodeFormView },
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
            <span class="pill" :class="row.status">{{PAY_STATUS[row.status]||row.status}}</span>
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

    <el-dialog v-model="dialog.visible" title="采购申请" width="720px">
      <!-- 画布动态表单: 以 PURCHASE_REQUEST 流程设计为准, 零硬编码 -->
      <NodeFormView
        v-if="formConfig && formConfig.fields && formConfig.fields.length"
        ref="formViewRef"
        :formConfig="formConfig"
        mode="create"
      />
      <el-empty v-else-if="!loadingFormConfig" description="未配置采购申请表单，请到【流程设计】给采购请求流程设计表单字段"/>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="create" :loading="submitting">创建申请</el-button></template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false); const dialog = reactive({ visible: false });
    const formViewRef = ref(null);
    const formConfig = ref(null);
    const loadingFormConfig = ref(false);
    const submitting = ref(false);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    async function load() { loading.value = true; try { const r = await api.get('/api/purchase-requests'); rows.value = r.data; } catch (e) { ElMessage.error(e.message); } loading.value = false; }
    async function loadFormConfig() {
      loadingFormConfig.value = true;
      try {
        const r = await api.get('/api/approvals/definitions?biz_type=PURCHASE_REQUEST');
        if (r.data && r.data.length && r.data[0].nodes && r.data[0].nodes.length) {
          formConfig.value = r.data[0].nodes[0].form_config || null;
        } else { formConfig.value = null; }
      } catch (e) { console.warn('[采购申请] 加载画布表单失败', e.message || e); formConfig.value = null; }
      finally { loadingFormConfig.value = false; }
    }
    function openCreate() { loadFormConfig(); dialog.visible = true; }
    async function create() {
      if (formViewRef.value && !formViewRef.value.validate()) { ElMessage.warning('请完善画布表单必填项'); return; }
      const fd = formViewRef.value ? formViewRef.value.getFormData() : {};
      submitting.value = true;
      try { await api.post('/api/purchase-requests', { form_data: fd }); ElMessage.success('申请已创建'); dialog.visible = false; load(); }
      catch (e) { if (e.message) ElMessage.error(e.message); }
      finally { submitting.value = false; }
    }
    async function submit(row) { try { await api.post('/api/purchase-requests/' + row.id + '/submit', {}); ElMessage.success('已提交审批'); load(); } catch (e) { ElMessage.error(e.message); } }
    onMounted(load);
    return { rows, loading, dialog, formConfig, formViewRef, loadingFormConfig, submitting, PR_STATUS, fmt, load, openCreate, create, submit, Icon };
  }
};

// ============ 商机管理 ============
const OPP_STAGE = { LEAD: '初步接触', FOLLOW: '跟进中', QUOTE: '报价中', WON: '已成交', LOST: '已流失' };
const OPP_NEXT = { LEAD: ['FOLLOW'], FOLLOW: ['QUOTE', 'LOST'], QUOTE: ['WON', 'LOST'] };

const OpportunitiesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('target',22)"></div>
        <div>
          <div class="ph-title">商机管理</div>
          <div class="ph-sub">线索跟进 · 报价 · 成交/流失全流程</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建商机</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.stage" placeholder="全部阶段" style="width:150px" clearable @change="search">
        <el-option v-for="(l,v) in OPP_STAGE" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="标题/商机号" style="width:220px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)"></span></template>
      </el-input>
      <el-button @click="search">查询</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="['doc-bar', oppBar(row.stage)]"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.oppo_no}}</span>
            <span class="pill" :class="row.stage">{{OPP_STAGE[row.stage]||row.stage}}</span>
            <span class="doc-cust" v-if="row.customer_name">{{row.customer_name}}</span>
            <span class="doc-amount">¥{{fmt(row.expected_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field" style="grid-column:1/-1"><span class="df-label">商机标题</span><span class="df-value">{{row.title||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">来源</span><span class="df-value">{{row.source||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">预计成交</span><span class="df-value">{{fmtDateShort(row.expected_close_date)}}</span></div>
            <div class="doc-field"><span class="df-label">交期</span><span class="df-value">{{fmtDateShort(row.delivery_date)}}</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <template v-if="OPP_NEXT[row.stage]">
            <el-button v-for="ns in OPP_NEXT[row.stage]" :key="ns" size="small" :type="ns==='LOST'?'danger':(ns==='WON'?'success':'primary')" @click="changeStage(row,ns)">{{OPP_STAGE[ns]}}</el-button>
          </template>
          <el-button v-if="row.stage==='WON' && !row.customer_id" size="small" type="primary" @click="convertOppo(row)">转为客户</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无商机</div>
        <div class="de-desc">点击右上方"新建商机"录入第一条销售线索</div>
      </div>
    </div>
    <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="dialog.visible" title="新建商机" width="620px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="公司名称" required>
          <el-input v-model="form.customer_name" placeholder="客户公司全称" style="width:100%"/>
        </el-form-item>
        <el-form-item label="机会标题" required>
          <el-input v-model="form.title" placeholder="例如：XX公司阳极氧化5000件" style="width:100%"/>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="联系人"><el-input v-model="form.contact_person" placeholder="姓名" style="width:100%"/></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话"><el-input v-model="form.contact_phone" placeholder="手机号" style="width:100%"/></el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="14"><el-form-item label="公司地址"><el-input v-model="form.company_address" placeholder="地址" style="width:100%"/></el-form-item></el-col>
          <el-col :span="10"><el-form-item label="所属行业"><el-input v-model="form.industry" placeholder="行业" style="width:100%"/></el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="预计金额"><el-input-number v-model="form.expected_amount" :min="0" :precision="2" style="width:100%"/></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="预计成交"><el-date-picker v-model="form.expected_close_date" type="date" style="width:100%" value-format="YYYY-MM-DD"/></el-form-item></el-col>
        </el-row>
        <el-form-item label="来源">
          <el-input v-model="form.source" placeholder="展会/转介绍/网络询盘/老客户复购" style="width:100%"/>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" style="width:100%"/>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog.visible=false">取消</el-button><el-button type="primary" @click="create" :loading="submitting">创建</el-button></template>
    </el-dialog>

    <el-drawer v-model="detail.visible" title="商机详情" size="520px">
      <template v-if="detail.data.id">
        <div class="detail-hero">
          <div class="dh-row">
            <span class="dh-no">{{detail.data.oppo_no}}</span>
            <span class="pill" :class="detail.data.stage">{{OPP_STAGE[detail.data.stage]||detail.data.stage}}</span>
            <span class="dh-amount">¥{{fmt(detail.data.expected_amount)}}</span>
          </div>
        </div>
        <div class="detail-section">
          <div class="ds-title">客户信息</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">公司名称</div><div class="ig-value">{{detail.data.customer_name||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">联系人</div><div class="ig-value">{{detail.data.contact_person||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">联系电话</div><div class="ig-value">{{detail.data.contact_phone||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">公司地址</div><div class="ig-value">{{detail.data.company_address||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">所属行业</div><div class="ig-value">{{detail.data.industry||'-'}}</div></div>
          </div>
        </div>
        <div class="detail-section">
          <div class="ds-title">商机信息</div>
          <div class="info-grid">
            <div class="ig-item"><div class="ig-label">商机标题</div><div class="ig-value">{{detail.data.title||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">来源</div><div class="ig-value">{{detail.data.source||'-'}}</div></div>
            <div class="ig-item"><div class="ig-label">预计成交</div><div class="ig-value">{{fmtDateShort(detail.data.expected_close_date)}}</div></div>
            <div class="ig-item"><div class="ig-label">交期</div><div class="ig-value">{{fmtDateShort(detail.data.delivery_date)}}</div></div>
            <div class="ig-item" style="grid-column:1/-1"><div class="ig-label">备注</div><div class="ig-value">{{detail.data.remark||'-'}}</div></div>
          </div>
        </div>
        <div class="detail-section" v-if="detail.data.stage==='WON'">
          <el-button v-if="!detail.data.customer_id" type="primary" @click="convertOppo(detail.data)" style="width:100%">转为客户档案</el-button>
          <div v-else style="color:var(--green);display:flex;align-items:center;gap:6px"><span v-html="Icon.icon('check',18)"></span>已转为客户：{{detail.data.customer_name}}</div>
        </div>
      </template>
    </el-drawer>
  </div>`,
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const page = reactive({ page: 1, size: 15 });
    const query = reactive({ stage: '', keyword: '' });
    const dialog = reactive({ visible: false });
    const detail = reactive({ visible: false, data: {} });
    const form = reactive({ customer_name: '', title: '', contact_person: '', contact_phone: '', company_address: '', industry: '', expected_amount: 0, expected_close_date: null, delivery_date: null, source: '', remark: '' });
    const submitting = ref(false);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    const oppBar = st => ({ LEAD: '', FOLLOW: 'warn', QUOTE: '', WON: 'success', LOST: 'danger' }[st] || '');
    async function load() {
      loading.value = true;
      try { const qs = new URLSearchParams({ page: page.page, size: page.size, ...Object.fromEntries(Object.entries(query).filter(([_,v])=>v!=='')) }).toString(); const r = await api.get('/api/opportunities?'+qs); rows.value = r.data; total.value = r.total ?? rows.value.length; } catch(e){ ElMessage.error(e.message); }
      loading.value = false;
    }
    function search() { page.page = 1; load(); }
    function openDetail(row) { detail.data = { ...row }; detail.visible = true; }
    function openCreate() { Object.assign(form, { customer_name: '', title: '', contact_person: '', contact_phone: '', company_address: '', industry: '', expected_amount: 0, expected_close_date: null, delivery_date: null, source: '', remark: '' }); dialog.visible = true; }
    async function create() {
      if (!form.customer_name.trim()) { ElMessage.warning('请填写公司名称'); return; }
      if (!form.title.trim()) { ElMessage.warning('请填写机会标题'); return; }
      submitting.value = true;
      try { await api.post('/api/opportunities', { ...form }); ElMessage.success('商机已创建'); dialog.visible = false; load(); } catch(e){ if(e.message) ElMessage.error(e.message); }
      finally { submitting.value = false; }
    }
    async function changeStage(row, stage) {
      try { await ElMessageBox.confirm(`确认转为"${OPP_STAGE[stage]}"?`, '提示', { type: stage === 'LOST' ? 'warning' : 'info' }); await api.put(`/api/opportunities/${row.id}/stage`, { stage }); ElMessage.success(`已转为${OPP_STAGE[stage]}`); if (detail.visible) detail.data = { ...detail.data, stage }; load(); } catch(e){ if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    async function convertOppo(row) {
      try { await ElMessageBox.confirm(`确认将"${row.customer_name}"转为客户档案?`, '提示', { type: 'info' }); const r = await api.post(`/api/opportunities/${row.id}/convert`); ElMessage.success(`已转为客户：${r.name}`); row.customer_id = r.id; if (detail.visible) detail.data = { ...detail.data, customer_id: r.id }; load(); } catch(e){ if (e !== 'cancel' && e.message) ElMessage.error(e.message); }
    }
    onMounted(load);
    return { rows, total, page, loading, query, dialog, detail, form, submitting, OPP_STAGE, OPP_NEXT, fmt, fmtDateShort, oppBar, load, search, openDetail, openCreate, create, changeStage, convertOppo, Icon };
  }
};

// ============ 工资 ============
const PAY_STATUS = { DRAFT: '草稿', CONFIRMED: '已确认', PAID: '已发放' };
const PAY_FLOW = [
  { key: 'DRAFT', label: '草稿', idx: 0 },
  { key: 'CONFIRMED', label: '已确认', idx: 1 },
  { key: 'PAID', label: '已发放', idx: 2 },
];

const PayrollPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('wallet',22)"></div>
        <div>
          <div class="ph-title">工资管理</div>
          <div class="ph-sub">花名册一次建档 · 自动算个税 · 一键生成计提/发放凭证 · 公账5000+现金</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button @click="openRoster"><span v-html="Icon.icon('users',14)" style="vertical-align:middle;margin-right:4px"></span>员工花名册</el-button>
        <el-button type="primary" @click="openCreate" v-if="!current.id"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>新建当月工资</el-button>
      </div>
    </div>

    <!-- 历史工资单列表 -->
    <div v-if="!current.id" class="doc-list" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+row.status"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.run_no}}</span>
            <span class="pill" :class="row.status">{{PAY_STATUS[row.status]||row.status}}</span>
            <span class="pill warn">{{row.period}}</span>
            <span class="doc-amount">¥{{fmt(row.total_amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">人数</span><span class="df-value">{{row.item_count||0}}人</span></div>
            <div class="doc-field" v-if="row.voucher_id"><span class="df-label">计提凭证</span><span class="df-value" style="color:var(--success)">已生成</span></div>
            <div class="doc-field" v-if="row.pay_voucher_id"><span class="df-label">发放凭证</span><span class="df-value" style="color:var(--success)">已生成</span></div>
          </div>
        </div>
        <div class="doc-actions" @click.stop>
          <el-button size="small" @click="openDetail(row)">查看明细</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox',56)"></div>
        <div class="de-title">暂无工资单</div>
        <div class="de-desc">点击右上角"新建当月工资"开始</div>
      </div>
    </div>

    <!-- 工资编辑表格 -->
    <div v-if="current.id" class="card" style="padding:0;overflow:hidden">
      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div style="font-size:16px;font-weight:600">{{current.period}} 工资单</div>
        <span class="pill" :class="current.status">{{PAY_STATUS[current.status]||current.status}}</span>
        <span class="doc-no">{{current.run_no}}</span>
        <div style="flex:1"></div>
        <div v-if="current.status==='DRAFT'" style="display:flex;gap:8px">
          <el-button size="small" @click="addEmployee"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:2px"></span>加人</el-button>
          <el-button size="small" type="primary" @click="savePayroll"><span v-html="Icon.icon('save',12)" style="vertical-align:middle;margin-right:2px"></span>保存/重算</el-button>
          <el-button size="small" type="success" @click="doConfirm"><span v-html="Icon.icon('check',12)" style="vertical-align:middle;margin-right:2px"></span>确认工资</el-button>
        </div>
        <div v-if="current.status==='CONFIRMED'" style="display:flex;gap:8px">
          <el-button size="small" type="warning" @click="doAccrue"><span v-html="Icon.icon('calculator',12)" style="vertical-align:middle;margin-right:2px"></span>生成计提凭证</el-button>
        </div>
        <div v-if="current.status==='CONFIRMED' && current.voucher_id" style="display:flex;gap:8px">
          <el-button size="small" type="success" @click="doPay"><span v-html="Icon.icon('cash',12)" style="vertical-align:middle;margin-right:2px"></span>生成发放凭证</el-button>
        </div>
        <el-button size="small" @click="closeCurrent" :icon="current.status==='DRAFT'?'':'ArrowLeft'">{{current.status==='DRAFT'?'取消返回':'返回列表'}}</el-button>
      </div>

      <!-- 汇总条 -->
      <div v-if="summary.gross_total" class="payroll-summary">
        <div class="ps-item"><div class="ps-label">人数</div><div class="ps-val">{{summary.headcount}}</div></div>
        <div class="ps-item"><div class="ps-label">应发合计</div><div class="ps-val">¥{{fmt(summary.gross_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">社保个人</div><div class="ps-val" style="color:var(--warning)">¥{{fmt(summary.ss_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">公积金个人</div><div class="ps-val" style="color:var(--warning)">¥{{fmt(summary.hf_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">个税</div><div class="ps-val" style="color:var(--danger)">¥{{fmt(summary.tax_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">实发合计</div><div class="ps-val" style="color:var(--success);font-size:18px">¥{{fmt(summary.net_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">公账发放</div><div class="ps-val">¥{{fmt(summary.bank_total)}}</div></div>
        <div class="ps-item"><div class="ps-label">现金发放</div><div class="ps-val" style="color:var(--warning)">¥{{fmt(summary.cash_total)}}</div></div>
      </div>

      <!-- 可编辑表格 -->
      <div class="payroll-table-wrap">
        <el-table :data="current.items" size="small" border stripe :row-class-name="rowDeptClass" height="calc(100vh - 320px)">
          <el-table-column prop="name" label="姓名" width="90" fixed/>
          <el-table-column prop="department" label="部门" width="70">
            <template #default="{row}">
              <el-select v-model="row.department" size="small" :disabled="current.status!=='DRAFT'" style="width:100%">
                <el-option label="管理" value="管理"/>
                <el-option label="销售" value="销售"/>
                <el-option label="生产" value="生产"/>
              </el-select>
            </template>
          </el-table-column>
          <el-table-column prop="position" label="岗位" width="100">
            <template #default="{row}"><el-input v-model="row.position" size="small" :disabled="current.status!=='DRAFT'" placeholder="岗位"/></template>
          </el-table-column>
          <el-table-column prop="base_salary" label="基本工资" width="100" align="right">
            <template #default="{row}"><el-input-number v-model="row.base_salary" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="bonus" label="绩效奖金" width="100" align="right">
            <template #default="{row}"><el-input-number v-model="row.bonus" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="allowance" label="补贴" width="90" align="right">
            <template #default="{row}"><el-input-number v-model="row.allowance" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="overtime" label="加班费" width="90" align="right">
            <template #default="{row}"><el-input-number v-model="row.overtime" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="deduction" label="扣款" width="90" align="right">
            <template #default="{row}"><el-input-number v-model="row.deduction" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="gross" label="应发" width="100" align="right">
            <template #default="{row}"><span style="font-weight:600">¥{{fmt(row.gross)}}</span></template>
          </el-table-column>
          <el-table-column prop="social_security" label="社保" width="90" align="right">
            <template #default="{row}"><el-input-number v-model="row.social_security" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="housing_fund" label="公积金" width="90" align="right">
            <template #default="{row}"><el-input-number v-model="row.housing_fund" size="small" :min="0" :precision="2" :disabled="current.status!=='DRAFT'" controls-position="right" style="width:100%"/></template>
          </el-table-column>
          <el-table-column prop="tax" label="个税" width="90" align="right">
            <template #default="{row}"><span style="color:var(--danger)">¥{{fmt(row.tax)}}</span></template>
          </el-table-column>
          <el-table-column prop="net" label="实发" width="100" align="right" fixed="right">
            <template #default="{row}"><span style="font-weight:700;color:var(--success);font-size:14px">¥{{fmt(row.net)}}</span></template>
          </el-table-column>
          <el-table-column label="公账" width="90" align="right">
            <template #default="{row}"><span>¥{{fmt(row.bank_amount)}}</span></template>
          </el-table-column>
          <el-table-column label="现金" width="90" align="right">
            <template #default="{row}"><span style="color:var(--warning)">¥{{fmt(row.cash_amount)}}</span></template>
          </el-table-column>
          <el-table-column label="" width="50" fixed="right" v-if="current.status==='DRAFT'">
            <template #default="{$index}"><el-button link type="danger" size="small" @click="current.items.splice($index,1)"><span v-html="Icon.icon('trash',14)"></span></el-button></template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 新建对话框：选择期间+生成方式 -->
    <el-dialog v-model="createDlg" title="新建工资单" width="480px">
      <el-form label-width="80px">
        <el-form-item label="期间">
          <el-date-picker v-model="createForm.period" type="month" format="YYYY-MM" value-format="YYYY-MM" placeholder="选择月份" style="width:200px"/>
        </el-form-item>
        <el-form-item label="生成方式">
          <el-radio-group v-model="createForm.mode">
            <el-radio label="roster">从花名册生成（带出所有在职员工）</el-radio>
            <el-radio label="copy">复制上月数据（变动项清零）</el-radio>
            <el-radio label="blank">空白表格（手动添加）</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="createDlg=false">取消</el-button><el-button type="primary" @click="doCreate">开始编辑</el-button></template>
    </el-dialog>

    <!-- 员工花名册管理 -->
    <el-dialog v-model="rosterDlg" title="员工花名册（HR库）" width="96vw" top="2vh" :close-on-click-modal="false">
      <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
        <el-button type="primary" size="small" @click="addEmp"><span v-html="Icon.icon('plus',12)" style="vertical-align:middle;margin-right:2px"></span>新增员工</el-button>
        <span style="color:var(--text-2);font-size:12px">一次建档每月复用 · 身份证号为银行代发必填 · 标注<span style="color:var(--danger)">*</span>为必填项</span>
      </div>
      <el-table :data="emps" size="small" border height="calc(100vh - 140px)" style="width:100%">
        <el-table-column label="姓名*" width="80">
          <template #default="{row}"><el-input v-model="row.name" size="small" placeholder="姓名"/></template>
        </el-table-column>
        <el-table-column label="性别" width="55">
          <template #default="{row}">
            <el-select v-model="row.gender" size="small" style="width:100%">
              <el-option label="男" value="男"/><el-option label="女" value="女"/>
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="部门" width="65">
          <template #default="{row}">
            <el-select v-model="row.department" size="small" style="width:100%">
              <el-option label="管理" value="管理"/><el-option label="销售" value="销售"/><el-option label="生产" value="生产"/>
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="岗位" width="85">
          <template #default="{row}"><el-input v-model="row.position" size="small" placeholder="岗位"/></template>
        </el-table-column>
        <el-table-column label="手机号" width="110">
          <template #default="{row}"><el-input v-model="row.phone" size="small" placeholder="手机号"/></template>
        </el-table-column>
        <el-table-column label="身份证号*" width="165">
          <template #default="{row}"><el-input v-model="row.id_number" size="small" placeholder="银行代发必需"/></template>
        </el-table-column>
        <el-table-column label="基本工资" width="90" align="right">
          <template #default="{row}"><el-input-number v-model="row.base_salary" :min="0" :precision="2" size="small" controls-position="right" style="width:100%"/></template>
        </el-table-column>
        <el-table-column label="社保" width="70" align="right">
          <template #default="{row}"><el-input-number v-model="row.social_security" :min="0" :precision="2" size="small" controls-position="right" style="width:100%"/></template>
        </el-table-column>
        <el-table-column label="公积金" width="70" align="right">
          <template #default="{row}"><el-input-number v-model="row.housing_fund" :min="0" :precision="2" size="small" controls-position="right" style="width:100%"/></template>
        </el-table-column>
        <el-table-column label="开户银行" width="100">
          <template #default="{row}"><el-input v-model="row.bank_name" size="small" placeholder="工商银行"/></template>
        </el-table-column>
        <el-table-column label="开户行支行" width="140">
          <template #default="{row}"><el-input v-model="row.bank_branch" size="small" placeholder="东莞长安支行"/></template>
        </el-table-column>
        <el-table-column label="银行账号" width="140">
          <template #default="{row}"><el-input v-model="row.bank_account" size="small" placeholder="银行账号"/></template>
        </el-table-column>
        <el-table-column label="持证情况" width="100">
          <template #default="{row}"><el-input v-model="row.certificates" size="small" placeholder="焊工证等"/></template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{row}">
            <el-button link type="primary" size="small" @click="saveEmp(row)">保存</el-button>
            <el-button v-if="row.id" link type="danger" size="small" @click="delEmp(row)">离职</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]); const loading = ref(false);
    const current = reactive({ id: null, period: '', run_no: '', status: 'DRAFT', items: [], voucher_id: null, pay_voucher_id: null });
    const summary = reactive({ headcount:0, gross_total:0, ss_total:0, hf_total:0, tax_total:0, net_total:0, bank_total:0, cash_total:0 });
    const createDlg = ref(false); const createForm = reactive({ period: '', mode: 'roster' });
    const rosterDlg = ref(false); const emps = ref([]);
    const fmt = n => Number(n || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    function calcSummary() {
      const items = current.items || [];
      summary.headcount = items.length;
      summary.gross_total = items.reduce((s,i) => s + (i.gross||0), 0);
      summary.ss_total = items.reduce((s,i) => s + (i.social_security||0), 0);
      summary.hf_total = items.reduce((s,i) => s + (i.housing_fund||0), 0);
      summary.tax_total = items.reduce((s,i) => s + (i.tax||0), 0);
      summary.net_total = items.reduce((s,i) => s + (i.net||0), 0);
      summary.bank_total = items.reduce((s,i) => s + (i.bank_amount||0), 0);
      summary.cash_total = items.reduce((s,i) => s + (i.cash_amount||0), 0);
    }

    async function load() {
      loading.value = true;
      try { const r = await api.get('/api/payroll'); rows.value = r.data || []; }
      catch(e) { ElMessage.error(e.message); }
      loading.value = false;
    }

    function closeCurrent() {
      Object.assign(current, { id: null, period: '', run_no: '', status: 'DRAFT', items: [], voucher_id: null, pay_voucher_id: null });
      load();
    }

    function openCreate() {
      createForm.period = new Date().toISOString().slice(0,7);
      createForm.mode = 'roster';
      createDlg.value = true;
    }

    async function doCreate() {
      try {
        let r;
        if (createForm.mode === 'roster') {
          r = await api.get('/api/payroll/generate?period=' + createForm.period);
        } else if (createForm.mode === 'copy') {
          r = await api.get('/api/payroll/copy-last?period=' + createForm.period);
        } else {
          r = { data: { period: createForm.period, items: [] } };
        }
        current.id = null;
        current.period = r.data.period;
        current.run_no = '';
        current.status = 'DRAFT';
        current.items = r.data.items;
        current.voucher_id = null;
        current.pay_voucher_id = null;
        createDlg.value = false;
        calcSummary();
      } catch(e) { ElMessage.error(e.message); }
    }

    function addEmployee() {
      current.items.push({ employee_id: null, name: '', department: '管理', position: '', base_salary: 0, bonus: 0, allowance: 0, overtime: 0, deduction: 0, social_security: 0, housing_fund: 0, gross: 0, tax: 0, net: 0, bank_amount: 0, cash_amount: 0 });
    }

    async function savePayroll() {
      try {
        const r = await api.post('/api/payroll/save', { period: current.period, items: current.items.map(i => ({
          employee_id: i.employee_id, name: i.name, department: i.department, position: i.position,
          base_salary: Number(i.base_salary||0), bonus: Number(i.bonus||0), allowance: Number(i.allowance||0),
          overtime: Number(i.overtime||0), deduction: Number(i.deduction||0),
          social_security: Number(i.social_security||0), housing_fund: Number(i.housing_fund||0),
        }))});
        Object.assign(current, { id: r.data.id, run_no: r.data.run_no, items: r.data.items });
        Object.assign(summary, r.data.summary);
        ElMessage.success('已保存，个税和实发已自动计算');
      } catch(e) { ElMessage.error(e.message); }
    }

    async function doConfirm() {
      if (!current.id) { await savePayroll(); }
      try {
        await ElMessageBox.confirm('确认后工资数据锁定，将生成计提凭证，确定？', '提示', { type: 'warning' });
        await api.post('/api/payroll/' + current.id + '/confirm', {});
        current.status = 'CONFIRMED';
        ElMessage.success('工资已确认');
      } catch(e) { if(e!=='cancel' && e.message) ElMessage.error(e.message); }
    }

    async function doAccrue() {
      try {
        const r = await api.post('/api/payroll/' + current.id + '/accrue', {});
        current.voucher_id = r.data.voucher_id;
        ElMessage.success('计提凭证已生成：' + r.data.voucher_no);
        load();
      } catch(e) { ElMessage.error(e.message); }
    }

    async function doPay() {
      try {
        await ElMessageBox.confirm('将生成发放凭证：公账5000/人+剩余现金，代扣个税/社保/公积金。确定？', '提示', { type: 'warning' });
        const r = await api.post('/api/payroll/' + current.id + '/pay', {});
        current.pay_voucher_id = r.data.voucher_id;
        current.status = 'PAID';
        ElMessage.success('发放凭证已生成：' + r.data.voucher_no + '（公账¥' + fmt(r.data.bank_amount) + ' + 现金¥' + fmt(r.data.cash_amount) + '）');
        load();
      } catch(e) { if(e!=='cancel' && e.message) ElMessage.error(e.message); }
    }

    async function openDetail(row) {
      try {
        const r = await api.get('/api/payroll/' + row.id);
        Object.assign(current, r.data);
        Object.assign(summary, r.data.summary);
      } catch(e) { ElMessage.error(e.message); }
    }

    async function openRoster() {
      rosterDlg.value = true;
      await loadEmps();
    }
    async function loadEmps() {
      try { const r = await api.get('/api/employees'); emps.value = r.data || []; }
      catch(e) { ElMessage.error(e.message); }
    }
    function addEmp() {
      emps.value.push({ name:'', gender:'男', department:'管理', position:'', phone:'', id_number:'', base_salary:0, social_security:0, housing_fund:0, bank_name:'', bank_branch:'', bank_account:'', certificates:'' });
    }
    async function saveEmp(row) {
      try {
        if (!row.name || !row.name.trim()) { ElMessage.warning('请输入姓名'); return; }
        if (!row.id_number || !row.id_number.trim()) { ElMessage.warning('身份证号为银行代发必填项'); return; }
        const body = {
          name:row.name.trim(), gender:row.gender||'男', department:row.department, position:row.position||'',
          phone:row.phone||'', id_number:row.id_number.trim(),
          base_salary:Number(row.base_salary||0), social_security:Number(row.social_security||0), housing_fund:Number(row.housing_fund||0),
          bank_name:row.bank_name||'', bank_branch:row.bank_branch||'', bank_account:row.bank_account||'',
          certificates:row.certificates||'', remark:row.remark||''
        };
        if (row.id) {
          await api.put('/api/employees/' + row.id, body);
          ElMessage.success('已更新');
        } else {
          const r = await api.post('/api/employees', body);
          row.id = r.data.id;
          ElMessage.success('已添加');
        }
      } catch(e) { ElMessage.error(e.message); }
    }
    async function delEmp(row) {
      try {
        await ElMessageBox.confirm('标记' + row.name + '为离职？（不删除数据，仅不再出现在工资表）', '提示', { type: 'warning' });
        await api.delete('/api/employees/' + row.id);
        row.status = 'RESIGNED';
        emps.value = emps.value.filter(e => e.id !== row.id);
        ElMessage.success('已标记离职');
      } catch(e) { if(e!=='cancel' && e.message) ElMessage.error(e.message); }
    }

    function rowDeptClass({row}) {
      return 'dept-' + (row.department || '管理');
    }

    onMounted(load);
    return { rows, loading, current, summary, createDlg, createForm, rosterDlg, emps,
      PAY_STATUS, PAY_FLOW, fmt, load, openCreate, doCreate, closeCurrent, addEmployee,
      savePayroll, doConfirm, doAccrue, doPay, openDetail, openRoster, loadEmps, addEmp, saveEmp, delEmp,
      rowDeptClass, calcSummary, Icon };
  }
};

// ============ 审批 (真实工作流:FlowTrack可视化 + 转交催办) ============
const BIZ_LABEL = {PURCHASE_REQUEST:'采购申请',COMPLETION:'完工单',EXPENSE:'费用报销',SALES_ADJUSTMENT:'调价申请'};
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
        const isApprove = action === 'approve';
        const { value } = await ElMessageBox.prompt(
          isApprove ? '审批意见（可直接确定，默认为"同意"）' : '审批意见（拒绝必须填写）',
          isApprove ? '通过' : '拒绝',
          {
            inputType: 'textarea',
            inputValue: isApprove ? '同意' : '',
            inputPlaceholder: isApprove ? '同意' : '如：信息不全，请补充 / 金额有误，请修改',
            inputValidator: v => (isApprove || (v && v.trim())) ? true : '拒绝必须填写审批意见',
          }
        );
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
        const ru = await api.get('/api/admin/users?page=1&size=200');
        const users = ru.data || [];
        if (!users.length) { ElMessage.warning('暂无可转交用户'); return; }
        const opts = users.map(u => `<option value="${u.id}">${u.name || u.username}</option>`).join('');
        const { value } = await ElMessageBox.confirm(
          `<select id="tf-sel" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border,#334155);background:var(--bg,#0f172a);color:inherit">${opts}</select>`,
          '转交给', { dangerouslyUseHTMLString: true, confirmButtonText: '转交', cancelButtonText: '取消' }
        );
        const uid = Number(document.getElementById('tf-sel')?.value);
        if (!uid) return;
        await api.post('/api/approvals/tasks/' + row.id + '/transfer', { to_user_id: uid });
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
        <el-select v-model="curBizType" placeholder="选择业务类型" style="width:180px" @change="onBizTypeChange">
          <el-option-group label="核心业务">
            <el-option v-for="o in bizCore" :key="o.v" :label="o.l" :value="o.v"/>
          </el-option-group>
          <el-option-group label="其他流程">
            <el-option v-for="o in bizOther" :key="o.v" :label="o.l" :value="o.v"/>
          </el-option-group>
        </el-select>
        <el-select v-model="loadedDefId" placeholder="加载已有流程" clearable style="width:260px" @change="onLoadDef" v-if="flowDefs.length">
          <el-option v-for="d in flowDefs" :key="d.id" :label="getDefLabel(d)" :value="d.id"/>
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
        <el-button size="small" @click="openFormConfig" style="float:left;margin-right:8px">
          <span v-html="Icon.icon('form',14)" style="vertical-align:middle;margin-right:4px"></span>
          表单配置<span v-if="hasFormConfig()" style="color:#10b981;margin-left:4px">✓</span>
        </el-button>
        <el-button size="small" type="danger" @click="delFromDlg" v-if="!dlg.isNew" style="float:left">删除节点</el-button>
        <el-button @click="dlg.vis=false">取消</el-button>
        <el-button type="primary" @click="saveDlg">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog 
      v-model="formDlg.vis" 
      :title="(formDlg.isNew ? '添加' : '编辑') + '表单字段'" 
      width="500px"
      append-to-body
      :close-on-click-modal="false">
      <el-form label-width="90px" size="default">
        <el-form-item label="字段Key" required>
          <el-input v-model="formDlg.tempField.key" placeholder="英文标识,如:material_name" :disabled="!formDlg.isNew"/>
          <div class="tiny muted">用于数据绑定,创建后不可修改</div>
        </el-form-item>
        <el-form-item label="字段标签" required>
          <el-input v-model="formDlg.tempField.label" placeholder="中文显示名称,如:物料名称"/>
        </el-form-item>
        <el-form-item label="组件类型" required>
          <el-select v-model="formDlg.tempField.type" style="width:100%">
            <el-option-group v-for="cat in formCategories" :key="cat" :label="cat">
              <el-option 
                v-for="comp in getFormComponentsByCategory(cat)" 
                :key="comp.type" 
                :label="comp.label" 
                :value="comp.type">
                {{comp.label}}
              </el-option>
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item label="占位符">
          <el-input v-model="formDlg.tempField.placeholder" placeholder="输入提示文本"/>
        </el-form-item>
        <el-form-item label="选项列表" v-if="formDlg.tempField.type==='select'">
          <div style="width:100%">
            <div v-for="(opt, oi) in formDlg.tempField.options" :key="oi" style="display:flex;gap:8px;margin-bottom:4px">
              <el-input v-model="opt.label" placeholder="显示文本" style="flex:1" size="small"/>
              <el-input v-model="opt.value" placeholder="值" style="flex:1" size="small"/>
              <el-button size="small" type="danger" @click="formDlg.tempField.options.splice(oi,1)">×</el-button>
            </div>
            <el-button size="small" @click="formDlg.tempField.options.push({label:'',value:''})">+ 添加选项</el-button>
          </div>
        </el-form-item>
        <el-form-item label="明细列" v-if="formDlg.tempField.type==='detail_table'">
          <div style="width:100%">
            <div v-for="(col, ci) in formDlg.tempField.config.columns" :key="ci" style="display:flex;gap:6px;margin-bottom:4px">
              <el-input v-model="col.key" placeholder="列key" style="flex:1" size="small"/>
              <el-input v-model="col.label" placeholder="列名" style="flex:1" size="small"/>
              <el-select v-model="col.type" style="width:80px" size="small">
                <el-option label="文本" value="text"/>
                <el-option label="数字" value="number"/>
              </el-select>
              <el-button size="small" type="danger" @click="formDlg.tempField.config.columns.splice(ci,1)">×</el-button>
            </div>
            <el-button size="small" @click="formDlg.tempField.config.columns.push({key:'col_'+(formDlg.tempField.config.columns.length+1),label:'',type:'text'})">+ 添加列</el-button>
          </div>
        </el-form-item>
        <el-form-item label="关联数据" v-if="formDlg.tempField.type==='ref_picker'">
          <el-select v-model="formDlg.tempField.config.source" placeholder="选择数据来源模块" size="small" style="width:100%">
            <el-option v-for="(label, key) in refSourceLabels" :key="key" :label="label" :value="key"/>
          </el-select>
        </el-form-item>
        <el-form-item label="带出映射" v-if="formDlg.tempField.type==='ref_picker'">
          <div style="width:100%">
            <div class="muted" style="font-size:12px;margin-bottom:4px">选中记录后自动填充到本表单字段（填充后仍可修改）</div>
            <div v-for="(fm, fi) in formDlg.tempField.config.fillRows" :key="fi" style="display:flex;gap:6px;margin-bottom:4px">
              <el-input v-model="fm.target" placeholder="本表单字段key" style="flex:1" size="small"/>
              <span style="line-height:24px;color:var(--text2)">←</span>
              <el-input v-model="fm.source" placeholder="数据记录字段" style="flex:1" size="small"/>
              <el-button size="small" type="danger" @click="formDlg.tempField.config.fillRows.splice(fi,1)">×</el-button>
            </div>
            <el-button size="small" @click="formDlg.tempField.config.fillRows.push({target:'',source:''})">+ 添加映射</el-button>
          </div>
        </el-form-item>
        <el-form-item label="列宽">
          <el-radio-group v-model="formDlg.tempField.columnWidth" size="small">
            <el-radio-button :label="1">整行</el-radio-button>
            <el-radio-button :label="2">半行</el-radio-button>
            <el-radio-button :label="3">三分之一</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="选项">
          <div style="display:flex;gap:16px">
            <el-checkbox v-model="formDlg.tempField.required">必填</el-checkbox>
            <el-checkbox v-model="formDlg.tempField.readonly">只读</el-checkbox>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formDlg.vis=false">取消</el-button>
        <el-button type="primary" @click="saveFormField">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog 
      v-model="formConfigVis" 
      :title="nodeFormConfig.title + ' - 字段配置'" 
      width="750px"
      append-to-body
      :close-on-click-modal="false"
      @update:modelValue="onFormConfigUpdate">
      <div class="form-config-panel">
        <div class="fc-header">
          <el-form :inline="true" size="small">
            <el-form-item label="表单标题">
              <el-input v-model="nodeFormConfig.title" placeholder="请输入表单标题" style="width:250px"/>
            </el-form-item>
            <el-form-item label="显示表头">
              <el-switch v-model="nodeFormConfig.showHeader"/>
            </el-form-item>
          </el-form>
        </div>
        
        <div class="fc-fields" v-if="nodeFormConfig.fields.length">
          <div v-for="(field, index) in nodeFormConfig.fields" :key="field.key" class="fc-field">
            <div class="fc-field-index">{{index + 1}}</div>
            <div class="fc-field-info">
              <div class="fc-field-label">{{field.label || '未命名'}}</div>
              <div class="fc-field-meta">
                <el-tag size="mini" type="info">{{getComponentLabel(field.type)}}</el-tag>
                <span v-if="field.required" style="color:#ef4444;margin-left:4px">必填</span>
                <span v-if="field.readonly" style="color:#9ca3af;margin-left:4px">只读</span>
                <span class="fc-field-key">{{field.key}}</span>
              </div>
            </div>
            <div class="fc-field-actions">
              <el-button size="mini" @click="moveFormField(index, -1)" :disabled="index===0">↑</el-button>
              <el-button size="mini" @click="moveFormField(index, 1)" :disabled="index===nodeFormConfig.fields.length-1">↓</el-button>
              <el-button size="mini" type="primary" @click="editFormField(index)">编辑</el-button>
              <el-button size="mini" type="danger" @click="deleteFormField(index)">删除</el-button>
            </div>
          </div>
        </div>
        <div class="fc-empty" v-else>
          <div style="text-align:center;padding:40px;color:#9ca3af">
            <span v-html="Icon.icon('file',48)"></span>
            <div style="margin-top:12px">暂无表单字段，请点击下方「添加字段」</div>
          </div>
        </div>
      </div>
      
      <template #footer>
        <el-button @click="addFormField" style="float:left">
          <span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>添加字段
        </el-button>
        <el-button @click="formConfigVis = false">取消</el-button>
        <el-button type="primary" @click="saveFormConfig">保存配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mgmtVis" title="流程管理" width="700px">
      <el-table :data="flowDefs" style="width:100%" size="small" max-height="400">
        <el-table-column prop="id" label="ID" width="60"/>
        <el-table-column label="业务类型" width="120">
          <template #default="s">
            {{getBizTypeLabel(s.row.biz_type)}}
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
    const { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } = Vue;
    const lfContainer = ref(null);
    let lf = null;
    const curBizType = ref('CORE_PRODUCTION');
    const loadedDefId = ref(null);
    const flowDefs = ref([]);
    const saveFlash = ref(false);
    const mgmtVis = ref(false);
    const dlg = reactive({
      vis: false, isNew: true, id: null,
      type: 'approve', name: '', role: 'DEPARTMENT_HEAD',
      flowAction: 'auto_advance', condition: '', ccRoles: [],
      formConfig: null  // 表单配置
    });
    
    const formComponentLib = [
      { type: 'input', label: '单行文本', icon: 'edit', category: '基础' },
      { type: 'textarea', label: '多行文本', icon: 'file-text', category: '基础' },
      { type: 'number', label: '数字', icon: 'hash', category: '基础' },
      { type: 'date', label: '日期', icon: 'calendar', category: '基础' },
      { type: 'select', label: '下拉选择', icon: 'list', category: '基础' },
      { type: 'display', label: '只读显示', icon: 'eye', category: '基础' },
      { type: 'ref_picker', label: '关联选择', icon: 'link', category: '业务' },
      { type: 'customer_picker', label: '客户选择', icon: 'users', category: '业务' },
      { type: 'product_picker', label: '产品选择', icon: 'package', category: '业务' },
      { type: 'employee_picker', label: '员工选择', icon: 'user', category: '业务' },
      { type: 'dept_picker', label: '部门选择', icon: 'building', category: '业务' },
      { type: 'detail_table', label: '明细表格', icon: 'table', category: '高级' },
      { type: 'approval_info', label: '审批信息', icon: 'check-circle', category: '高级' },
      { type: 'print_button', label: '打印按钮', icon: 'printer', category: '高级' },
      { type: 'section', label: '分组标题', icon: 'folder', category: '布局' },
    ];
    const formCategories = ['基础', '业务', '高级', '布局'];
    // 关联选择器数据源(中文名), 供字段编辑选择
    const refSourceLabels = {
      orders: '订单', customers: '客户', opportunities: '商机', products: '产品/物料',
      suppliers: '供应商', work_orders: '加工工单', employees: '员工',
    };
    function getFormComponentsByCategory(cat) {
      return formComponentLib.filter(function(c) { return c.category === cat; });
    }
    
    // 表单字段配置模板
    const formFieldTemplate = () => ({
      key: '',           // 字段key
      label: '',         // 字段标签
      type: 'input',     // 组件类型
      required: false,   // 是否必填
      readonly: false,   // 是否只读
      placeholder: '',   // 占位符
      options: [],       // 选项（select类型）
      columnWidth: 1,    // 列宽（1=100%, 2=50%, 3=33%）
      config: {}         // 额外配置
    });
    
    const nodeFormConfig = reactive({
      title: '',
      showHeader: true,
      fields: []
    });
    const formConfigVis = ref(false);
    
    const formDlg = reactive({
      vis: false,
      editingField: null,
      isNew: true,
      tempField: formFieldTemplate()
    });
    let dragType = '';
    var _lastClickNode = null;

    const bizTypes = [
      {v:'CORE_PRODUCTION',l:'核心生产流'},
      {v:'PROCUREMENT',l:'采购审批流'},
      {v:'EXPENSE',l:'费用报销流'},
      {v:'SALES_ADJUSTMENT',l:'调价审批流'},
      {v:'SAMPLE_REQUEST',l:'打样申请流'},
      {v:'RECEIVING',l:'来货登记流程'},
      {v:'COMPLETION',l:'完工单确认'},
      {v:'PURCHASE_REQUEST',l:'采购请求审批'},
    ];
    const bizCore = [bizTypes[0], bizTypes[1], bizTypes[2], bizTypes[3]];
    const bizOther = [bizTypes[4], bizTypes[5], bizTypes[6]];
    const roles = [
      {v:'DEPARTMENT_HEAD',l:'部门主管'},
      {v:'FINANCE',l:'财务'},
      {v:'OPERATION',l:'运营'},
      {v:'MANAGER',l:'厂长'},
      {v:'GM',l:'总经理'},
      {v:'SALES',l:'销售'},
    ];
    const palTypes = [
      {type:'approve', label:'审批节点', icon:'check', desc:'指定角色审批', ntype:'rect', color:'#8b5cf6'},
      {type:'flow', label:'流转节点', icon:'arrow-right', desc:'自动流转', ntype:'rect', color:'#06b6d4'},
      {type:'cc', label:'抄送节点', icon:'mail', desc:'审批后抄送知会', ntype:'rect', color:'#6366f1'},
    ];

    function typeMeta(type) { return palTypes.find(p=>p.type===type) || palTypes[1]; }
    function getDefLabel(d) {
      return getBizTypeLabel(d.biz_type) + ' - ' + d.name + ' v' + (d.version || 1);
    }
    function getBizTypeLabel(bt) {
      var found = bizTypes.find(function(b) { return b.v === bt; });
      return found ? found.l : bt;
    }

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
          // 允许直接拖动连线端点改接到新节点,无需删线重连
          adjustEdgeStartAndEnd: true,
          edgeSelectedOutline: true,
          hoverOutline: false,
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

        // 首次进入:库里只要有定义,画布自动读取并加载第一条(含表单)
        loadFlowDefs().then(function() {
          if (flowDefs.value.length) {
            if (!flowDefs.value.some(d => d.biz_type === curBizType.value)) {
              curBizType.value = flowDefs.value[0].biz_type;
            }
            const first = flowDefs.value.find(d => d.biz_type === curBizType.value) || flowDefs.value[0];
            const defId = first.id;
            loadedDefId.value = defId;
            onLoadDef(defId, true);
          }
        });
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
        if (t === 'diamond') rawType = 'branch';
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
      dlg.formConfig = props.formConfig || null;
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
      addNode(dragType, meta.label, x, y, 'DEPARTMENT_HEAD');
      dragType = '';
    }

    function onRoleChange(roleCode) {
      const roleCN = {DEPARTMENT_HEAD:'部门主管',FINANCE:'财务',OPERATION:'运营',FACTORY_MANAGER:'厂长',MANAGER:'厂长',GM:'总经理',SALES:'销售'};
      if (!dlg.name || dlg.name === '审批' || dlg.name === '人工审批') {
        dlg.name = (roleCN[roleCode] || '') + '审批';
      }
    }
    function onCcRoleChange(roles) {
      if (!dlg.name || dlg.name === '抄送' || dlg.name === '抄送节点') {
        dlg.name = '抄送';
      }
    }
    
    // 表单配置相关函数
    function openFormConfig() {
      // 从节点properties加载已有表单配置
      if (dlg.id && lf) {
        try {
          const model = lf.getNodeModelById(dlg.id);
          if (model && model.properties && model.properties.formConfig) {
            const saved = model.properties.formConfig;
            nodeFormConfig.title = saved.title || '';
            nodeFormConfig.showHeader = saved.showHeader !== false;
            nodeFormConfig.fields = JSON.parse(JSON.stringify(saved.fields || []));
          } else {
            nodeFormConfig.title = dlg.name + '表单';
            nodeFormConfig.showHeader = true;
            nodeFormConfig.fields = [];
          }
        } catch(_) {}
      } else {
        nodeFormConfig.title = dlg.name + '表单';
        nodeFormConfig.showHeader = true;
        nodeFormConfig.fields = [];
      }
      formConfigVis.value = true;
      dlg.vis = false;
    }
    
    function saveFormConfig() {
      if (dlg.id && lf) {
        try {
          const model = lf.getNodeModelById(dlg.id);
          if (model) {
            model.setProperties({ formConfig: JSON.parse(JSON.stringify(nodeFormConfig)) });
          }
        } catch(_) {}
      }
      dlg.formConfig = JSON.parse(JSON.stringify(nodeFormConfig));
      formConfigVis.value = false;
      ElementPlus.ElMessage.success('表单配置已保存');
    }

    function onFormConfigUpdate(v) {
      if (!v) { /* do nothing, handled by saveFormConfig */ }
    }
    
    function addFormField() {
      formDlg.isNew = true;
      formDlg.editingField = null;
      formDlg.tempField = formFieldTemplate();
      formDlg.tempField.key = 'field_' + Date.now().toString(36);
      formDlg.tempField.options = [{label:'', value:''}];
      formDlg.tempField.config = { columns: [{key:'col_1',label:'',type:'text'}], source: '', fillRows: [{ target: '', source: '' }] };
      formDlg.vis = true;
    }

    function editFormField(index) {
      formDlg.isNew = false;
      formDlg.editingField = index;
      formDlg.tempField = JSON.parse(JSON.stringify(nodeFormConfig.fields[index]));
      if (!formDlg.tempField.options || formDlg.tempField.options.length === 0) {
        formDlg.tempField.options = [{label:'', value:''}];
      }
      if (!formDlg.tempField.config) formDlg.tempField.config = {};
      if (!Array.isArray(formDlg.tempField.config.columns)) {
        formDlg.tempField.config.columns = [{key:'col_1',label:'',type:'text'}];
      }
      // ref_picker: fillMap对象↔可编辑行
      const fm = formDlg.tempField.config.fillMap || {};
      formDlg.tempField.config.fillRows = Object.keys(fm).map(k => ({ target: k, source: fm[k] }));
      if (!formDlg.tempField.config.fillRows.length) {
        formDlg.tempField.config.fillRows = [{ target: '', source: '' }];
      }
      if (!formDlg.tempField.config.source) formDlg.tempField.config.source = '';
      formDlg.vis = true;
    }
    
    function saveFormField() {
      if (!formDlg.tempField.label) {
        ElementPlus.ElMessage.warning('请输入字段标签');
        return;
      }
      if (!formDlg.tempField.key) {
        ElementPlus.ElMessage.warning('请输入字段key');
        return;
      }
      // ref_picker: fillRows行编辑回填为fillMap对象
      if (formDlg.tempField.type === 'ref_picker') {
        const rows = (formDlg.tempField.config && formDlg.tempField.config.fillRows) || [];
        const map = {};
        rows.forEach(r => { if (r.target && r.source) map[r.target] = r.source; });
        formDlg.tempField.config.fillMap = map;
        delete formDlg.tempField.config.fillRows;
      }
      if (formDlg.isNew) {
        nodeFormConfig.fields.push(JSON.parse(JSON.stringify(formDlg.tempField)));
      } else {
        nodeFormConfig.fields[formDlg.editingField] = JSON.parse(JSON.stringify(formDlg.tempField));
      }
      formDlg.vis = false;
    }
    
    function deleteFormField(index) {
      ElementPlus.ElMessageBox.confirm('确定删除该字段？', '提示', { type: 'warning' })
        .then(() => {
          nodeFormConfig.fields.splice(index, 1);
        }).catch(() => {});
    }
    
    function moveFormField(index, direction) {
      const newIndex = index + direction;
      if (newIndex < 0 || newIndex >= nodeFormConfig.fields.length) return;
      const field = nodeFormConfig.fields.splice(index, 1)[0];
      nodeFormConfig.fields.splice(newIndex, 0, field);
    }
    
    function getComponentLabel(type) {
      const comp = formComponentLib.find(c => c.type === type);
      return comp ? comp.label : type;
    }
    
    function hasFormConfig() {
      // 检查节点属性中是否有已保存的表单配置
      if (dlg.formConfig && dlg.formConfig.fields && dlg.formConfig.fields.length > 0) {
        return true;
      }
      // 也检查当前编辑中的表单配置
      return nodeFormConfig.fields && nodeFormConfig.fields.length > 0;
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
        // 保存表单配置以便在类型变更后恢复
        var savedFormConfig = (model && model.properties && model.properties.formConfig) ? model.properties.formConfig : null;
        if (oldType && oldType !== dlg.type) {
          const ox = (model && model.x) || (lfContainer.value.clientWidth / 2);
          const oy = (model && model.y) || (lfContainer.value.clientHeight / 2);
          try { lf.deleteNode(dlg.id); } catch(_) {}
          var newNodeResult = addNode(dlg.type, dlg.name, ox, oy, dlg.type === 'cc' ? dlg.ccRoles : dlg.role);
          var newNodeId = (newNodeResult && newNodeResult.id) ? newNodeResult.id : null;
          // 恢复表单配置到新节点
          if (newNodeId && savedFormConfig) {
            try {
              var newModel = lf.getNodeModelById(newNodeId);
              if (newModel) newModel.setProperties({ formConfig: savedFormConfig });
            } catch(_) {}
          }
        } else {
          try {
            if (model) {
              var newProps = { bizNodeType: dlg.type };
              if (dlg.type === 'approve') newProps.role = dlg.role;
              if (dlg.type === 'flow') newProps.flowAction = dlg.flowAction;
              if (dlg.type === 'branch') newProps.condition = dlg.condition;
              if (dlg.type === 'cc') newProps.ccRoles = dlg.ccRoles;
              // 保留已保存的表单配置
              if (model.properties && model.properties.formConfig) {
                newProps.formConfig = model.properties.formConfig;
              }
              model.setProperties(newProps);
              // 更新节点文本: LogicFlow标准API setNodeText
              try { lf.setNodeText(dlg.id, dlg.name); } catch(_) {
                // fallback: 直接设置model.text
                try { model.text = { value: dlg.name }; } catch(_) {}
              }
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
          sel.nodes.forEach(n => { lf.deleteNode(n.id); });
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
        let all = r.data || [];
        // 如果已选业务类型，只显示该类型的流程定义
        if (curBizType.value) {
          all = all.filter(d => d.biz_type === curBizType.value);
        }
        flowDefs.value = all;
      } catch(_) { flowDefs.value = []; }
    }

    async function onBizTypeChange() {
      loadedDefId.value = null;
      await loadFlowDefs();
      // 库中已有该类型定义时，自动加载第一条到画布（含表单），无需再手动点"加载"
      if (flowDefs.value.length && lf) {
        onLoadDef(flowDefs.value[0].id, true);
        loadedDefId.value = flowDefs.value[0].id;
      }
    }

    function onLoadDef(defId, silent) {
      if (!defId) return;
      const def = flowDefs.value.find(d => d.id === defId);
      if (!def || !lf) return;

      // 自动同步业务类型
      if (def.biz_type && def.biz_type !== curBizType.value) {
        curBizType.value = def.biz_type;
      }

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
          });
        }
        
        // 按seq排序确保顺序正确
        nodes = nodes.slice().sort(function(a, b) { return (a.seq || 0) - (b.seq || 0); });
        
        const startX = 120, startY = 250, gapX = 260, gapY = 120;
        let hasBranch = false;
        
        // 先检测是否有分支节点,并设置默认类型
        nodes.forEach(function(node) {
          // 向后兼容:如果没有type字段,根据approver_role判断,有role则为approve,否则为flow
          if (!node.type || node.type === 'item') {
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
            const roleCN = {DEPARTMENT_HEAD:'部门主管',FINANCE:'财务',OPERATION:'运营',FACTORY_MANAGER:'厂长',MANAGER:'厂长',GM:'总经理',SALES:'销售'};
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
          // 加载表单配置
          if (node.form_config) props.formConfig = node.form_config;

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
        return bt;
      });
      // 按实际连线拓扑排序: 连线决定流程顺序; 未连入的孤立节点按x坐标追加
      const edges = data.edges || [];
      const byId = {};
      bizNodes.forEach(n => { byId[n.id] = n; });
      const outMap = {}, inDeg = {};
      bizNodes.forEach(n => { outMap[n.id] = []; inDeg[n.id] = 0; });
      edges.forEach(e => {
        if (byId[e.sourceNodeId] && byId[e.targetNodeId]) {
          outMap[e.sourceNodeId].push(e.targetNodeId);
          inDeg[e.targetNodeId] += 1;
        }
      });
      // 起点: 入度为0的节点, 取x最小者; 若无则取x最小节点
      const starts = bizNodes.filter(n => inDeg[n.id] === 0)
        .sort((a, b) => (a.x || 0) - (b.x || 0));
      const ordered = [], visited = {};
      function walk(id) {
        if (visited[id] || !byId[id]) return;
        visited[id] = true;
        ordered.push(byId[id]);
        outMap[id]
          .slice()
          .sort((a, b) => (byId[a].x || 0) - (byId[b].x || 0))
          .forEach(walk);
      }
      starts.forEach(s => walk(s.id));
      // 兜底: 环或孤立节点按x坐标追加
      bizNodes.sort((a, b) => (a.x || 0) - (b.x || 0)).forEach(n => walk(n.id));
      return ordered.map((n, idx) => {
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
        // 获取表单配置
        let formConfig = null;
        try {
          if (model && model.properties && model.properties.formConfig) {
            formConfig = model.properties.formConfig;
          } else if (props.formConfig) {
            formConfig = props.formConfig;
          }
        } catch(_) {}
        return {
          seq: idx + 1,
          name: nodeText,
          type: type,
          approver_role: props.role || '',
          flow_action: props.flowAction || '',
          condition: props.condition || null,
          cc_roles: props.ccRoles || [],
          form_config: formConfig  // 表单配置
        };
      });
    }

    async function doSave() {
      if (!curBizType.value) { ElementPlus.ElMessage.warning('请选择业务类型'); return; }
      const data = lf.getGraphData();
      if (!data.nodes || !data.nodes.length) { ElementPlus.ElMessage.warning('请至少添加一个节点'); return; }

      try {
        const nodeList = _buildNodeList();
        const bizName = (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程';
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

      try {
        const nodeList = _buildNodeList();
        const bizName = (bizTypes.find(b=>b.v===curBizType.value)||{}).l||'流程';
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
              addNode(type, meta.label, cx, cy, 'DEPARTMENT_HEAD');
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
      mgmtVis, dlg, bizTypes, bizCore, bizOther, roles, palTypes, Icon,
      onPalDragStart, onRoleChange, onCcRoleChange, saveDlg, delFromDlg,
      doClear, doSave, doSaveAs, doDelete, openMgmt, doMgmtDelete, onBizTypeChange, onLoadDef, typeMeta, getDefLabel, getBizTypeLabel,
      formComponentLib, formCategories, getFormComponentsByCategory, nodeFormConfig, formConfigVis, formDlg,
      openFormConfig, saveFormConfig, addFormField, editFormField, refSourceLabels,
      saveFormField, deleteFormField, moveFormField, getComponentLabel, hasFormConfig,
      onFormConfigUpdate
    };
  }
};

const ScreenPage = {
  template: `
  <div class="screen-page">
    <div class="screen-header">
      <h1>峰业精密 · 车间生产大屏</h1>
      <div class="screen-meta">
        <span>{{currentDate}}</span>
        <span>订单总数: {{stats.totalOrders}}</span>
        <span>本月完工: {{stats.completedOrders}}</span>
        <span>在制工单: {{stats.workingOrders}}</span>
      </div>
    </div>

    <div class="screen-grid">
      <div class="screen-card card-lg">
        <div class="card-title">各车间产量分布（本月）</div>
        <div class="card-body" v-if="workshopData.length">
          <div v-for="item in workshopData" :key="item.workshop" class="ws-item">
            <div class="ws-name">{{item.workshop}}</div>
            <div class="ws-bar-wrap">
              <div class="ws-bar" :style="{width: item.percent + '%', backgroundColor: item.color}"></div>
            </div>
            <div class="ws-num">{{item.count}} 单</div>
          </div>
        </div>
        <div class="card-body empty" v-else>暂无数据</div>
      </div>

      <div class="screen-card card-md">
        <div class="card-title">订单状态分布</div>
        <div class="card-body" v-if="orderStats.length">
          <div v-for="s in orderStats" :key="s.status" class="os-item">
            <span class="os-label">{{s.label}}</span>
            <span class="os-count">{{s.count}}</span>
          </div>
        </div>
        <div class="card-body empty" v-else>暂无数据</div>
      </div>

      <div class="screen-card card-md">
        <div class="card-title">经营快报</div>
        <div class="card-body">
          <div class="kp-item">
            <span class="kp-label">今日订单</span>
            <span class="kp-value">{{kpi.today_orders || 0}}</span>
          </div>
          <div class="kp-item">
            <span class="kp-label">本月销售额</span>
            <span class="kp-value">{{kpi.month_sales || '¥0'}}</span>
          </div>
          <div class="kp-item">
            <span class="kp-label">待审批</span>
            <span class="kp-value">{{kpi.pending_ap || 0}}</span>
          </div>
          <div class="kp-item">
            <span class="kp-label">低库存告警</span>
            <span class="kp-value">{{kpi.low_stock || 0}}</span>
          </div>
        </div>
      </div>

      <div class="screen-card card-lg">
        <div class="card-title">最近进行中工单</div>
        <div class="card-body" v-if="recentWorkOrders.length">
          <table class="screen-table">
            <thead>
              <tr><th>工单编号</th><th>来源订单</th><th>状态</th><th>下达时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="wo in recentWorkOrders" :key="wo.id">
                <td>{{wo.wo_no}}</td>
                <td>{{wo.order_no}}</td>
                <td><el-tag :type="wo.status === 'RELEASED' ? 'warning' : 'success'">{{statusLabel(wo.status)}}</el-tag></td>
                <td>{{formatTime(wo.released_at)}}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card-body empty" v-else>暂无进行中工单</div>
      </div>
    </div>
  </div>
  `,
  setup() {
    const { ref, onMounted, computed } = Vue;
    const stats = ref({totalOrders: 0, completedOrders: 0, workingOrders: 0});
    const workshopData = ref([]);
    const orderStats = ref([]);
    const recentWorkOrders = ref([]);
    const kpi = ref({});
    const colors = ['#10b981', '#3b82f6', '#8b5cf6', '#f59e0b', '#ef4444', '#06b6d4'];

    const currentDate = computed(() => {
      const d = new Date();
      return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
    });

    const statusLabel = (status) => {
      const labels = {DRAFT: '草稿', RELEASED: '已下达', COMPLETED: '已完工'};
      return labels[status] || status;
    };

    const formatTime = (t) => {
      if (!t) return '-';
      return t.slice(0, 10);
    };

    async function loadData() {
      try {
        const r = await api.get('/api/workbench');
        const d = r.data.data || {};
        kpi.value = d.kpis || {};
        stats.value = {
          totalOrders: d.kpis?.today_orders || 0,
          completedOrders: d.kpis?.completion_count || 0,
          workingOrders: d.kpis?.work_order_count || 0
        };
      } catch (e) {
        console.error('加载大屏数据失败', e);
      }
    }

    onMounted(() => {
      loadData();
    });

    return { currentDate, stats, workshopData, orderStats, recentWorkOrders, kpi, statusLabel, formatTime };
  }
};

// 打样申请
const SampleRequestPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('beaker', 22)"></div>
        <div>
          <div class="ph-title">打样申请</div>
          <div class="ph-sub">客户打样申请登记与审批</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>+ 打样申请</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-input v-model="q.keyword" placeholder="单号/工件名称" style="width:200px" clearable @keyup.enter="load"/>
      <el-select v-model="q.status" placeholder="状态" style="width:140px" clearable>
        <el-option label="全部" value=""/>
        <el-option label="草稿" value="DRAFT"/>
        <el-option label="审批中" value="PENDING"/>
        <el-option label="已通过" value="APPROVED"/>
        <el-option label="已驳回" value="REJECTED"/>
      </el-select>
      <el-button @click="load">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card" @click="openDetail(row)">
        <div :class="'doc-bar '+statusCls(row.status)"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.log_no}}</span>
            <span class="pill" :class="statusCls(row.status)">{{statusLabel(row.status)}}</span>
            <span class="doc-cust">{{row.customer_name}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">工件名称</span><span class="df-value">{{row.part_name||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">数量</span><span class="df-value">{{row.qty||0}}</span></div>
            <div class="doc-field"><span class="df-label">打样原因</span><span class="df-value">{{row.sample_reason||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">期望完成</span><span class="df-value">{{row.expected_date||'-'}}</span></div>
          </div>
          <flow-mini biz-type="SAMPLE_REQUEST" :biz-id="row.id"/>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox', 56)"></div>
        <div class="de-title">暂无数据</div>
        <div class="de-desc">点击右上方按钮创建打样申请</div>
      </div>
    </div>

    <el-pagination v-if="total>pageSize" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev,pager,next,total" @current-change="load"/>

    <el-dialog v-model="createDlg.show" :title="createDlg.edit?'编辑打样申请':'新建打样申请'" width="820px" top="3vh">
      <el-form :model="createDlg.data" label-width="110px" label-position="top">
      <NodeFormView
        v-if="formConfig && formConfig.fields && formConfig.fields.length"
        ref="formRef"
        :formConfig="formConfig"
        mode="create"
      />
      <el-empty v-else description="未配置打样申请表单，请到【流程设计】为SAMPLE_REQUEST配置表单字段"/>
      </el-form>
      <template #footer>
        <el-button @click="createDlg.show=false">取消</el-button>
        <el-button type="primary" @click="submit" :loading="saving">提交申请</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detail.show" :title="'打样申请详情'" size="620px">
      <div class="detail-hero" v-if="detail.data">
        <div class="dh-row"><span class="dh-no">{{detail.data.log_no}}</span><span class="pill" :class="statusCls(detail.data.status)">{{statusLabel(detail.data.status)}}</span></div>
        <div class="dh-divider"></div>
        <div class="dh-grid">
          <div class="dh-item"><span class="dh-label">客户名称</span><span>{{detail.data.customer_name}}</span></div>
          <div class="dh-item"><span class="dh-label">联系人</span><span>{{detail.data.contact_person||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">联系电话</span><span>{{detail.data.contact_phone||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">电子邮箱</span><span>{{detail.data.email||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">公司地址</span><span>{{detail.data.company_address||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">打样原因</span><span>{{detail.data.sample_reason||'-'}}{{detail.data.sample_reason_other?'('+detail.data.sample_reason_other+')':''}}</span></div>
          <div class="dh-item"><span class="dh-label">工件名称</span><span>{{detail.data.part_name||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">工件材质</span><span>{{detail.data.material||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">工件尺寸</span><span>{{detail.data.size_desc||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">数量</span><span>{{detail.data.qty||0}}</span></div>
          <div class="dh-item"><span class="dh-label">样品提供方式</span><span>{{detail.data.sample_provided_by||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">期望完成</span><span>{{detail.data.expected_date||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">喷涂工艺</span><span>{{detail.data.spray_process||'-'}}{{detail.data.spray_process_other?'('+detail.data.spray_process_other+')':''}}</span></div>
          <div class="dh-item"><span class="dh-label">涂层材料</span><span>{{detail.data.coating_material||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">涂层厚度</span><span>{{detail.data.coating_thickness||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">硬度要求</span><span>{{detail.data.hardness_req||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">结合强度</span><span>{{detail.data.bond_strength_req||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">表面粗糙度</span><span>{{detail.data.surface_roughness_req||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">其他性能要求</span><span>{{detail.data.other_performance_req||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">图纸资料</span><span>{{detail.data.drawings||'-'}}{{detail.data.drawings_other?'('+detail.data.drawings_other+')':''}}</span></div>
          <div class="dh-item"><span class="dh-label">是否收费</span><span>{{detail.data.is_charged||'-'}}</span></div>
          <div class="dh-item"><span class="dh-label">预计费用</span><span>{{detail.data.estimated_cost||0}}</span></div>
          <div class="dh-item"><span class="dh-label">费用说明</span><span>{{detail.data.cost_remark||'-'}}</span></div>
          <div class="dh-item" style="grid-column:1/-1"><span class="dh-label">备注</span><span>{{detail.data.remark||'-'}}</span></div>
        </div>
        <flow-mini v-if="detail.data.approval_instance_id" :instance-id="detail.data.approval_instance_id" biz-type="SAMPLE_REQUEST" :biz-id="detail.data.id"/>
      </div>
    </el-drawer>
  </div>`,
  data() {
    return {
      rows: [], total: 0, page: 1, pageSize: 20, loading: false,
      q: {keyword: '', status: ''},
      createDlg: {show: false, data: {}, edit: false},
      detail: {show: false, data: null},
      saving: false, customers: [],
      formConfig: null, formRef: null,
    };
  },
  mounted() {
    this.load();
    this.loadCustomers();
  },
  components: { FlowMini, NodeFormView },
  methods: {
    async loadFormConfig() {
      try {
        const d = await api.get('/api/approvals/definitions?biz_type=SAMPLE_REQUEST');
        this.formConfig = (d.data && d.data.length && d.data[0].nodes && d.data[0].nodes.length) ? (d.data[0].nodes[0].form_config || null) : null;
      } catch(e) { console.warn('[打样] 加载画布表单失败', e.message || e); this.formConfig = null; }
    },
    async load() {
      this.loading = true;
      try {
        const params = new URLSearchParams();
        params.set('page', this.page); params.set('size', this.pageSize);
        if (this.q.keyword) params.set('keyword', this.q.keyword);
        if (this.q.status) params.set('status', this.q.status);
        const r = await api.get('/api/sample-requests?' + params.toString());
        this.rows = r.data || [];
        this.total = r.total || 0;
      } catch(e) {ElMessage.error('加载失败: '+e.message)}
      finally {this.loading = false}
    },
    reset() { this.q = {keyword:'',status:''}; this.page=1; this.load(); },
    async loadCustomers() {
      try { const r = await api.get('/api/customers?size=999'); this.customers = r.data || []; }
      catch(e) {}
    },
    openCreate() {
      this.createDlg.data = {customer_id:null, qty:0, estimated_cost:0};
      this.createDlg.show = true;
      this.loadFormConfig();
    },
    onSampleReasonChange(v) { if (v !== '其他') this.createDlg.data.sample_reason_other = ''; },
    onSprayProcessChange(v) { if (v !== '其他') this.createDlg.data.spray_process_other = ''; },
    onDrawingsChange(v) { if (v !== '其他') this.createDlg.data.drawings_other = ''; },
    async submit() {
      this.saving = true;
      try {
        if (this.formRef && !this.formRef.validate()) { ElMessage.warning('请完善画布表单必填项'); this.saving=false; return; }
        const fd = this.formRef && this.formRef.getFormData ? this.formRef.getFormData() : this.createDlg.data;
        await api.post('/api/sample-requests', fd);
        ElMessage.success('打样申请已提交');
        this.createDlg.show = false;
        this.load();
      } catch(e) {ElMessage.error('提交失败: '+e.message)}
      finally {this.saving = false}
    },
    openDetail(row) {
      api.get('/api/sample-requests/'+row.id).then(r => {
        this.detail.data = r.data;
        this.detail.show = true;
      }).catch(e => ElMessage.error('加载详情失败'));
    },
    statusCls(s) { return {DRAFT:'gray', PENDING:'orange', APPROVED:'green', REJECTED:'red'}[s]||'gray'; },
    statusLabel(s) { return {DRAFT:'草稿', PENDING:'审批中', APPROVED:'已通过', REJECTED:'已驳回'}[s]||s; },
  }
};

// 费用报销
const ExpensePage = makeListPage({
  title: '费用报销',
  sub: '公司日常费用报销走审批',
  apiUrl: '/api/expenses',
  createUrl: '/api/expenses',
  detailUrl: r => `/api/expenses/${r.id}`,
  createLabel: '+ 新建报销',
  icon: 'receipt',
  card: {
    statusMap: {DRAFT: '草稿', PENDING: '审批中', APPROVED: '已通过', REJECTED: '已驳回'},
    fields: [
      {key: 'claim_no', label: '报销单号'},
      {key: 'applicant_name', label: '申请人'},
      {key: 'amount', label: '总金额', fmt: 'money'},
      {key: 'claim_type', label: '类型'},
      {key: 'status', label: '状态'},
    ],
    actions: [
      {key: 'submit', label: '提交审批', type: 'primary', show: r => r.status === 'DRAFT'},
    ],
  },
  query: {status: 'select'},
  queryPlaceholders: {status: '状态'},
  queryOptions: {status: [
    {v: '', l: '全部'}, {v: 'DRAFT', l: '草稿'}, {v: 'PENDING', l: '审批中'},
    {v: 'APPROVED', l: '已通过'}, {v: 'REJECTED', l: '已驳回'},
  ]},
  bizType: 'EXPENSE',
  formConfigBlType: 'EXPENSE',
  formFields: [
    {key: 'claim_type', label: '报销类型', type: 'select', w: 240, options: [
      {v: 'TRAVEL', l: '差旅'}, {v: 'MEAL', l: '餐饮'}, {v: 'OFFICE', l: '办公用品'},
      {v: 'TRANSPORT', l: '交通'}, {v: 'OTHER', l: '其他'},
    ]},
    {key: 'description', label: '事由说明', type: 'textarea', rows: 2},
  ],
  extraCreateSection: `
    <el-form-item label="发票凭证">
      <div style="width:100%">
        <el-upload :http-request="customUpload" :show-file-list="false" accept="image/jpeg,image/png,image/webp,application/pdf">
          <el-button type="primary" plain><span v-html="Icon.icon('cloud-arrow-up',14)" style="vertical-align:middle;margin-right:4px"></span>上传凭证(扫描/拍照/PDF)</el-button>
        </el-upload>
        <div v-if="attForm.list.length" style="margin-top:10px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px">
          <div v-for="(att,i) in attForm.list" :key="att.aid" style="border:1px solid #e5e7eb;border-radius:8px;padding:8px;background:#fafafa">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
              <a :href="att.url" target="_blank" style="display:flex;align-items:center;gap:4px;color:#3b82f6;font-size:13px">
                <span v-html="Icon.icon(att.mime && att.mime.indexOf('pdf')>=0 ? 'document-text' : 'photo', 16)"></span>
                <span style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">{{att.filename}}</span>
              </a>
              <span v-if="att.risk_flag==='DUPLICATE'" class="pill UNPAID">重复</span>
              <span v-else-if="att.risk_flag==='MISSING_NO'" class="pill OPEN">未填号</span>
              <el-button size="small" link type="danger" @click="attForm.list.splice(i,1)" style="margin-left:auto"><span v-html="Icon.icon('x-mark',12)"></span></el-button>
            </div>
            <div style="font-size:12px;color:#475569;line-height:1.6">
              <el-input v-model="att.invoice_no" placeholder="发票号(查重)" size="small" style="margin-bottom:4px"/>
              <el-input v-model="att.invoice_amount" placeholder="金额" size="small" style="margin-bottom:4px"/>
              <el-input v-model="att.invoice_date" placeholder="开票日 YYYY-MM-DD" size="small" style="margin-bottom:4px"/>
              <el-input v-model="att.issuer" placeholder="开票方" size="small"/>
              <div v-if="att.risk_reason" style="color:#ef4444;margin-top:4px">{{att.risk_reason}}</div>
            </div>
          </div>
        </div>
        <div v-if="!attForm.list.length" style="color:#94a3b8;font-size:13px;margin-top:6px">可上传多张发票/收据照片或PDF,每张需填发票号供查重</div>
      </div>
    </el-form-item>`,
  setupExtra({ load, detail }) {
    const attForm = reactive({ list: [] });
    async function customUpload(opt) {
      const file = opt.file;
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await api.post('/api/expenses/tmp-attachment', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
        attForm.list.push(r.data);
        ElMessage.success('已上传,请补全发票号等元数据');
      } catch(e) { ElMessage.error(e.message); }
    }
    async function beforeSubmit(body) {
      // 把临时上传的附件数组塞进 body
      if (attForm.list.length) {
        const form_data = body.form_data || body;
        form_data.attachments = attForm.list.map(a => ({
          aid: a.aid, filename: a.filename, url: a.url, mime: a.mime, size: a.size,
          invoice_no: a.invoice_no || '', invoice_code: a.invoice_code || '',
          invoice_amount: a.invoice_amount || null, invoice_date: a.invoice_date || '',
          issuer: a.issuer || '', uploaded_at: a.uploaded_at,
        }));
        body.form_data = form_data;
      }
      return body;
    }
    function afterSubmit() {
      attForm.list = [];
    }
    // 详情抽屉的附件操作(查看/删除)
    const canEditAtt = computed(() => detail.data && ['DRAFT', 'REJECTED', 'SUBMITTED'].includes(detail.data.status));
    async function delAtt(att) {
      try {
        await api.delete(`/api/expenses/${detail.data.id}/attachments/${att.aid}`);
        ElMessage.success('已删除');
        load();
      } catch(e) { ElMessage.error(e.message); }
    }
    async function editAttMeta(att) {
      // 简化: 直接弹输入框改发票号
      try {
        const { value } = await ElMessageBox.prompt('发票号', '编辑元数据', { inputValue: att.invoice_no || '' });
        await api.put(`/api/expenses/${detail.data.id}/attachments/${att.aid}`, { invoice_no: value });
        ElMessage.success('已更新');
        load();
      } catch(e) {}
    }
    return { attForm, customUpload, beforeSubmit, afterSubmit, canEditAtt, delAtt, editAttMeta };
  }
});

// 借款申请
const LoanRequestPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('banknotes',22)"></div>
        <div>
          <div class="ph-title">借款申请</div>
          <div class="ph-sub">备用金/周转金 · 财务支付自动生成资金流水+凭证</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>+ 新建借款申请</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select v-model="query.status" placeholder="全部状态" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in LOAN_STATUS" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <div class="grow"></div>
    </div>

    <div class="doc-list" :class="{loading}" v-loading="loading">
      <div v-for="row in rows" :key="row.id" class="doc-card">
        <div :class="'doc-bar '+statusCls(row.status)"></div>
        <div class="doc-main">
          <div class="doc-top">
            <span class="doc-no">{{row.loan_no}}</span>
            <span class="pill" :class="statusCls(row.status)">{{row.status_label}}</span>
            <span class="doc-cust">{{row.applicant_name}}</span>
            <span class="doc-amount">¥{{fmt(row.amount)}}</span>
          </div>
          <div class="doc-fields">
            <div class="doc-field"><span class="df-label">类型</span><span class="df-value">{{row.loan_type_label||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">借款账户</span><span class="df-value">{{row.fund_account_name||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">预计还款</span><span class="df-value">{{row.expected_return_date||'-'}}</span></div>
            <div class="doc-field"><span class="df-label">申请日</span><span class="df-value">{{fmtDateShort(row.created_at)}}</span></div>
            <div class="doc-field"><span class="df-label">用途</span><span class="df-value">{{row.purpose||'-'}}</span></div>
          </div>
          <flow-mini v-if="row.approval_instance_id" biz-type="LOAN" :biz-id="row.id"/>
        </div>
        <div class="doc-actions" @click.stop v-if="isFinance">
          <el-button v-if="row.status==='APPROVED'" size="small" type="primary" @click="doPay(row)">支付</el-button>
          <el-button v-if="row.status==='PAID'" size="small" type="success" @click="doClear(row)">核销</el-button>
        </div>
      </div>
      <div v-if="!loading && !rows.length" class="doc-empty">
        <div v-html="Icon.icon('inbox', 56)"></div>
        <div class="de-title">暂无借款申请</div>
        <div class="de-desc">点击右上方按钮创建第一条借款申请</div>
      </div>
    </div>

    <el-pagination v-if="total>query.size" style="margin-top:14px;justify-content:flex-end;display:flex" background :current-page="query.page" :page-size="query.size" :total="total" layout="prev,pager,next,total" @current-change="p=>{query.page=p;load()}"/>

    <el-dialog v-model="createDlg.visible" title="新建借款申请" width="520px">
      <el-form :model="createDlg.form" label-width="100px">
        <el-form-item label="借款类型" required>
          <el-select v-model="createDlg.form.loan_type" style="width:100%" placeholder="选择类型">
            <el-option v-for="(l,v) in LOAN_TYPE" :key="v" :label="l" :value="v"/>
          </el-select>
        </el-form-item>
        <el-form-item label="金额" required>
          <el-input-number v-model="createDlg.form.amount" :min="0.01" :precision="2" style="width:100%"/>
        </el-form-item>
        <el-form-item label="借款账户">
          <el-select v-model="createDlg.form.fund_account_id" style="width:100%" placeholder="选择出账账户" clearable>
            <el-option v-for="a in fundAccounts" :key="a.id" :label="a.name+' (余额 ¥'+fmt(a.balance)+')'" :value="a.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="预计还款日">
          <el-date-picker v-model="createDlg.form.expected_return_date" type="date" value-format="YYYY-MM-DD" style="width:100%"/>
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="createDlg.form.department" placeholder="可选"/>
        </el-form-item>
        <el-form-item label="借款用途">
          <el-input v-model="createDlg.form.purpose" type="textarea" :rows="2" placeholder="简述借款用途"/>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createDlg.form.remark" type="textarea" :rows="2"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg.visible=false">取消</el-button>
        <el-button type="primary" :loading="createDlg.saving" @click="submitCreate">提交申请</el-button>
      </template>
    </el-dialog>
  </div>`,
  components: { FlowMini },
  setup() {
    const rows = ref([]); const total = ref(0); const loading = ref(false);
    const query = reactive({ status: '', page: 1, size: 20 });
    const fundAccounts = ref([]);
    const createDlg = reactive({ visible: false, saving: false, form: {} });
    const user = JSON.parse(localStorage.getItem(USER_KEY) || '{}');
    const isFinance = ['FINANCE', 'GM', 'ADMIN'].includes(user.role);
    const LOAN_STATUS = {SUBMITTED:'审批中', APPROVED:'待支付', REJECTED:'已驳回', PAID:'已支付', CLEARED:'已核销'};
    const LOAN_TYPE = {PETTY_CASH:'备用金', TURN_OVER:'周转金'};
    const fmt = n => Number(n||0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const fmtDateShort = s => s ? new Date(s).toLocaleDateString('zh-CN') : '-';
    function statusCls(s) { return {SUBMITTED:'orange', APPROVED:'blue', REJECTED:'red', PAID:'green', CLEARED:'gray'}[s]||'gray'; }

    async function loadFundAccounts() {
      try { const r = await api.get('/api/finance/fund-accounts'); fundAccounts.value = r.data || []; }
      catch(e) {}
    }
    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/loans?' + new URLSearchParams({ status: query.status||'', page: query.page, size: query.size }));
        rows.value = (r.data && r.data.items) || []; total.value = (r.data && r.data.total) || 0;
      } catch(e) {}
      loading.value = false;
    }
    function search() { query.page = 1; load(); }
    function openCreate() {
      createDlg.form = { loan_type:'PETTY_CASH', amount: null, fund_account_id: null, expected_return_date: '', department: '', purpose: '', remark: '' };
      createDlg.visible = true;
      if (!fundAccounts.value.length) loadFundAccounts();
    }
    async function submitCreate() {
      const f = createDlg.form;
      if (!f.loan_type || !f.amount) { ElMessage.warning('借款类型/金额必填'); return; }
      createDlg.saving = true;
      try {
        await api.post('/api/loans', f);
        ElMessage.success('借款申请已提交');
        createDlg.visible = false; load();
      } catch(e) {}
      createDlg.saving = false;
    }
    async function doPay(row) {
      try {
        await ElMessageBox.confirm(`确认支付借款 ${row.loan_no} ¥${fmt(row.amount)}? 将从 ${row.fund_account_name||'-'} 出账并生成凭证`, '支付确认', { type: 'warning' });
        const r = await api.post('/api/loans/'+row.id+'/pay');
        ElMessage.success('已支付, 凭证号 ' + r.data.voucher_no);
        load();
      } catch(e) {}
    }
    async function doClear(row) {
      try {
        await ElMessageBox.confirm(`确认核销借款 ${row.loan_no}? 表示员工已归还`, '核销确认', { type: 'warning' });
        await api.post('/api/loans/'+row.id+'/clear');
        ElMessage.success('已核销');
        load();
      } catch(e) {}
    }

    onMounted(load);
    return { rows, total, loading, query, fundAccounts, createDlg, isFinance, LOAN_STATUS, LOAN_TYPE, fmt, fmtDateShort, statusCls, load, search, openCreate, submitCreate, doPay, doClear, Icon };
  }
};

// 采购申请列表
const PurchaseRequestsPage = makeListPage({
  title: '采购申请',
  sub: '提交采购需求走审批流程',
  apiUrl: '/api/purchase-requests',
  createLabel: '+ 采购申请',
  icon: 'file-text',
  card: {
    statusMap: {DRAFT: '草稿', PENDING: '审批中', APPROVED: '已批准', REJECTED: '已驳回'},
    fields: [
      {key: 'req_no', label: '申请单号'},
      {key: 'total_amount', label: '估算金额'},
      {key: 'status', label: '状态'},
      {key: 'reason', label: '申请原因'},
    ],
    actions: [
      {key: 'submit', label: '提交审批', type: 'primary', show: r => r.status === 'DRAFT'},
    ],
    subTable: { title: '物料明细', itemsKey: 'items' },
  },
  bizType: 'PURCHASE_REQUEST',
  formConfigBlType: 'PURCHASE_REQUEST', // 创建表单渲染画布设计, 零硬编码
  dialogWidth: '720px',
  formFields: [
    {key: 'reason', label: '申请理由', type: 'textarea', rows: 2},
  ],
});

// 应收管理
const ReceivablesPage = makeListPage({
  title: '应收管理',
  sub: '客户应收账款与收款管理',
  apiUrl: '/api/finance/docs',
  createLabel: '+ 登记收款',
  icon: 'wallet',
  card: {
    statusMap: {UNPAID: '未收款', PARTIAL: '部分收款', PAID: '已收款', OVERDUE: '逾期'},
    fields: [
      {key: 'doc_no', label: '单据号'},
      {key: 'customer_name', label: '客户'},
      {key: 'amount', label: '金额'},
      {key: 'paid_amount', label: '已收'},
      {key: 'due_date', label: '到期日'},
      {key: 'status', label: '状态'},
    ],
  },
  query: {type: 'select', status: 'select'},
  queryPlaceholders: {type: '类型', status: '状态'},
  queryOptions: {
    type: [{v: '', l: '全部'}, {v: 'RECEIVABLE', l: '应收'}, {v: 'PAYABLE', l: '应付'}],
    status: [{v: '', l: '全部'}, {v: 'UNPAID', l: '未收款'}, {v: 'PARTIAL', l: '部分'}, {v: 'PAID', l: '已收'}],
  },
  formFields: [
    {key: 'doc_type', label: '单据类型', type: 'select', options: [
      {v: 'RECEIVABLE', l: '应收'}, {v: 'PAYABLE', l: '应付'},
    ]},
    {key: 'customer_id', label: '客户ID', type: 'number', w: 160},
    {key: 'amount', label: '金额', type: 'number', precision: 2, w: 180},
    {key: 'due_date', label: '到期日', type: 'text', ph: 'YYYY-MM-DD', w: 140},
    {key: 'remark', label: '备注', type: 'text', w: 280},
  ],
});

// 出入库流水
const StockMovesPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('arrow-swap',22)"></div>
        <div>
          <div class="ph-title">出入库流水</div>
          <div class="ph-sub">所有库存变动记录 · 领料/收货/出货/盘点 自动入账,只读不填</div>
        </div>
      </div>
    </div>

    <div class="kpi-row" style="margin:16px 24px 0;display:grid;grid-template-columns:repeat(4,1fr);gap:14px">
      <div class="kpi-card" style="background:linear-gradient(135deg,#e6f9f1,#fff)">
        <div class="kpi-label">期间入库金额</div>
        <div class="kpi-value pos">¥{{fmt(summary.in_amount||0)}}</div>
      </div>
      <div class="kpi-card" style="background:linear-gradient(135deg,#ffe9ec,#fff)">
        <div class="kpi-label">期间出库金额</div>
        <div class="kpi-value neg">¥{{fmt(summary.out_amount||0)}}</div>
      </div>
      <div class="kpi-card" style="background:linear-gradient(135deg,#eef2ff,#fff)">
        <div class="kpi-label">净变动</div>
        <div class="kpi-value" :class="(summary.net_amount||0)>=0?'pos':'neg'">{{(summary.net_amount||0)>=0?'+':''}}¥{{fmt(summary.net_amount||0)}}</div>
      </div>
      <div class="kpi-card" style="background:linear-gradient(135deg,#fff7e6,#fff)">
        <div class="kpi-label">流水条数</div>
        <div class="kpi-value">{{summary.txn_count||0}}</div>
      </div>
    </div>

    <div class="filter-bar">
      <el-date-picker v-model="query.daterange" type="daterange" value-format="YYYY-MM-DD" range-separator="至"
        start-placeholder="起始日期" end-placeholder="结束日期" style="width:260px" @change="search"/>
      <el-select v-model="query.txn_type" placeholder="变动类型" style="width:140px" clearable @change="search">
        <el-option v-for="(l,v) in TYPE_OPT" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-select v-model="query.ref_doc_type" placeholder="来源单据" style="width:150px" clearable @change="search">
        <el-option v-for="(l,v) in REF_OPT" :key="v" :label="l" :value="v"/>
      </el-select>
      <el-input v-model="query.keyword" placeholder="物料名称搜索" style="width:200px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)" style="color:#94a3b8;margin-right:4px"></span></template>
      </el-input>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <div v-loading="loading" style="margin:0 24px 24px">
      <el-table :data="rows" size="small" border stripe>
        <el-table-column label="流水号" prop="txn_no" width="180"/>
        <el-table-column label="类型" width="80" align="center">
          <template #default="{row}">
            <span class="pill" :class="row.txn_type==='IN'?'SETTLED':row.txn_type==='OUT'?'UNPAID':'OPEN'">{{row.txn_type_label}}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源单据" width="110">
          <template #default="{row}">{{row.ref_doc_type_label}}</template>
        </el-table-column>
        <el-table-column label="物料编码" prop="item_code" width="130"/>
        <el-table-column label="物料名" prop="item_name" min-width="180"/>
        <el-table-column label="数量" width="100" align="right">
          <template #default="{row}">
            <span :class="row.txn_type==='IN'?'pos':row.txn_type==='OUT'?'neg':''">{{row.txn_type==='OUT'?'-':'+'}}{{fmt(row.qty)}}{{row.unit}}</span>
          </template>
        </el-table-column>
        <el-table-column label="单价" width="100" align="right">
          <template #default="{row}">¥{{fmt(row.unit_cost)}}</template>
        </el-table-column>
        <el-table-column label="金额" width="120" align="right">
          <template #default="{row}"><b>{{row.txn_type==='OUT'?'-':'+'}}¥{{fmt(row.amount)}}</b></template>
        </el-table-column>
        <el-table-column label="仓库" prop="warehouse" width="90"/>
        <el-table-column label="关联单号" width="150">
          <template #default="{row}">
            <span v-if="row.ref_doc_id">#{{row.ref_doc_type_label}}-{{row.ref_doc_id}}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="发生时间" width="160">
          <template #default="{row}">{{fmtDate(row.occurred_at)}}</template>
        </el-table-column>
        <el-table-column label="备注" prop="remark" min-width="140"/>
      </el-table>
      <el-pagination v-if="total>page.size" style="margin-top:14px;justify-content:flex-end;display:flex" background v-model:current-page="page.page" :page-size="page.size" :total="total" layout="prev,pager,next,total" @current-change="load"/>
    </div>
  </div>`,
  setup() {
    const rows = ref([]);
    const loading = ref(false);
    const total = ref(0);
    const summary = reactive({in_amount:0, out_amount:0, net_amount:0, txn_count:0});
    const TYPE_OPT = {IN:'入库', OUT:'出库', RETURN:'退库', ADJUST:'调整'};
    const REF_OPT = {REQUISITION:'领料单', COMPLETION:'完工入库', PURCHASE:'采购收货', SHIPMENT:'销售出货', MANUAL:'手工登记', STOCK_CHECK:'盘点调账', RETURN_MAT:'退料入库', RETURN_GOODS:'销售退货', SAMPLE:'打样出库', OUTSOURCE:'外协出库'};
    const query = reactive({daterange:'', txn_type:'', ref_doc_type:'', keyword:''});
    const page = reactive({page:1, size:50});
    const fmt = n => Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:2});
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    async function load() {
      loading.value = true;
      try {
        const params = new URLSearchParams();
        params.append('page', page.page); params.append('size', page.size);
        if (query.txn_type) params.append('txn_type', query.txn_type);
        if (query.ref_doc_type) params.append('ref_doc_type', query.ref_doc_type);
        if (query.keyword) params.append('keyword', query.keyword);
        if (query.daterange && query.daterange[0]) params.append('date_from', query.daterange[0]);
        if (query.daterange && query.daterange[1]) params.append('date_to', query.daterange[1]);
        const r = await api.get('/api/inventory/txns?' + params.toString());
        rows.value = r.data?.data || [];
        total.value = r.data?.total || 0;
        Object.assign(summary, r.data?.summary || {});
      } catch(e) { ElMessage.error(e.message); }
      finally { loading.value = false; }
    }
    function search() { page.page = 1; load(); }
    function reset() { Object.assign(query, {daterange:'',txn_type:'',ref_doc_type:'',keyword:''}); page.page = 1; load(); }
    onMounted(load);
    return { rows, loading, total, summary, TYPE_OPT, REF_OPT, query, page, fmt, fmtDate, load, search, reset, Icon };
  }
};

// ============ 客供料台账 ============
const ConsignLogPage = {
  template: `
  <div class="page">
    <div class="page-head">
      <div class="ph-left">
        <div class="ph-icon" v-html="Icon.icon('inbox-stack',22)"></div>
        <div>
          <div class="ph-title">客供料台账</div>
          <div class="ph-sub">客户来料收发耗用记录 · 不计入自有库存账</div>
        </div>
      </div>
      <div class="ph-actions">
        <el-button type="primary" @click="openCreate"><span v-html="Icon.icon('plus',14)" style="vertical-align:middle;margin-right:4px"></span>登记收料</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-input v-model="query.keyword" placeholder="件名搜索" style="width:200px" clearable @keyup.enter="search">
        <template #prefix><span v-html="Icon.icon('search',14)" style="color:#94a3b8;margin-right:4px"></span></template>
      </el-input>
      <el-select v-model="query.status" placeholder="状态" style="width:140px" clearable @change="search">
        <el-option label="已收料" value="RECEIVED"/>
        <el-option label="已消耗" value="CONSUMED"/>
        <el-option label="已退回" value="RETURNED"/>
      </el-select>
      <el-button @click="search">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <div v-loading="loading" style="margin:0 24px 24px">
      <el-table :data="rows" size="small" border stripe>
        <el-table-column label="订单号" prop="order_no" width="160"/>
        <el-table-column label="客户" prop="customer_name" min-width="160"/>
        <el-table-column label="件名" prop="part_name" min-width="160"/>
        <el-table-column label="规格" prop="part_spec" min-width="160"/>
        <el-table-column label="收料数" prop="received_qty" width="100" align="right"/>
        <el-table-column label="已消耗" prop="consumed_qty" width="100" align="right"/>
        <el-table-column label="已退回" prop="returned_qty" width="100" align="right"/>
        <el-table-column label="在制库存" width="110" align="right">
          <template #default="{row}"><b :class="row.stock_qty>0?'pos':''">{{fmt(row.stock_qty)}}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{row}">
            <span class="pill" :class="row.status==='CONSUMED'?'SETTLED':row.status==='RETURNED'?'DRAFT':'OPEN'">{{STATUS_LABEL[row.status]||row.status}}</span>
          </template>
        </el-table-column>
        <el-table-column label="收料时间" width="150">
          <template #default="{row}">{{fmtDate(row.received_at)}}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{row}">
            <el-button v-if="row.stock_qty>0.0005" size="small" type="primary" link @click="openMove(row,'consume')">登记消耗</el-button>
            <el-button v-if="row.stock_qty>0.0005" size="small" type="warning" link @click="openMove(row,'return')">登记退回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog.visible" title="登记收料" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="关联订单" required>
          <el-select v-model="form.order_id" filterable placeholder="选择订单(自动带出客户)" style="width:100%" @change="onOrderChange">
            <el-option v-for="o in orders" :key="o.id" :label="o.order_no+' · '+o.customer_name" :value="o.id"/>
          </el-select>
        </el-form-item>
        <el-form-item label="客户">
          <el-input v-model="form.customer_name" disabled style="width:100%"/>
        </el-form-item>
        <el-form-item label="件名" required>
          <el-input v-model="form.part_name" placeholder="如：齿轮轴" style="width:100%"/>
        </el-form-item>
        <el-form-item label="规格">
          <el-input v-model="form.part_spec" placeholder="如：Φ85-A*8.7" style="width:100%"/>
        </el-form-item>
        <el-form-item label="收料数" required>
          <el-input-number v-model="form.received_qty" :min="0" :precision="3" style="width:200px"/>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible=false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="moveDialog.visible" :title="moveDialog.title" width="420px">
      <el-form :model="moveDialog.form" label-width="100px">
        <el-form-item label="件名">
          <el-input :model-value="moveDialog.row?.part_name" disabled/>
        </el-form-item>
        <el-form-item label="可操作量">
          <el-input :model-value="fmt(moveDialog.row?.stock_qty)" disabled/>
        </el-form-item>
        <el-form-item label="本次数量" required>
          <el-input-number v-model="moveDialog.form.qty" :min="0" :precision="3" :max="moveDialog.row?.stock_qty||0" style="width:200px"/>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="moveDialog.form.remark" type="textarea" :rows="2"/>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moveDialog.visible=false">取消</el-button>
        <el-button :type="moveDialog.action==='consume'?'primary':'warning'" @click="submitMove">确认</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const rows = ref([]);
    const loading = ref(false);
    const orders = ref([]);
    const STATUS_LABEL = {RECEIVED:'已收料', CONSUMED:'已消耗', RETURNED:'已退回'};
    const query = reactive({keyword:'', status:''});
    const dialog = reactive({visible:false});
    const form = reactive({order_id:'', customer_name:'', customer_id:'', part_name:'', part_spec:'', received_qty:0, remark:''});
    const moveDialog = reactive({visible:false, title:'', action:'consume', row:null, form:{qty:0, remark:''}});
    const fmt = n => Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:3});
    const fmtDate = s => s ? new Date(s).toLocaleString('zh-CN') : '-';
    async function load() {
      loading.value = true;
      try {
        const params = new URLSearchParams();
        if (query.keyword) params.append('keyword', query.keyword);
        if (query.status) params.append('status', query.status);
        const r = await api.get('/api/inventory/consign-log?' + params.toString());
        rows.value = r.data || [];
      } catch(e) { ElMessage.error(e.message); }
      finally { loading.value = false; }
    }
    function search() { load(); }
    function reset() { Object.assign(query, {keyword:'',status:''}); load(); }
    async function loadOrders() {
      try {
        const r = await api.get('/api/orders?page=1&size=200');
        orders.value = (r.data?.data || r.data || []).filter(o => o.status !== 'CANCELED');
      } catch(e) {}
    }
    function openCreate() {
      Object.assign(form, {order_id:'', customer_name:'', customer_id:'', part_name:'', part_spec:'', received_qty:0, remark:''});
      if (!orders.value.length) loadOrders();
      dialog.visible = true;
    }
    function onOrderChange(oid) {
      const o = orders.value.find(x => x.id === oid);
      if (o) { form.customer_name = o.customer_name; form.customer_id = o.customer_id; }
    }
    async function submit() {
      if (!form.order_id) { ElMessage.warning('请选择订单'); return; }
      if (!form.part_name) { ElMessage.warning('请填写件名'); return; }
      if (form.received_qty <= 0) { ElMessage.warning('收料数必须大于0'); return; }
      try {
        await api.post('/api/inventory/consign-log', {
          order_id: form.order_id, customer_id: form.customer_id || null,
          part_name: form.part_name, part_spec: form.part_spec,
          received_qty: form.received_qty, remark: form.remark,
        });
        ElMessage.success('已登记收料');
        dialog.visible = false;
        load();
      } catch(e) { ElMessage.error(e.message); }
    }
    function openMove(row, action) {
      moveDialog.row = row;
      moveDialog.action = action;
      moveDialog.title = action === 'consume' ? '登记消耗' : '登记退回客户';
      moveDialog.form = {qty: 0, remark: ''};
      moveDialog.visible = true;
    }
    async function submitMove() {
      if (moveDialog.form.qty <= 0) { ElMessage.warning('数量必须大于0'); return; }
      if (moveDialog.form.qty > (moveDialog.row?.stock_qty || 0) + 0.0005) {
        ElMessage.warning('数量超过可操作量'); return;
      }
      try {
        const url = `/api/inventory/consign-log/${moveDialog.row.id}/${moveDialog.action}`;
        await api.post(url, {qty: moveDialog.form.qty, remark: moveDialog.form.remark});
        ElMessage.success('操作成功');
        moveDialog.visible = false;
        load();
      } catch(e) { ElMessage.error(e.message); }
    }
    onMounted(() => { load(); loadOrders(); });
    return { rows, loading, orders, STATUS_LABEL, query, dialog, form, moveDialog, fmt, fmtDate, load, search, reset, openCreate, onOrderChange, submit, openMove, submitMove, Icon };
  }
};

// 经营分析仪表盘(多Tab: KPI看板 + AI提问)
const AnalysisPage = {
  template: `
  <div class="page-container analysis-page">
    <div class="page-header">
      <h2>{{ isFinanceEntry ? '财务AI助手' : '经营分析' }}</h2>
      <div class="analysis-tabs">
        <div v-if="!isFinanceEntry" :class="['analysis-tab', {active: activeTab==='kpi'}]" @click="switchTab('kpi')">📊 KPI看板</div>
        <div :class="['analysis-tab', {active: activeTab==='ai'}]" @click="switchTab('ai')">🤖 AI分析</div>
      </div>
    </div>

    <!-- Tab1: KPI看板 -->
    <div v-if="activeTab==='kpi'" class="analysis-grid" v-loading="loading">
      <div class="analysis-card kpi-card">
        <div class="card-title">📊 经营KPI</div>
        <div class="kpi-grid" v-if="kpi">
          <div class="kpi-item"><div class="kpi-value">¥{{fmt(kpi.revenue)}}</div><div class="kpi-label">总营收</div></div>
          <div class="kpi-item"><div class="kpi-value">¥{{fmt(kpi.cost)}}</div><div class="kpi-label">总成本</div></div>
          <div class="kpi-item"><div class="kpi-value" :class="{neg: kpi.profit < 0}">¥{{fmt(kpi.profit)}}</div><div class="kpi-label">利润</div></div>
          <div class="kpi-item"><div class="kpi-value">{{kpi.gross_margin_pct}}%</div><div class="kpi-label">毛利率</div></div>
          <div class="kpi-item"><div class="kpi-value">¥{{fmt(kpi.ar_balance)}}</div><div class="kpi-label">应收余额</div></div>
          <div class="kpi-item"><div class="kpi-value">¥{{fmt(kpi.ap_balance)}}</div><div class="kpi-label">应付余额</div></div>
          <div class="kpi-item"><div class="kpi-value">¥{{fmt(kpi.inventory_value)}}</div><div class="kpi-label">库存价值</div></div>
          <div class="kpi-item"><div class="kpi-value">{{kpi.order_count}}</div><div class="kpi-label">订单数</div></div>
        </div>
      </div>
      <div class="analysis-card">
        <div class="card-title">💰 应收账龄分析</div>
        <div v-if="aging && Object.keys(aging).length" class="aging-list">
          <div v-for="(items, bucket) in aging" :key="bucket" class="aging-bucket">
            <div class="aging-header"><span>{{bucket}}</span><span class="aging-total">¥{{fmt(agingTotals[bucket]||0)}}</span><span class="aging-count">{{(items||[]).length}}笔</span></div>
            <div class="aging-bar-wrap"><div class="aging-bar" :style="{width:(agingTotals[bucket]/agingMax*100)+'%'}"></div></div>
          </div>
        </div>
        <div v-else class="empty-state">暂无应收数据</div>
      </div>
      <div class="analysis-card">
        <div class="card-title">⚠️ 预警监控</div>
        <div v-if="alerts.length" class="alert-log-list">
          <div v-for="a in alerts" :key="a.id" class="alert-log-item">
            <span class="alert-rule">{{a.rule_code}}</span><span class="alert-msg">{{a.message}}</span><span class="alert-time">{{fmtTime(a.created_at)}}</span>
          </div>
        </div>
        <div v-else class="empty-state">暂无预警</div>
      </div>
      <div class="analysis-card">
        <div class="card-title">📈 成本结构</div>
        <div v-if="costBreakdown && Object.keys(costBreakdown).length" class="cost-list">
          <div v-for="(amt,type) in costBreakdown" :key="type" class="cost-item">
            <span class="cost-type">{{costTypeLabel(type)}}</span>
            <div class="cost-bar-wrap"><div class="cost-bar" :style="{width:(amt/totalCost*100)+'%'}"></div></div>
            <span class="cost-amt">¥{{fmt(amt)}}</span>
          </div>
        </div>
        <div v-else class="empty-state">暂无成本数据</div>
      </div>
      <div class="analysis-card pivot-card">
        <div class="card-title">🔍 多维度透视分析</div>
        <div class="pivot-controls">
          <el-select v-model="pivot.dataset" placeholder="选择数据源" style="width:150px" @change="loadDatasets">
            <el-option v-for="d in datasets" :key="d.key" :label="d.label" :value="d.key"/>
          </el-select>
          <el-select v-model="pivot.rows_dim" placeholder="行维度" style="width:130px">
            <el-option-group label="分类维度">
              <el-option v-for="f in currentDataset?.dims" :key="f.key" :label="f.label" :value="f.key"/>
            </el-option-group>
            <el-option-group label="时间维度">
              <el-option v-for="f in currentDataset?.time_dims" :key="f.key" :label="f.label" :value="f.key"/>
            </el-option-group>
          </el-select>
          <el-select v-model="pivot.cols_dim" placeholder="列维度(可选)" style="width:130px">
            <el-option label="(无)" value=""></el-option>
            <el-option-group label="分类维度">
              <el-option v-for="f in currentDataset?.dims" :key="f.key" :label="f.label" :value="f.key"/>
            </el-option-group>
            <el-option-group label="时间维度">
              <el-option v-for="f in currentDataset?.time_dims" :key="f.key" :label="f.label" :value="f.key"/>
            </el-option-group>
          </el-select>
          <el-select v-model="pivot.metric" placeholder="指标" style="width:110px">
            <el-option v-for="f in currentDataset?.metrics" :key="f.key" :label="f.label" :value="f.key"/>
          </el-select>
          <el-select v-model="pivot.agg" placeholder="聚合" style="width:80px">
            <el-option v-for="a in currentDataset?.aggs" :key="a.key" :label="a.label" :value="a.key"/>
          </el-select>
          <el-select v-model="pivot.chart_type" placeholder="图形" style="width:90px">
            <el-option label="自动" value="auto"/>
            <el-option label="柱状" value="bar"/>
            <el-option label="折线" value="line"/>
            <el-option label="饼图" value="pie"/>
          </el-select>
          <el-button type="primary" @click="runPivot" :loading="loadingPivot">分析</el-button>
        </div>
        <!-- 筛选器 -->
        <div v-if="currentDataset?.filter_fields?.length || currentDataset?.dims?.length" class="pivot-filters">
          <span class="filter-label">筛选:</span>
          <el-select v-model="newFilterField" placeholder="选择筛选字段" style="width:140px" size="small" @change="onFilterFieldChange">
            <el-option-group v-if="currentDataset?.dims?.length" label="维度字段(可按名称筛选)">
              <el-option v-for="d in currentDataset.dims" :key="'dim_'+d.key" :label="d.label" :value="d.key"/>
            </el-option-group>
            <el-option-group v-if="currentDataset?.filter_fields?.length" label="数据字段">
              <el-option v-for="f in currentDataset.filter_fields" :key="'ff_'+f.key" :label="f.label" :value="f.key"/>
            </el-option-group>
          </el-select>
          <!-- 操作符 -->
          <el-select v-model="newFilterOp" placeholder="操作符" style="width:80px" size="small">
            <el-option label="等于" value="eq"/>
            <el-option label="不等于" value="ne"/>
            <el-option label="包含" value="contains"/>
            <el-option label="大于" value="gt"/>
            <el-option label="小于" value="lt"/>
            <el-option label="范围" value="between"/>
          </el-select>
          <!-- 值输入: 根据字段类型动态切换 -->
          <div v-if="newFilterValueIsEnum" class="filter-value-wrap">
            <el-select v-model="newFilterValue" placeholder="选择值" style="width:160px" size="small" filterable>
              <el-option v-for="opt in newFilterEnumOptions" :key="opt.value" :label="opt.label" :value="opt.value"/>
            </el-select>
          </div>
          <div v-else-if="newFilterValueIsDate" class="filter-value-wrap">
            <el-date-picker v-model="newFilterValue" type="date" placeholder="选择日期" size="small" style="width:140px" value-format="YYYY-MM-DD"/>
          </div>
          <div v-else-if="newFilterValueIsDateRange" class="filter-value-wrap">
            <el-date-picker v-model="newFilterValue" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" size="small" style="width:260px" value-format="YYYY-MM-DD"/>
          </div>
          <div v-else-if="newFilterValueIsNumber" class="filter-value-wrap">
            <el-input-number v-model="newFilterValue" placeholder="输入数值" size="small" style="width:130px"/>
          </div>
          <div v-else class="filter-value-wrap">
            <el-input v-model="newFilterValue" placeholder="输入筛选值" style="width:140px" size="small" @keyup.enter="addFilter"/>
          </div>
          <el-button size="small" type="primary" @click="addFilter">+添加</el-button>
          <div v-if="pivot.filters.length" class="active-filters">
            <el-tag v-for="(f,idx) in pivot.filters" :key="idx" closable @close="removeFilter(idx)" size="small" type="info">
              {{getFilterLabel(f.field)}} {{getOpLabel(f.op)}} {{formatFilterVal(f)}}
            </el-tag>
            <el-button size="small" text @click="clearFilters">清除</el-button>
          </div>
        </div>
        <!-- 汇总卡片 -->
        <div v-if="pivotResult?.summary" class="pivot-summary">
          <span class="summary-item">合计: <b>{{fmtMoney(pivotResult.summary.total)}}</b></span>
          <span class="summary-item">分组数: <b>{{pivotResult.summary.count}}</b></span>
        </div>
        <!-- 当前筛选条件展示 -->
        <div v-if="pivot.filters.length" class="pivot-filter-summary">
          <span class="filter-summary-label">📋 查询条件:</span>
          <el-tag v-for="(f, idx) in pivot.filters" :key="'fs_'+idx" size="default" type="warning" effect="plain" style="margin-right:6px">
            {{getFilterLabel(f.field)}} {{getOpLabel(f.op)}} {{formatFilterVal(f)}}
          </el-tag>
          <span class="filter-summary-dataset" v-if="pivot.dataset">数据源: {{currentDataset?.label || pivot.dataset}}</span>
          <span class="filter-summary-dim" v-if="pivot.rows_dim">维度: {{getFilterLabel(pivot.rows_dim)}}</span>
          <span class="filter-summary-metric" v-if="pivot.metric">指标: {{getMetricLabel(pivot.metric)}} / {{getAggLabel(pivot.agg)}}</span>
        </div>
        <div v-if="pivotResult" class="pivot-result" :class="{ 'has-chart': !!pivotResult.chart }">
          <table class="pivot-table" v-if="pivotResult.table">
            <thead>
              <tr>
                <th>{{pivotResult.rows_label || pivotResult.rows_dim}}</th>
                <!-- extra_dims列 -->
                <th v-for="ed in pivotResult.extra_dims" :key="'eh_'+ed.index">{{ed.label}}</th>
                <th v-for="col in pivotResult.col_keys" :key="col">{{col === '__total__' ? '合计' : col}}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in pivotResult.table" :key="row.dim" class="drill-row" @click="drillDown(row.dim)">
                <td class="dim-cell">{{row.dim}} <span class="drill-hint">📊</span></td>
                <!-- extra_dims列 -->
                <td v-for="ed in pivotResult.extra_dims" :key="'ed_'+ed.index">{{row['extra_' + ed.index]}}</td>
                <td v-for="col in pivotResult.col_keys" :key="col">{{fmtVal(row[col], pivotResult.metric_type)}}</td>
              </tr>
              <tr class="total-row" v-if="pivotResult.col_keys.length > 1">
                <td class="dim-cell">合计</td>
                <td v-for="ed in pivotResult.extra_dims" :key="'et_'+ed.index">-</td>
                <td v-for="col in pivotResult.col_keys" :key="col">{{fmtVal(colTotal(pivotResult.table, col), pivotResult.metric_type)}}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="pivotResult.chart && pivotResult.chart.x.length" class="pivot-chart">
            <div :ref="el => setChartRef(el)" class="echart-container"></div>
          </div>
        </div>
        <div v-else-if="pivotResult && pivotResult.error" class="empty-state">{{pivotResult.error}}</div>
      </div>
      <div class="analysis-card">
        <div class="card-title">🏭 生产统计</div>
        <div class="prod-stats">
          <div class="prod-item"><div class="prod-value">{{kpi?.order_count||0}}</div><div class="prod-label">订单总数</div></div>
          <div class="prod-item"><div class="prod-value">{{kpi?.work_order_count||0}}</div><div class="prod-label">工单总数</div></div>
          <div class="prod-item"><div class="prod-value">{{kpi?.completion_count||0}}</div><div class="prod-label">完工确认</div></div>
        </div>
      </div>
    </div>

    <!-- 下钻对话框 -->
    <el-dialog v-model="drillVisible" :title="drillTitle" width="80%" top="5vh" destroy-on-close>
      <div class="drill-summary" v-if="drillRows.length">共 {{drillRows.length}} 条记录</div>
      <div class="drill-table-wrapper">
        <table class="pivot-table drill-table" v-if="drillRows.length">
          <thead>
            <tr>
              <th v-for="col in drillColumns" :key="col.key">{{col.label}}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in drillRows" :key="idx">
              <td v-for="col in drillColumns" :key="col.key">{{fmtVal(row[col.key], col.type)}}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">加载中...</div>
      </div>
    </el-dialog>

    <!-- Tab2: AI分析 -->
    <div v-if="activeTab==='ai'" class="ai-analysis-container ai-with-sidebar">
      <div class="ai-sidebar">
        <div class="ai-sidebar-header">
          <span>对话历史</span>
          <el-button size="small" type="primary" text @click="createConv">+ 新对话</el-button>
        </div>
        <div class="ai-conv-list">
          <div v-for="c in convList" :key="c.id"
            :class="['ai-conv-item', {active: currentConvId===c.id}]"
            @click="selectConv(c.id)">
            <span class="ai-conv-title">{{c.title || '新对话'}}</span>
            <span class="ai-conv-count">{{c.message_count || 0}}</span>
            <el-icon class="ai-conv-del" @click.stop="deleteConv(c.id)"><Close /></el-icon>
          </div>
          <div v-if="convList.length===0" class="ai-conv-empty">暂无对话</div>
        </div>
      </div>
      <div class="ai-main">
        <div class="ai-scope-bar">
          <div class="ai-scope-title">{{ isFinanceEntry ? '财务专职助手' : 'AI 助手' }}</div>
          <div v-if="!isFinanceEntry" class="ai-scope-tabs">
            <span :class="['ai-scope-tab', {active: aiScope==='analysis'}]" @click="switchAiScope('analysis')">经营分析</span>
            <span :class="['ai-scope-tab', {active: aiScope==='finance'}]" @click="switchAiScope('finance')">财务助手</span>
          </div>
          <div class="ai-scope-desc">{{ aiScope==='finance' ? '财务/税务问题诊断·漏洞发现·建议方案' : '业务数据多维分析·趋势洞察' }}</div>
        </div>
        <div class="chat-area" ref="chatContainer">
          <div v-for="(msg, idx) in messages" :key="msg.key || msg.id || idx" :class="'chat-message ' + msg.role">
            <!-- 用户消息: 无气泡, 右对齐 -->
            <div v-if="msg.role==='user'" class="ai-user">
              <span class="ai-user-tag">你</span>
              <div class="ai-user-text">{{ msg.text }}</div>
            </div>
            <!-- AI消息 -->
            <template v-else>
              <!-- 1. 查询条件卡片(透明化) -->
              <div v-if="msg.plan" class="ai-plan">
                <div class="ai-plan-title"><span v-html="Icon.icon('layers',14)"></span><span>查询条件</span></div>
                <div class="ai-plan-grid">
                  <div class="ai-plan-item"><span>数据源</span>{{ msg.plan.dataset_label || msg.plan.dataset }}</div>
                  <div class="ai-plan-item"><span>行维度</span>{{ msg.plan.rows_label || msg.plan.rows_dim }}</div>
                  <div class="ai-plan-item" v-if="msg.plan.cols_dim"><span>列维度</span>{{ msg.plan.cols_label || msg.plan.cols_dim }}</div>
                  <div class="ai-plan-item"><span>指标</span>{{ msg.plan.metric_label || msg.plan.metric }}</div>
                  <div class="ai-plan-item"><span>聚合</span>{{ msg.plan.agg_label || msg.plan.agg }}</div>
                  <div class="ai-plan-item" v-if="msg.plan.filters && msg.plan.filters.length"><span>筛选</span>{{ planFiltersText(msg.plan.filters) }}</div>
                </div>
              </div>
              <!-- 2. thinking (默认展开, 不截断) -->
              <div v-if="msg.thinking" class="ai-thinking" :class="{open: !thinkingCollapsed[msg.key]}">
                <div class="ai-thinking-head" @click="thinkingCollapsed[msg.key] = !thinkingCollapsed[msg.key]">
                  <span v-html="Icon.icon('brain',14)"></span>
                  <span>思考过程</span>
                  <el-icon class="ai-thinking-arrow"><ArrowDown v-if="!thinkingCollapsed[msg.key]"/><ArrowUp v-else/></el-icon>
                </div>
                <div v-if="!thinkingCollapsed[msg.key]" class="ai-thinking-body">
                  <span>{{ msg.thinking }}</span>
                </div>
              </div>
              <!-- 3. 回答(无气泡) -->
              <div class="ai-reply">
                <div v-html="formattedReply(msg.text)"></div>
                <span v-if="msg.streaming" class="ai-streaming"></span>
                <span v-if="msg.stageMsg && msg.streaming && !msg.text" class="ai-stage-inline">{{ msg.stageMsg }}</span>
              </div>
              <!-- 3-1. 单任务透视图表 (仅pivot类型,根据推荐决定是否显示) -->
              <div v-if="msg.type === 'pivot' && msg.result && msg.result.chart && !msg.result._overview_results && (!msg.result.chart_recommend || msg.result.chart_recommend.show !== false)" class="ai-result-chart">
                <div class="ai-chart-title">{{ msg.result.alias || msg.result.dataset_label || '分析图表' }}</div>
                <div :id="'ai-chart-' + msg.key" class="ai-chart-canvas"></div>
              </div>
              <!-- 3-2. 综合分析多图表 (仅pivot类型,根据推荐决定是否显示) -->
              <div v-if="msg.type === 'pivot' && msg.result && msg.result._overview_results && msg.result._overview_results.length" class="ai-result-charts">
                <template v-for="(ov, oi) in msg.result._overview_results" :key="ov.alias+'_'+oi">
                  <div v-if="ov.chart && (!ov.chart_recommend || ov.chart_recommend.show !== false)" class="ai-result-chart">
                    <div class="ai-chart-title">{{ ov.alias || ov.dataset || '分析项' }}</div>
                    <div :id="'ai-chart-' + msg.key + '-' + oi" class="ai-chart-canvas"></div>
                  </div>
                </template>
              </div>
              <!-- 4. 联网搜索引用 -->
              <div v-if="msg.webResults && msg.webResults.length" class="ai-web-refs">
                <div class="ai-web-refs-head">🌐 网络来源</div>
                <div class="ai-web-refs-list">
                  <a v-for="(r,i) in msg.webResults" :key="i" :href="r.url" target="_blank" class="ai-web-ref-item" :title="r.snippet">
                    <span class="ai-web-ref-num">{{ i+1 }}</span>
                    <span class="ai-web-ref-title">{{ r.title }}</span>
                  </a>
                </div>
              </div>
            </template>
          </div>
          <div v-if="messages.length === 0 && !aiLoading" class="ai-empty">
            <div class="ai-empty-hint">
              <span v-html="Icon.icon('sparkles', 48)"></span>
              <h3>AI 智能助手</h3>
              <p>支持数据分析查询，也可以自然对话，AI 会自动判断意图</p>
              <div class="examples">
                <div class="example-tag" @click="quickExample('经营概况')">📊 经营概况</div>
                <div class="example-tag" @click="quickExample('本月各车间产量对比')">🏭 车间产量对比</div>
                <div class="example-tag" @click="quickExample('哪家客户欠款最多')">💰 欠款最多客户</div>
                <div class="example-tag" @click="quickExample('你好')">👋 打个招呼</div>
                <div class="example-tag" @click="quickExample('帮我分析一下这个月的销售情况')">📈 销售分析</div>
              </div>
            </div>
          </div>
        </div>
        <div class="input-area">
          <el-input v-model="question" type="textarea" :rows="3" placeholder="可以问我业务数据，也可以自然对话...（Enter 发送，Shift+Enter 换行）" @keydown.enter.exact.prevent="sendQuestion"/>
          <div class="input-actions">
            <el-button @click="clearHistory">清空</el-button>
            <el-button v-if="aiLoading" type="danger" @click="stopGeneration">暂停</el-button>
            <el-button v-else type="primary" @click="sendQuestion">发送</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
  `,
  setup() {
    const { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } = Vue;
    // 财务AI助手入口(ai-finance): 仅渲染财务助手, 不暴露经营KPI/经营助手
    const isFinanceEntry = (location.hash || '').indexOf('ai-finance') >= 0;
    const activeTab = ref(isFinanceEntry ? 'ai' : 'kpi');
    const Icon = window.Icon;

    // === KPI Tab ===
    const loading = ref(true);
    const kpi = ref(null);
    const aging = ref({});
    const alerts = ref([]);
    const costBreakdown = ref({});
    const datasets = ref([]);
    const currentDataset = ref(null);
    const pivot = reactive({ dataset: '', rows_dim: '', cols_dim: '', metric: '', agg: 'sum', chart_type: 'auto', filters: [] });
    const pivotResult = ref(null);
    const activeFilters = ref([]);
    const newFilterField = ref('');
    const newFilterOp = ref('eq');
    const newFilterValue = ref('');
    const drillVisible = ref(false);
    const drillTitle = ref('');
    const drillColumns = ref([]);
    const drillRows = ref([]);
    const loadingPivot = ref(false);
    const chartEl = ref(null);
    let chartInstance = null;
    const aiCharts = new Map(); // id -> echarts实例

    function renderAiChartById(id, chart, tableData, attempt) {
      attempt = attempt || 0;
      const el = document.getElementById(id);
      if (!el) {
        if (attempt < 30) setTimeout(function() { renderAiChartById(id, chart, tableData, attempt + 1); }, 100);
        return;
      }
      if (!window.echarts) {
        if (attempt < 30) setTimeout(function() { renderAiChartById(id, chart, tableData, attempt + 1); }, 200);
        return;
      }
      if (el.clientWidth === 0 || el.clientHeight === 0) {
        if (attempt < 30) setTimeout(function() { renderAiChartById(id, chart, tableData, attempt + 1); }, 100);
        return;
      }
      var old = aiCharts.get(id);
      if (old) { try { old.dispose(); } catch(e) {} }
      var inst;
      try {
        inst = window.echarts.init(el);
      } catch(e) {
        if (attempt < 30) setTimeout(function() { renderAiChartById(id, chart, tableData, attempt + 1); }, 150);
        return;
      }
      aiCharts.set(id, inst);

      var ct = (chart && chart.chart_type) || 'bar';
      var mlabel = (chart && chart.metric_label) || '';
      var xData = (chart && chart.x) || [];
      var seriesData = (chart && chart.series) || [];
      var td = tableData || (chart && chart.table) || [];

      var finalSeries = seriesData;
      if (!finalSeries.length && td.length) {
        var sample = td[0] || {};
        var numericKeys = Object.keys(sample).filter(function(k) { return k !== 'dim' && typeof sample[k] === 'number'; });
        finalSeries = numericKeys.map(function(k) {
          return { name: k, data: td.map(function(r) { return r[k] || 0; }) };
        });
        if (!xData.length) xData = td.map(function(r) { return r.dim || ''; });
      }
      if (!finalSeries.length && xData.length) {
        finalSeries = [{ name: mlabel || '值', data: xData.map(function() { return 0; }) }];
      }
      if (!xData.length) {
        xData = ['暂无数据'];
        finalSeries = [{ name: mlabel || '值', data: [0] }];
      }

      var base = { tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid: { left: 60, right: 20, top: 40, bottom: 30 } };
      if (ct === 'pie') {
        var pd = xData.map(function(n, i) { return { name: n, value: (finalSeries[0] && finalSeries[0].data || [])[i] || 0 }; });
        inst.setOption({
          tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
          legend: { orient: 'vertical', right: '5%', top: 'center' },
          series: [{ type: 'pie', radius: ['40%', '70%'], itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 }, label: { show: true, formatter: '{b}\n{c}' }, data: pd }]
        }, true);
      } else {
        var isLine = ct === 'line';
        inst.setOption({
          tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid: { left: 60, right: 20, top: 40, bottom: 30 },
          xAxis: { type: 'category', data: xData, axisLabel: { rotate: xData.length > 6 ? 30 : 0 } },
          yAxis: { type: 'value', name: mlabel },
          series: finalSeries.map(function(s) {
            return {
              name: s.name || mlabel || '值',
              type: isLine ? 'line' : 'bar',
              data: s.data,
              smooth: isLine,
              itemStyle: isLine ? {} : { borderRadius: [4, 4, 0, 0] },
              areaStyle: isLine ? { opacity: 0.3 } : undefined
            };
          })
        }, true);
      }
      inst.resize();
    }
    function setChartRef(el) {
      if (!el) {
        // DOM元素被销毁时，清理chartInstance
        if (chartInstance) {
          chartInstance.dispose();
          chartInstance = null;
        }
        chartEl.value = null;
        return;
      }
      chartEl.value = el;
      // 如果chartInstance已存在但DOM元素变了，需要重新初始化
      if (chartInstance) {
        chartInstance.dispose();
        chartInstance = null;
      }
      if (!chartInstance) chartInstance = window.echarts.init(el);
      renderChart();
    }
    function renderChart() {
      const pr = pivotResult.value;
      if (!pr || !pr.chart || !pr.chart.x.length) return;
      if (!window.echarts) return;
      const el = chartEl.value;
      if (!el) return;
      if (!chartInstance) chartInstance = window.echarts.init(el);
      const cfg = pr.chart;
      const chartType = cfg.chart_type;
      const metricLabel = pr.metric_label || '';

      // 构建筛选条件摘要
      let filterSubtext = '';
      if (pivot.filters.length) {
        filterSubtext = pivot.filters.map(f => {
          const label = getFilterLabel(f.field);
          const op = getOpLabel(f.op);
          const val = formatFilterVal(f);
          return `${label} ${op} ${val}`;
        }).join(' | ');
      }

      const titleConfig = {
        title: {
          text: filterSubtext ? '筛选: ' + filterSubtext : '',
          left: 'center',
          top: 5,
          textStyle: { fontSize: 12, color: '#e0e0e0', fontWeight: 'normal' }
        }
      };

      const baseOption = {
        tooltip: { trigger: 'axis' },
        legend: { top: filterSubtext ? 30 : 0 },
        grid: { left: 60, right: 20, top: filterSubtext ? 70 : 40, bottom: 30 },
        ...titleConfig
      };

      if (chartType === 'pie') {
        const pieData = cfg.x.map((name, i) => ({
          name, value: cfg.series[0].data[i]
        }));
        chartInstance.setOption({
          ...titleConfig,
          tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
          legend: { orient: 'vertical', right: '5%', top: 'center' },
          series: [{
            type: 'pie',
            radius: ['40%', '70%'],
            avoidLabelOverlap: false,
            itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
            label: { show: true, formatter: '{b}\n¥{c}' },
            data: pieData,
          }]
        }, true);
      } else if (chartType === 'bar' && cfg.series.length > 1) {
        chartInstance.setOption({
          ...baseOption,
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          xAxis: { type: 'category', data: cfg.x, axisLabel: { rotate: cfg.x.length > 6 ? 30 : 0 } },
          yAxis: { type: 'value', name: metricLabel },
          series: cfg.series.map(s => ({
            name: s.name,
            type: 'bar',
            stack: 'total',
            data: s.data,
            itemStyle: { borderRadius: [4, 4, 0, 0] },
          }))
        }, true);
      } else {
        const isLine = chartType === 'line';
        chartInstance.setOption({
          ...baseOption,
          xAxis: { type: 'category', data: cfg.x, axisLabel: { rotate: cfg.x.length > 6 ? 30 : 0 } },
          yAxis: { type: 'value', name: metricLabel },
          series: cfg.series.map(s => ({
            name: s.name,
            type: isLine ? 'line' : 'bar',
            data: s.data,
            smooth: isLine,
            itemStyle: isLine ? {} : { borderRadius: [4, 4, 0, 0] },
            areaStyle: isLine ? { opacity: 0.3 } : undefined,
          }))
        }, true);
      }
    }
    const fmt = n => Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:2});
    const fmtTime = t => t ? new Date(t).toLocaleString('zh-CN') : '-';
    const costTypeLabel = t => ({MATERIAL:'材料费',LABOR:'人工费',OVERHEAD:'制造费',OTHER:'其他'}[t]||t);
    const totalCost = computed(() => Object.values(costBreakdown.value||{}).reduce((s,v)=>s+Number(v||0),0));
    const agingTotals = computed(() => { const r={}; for(const [k,v] of Object.entries(aging.value||{})) r[k]=(v||[]).reduce((s,x)=>s+Number(x.balance||0),0); return r; });
    const agingMax = computed(() => Math.max(...Object.values(agingTotals.value), 1));

    async function load() {
      loading.value = true;
      try {
        const [kpiR,agingR,alertR,dsR] = await Promise.all([
          api.get('/api/analysis/kpi'),
          api.get('/api/analysis/receivable-aging'),
          api.get('/api/analysis/alert-logs'),
          api.get('/api/analysis/datasets'),
        ]);
        kpi.value = kpiR.data;
        costBreakdown.value = kpiR.data?.cost_breakdown || {};
        aging.value = agingR.data?.buckets || {};
        alerts.value = alertR.data?.slice(0,10) || [];
        // 将后端返回的字典 {orders:{label,dims,...},...} 转为 [{key,label,dims,...},...]
        const rawDatasets = dsR.data || {};
        datasets.value = Object.keys(rawDatasets).map(k => ({ key: k, ...rawDatasets[k] }));
        // 默认选第一个数据源
        if (datasets.value.length) {
          pivot.dataset = datasets.value[0].key;
          loadDatasets();
        }
      } catch(e) { console.error('加载分析数据失败',e); }
      finally { loading.value = false; }
    }
    function loadDatasets() {
      currentDataset.value = datasets.value.find(d=>d.key===pivot.dataset);
      if (currentDataset.value) {
        if (!pivot.rows_dim) pivot.rows_dim = currentDataset.value.dims?.[0]?.key || '';
        if (!pivot.metric) pivot.metric = currentDataset.value.metrics?.[0]?.key || '';
      }
      pivot.filters = [];
      newFilterField.value = '';
      newFilterValue.value = '';
      pivotResult.value = null;
    }
    function addFilter() {
      try {
        if (!newFilterField.value || newFilterValue.value === '' || newFilterValue.value == null) return;
        let val = newFilterValue.value;
        let op = newFilterOp.value;
        
        // 处理值 - 确保提取正确的值
        if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
          if (val.value !== undefined) {
            val = val.value;
          } else if (val.id !== undefined) {
            val = val.id;
          }
        }
        
        // 如果是范围操作符，确保值是数组
        if (op === 'between' && !Array.isArray(val)) {
          if (typeof val === 'string' && val.includes(',')) {
            val = val.split(',').map(s => s.trim());
          } else {
            val = [val, val];
          }
        }
        
        pivot.filters.push({field: newFilterField.value, op: op, value: val});
        newFilterField.value = '';
        newFilterValue.value = '';
        newFilterOp.value = 'eq';
      } catch(e) {
        console.error('添加筛选失败:', e);
        ElMessage.error('添加筛选失败: ' + (e.message || e));
      }
    }
    function removeFilter(idx) {
      pivot.filters.splice(idx, 1);
    }
    function clearFilters() {
      pivot.filters = [];
    }
    function getFilterLabel(fieldKey) {
      const ff = currentDataset.value?.filter_fields?.find(x => x.key === fieldKey);
      if (ff) return ff.label;
      const dim = currentDataset.value?.dims?.find(x => x.key === fieldKey);
      if (dim) return dim.label;
      return fieldKey;
    }
    function getOpLabel(op) {
      const map = {eq:'=', ne:'≠', contains:'包含', gt:'>', lt:'<', ge:'≥', le:'≤', between:'范围', in:'属于'};
      return map[op] || op;
    }
    function formatFilterVal(f) {
      const v = f.value;
      if (Array.isArray(v)) return v.join(' ~ ');
      return v;
    }
    function getMetricLabel(metricKey) {
      const m = currentDataset.value?.metrics?.find(x => x.key === metricKey);
      if (m) return m.label;
      return metricKey;
    }
    function getAggLabel(agg) {
      const map = {sum:'求和', avg:'平均', count:'计数', max:'最大', min:'最小'};
      return map[agg] || agg;
    }

    // 智能筛选 - 计算属性
    const newFilterValueIsEnum = computed(() => {
      const fk = newFilterField.value;
      if (!fk) return false;
      const dim = currentDataset.value?.dims?.find(d => d.key === fk);
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      // 检查维度是否有枚举或外键
      if (dim && (dim.has_enum || dim.has_fk)) return true;
      // 检查筛选字段类型
      if (ff && (ff.type === 'enum' || ff.type === 'fk')) return true;
      return false;
    });
    const newFilterEnumOptions = computed(() => {
      const fk = newFilterField.value;
      if (!fk) return [];
      const dim = currentDataset.value?.dims?.find(d => d.key === fk);
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      // 枚举维度
      if (dim && dim.has_enum && enumDimOptions[fk]) {
        return enumDimOptions[fk];
      }
      // 外键维度 - 从已加载数据中提取选项
      if ((dim && dim.has_fk) || (ff && ff.type === 'fk')) {
        if (fkCache[fk]) return fkCache[fk];
      }
      // filter_fields中直接定义的枚举选项
      if (ff && ff.type === 'enum' && ff.options) {
        return ff.options.map(v => ({value: v, label: v}));
      }
      return [];
    });
    const newFilterValueIsDate = computed(() => {
      const fk = newFilterField.value;
      if (!fk) return false;
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      return ff && ff.type === 'date' && newFilterOp.value !== 'between';
    });
    const newFilterValueIsDateRange = computed(() => {
      const fk = newFilterField.value;
      if (!fk) return false;
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      return ff && ff.type === 'date' && newFilterOp.value === 'between';
    });
    const newFilterValueIsNumber = computed(() => {
      const fk = newFilterField.value;
      if (!fk) return false;
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      return ff && ff.type === 'number';
    });

    // 枚举选项缓存
    const enumDimOptions = reactive({});
    // 外键选项缓存 (异步加载)
    const fkCache = reactive({});

    function onFilterFieldChange() {
      const fk = newFilterField.value;
      newFilterValue.value = '';
      if (!fk) return;
      const dim = currentDataset.value?.dims?.find(d => d.key === fk);
      const ff = currentDataset.value?.filter_fields?.find(f => f.key === fk);
      const isDim = !!dim;
      const isEnum = (dim && dim.has_enum) || (ff && ff.type === 'enum');
      const isFk = (dim && dim.has_fk) || (ff && ff.type === 'fk');

      if (isEnum && !enumDimOptions[fk]) {
        // 从后端加载枚举值（翻译后的中文选项）
        api.get(`/api/analysis/dataset-values?pivot_field=${encodeURIComponent(fk)}&dataset=${pivot.dataset}`).then(r => {
          if (r.data && r.data.values) {
            enumDimOptions[fk] = r.data.values;
          }
        }).catch(() => {});
      }
      if (isFk && !fkCache[fk]) {
        // 从后端加载外键选项（如客户列表等）
        api.get(`/api/analysis/dataset-values?pivot_field=${encodeURIComponent(fk)}&dataset=${pivot.dataset}`).then(r => {
          if (r.data && r.data.values) {
            fkCache[fk] = r.data.values;
          }
        }).catch(() => {});
      }
    }
    function fmtMoney(v) {
      if (v == null || v === 0) return '¥0';
      return '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }
    function fmtVal(v, type) {
      if (v == null) return '-';
      if (type === 'currency') return fmtMoney(v);
      if (type === 'number') return Number(v).toLocaleString('zh-CN');
      return Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 2});
    }
    function colTotal(table, col) {
      return table.reduce((sum, row) => sum + (row[col] || 0), 0);
    }
    async function drillDown(dimValue) {
      drillTitle.value = `${pivotResult.value.rows_label}: ${dimValue}`;
      drillVisible.value = true;
      drillColumns.value = [];
      drillRows.value = [];
      try {
        const body = {dataset:pivot.dataset, rows_dim:pivot.rows_dim, dim_value:dimValue};
        if (pivot.filters.length) body.filters = pivot.filters;
        const r = await api.post('/api/analysis/pivot/drill', body);
        drillColumns.value = r.data.columns || [];
        drillRows.value = r.data.rows || [];
      } catch(e) { console.error(e); }
    }
    async function runPivot() {
      if(!pivot.dataset||!pivot.rows_dim||!pivot.metric) return;
      loadingPivot.value = true;
      try {
        const body = {dataset:pivot.dataset, rows_dim:pivot.rows_dim, metric:pivot.metric, agg:pivot.agg, chart_type:pivot.chart_type};
        if (pivot.cols_dim) body.cols_dim = pivot.cols_dim;
        
        // 处理筛选条件 - 确保值格式正确
        if (pivot.filters.length) {
          body.filters = pivot.filters.map(f => {
            let val = f.value;
            // 确保值不是对象
            if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
              val = val.value !== undefined ? val.value : (val.id !== undefined ? val.id : String(val));
            }
            return {field: f.field, op: f.op, value: val};
          });
        }
        
        console.log('发送的筛选条件:', body.filters);
        const r = await api.post('/api/analysis/pivot', body);
        pivotResult.value = r.data;
      } catch(e) { pivotResult.value={error:e.message}; }
      finally { loadingPivot.value = false; nextTick(renderChart); }
    }

    // === AI提问 Tab ===
    const messages = ref([]);
    const question = ref('');
    const aiLoading = ref(false);
    const chatContainer = ref(null);
    const convList = ref([]);
    const currentConvId = ref(null);
    const abortController = ref(null);
    const aiScope = ref(isFinanceEntry ? 'finance' : 'analysis'); // analysis经营助手 / finance财务专职助手
    const aiBase = Vue.computed(() => aiScope.value === 'finance' ? '/api/ai-finance' : '/api/ai');
    const aiScopeName = Vue.computed(() => aiScope.value === 'finance' ? '财务助手' : '经营分析');

    function switchAiScope(scope) {
      if (scope === aiScope.value) return;
      aiScope.value = scope;
      currentConvId.value = null;
      messages.value = [];
      abortController.value && abortController.value.abort();
      loadConversations();
    }

    async function loadConversations() {
      try {
        const r = await api.get(aiBase.value + '/conversations');
        convList.value = r.data || [];
      } catch(e) { console.error(e); }
    }
    async function selectConv(id) {
      currentConvId.value = id;
      try {
        const r = await api.get(aiBase.value + '/conversations/' + id + '/messages');
        messages.value = (r.data || []).map(m => {
          let result = null;
          if (m.extra) { try { result = JSON.parse(m.extra); } catch(e) {} }
          return {role: m.role, text: m.text, id: m.id, result};
        });
        scrollToBottom();
        nextTick(() => renderMermaid());
        renderAiCharts();
      } catch(e) { console.error(e); }
    }
    async function createConv() {
      try {
        const r = await api.post('/api/ai/conversations', {title: '新对话'});
        convList.value.unshift(r.data);
        currentConvId.value = r.data.id;
        messages.value = [];
      } catch(e) { console.error(e); }
    }
    async function deleteConv(id) {
      try {
        await api.del(aiBase.value + '/conversations/' + id);
        convList.value = convList.value.filter(c => c.id !== id);
        if (currentConvId.value === id) {
          currentConvId.value = null;
          messages.value = [];
        }
      } catch(e) { console.error(e); }
    }
    function scrollToBottom() { nextTick(() => { if(chatContainer.value) chatContainer.value.scrollTop = chatContainer.value.scrollHeight; }); }
    const thinkingCollapsed = Vue.reactive({});
    function planFiltersText(filters) {
      const OPS = {eq:'等于', ne:'不等于', like:'包含', in:'属于', ge:'≥', le:'≤', gt:'>', lt:'<', between:'范围'};
      const fmtVal = (v) => Array.isArray(v) ? v.join(' / ') : v;
      const p = item => (item.label || item.field) + ' ' + (OPS[item.op] || item.op) + ' ' + fmtVal(item.value);
      return filters.map(p).join('；');
    }
    function formattedReply(text) {
      let html = text;
      // 1. 处理 mermaid 代码块 -> 尝试渲染,失败则显示代码
      html = html.replace(/```mermaid\n([\s\S]*?)```/g, (match, code) => {
        const id = 'mermaid-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        return `<div class="mermaid-container" id="${id}" data-mermaid="${encodeURIComponent(code.trim())}"></div>`;
      });
      // 2. 处理 markdown 表格
      html = html.replace(/\n\n/g, '\n');
      html = html.replace(/(\|.+\|)\n\|[-\s|:]+\|\n((?:\|.+\|\n?)*)/g, (match, header, body) => {
        const headers = header.split('|').filter(c => c.trim()).map(c => c.trim());
        const rows = body.trim().split('\n').filter(r => r.trim()).map(r => {
          return r.split('|').filter(c => c.trim()).map(c => c.trim());
        });
        let table = '<table style="width:100%;border-collapse:collapse;margin:8px 0;font-size:13px">';
        table += '<thead><tr>';
        headers.forEach(h => table += `<th style="border:1px solid #555;padding:10px 12px;background:#2d3a5e;color:#e0e8f0;text-align:left;font-weight:600">${h}</th>`);
        table += '</tr></thead><tbody>';
        rows.forEach((row, i) => {
          const bg = i % 2 === 0 ? '#1a2332' : '#242d3d';
          table += `<tr style="background:${bg}">`;
          row.forEach(cell => {
            // 清理单元格中的markdown标记
            cell = cell.replace(/\*\*(.+?)\*\*/g, '$1').replace(/\*(.+?)\*/g, '$1');
            table += `<td style="border:1px solid #3a4556;padding:10px 12px;color:#c8d0dc">${cell}</td>`;
          });
          table += '</tr>';
        });
        table += '</tbody></table>';
        return table;
      });
      // 3. 清理markdown标记 (表格外的)
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong style="color:#e8b84a">$1</strong>');
      html = html.replace(/\*(.+?)\*/g, '<em style="color:#a8c8e8">$1</em>');
      html = html.replace(/^#{1,6}\s+/gm, '');  // 移除标题标记
      html = html.replace(/`([^`]+)`/g, '<code style="background:#2d3a5e;color:#e8b84a;padding:2px 6px;border-radius:3px;font-size:12px">$1</code>');
      // 4. 普通换行
      html = html.replace(/\n/g, '<br>');
      return html;
    }
    // 渲染 mermaid 图表
    async function renderMermaid() {
      if (!window.mermaid) {
        // mermaid库未加载: 显示原始代码块
        document.querySelectorAll('.mermaid-container').forEach(el => {
          if (el.dataset.rendered) return;
          const code = decodeURIComponent(el.dataset.mermaid);
          el.innerHTML = '<pre style="background:#1a2332;color:#a8c8e8;padding:12px;border-radius:6px;font-size:12px;overflow-x:auto">' + code.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
          el.dataset.rendered = '1';
        });
        return;
      }
      document.querySelectorAll('.mermaid-container').forEach(el => {
        if (el.dataset.rendered) return;
        const code = decodeURIComponent(el.dataset.mermaid);
        el.textContent = code;
        try {
          window.mermaid.initialize({ startOnLoad: false, theme: 'default' });
          window.mermaid.run({ querySelector: '#' + el.id }).then(() => {
            el.dataset.rendered = '1';
          }).catch(() => {
            el.innerHTML = '<pre style="background:#1a2332;color:#a8c8e8;padding:12px;border-radius:6px;font-size:12px;overflow-x:auto">' + code.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
            el.dataset.rendered = '1';
          });
        } catch(e) {
          el.innerHTML = '<pre style="background:#1a2332;color:#a8c8e8;padding:12px;border-radius:6px;font-size:12px;overflow-x:auto">' + code.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
          el.dataset.rendered = '1';
        }
      });
    }
    function quickExample(text) { question.value = text; }
    function clearHistory() {
      messages.value = [];
      if (currentConvId.value) {
        deleteConv(currentConvId.value);
        currentConvId.value = null;
      }
    }
    async function sendQuestion() {
      const q = question.value.trim(); if(!q) return;
      const userMsg = {role:'user', text:q, key:'m'+Date.now()+'-u'};
      const aiMsg = Vue.reactive({role:'ai', text:'', thinking:'', stageMsg:'正在连接...', plan:null, webResults:null, streaming:true, key:'m'+Date.now()+'-a', convId:null});
      messages.value.push(userMsg, aiMsg);
      question.value=''; aiLoading.value=true; scrollToBottom();
      const body = {text: q};
      if (currentConvId.value) body.conversation_id = currentConvId.value;
      const controller = new AbortController();
      abortController.value = controller;
      try {
        const tk = localStorage.getItem(TOKEN_KEY);
        const resp = await fetch(aiBase.value + '/stream', {
          method:'POST',
          headers:{'Content-Type':'application/json', 'Authorization': tk ? 'Bearer '+tk : ''},
          body: JSON.stringify(body),
          signal: controller.signal
        });
        if (!resp.ok) {
          let msg='请求失败('+resp.status+')';
          try { const j = await resp.json(); msg = j.detail || msg; } catch {}
          if (resp.status===403) msg='权限不足'; else if (resp.status===401) msg='登录已过期';
          aiMsg.text = '分析失败: ' + msg; aiMsg.streaming=false; return;
        }
        if (!resp.body) { aiMsg.streaming=false; return; }
        const reader = resp.body.getReader();
        const dec = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
          if (controller.signal.aborted) break;
          const { done, value } = await reader.read();
          if (done) break;
          buffer += dec.decode(value, {stream:true});
          let i;
          while ((i = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, i); buffer = buffer.slice(i+2);
            parseAIBlock(block, aiMsg);
          }
        }
      } catch(e) {
        if (e.name === 'AbortError') {
          aiMsg.text += (aiMsg.text ? '\n\n' : '') + '已停止生成';
        } else {
          aiMsg.text += (aiMsg.text ? '\n\n' : '') + '分析失败: ' + (e.message || e);
        }
      } finally {
        abortController.value = null;
        aiMsg.streaming = false;
        if (aiMsg.convId && aiMsg.convId !== currentConvId.value) currentConvId.value = aiMsg.convId;
        aiLoading.value = false;
        loadConversations();
        scrollToBottom();
        nextTick(() => renderMermaid());
        nextTick(() => renderAiCharts());
      }
    }
    function stopGeneration() {
      if (abortController.value) {
        abortController.value.abort();
      }
    }
    function parseAIBlock(block, aiMsg) {
      let ev='', data='';
      block.split('\n').forEach(line => {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data = line.slice(5).trim();
      });
      if (!data) return;
      let j; try { j = JSON.parse(data); } catch { return; }
      if (ev==='stage') { aiMsg.stageMsg = j.msg || j.stage || '处理中...'; scrollToBottom(); }
      else if (ev==='thinking') { aiMsg.thinking = (aiMsg.thinking||'') + (j.delta||''); scrollToBottom(); }
      else if (ev==='web') { aiMsg.webResults = j.results || []; scrollToBottom(); }
      else if (ev==='plan') { aiMsg.plan = j; scrollToBottom(); }
      else if (ev==='answer') { aiMsg.text += (j.delta||''); scrollToBottom(); }
      else if (ev==='done') { if (j.conversation_id) aiMsg.convId = j.conversation_id; if (j.pivot_data) { aiMsg.result = j.pivot_data; renderAiCharts(); } }
      else if (ev==='result') { aiMsg.result = j; }
    }
    function renderAiCharts() {
      nextTick(function() {
        nextTick(doRenderAiCharts);
      });
      setTimeout(doRenderAiCharts, 200);
      setTimeout(doRenderAiCharts, 500);
    }
    function doRenderAiCharts() {
      messages.value.forEach(function(m) {
        // 仅pivot类型才渲染图表(chat/discuss类型跳过)
        if (m.type !== 'pivot') return;
        var res = m.result;
        if (!res) return;
        // 单分析结果: 根据chart_recommend决定是否渲染
        if (res.chart && !res._overview_results) {
          var rec = res.chart_recommend || {};
          // 只有当推荐显示 或 没有推荐信息时才渲染(兼容旧数据)
          if (rec.show === false) {
            console.log('[chart] skip, not recommended');
            return;
          }
          var id = 'ai-chart-' + m.key;
          var el = document.getElementById(id);
          renderAiChartById(id, res.chart, res.table);
        }
        // 综合分析结果: 每个子项独立判断
        if (res._overview_results && res._overview_results.length) {
          res._overview_results.forEach(function(ov, oi) {
            if (!ov.chart) return;
            var ovRec = ov.chart_recommend || {};
            if (ovRec.show === false) {
              console.log('[chart] skip overview item ' + oi + ', not recommended');
              return;
            }
            var id = 'ai-chart-' + m.key + '-' + oi;
            renderAiChartById(id, ov.chart, ov.table);
          });
        }
      });
    }

    function switchTab(t) {
      activeTab.value = t;
      if (t === 'ai') loadConversations();
    }

    // 监听pivotResult变化，确保图表重新渲染
    watch(pivotResult, (newVal) => {
      if (newVal && newVal.chart && newVal.chart.x.length) {
        nextTick(() => {
          if (!chartInstance && chartEl.value) {
            chartInstance = window.echarts.init(chartEl.value);
          }
          renderChart();
        });
      }
    });

    // 深度监听messages中result变化，自动渲染AI图表
    watch(messages, () => {
      nextTick(() => renderAiCharts());
    }, { deep: true, flush: 'post' });

    // 窗口大小变化时调整图表
    const handleResize = () => {
      if (chartInstance) chartInstance.resize();
      aiCharts.forEach(inst => { try { inst.resize(); } catch {} });
    };

    onMounted(() => {
      load();
      window.addEventListener('resize', handleResize);
      if (activeTab.value === 'ai') loadConversations();
    });

    onBeforeUnmount(() => {
      window.removeEventListener('resize', handleResize);
      if (chartInstance) {
        chartInstance.dispose();
        chartInstance = null;
      }
    });
    const Close = ElementPlusIconsVue.Close;
    const ArrowDown = ElementPlusIconsVue.ArrowDown;
    const ArrowUp = ElementPlusIconsVue.ArrowUp;
    return { activeTab, switchTab, loading, kpi, aging, alerts, costBreakdown, datasets, currentDataset, pivot, pivotResult, loadingPivot, setChartRef, totalCost, agingTotals, agingMax, fmt, fmtTime, costTypeLabel, loadDatasets, runPivot, addFilter, removeFilter, clearFilters, getFilterLabel, getOpLabel, formatFilterVal, getMetricLabel, getAggLabel, onFilterFieldChange, newFilterValueIsEnum, newFilterEnumOptions, newFilterValueIsDate, newFilterValueIsDateRange, newFilterValueIsNumber, fmtMoney, fmtVal, colTotal, drillDown, drillVisible, drillTitle, drillColumns, drillRows, newFilterField, newFilterOp, newFilterValue, messages, question, aiLoading, chatContainer, formattedReply, quickExample, clearHistory, sendQuestion, stopGeneration, convList, currentConvId, loadConversations, selectConv, createConv, deleteConv, Icon, Close, ArrowDown, ArrowUp, thinkingCollapsed, planFiltersText, renderAiCharts, aiScope, switchAiScope, isFinanceEntry };
  }
};

// ============ 财务看板 (GM/管理员) ============
const FinanceDashboardPage = {
  template: `
  <div class="page" style="padding:12px 16px 24px 16px">
    <!-- 顶部控制条 -->
    <div class="fin-topbar">
      <div class="fin-tb-left">
        <div class="ph-icon" v-html="Icon.icon('document-chart-bar',22)"></div>
        <div>
          <div class="ph-title" style="font-size:18px;font-weight:800">财务看板 · 驾驶舱</div>
          <div class="ph-sub" style="color:#64748b">矩阵主表 + 可展开明细 + 多维切换（默认年度×按月，可切周/季/日/当天）</div>
        </div>
      </div>
      <div class="fin-tb-ctl">
        <div class="ctl-row-1">
          <div class="ctl-item">
            <span class="ctl-label">年度</span>
            <el-select v-model="year" size="small" style="width:92px" @change="onYearChange">
              <el-option v-for="y in yearOptions" :key="y" :label="y+'年'" :value="y"/>
            </el-select>
          </div>
          <div class="ctl-item">
            <span class="ctl-label">粒度</span>
            <el-radio-group v-model="granularity" size="small" @change="loadAll">
              <el-radio-button value="quarter" :disabled="!granAllowed('quarter')">季</el-radio-button>
              <el-radio-button value="month" :disabled="!granAllowed('month')">月</el-radio-button>
              <el-radio-button value="week" :disabled="!granAllowed('week')">周</el-radio-button>
              <el-radio-button value="day" :disabled="!granAllowed('day')">日</el-radio-button>
            </el-radio-group>
          </div>
          <div class="ctl-item">
            <span class="ctl-label">周期</span>
            <el-button-group size="small">
              <el-button v-for="(l,p) in PERIODS" :key="p" :type="period===p?'primary':'default'" @click="setPeriod(p)">{{l}}</el-button>
            </el-button-group>
            <el-date-picker v-model="customRange" type="daterange" size="small" value-format="YYYY-MM-DD"
              range-separator="至" start-placeholder="起" end-placeholder="止" style="width:220px;margin-left:6px" @change="onCustomRange" />
          </div>
        </div>
        <div class="ctl-row-2">
          <div class="ctl-item">
            <span class="ctl-label">公司</span>
            <el-select v-model="company" size="small" style="width:130px" clearable @change="loadAll" placeholder="全集团">
              <el-option label="峰业精密机械(主体1)" :value="1"/>
              <el-option label="东莞加工厂(主体2)" :value="2"/>
            </el-select>
          </div>
          <div class="ctl-item">
            <span class="ctl-label">资金口径</span>
            <el-select v-model="view" size="small" style="width:170px" @change="loadAll">
              <el-option label="全部（含承兑现金）" value="all"/>
              <el-option label="对公账户（不含承兑）" value="bank-no-acceptance"/>
              <el-option label="仅机械公账" value="jx"/>
              <el-option label="仅加工厂公账" value="dg"/>
              <el-option label="仅承兑汇票" value="acceptance"/>
              <el-option label="仅现金" value="cash"/>
            </el-select>
          </div>
          <div class="ctl-spacer"></div>
          <el-button size="small" @click="doExport"><span v-html="Icon.icon('arrow-down-tray',14)" style="vertical-align:-2px;margin-right:4px"></span>导出Excel</el-button>
          <el-button size="small" plain @click="toggleView">{{viewMode==='matrix'?'切换透视视图':'切回矩阵视图'}}</el-button>
        </div>
      </div>
    </div>

    <!-- 总经理速览条（钉在顶部滚动不消失） -->
    <div class="fin-summary" v-loading="sumLoading" v-if="summary">
      <div class="sum-head">
        <span class="sum-title">{{sumPeriodText}}</span>
        <span class="sum-scope">
          <el-tag size="small" :type="company===1?'success':(company===2?'warning':'primary')">{{companyLabel}}</el-tag>
          <el-tag size="small" style="margin-left:4px">{{viewLabel}}</el-tag>
        </span>
      </div>
      <div class="sum-grid">
        <div class="sum-card profit">
          <div class="sc-lbl">本期利润 <span class="sc-sub">营收−费用</span></div>
          <div class="sc-val">¥{{fmt(summary.period_profit)}}</div>
          <div class="sc-foot">营收¥{{fmt(summary.revenue_6001)}} · 利润环比 <b :class="summary.profit_qoq>=0?'up':'down'">{{summary.profit_qoq}}%</b></div>
        </div>
        <div class="sum-card">
          <div class="sc-lbl">资金总额（实时）</div>
          <div class="sc-val">¥{{fmt(summary.fund_total)}}</div>
          <div class="sc-foot">公账 ¥{{fmt(summary.bank_balance)}} / 承兑 ¥{{fmt(summary.acceptance_balance)}}</div>
        </div>
        <div class="sum-card cash" :class="{low:summary.cash_balance<30000, neg:summary.cash_balance<0}">
          <div class="sc-lbl">库存现金 · 借备用金前速查</div>
          <div class="sc-val">¥{{fmt(summary.cash_balance)}}</div>
          <div class="sc-foot" v-if="summary.cash_balance<30000"><b>⚠现金偏低，请先从公账提现</b></div>
          <div class="sc-foot" v-else>可支用 · 借现金前先确认此余额</div>
        </div>
        <div class="sum-card ar">
          <div class="sc-lbl">应收逾期·超30天</div>
          <div class="sc-val" style="color:#dc2626">¥{{fmt(summary.ar_overdue_30d)}}</div>
          <div class="sc-foot">
            <div v-for="t in summary.ar_overdue_top3" :key="t.name" class="ar-item">
              <span>{{t.name}}</span><b>¥{{fmt(t.amount)}}</b>
            </div>
          </div>
        </div>
        <div class="sum-card ap">
          <div class="sc-lbl">应付 7天内到期</div>
          <div class="sc-val" style="color:#ea580c">¥{{fmt(summary.ap_due_7d)}}</div>
          <div class="sc-foot">即将到期需安排资金</div>
        </div>
      </div>
      <!-- 分账户余额条 -->
      <div class="acc-strip" v-if="summary.account_balances && summary.account_balances.length">
        <div v-for="a in summary.account_balances" :key="a.code" class="acc-chip" :class="{low: a.balance<30000 && a.code!=='ACCEPTANCE'}">
          <span class="acc-name">{{a.name}}</span>
          <b class="acc-bal" :style="{color: a.balance<0 ? '#dc2626' : (a.balance<30000 && a.code!=='ACCEPTANCE' ? '#ea580c' : '')}">¥{{fmt(a.balance)}}</b>
        </div>
      </div>
    </div>

    <!-- 矩阵主表 -->
    <div class="panel mat-panel" v-loading="matLoading" v-if="viewMode==='matrix'">
      <div class="panel-title mat-title">
        <span>{{winTitle}} 财务统计表 · {{granLabel}}</span>
        <span class="mat-sub">
          {{companyLabel}} · {{viewLabel}} · 共{{columns.length}}个时间区间
        </span>
        <span class="mat-spacer"></span>
        <el-tooltip content="点击行首 + 展开该行该月构成；点击单元格🔻看逐笔流水；鼠标放数字上看环比/同比">
          <span class="mat-hint" v-html="Icon.icon('information-circle',14)"></span>
        </el-tooltip>
      </div>

      <div class="mat-scroll">
      <table class="mat-table" :class="'gran-' + granularity">
        <thead>
          <tr>
            <th class="col-section">分组</th>
            <th class="col-rowhead"><span class="row-head-cap">{{rowHeadCap}}</span></th>
            <th class="col-num" v-for="c in columns" :key="c.key" :title="c.start.slice(0,10)+' ~ '+c.end.slice(0,10)">
              {{colLabel(c)}}
            </th>
            <th class="col-num col-total">{{totalCap}}</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in rows" :key="r.id">
            <!-- 分组分隔行（每进入新分组输出一次分组标题行，利用上一条的 section） -->
            <tr v-if="sectionBreakBefore(r)" class="sec-break"><td colspan="100">{{r.section_title}}</td></tr>
            <tr :class="rowClass(r)">
              <td class="col-section" :rowspan="1"><span v-if="firstInSection(r)" :class="'sec-dot sec-'+r.section">●</span></td>
              <td class="col-rowhead">
                <span v-if="r.section==='A'||r.section==='C'||r.expandable" @click="toggleExpand(r, null)" class="row-plus"
                      :class="{opened: isExpanded(r)}">
                  {{isExpanded(r) ? '−' : '+'}}
                </span>
                <span v-else class="row-plus ph"></span>
                <span class="row-title">{{rowTitle(r)}}</span>
              </td>
              <td v-for="(c, ci) in columns" :key="c.key"
                  :class="cellClass(r,c,ci)"
                  @mouseenter="focusedColIdx = ci"
                  @click="onCellClick(r,c,ci)">
                <div class="cell-v">
                  <span>{{fmt(r.cells[c.key].v)}}</span>
                  <span v-if="ci>0 && r.cells[c.key].qv_label" class="qv-tag" :class="r.cells[c.key].qv_label">{{r.cells[c.key].qv_label}}{{Math.abs(r.cells[c.key].qv)}}%</span>
                </div>
                <span class="cell-expand" v-if="hasFlow(r)" title="查看该格逐笔流水">🔻</span>
              </td>
              <td class="col-num col-total"><b>{{fmt(r.total)}}</b></td>
            </tr>
            <!-- 展开面板：先明细后原因（金蝶明细账式逐笔流水+当时余额+穿透凭证） -->
            <tr v-if="isExpanded(r)" class="exp-detail-row">
              <td :colspan="columns.length + 3">
                <div class="exp-panel" v-if="expDetail(r)">
                  <div class="exp-sum">
                    <span class="exp-sum-col">{{expDetail(r).colLabel}}明细</span>
                    <span>共 <b>{{expDetail(r).summary.count}}</b> 笔</span>
                    <span class="in">进 +{{fmt(expDetail(r).summary.in)}}</span>
                    <span class="out">出 -{{fmt(expDetail(r).summary.out)}}</span>
                    <span>净 <b>{{fmt(expDetail(r).summary.in - expDetail(r).summary.out)}}</b></span>
                    <span v-if="expDetail(r).summary.opening != null" class="op">期初 {{fmt(expDetail(r).summary.opening)}}</span>
                  </div>
                  <table class="exp-table" v-if="expDetail(r).list.length">
                    <thead><tr>
                      <th>日期</th><th>账户</th><th>对方</th><th>摘要</th>
                      <th class="num">收</th><th class="num">支</th>
                      <th class="num" v-if="singleAccRow(r)">当时余额</th><th>凭证</th>
                    </tr></thead>
                    <tbody>
                      <tr v-for="it in expDetail(r).list" :key="it.id" :class="{'exp-big': isBigFlow(it, expDetail(r).list)}">
                        <td>{{it.date.slice(5,10)}}</td>
                        <td class="acc">{{it.fund_account}}</td>
                        <td>{{it.counterparty}}</td>
                        <td class="sum">{{it.summary}}</td>
                        <td class="num in">{{it.direction==='IN' ? fmt(it.amount) : ''}}</td>
                        <td class="num out">{{it.direction==='OUT' ? fmt(it.amount) : ''}}</td>
                        <td class="num bal" v-if="singleAccRow(r)">{{fmt(it.balance_after)}}</td>
                        <td><a v-if="it.voucher_id" class="vc-link" @click="goVoucher(it)">{{it.voucher_no}}</a><span v-else class="na">—</span></td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-else class="exp-empty">该期间无流水</div>
                  <div class="exp-insight" v-if="insightText(r)">💡 {{insightText(r)}}</div>
                </div>
                <div class="exp-panel exp-loading" v-else>明细加载中…</div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      </div>
    </div>

    <!-- 透视图表 -->
    <div class="panel mat-panel" v-if="viewMode==='pivot'" v-loading="matLoading">
      <div class="panel-title mat-title">
        <span>{{winTitle}} 透视图表 · {{granLabel}}</span>
        <span class="mat-sub">{{companyLabel}} · {{viewLabel}}</span>
        <span class="mat-spacer"></span>
        <div style="display:flex;gap:6px;align-items:center">
          <el-select v-model="pivotDim" size="small" style="width:120px" @change="renderPivot">
            <el-option label="按时间展开" value="time"/>
            <el-option label="按科目展开" value="section"/>
          </el-select>
          <el-select v-model="pivotType" size="small" style="width:110px" @change="renderPivot">
            <el-option label="堆叠柱状" value="stack-bar"/>
            <el-option label="折线趋势" value="line"/>
            <el-option label="热力图" value="heatmap"/>
            <el-option label="瀑布图(累计净流)" value="waterfall"/>
          </el-select>
        </div>
      </div>
      <div ref="pivotChartRef" style="height:480px;width:100%"></div>
    </div>

    <!-- 详细看板（折叠） -->
    <div class="panel detail-fold" style="margin-top:14px">
      <div class="df-head" @click="detailFold=!detailFold">
        <span class="df-icon" v-html="Icon.icon('chevron-right',14)" :class="{rot:!detailFold}"></span>
        <span>详细看板：收支趋势 / 支出结构 / 账龄 / 公司对比</span>
        <span class="df-hint">点击{{detailFold?'收起':'展开'}}</span>
      </div>
      <div v-show="detailFold" style="padding:6px 14px 14px">
        <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:6px 0 16px" v-if="dashboard">
          <div class="kpi-card"><div class="k-label">资金总额</div><div class="k-val">¥{{fmt(dashboard.kpis.fund_total)}}</div><div class="k-sub">期初 ¥{{fmt(dashboard.kpis.fund_begin)}}</div></div>
          <div class="kpi-card green"><div class="k-label">本期收款</div><div class="k-val">¥{{fmt(dashboard.kpis.income)}}</div><div class="k-sub">净流入 ¥{{fmt(dashboard.kpis.net)}}</div></div>
          <div class="kpi-card red"><div class="k-label">本期付款</div><div class="k-val">¥{{fmt(dashboard.kpis.expense)}}</div></div>
          <div class="kpi-card"><div class="k-label">应收余额</div><div class="k-val">¥{{fmt(dashboard.kpis.ar_balance)}}</div><div class="k-sub">待回款</div></div>
          <div class="kpi-card"><div class="k-label">应付余额</div><div class="k-val">¥{{fmt(dashboard.kpis.ap_balance)}}</div><div class="k-sub">待支付</div></div>
          <div class="kpi-card" :class="dashboard.kpis.net>=0?'green':'red'"><div class="k-label">净现金流量</div><div class="k-val">¥{{fmt(dashboard.kpis.net)}}</div></div>
        </div>
        <div style="display:grid;grid-template-columns:2fr 1fr;gap:12px;margin:12px 0">
          <div class="panel"><div class="panel-title">收支趋势</div><div ref="trendRef" style="height:280px"></div></div>
          <div class="panel"><div class="panel-title">支出结构</div><div ref="pieRef" style="height:280px"></div></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="panel"><div class="panel-title">应收/应付账龄</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div><div style="font-size:12px;color:#909399;margin-bottom:4px">应收账龄</div><div ref="arRef" style="height:210px"></div></div>
              <div><div style="font-size:12px;color:#909399;margin-bottom:4px">应付账龄</div><div ref="apRef" style="height:210px"></div></div>
            </div>
          </div>
          <div class="panel"><div class="panel-title">双公司对比</div><div ref="compRef" style="height:260px"></div></div>
        </div>
      </div>
    </div>

    <!-- 抽屉：单元格逐笔流水 -->
    <el-drawer v-model="drawer.open" :title="drawer.title" direction="rtl" size="55%" destroy-on-close>
      <div style="padding:4px 4px 10px 4px">
        <div class="dr-filter">
          <el-input size="small" v-model="drawer.q" placeholder="搜索对方/摘要/金额" clearable style="width:220px">
            <template #prefix><span v-html="Icon.icon('search',14)" style="color:#94a3b8;margin-right:4px"></span></template>
          </el-input>
          <span class="ctl-spacer"></span>
          <span class="dr-total">共{{drawer.total}}笔 · 合计 ¥{{fmt(drawer.totalAmt)}}</span>
        </div>
        <el-table :data="drawerList" size="small" border stripe height="52vh">
          <el-table-column prop="date" label="日期" width="140" fixed="left"/>
          <el-table-column prop="company" label="归属" width="100"/>
          <el-table-column prop="fund_account" label="资金账户" width="110"/>
          <el-table-column prop="direction" label="方向" width="60">
            <template #default="{row}"><b :style="{color:row.direction==='IN'?'#16a34a':'#dc2626'}">{{row.direction==='IN'?'入':'出'}}</b></template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="90"/>
          <el-table-column prop="counterparty" label="对方单位" width="170" show-overflow-tooltip/>
          <el-table-column prop="summary" label="摘要" min-width="220" show-overflow-tooltip/>
          <el-table-column prop="amount" label="金额" width="130" align="right">
            <template #default="{row}"><b :style="{color:row.direction==='IN'?'#16a34a':'#dc2626'}">{{row.direction==='IN'?'+':'-'}}{{fmt(row.amount)}}</b></template>
          </el-table-column>
          <el-table-column prop="voucher_no" label="凭证号" width="110">
            <template #default="{row}">
              <el-link v-if="row.voucher_id" type="primary" @click="goVoucher(row.voucher_id)">{{row.voucher_no || '查看凭证 →'}}</el-link>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-if="drawer.total>drawer.size"
          layout="total, prev, pager, next"
          :total="drawer.total" :page-size="drawer.size" :current-page.sync="drawer.page"
          @current-change="loadDrawerList"
          background style="margin-top:10px;justify-content:flex-end;display:flex" />
      </div>
    </el-drawer>
  </div>`,
  setup() {
    const { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch, markRaw } = Vue;
    const PERIODS = { week:'本周', month:'本月', quarter:'本季', half:'半年', year:'本年', last_week:'上周', last_month:'上月', last_quarter:'上季' };
    const GRAN_LBL = { quarter:'季度切片', month:'月份切片', week:'周度切片', day:'逐日切片' };
    // 周期×粒度合法矩阵（数组末位=该周期默认粒度）：老板视角杜绝"1列废表"
    const GRANS_BY_PERIOD = {
      week: ['day'], last_week: ['day'],
      month: ['week', 'day'], last_month: ['week', 'day'],
      quarter: ['week', 'month'], last_quarter: ['week', 'month'],
      half: ['month'],
      year: ['quarter', 'month'], last_year: ['quarter', 'month'],
      custom: ['day', 'week', 'month', 'quarter'],
    };
    const VIEW_LBL = { all:'全部（含承兑现金）', 'bank-no-acceptance':'对公（不含承兑）', jx:'仅机械公账', dg:'仅加工厂公账', acceptance:'仅承兑', cash:'仅现金' };

    // 筛选
    const period = ref('year');
    const customRange = ref(null);
    const thisYear = new Date().getFullYear();
    const year = ref(thisYear);
    const yearOptions = [thisYear - 1, thisYear, thisYear + 1];
    const granularity = ref('month');
    const company = ref(null);
    const view = ref('all');
    const viewMode = ref('matrix');
    const pivotDim = ref('time');
    const pivotType = ref('stack-bar');
    const pivotChartRef = ref(null);
    let pivotChart = null;
    const detailFold = ref(false);

    // 数据
    const summary = ref(null);
    const matrix = ref(null);
    const dashboard = ref(null);
    const sumLoading = ref(false);
    const matLoading = ref(false);
    const focusedColIdx = ref(0);
    const columns = computed(() => matrix.value?.columns || []);
    const rows = computed(() => matrix.value?.rows || []);
    const rowHeadCap = computed(() => granularity.value === 'quarter' ? '(季度)' : granularity.value==='week' ? '(周别)' : granularity.value==='day' ? '(日期)' : '(月份)');
    const granLabel = computed(() => GRAN_LBL[granularity.value] || '');
    // 矩阵标题：窗口模式显日期范围，本年模式显年份
    const winTitle = computed(() => {
      const w = matrix.value && matrix.value.window;
      if (!w || !w.win_mode || period.value === 'year') return year.value + '年';
      return w.start.slice(5).replace('-', '.') + ' ~ ' + w.end.slice(5).replace('-', '.');
    });
    const totalCap = computed(() => (period.value !== 'year' && matrix.value && matrix.value.window && matrix.value.window.win_mode) ? '区间合计' : '全年累计');
    // B行标题：日/周粒度=资金回款口径；月/季=账套营收口径
    function rowTitle(r){
      if (r.id === 'B:REV') return (granularity.value === 'day' || granularity.value === 'week') ? '本期回款' : '本期营收';
      return r.title;
    }
    const companyLabel = computed(() => ({1:'峰业精密机械', 2:'东莞加工厂'}[company.value] || '集团全公司'));
    const viewLabel = computed(() => VIEW_LBL[view.value] || view.value);

    const charts = [];
    const trendRef = ref(null), pieRef = ref(null);
    const arRef = ref(null), apRef = ref(null), compRef = ref(null);

    const expanded = reactive({});
    const drawer = reactive({ open:false, title:'', row_id:'', col_key:'', list:[], total:0, totalAmt:0, page:1, size:50, q:'' });
    const drawerList = computed(() => {
      const q = (drawer.q||'').trim();
      const raw = drawer.list;
      if (!q) return raw;
      const ql = q.toLowerCase();
      return raw.filter(r => String(r.counterparty||'').toLowerCase().includes(ql)
        || String(r.summary||'').toLowerCase().includes(ql)
        || String(r.amount||'').includes(ql));
    });

    const fmt = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
    const sumPeriodText = computed(() => summary.value ? `${summary.value.range.start.slice(0,10)} ~ ${summary.value.range.end.slice(0,10)} (${{week:'本周',month:'本月',quarter:'本季',half:'半年',year:'本年',last_week:'上周',last_month:'上月',last_quarter:'上季'}[period.value] || '自定义'})` : '');

    function colLabel(c){
      if (!c || !c.key) return '';
      const g = granularity.value;
      if (g==='year') return c.key; // Q1 Q2
      if (g==='week') return c.key; // W03
      if (g==='day') return c.key; // 03-15
      return parseInt(c.key,10)+'月';
    }

    // section 展示: 仅首行输出一次 分组标题行
    let _lastSec = '';
    function sectionBreakBefore(r){ return r.section !== _lastSec && (_lastSec = r.section, true); }
    // hack: 每次重渲染rows前重置
    watch(() => rows.value.length, () => { _lastSec = ''; });
    function firstInSection(r){ return true; /* 每行都打圆点标记 */ }
    function rowClass(r){
      const cls = ['row-'+r.section];
      if (r.id === 'E:NET' || r.id === 'F:CUMNET') cls.push('row-result');
      if (r.id === 'F2:NONOP') cls.push('row-nonop');
      if (r.id === 'C:CASH' || r.id === 'A:CASH') {
        cls.push('row-cash');
        if ((summary.value && Number(summary.value.cash_balance) < 30000)) cls.push('row-cash-low');
      }
      return cls;
    }
    function cellClass(r,c,ci){
      const cls = ['col-num'];
      if (ci === (focusedColIdx.value)) cls.push('focus-col');
      const cell = r.cells[c.key];
      if (cell && cell.qv_label === '↑') cls.push('cell-warn-up');
      if (cell && cell.qv_label === '↓') cls.push('cell-warn-down');
      if (r.section === 'B') cls.push('cell-rev');
      if (r.section === 'D') cls.push('cell-exp');
      if (r.section === 'E' || r.section === 'F' || r.section === 'F2') cls.push('cell-net');
      if (r.section === 'C' && r.id === 'C:CASH') cls.push('cell-cash');
      return cls;
    }
    function hasFlow(r){
      // B(营收)/D(费用) 是单元格逐笔流水抽屉；A/C 是余额类行，用 + 号行展开，不显示🔻
      return r.section === 'B' || r.section === 'D';
    }
    // 点击单元格 => 抽屉 (B/D) 或 行展开(A/C)
    function onCellClick(r, c, ci){
      focusedColIdx.value = ci;
      if (r.section === 'B' || r.section === 'D') {
        openDrawer(r, c);
      } else if (r.section === 'A' || r.section === 'C') {
        toggleExpand(r, c.key, ci);
      }
    }
    async function openDrawer(r, c){
      drawer.open = true;
      drawer.title = `${r.title} · ${colLabel(c)} 明细（${companyLabel.value} · ${viewLabel.value}）`;
      drawer.row_id = r.id; drawer.col_key = c.key; drawer.page = 1; drawer.q = '';
      await loadDrawerList();
    }
    async function loadDrawerList(){
      try {
        const qs = new URLSearchParams();
        qs.set('row_id', drawer.row_id); qs.set('col_key', drawer.col_key);
        qs.set('year', year.value); qs.set('granularity', granularity.value);
        if (company.value != null) qs.set('company', company.value);
        qs.set('view', view.value);
        qs.set('page', drawer.page); qs.set('size', drawer.size);
        const r = await api.get('/api/finance/cell-details?' + qs.toString());
        drawer.list = r.data.list;
        drawer.total = r.data.total;
        drawer.totalAmt = r.data.list.reduce((a,x) => a + Number(x.amount||0), 0);
      } catch(e){ ElMessage.error(e.message||'明细加载失败'); }
    }

    // 行展开：金蝶明细账式——先明细（逐笔流水+当时余额）后原因（TOP洞察）
    const expandDetailMap = reactive({}); // key=row_id:colKey -> {colLabel, summary, list, insight}
    function isExpanded(r){ return Object.keys(expanded).some(k => k.startsWith(r.id + ':')); }
    function expDetail(r){
      const k = Object.keys(expanded).find(k => k.startsWith(r.id + ':'));
      return k ? expandDetailMap[k] : null;
    }
    function singleAccRow(r){ return r.section === 'A' || r.section === 'C'; }
    function toggleExpand(r, colKey, ci){
      if (!(r.section === 'A' || r.section === 'C' || r.expandable)) return;
      if (ci != null) focusedColIdx.value = ci;
      const ck = colKey || (columns.value[focusedColIdx.value || 0] || {}).key;
      if (!ck) return;
      const k = r.id + ':' + ck;
      if (expanded[k]) { delete expanded[k]; return; }
      // 同行只展开一个面板：清掉旧key
      Object.keys(expanded).filter(x => x.startsWith(r.id + ':')).forEach(x => delete expanded[x]);
      expanded[k] = true;
      loadExpandDetail(r, ck, k);
    }
    async function loadExpandDetail(r, ck, k){
      const qs = new URLSearchParams();
      qs.set('row_id', r.id); qs.set('col_key', ck);
      qs.set('year', year.value); qs.set('granularity', granularity.value);
      if (company.value != null) qs.set('company', company.value);
      qs.set('view', view.value);
      const colObj = columns.value.find(c => c.key === ck) || {};
      try {
        const qs2 = new URLSearchParams(qs); qs2.set('size', 200);
        const [d1, d2] = await Promise.all([
          api.get('/api/finance/cell-details?' + qs2.toString()),
          api.get('/api/finance/row-expand?' + qs.toString()),
        ]);
        expandDetailMap[k] = {
          colLabel: colLabel(colObj),
          summary: (d1.data && d1.data.summary) || { count: 0, in: 0, out: 0 },
          list: (d1.data && d1.data.list) || [],
          insight: (d2.data && d2.data.items) || [],
        };
      } catch(e){
        expandDetailMap[k] = { colLabel: colLabel(colObj), summary: { count: 0, in: 0, out: 0 }, list: [], insight: [] };
      }
    }
    // 洞察行：TOP榜一行人话
    function insightText(r){
      const d = expDetail(r);
      if (!d || !d.insight.length) return '';
      return d.insight.map(g => {
        const top = (g.rows || []).slice(0, 3).map(x => x.name + ' ' + fmt(x.amount)).join(' / ');
        return top ? (g.group + '：' + top) : '';
      }).filter(Boolean).join('　·　');
    }
    // 大额标色：超面板均值2倍
    function isBigFlow(it, list){
      if (!list || list.length < 3) return false;
      const avg = list.reduce((s, x) => s + Number(x.amount || 0), 0) / list.length;
      return avg > 0 && Number(it.amount || 0) >= avg * 2;
    }

    // 加载
    async function loadSummary(){
      sumLoading.value = true;
      try {
        const qs = new URLSearchParams();
        qs.set('year', year.value); qs.set('granularity', granularity.value);
        qs.set('period', period.value);
        if (customRange.value && customRange.value.length === 2) {
          qs.set('start', customRange.value[0]); qs.set('end', customRange.value[1]);
        }
        if (company.value != null) qs.set('company', company.value);
        qs.set('view', view.value);
        const r = await api.get('/api/finance/summary?' + qs.toString());
        summary.value = r.data;
      } catch(e){ ElMessage.error(e.message); }
      finally { sumLoading.value = false; }
    }
    async function loadMatrix(){
      matLoading.value = true;
      try {
        const qs = new URLSearchParams();
        qs.set('year', year.value); qs.set('granularity', granularity.value);
        qs.set('period', period.value);
        if (customRange.value && customRange.value.length === 2) {
          qs.set('start', customRange.value[0]); qs.set('end', customRange.value[1]);
        }
        if (company.value != null) qs.set('company', company.value);
        qs.set('view', view.value);
        const r = await api.get('/api/finance/matrix?' + qs.toString());
        matrix.value = r.data;
        // 筛选变了，展开面板与明细缓存全部失效
        Object.keys(expanded).forEach(k => delete expanded[k]);
        Object.keys(expandDetailMap).forEach(k => delete expandDetailMap[k]);
        // 后端列数防爆降级后，同步粒度显示
        if (r.data.granularity && r.data.granularity !== granularity.value) granularity.value = r.data.granularity;
        // 默认聚焦最后一个有数据的列（未来列空，不聚）
        const nowT = new Date();
        const futIdx = columns.value.findIndex(c => c.start && new Date(c.start) > nowT);
        focusedColIdx.value = futIdx === -1 ? Math.max(0, columns.value.length - 1) : Math.max(0, futIdx - 1);
      } catch(e){ ElMessage.error(e.message); }
      finally { matLoading.value = false; }
    }
    async function loadDashboard(){
      try {
        const qs = new URLSearchParams();
        qs.set('period', period.value);
        if (customRange.value && customRange.value.length === 2) {
          qs.set('start', customRange.value[0]); qs.set('end', customRange.value[1]);
        }
        if (company.value != null) qs.set('company', company.value);
        qs.set('view', view.value);
        const r = await api.get('/api/finance/dashboard?' + qs.toString());
        dashboard.value = r.data;
        nextTick(() => { disposeCharts(); if (detailFold.value) renderDetailCharts(); });
      } catch(e){}
    }
    function loadAll(){ loadSummary(); loadMatrix(); loadDashboard(); }
    // 粒度合法性：当前周期下该粒度是否可用
    function granAllowed(g){ return (GRANS_BY_PERIOD[period.value] || GRANS_BY_PERIOD.custom).includes(g); }
    function setPeriod(p){
      period.value = p; customRange.value = null;
      // 周期变了，粒度若非法→自动校正为该周期默认粒度（末位）
      const allowed = GRANS_BY_PERIOD[p] || GRANS_BY_PERIOD.custom;
      if (!allowed.includes(granularity.value)) granularity.value = allowed[allowed.length - 1];
      loadAll();
    }
    function onCustomRange(v){
      if (v && v.length === 2) {
        period.value = 'custom';
        // 自定义按跨度自动配粒度：≤31天→日 ≤26周→周 ≤24月→月 否则季
        const days = (new Date(v[1]) - new Date(v[0])) / 86400000 + 1;
        granularity.value = days <= 31 ? 'day' : days <= 182 ? 'week' : days <= 730 ? 'month' : 'quarter';
        loadAll();
      }
    }
    function onYearChange(){ period.value = 'year'; customRange.value = null; granularity.value = 'month'; loadAll(); }
    function toggleView(){
      viewMode.value = viewMode.value === 'matrix' ? 'pivot' : 'matrix';
      if (viewMode.value === 'pivot') nextTick(() => renderPivot());
    }

    // 透视图表渲染: 从matrix.value提取数据 → ECharts option
    function renderPivot(){
      if (!pivotChartRef.value || !matrix.value) return;
      if (pivotChart) { pivotChart.dispose(); pivotChart = null; }
      pivotChart = window.echarts.init(pivotChartRef.value);
      const cols = matrix.value.columns || [];
      const allRows = matrix.value.rows || [];
      // 过滤掉汇总行(E/F/F2), 只保留明细行
      const dataRows = allRows.filter(r => !['E:NET','F:CUMNET','F2:NONOP'].includes(r.id));
      const colLabels = cols.map(c => colLabel(c));
      const dim = pivotDim.value;
      const type = pivotType.value;

      if (type === 'heatmap') {
        renderHeatmap(pivotChart, dataRows, cols, colLabels, dim);
      } else if (type === 'waterfall') {
        renderWaterfall(pivotChart, allRows, cols, colLabels);
      } else if (dim === 'time') {
        renderByTime(pivotChart, dataRows, cols, colLabels, type);
      } else {
        renderBySection(pivotChart, dataRows, cols, colLabels, type);
      }
    }

    function renderByTime(chart, dataRows, cols, colLabels, type){
      // X=时间, 每个section一条series
      const sectionMap = {};
      dataRows.forEach(r => {
        if (!sectionMap[r.section]) sectionMap[r.section] = { name: r.section_title || r.title, data: [] };
        sectionMap[r.section].data = cols.map(c => Number(r.cells[c.key]?.v || 0));
      });
      const series = Object.values(sectionMap);
      const colors = { A:'#3b82f6', B:'#16a34a', C:'#06b6d4', D:'#dc2626' };
      chart.setOption({
        tooltip: { trigger: 'axis', axisPointer: {type:'shadow'} },
        legend: { top: 0, data: series.map(s=>s.name) },
        grid: { left: 70, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: colLabels, axisLabel: { rotate: colLabels.length>6?30:0 } },
        yAxis: { type: 'value', axisLabel: { formatter: v => (v/10000).toFixed(0)+'万' } },
        series: series.map((s,i) => ({
          name: s.name, type: type==='line'?'line':'bar', stack: type==='stack-bar'?'total':null,
          data: s.data, smooth: type==='line',
          itemStyle: { color: colors[s.name[0]] },
          areaStyle: type==='line' ? { opacity: 0.08 } : null,
        })),
      });
    }

    function renderBySection(chart, dataRows, cols, colLabels, type){
      // X=科目, 每个时间区间一条series
      const rowLabels = dataRows.map(r => r.title);
      const series = cols.map((c,ci) => ({
        name: colLabels[ci],
        type: type==='line'?'line':'bar', stack: type==='stack-bar'?'total':null,
        data: dataRows.map(r => Number(r.cells[c.key]?.v || 0)),
        smooth: type==='line',
      }));
      chart.setOption({
        tooltip: { trigger: 'axis', axisPointer: {type:'shadow'} },
        legend: { top: 0, type: 'scroll', data: series.map(s=>s.name) },
        grid: { left: 70, right: 20, top: 40, bottom: 40 },
        xAxis: { type: 'category', data: rowLabels, axisLabel: { rotate: 35, interval: 0 } },
        yAxis: { type: 'value', axisLabel: { formatter: v => (v/10000).toFixed(0)+'万' } },
        series,
      });
    }

    function renderHeatmap(chart, dataRows, cols, colLabels, dim){
      const rowLabels = dataRows.map(r => r.title);
      const data = [];
      let maxVal = 0;
      dataRows.forEach((r,ri) => {
        cols.forEach((c,ci) => {
          const v = Math.abs(Number(r.cells[c.key]?.v || 0));
          if (v > maxVal) maxVal = v;
          data.push([ci, ri, v]);
        });
      });
      chart.setOption({
        tooltip: { position: 'top', formatter: p => `${colLabels[p.value[0]]} / ${rowLabels[p.value[1]]}<br/>¥${fmt(p.value[2])}` },
        grid: { left: 120, right: 20, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: colLabels, splitArea: { show: true }, axisLabel: { rotate: colLabels.length>6?30:0 } },
        yAxis: { type: 'category', data: rowLabels, splitArea: { show: true } },
        visualMap: { min: 0, max: maxVal||1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
          inRange: { color: ['#e0f2fe','#7dd3fc','#0ea5e9','#0369a1','#075985'] } },
        series: [{ type: 'heatmap', data, label: { show: false },
          emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,.3)' } } }],
      });
    }

    function renderWaterfall(chart, allRows, cols, colLabels){
      // 瀑布图: 每个时间区间的净流(收-支), 累计
      const revRow = allRows.find(r => r.id === 'B:REV');
      const expRows = allRows.filter(r => r.section === 'D');
      const netByCol = cols.map(c => {
        const rev = Number(revRow?.cells[c.key]?.v || 0);
        const exp = expRows.reduce((s,r) => s + Number(r.cells[c.key]?.v || 0), 0);
        return rev - exp;
      });
      // 累计
      let cum = 0;
      const cumData = netByCol.map(v => { cum += v; return cum; });
      // 瀑布: base + 增量
      const base = [0]; const fall = [];
      for (let i = 0; i < netByCol.length; i++) {
        if (netByCol[i] >= 0) { base.push(cumData[i]); fall.push(netByCol[i]); }
        else { base.push(cumData[i]); fall.push(netByCol[i]); }
      }
      chart.setOption({
        tooltip: { trigger: 'axis', formatter: p => {
          const idx = p[0].dataIndex;
          return `${colLabels[idx]}<br/>净流: ¥${fmt(netByCol[idx])}<br/>累计: ¥${fmt(cumData[idx])}`;
        }},
        grid: { left: 70, right: 20, top: 30, bottom: 30 },
        xAxis: { type: 'category', data: colLabels, axisLabel: { rotate: colLabels.length>6?30:0 } },
        yAxis: { type: 'value', axisLabel: { formatter: v => (v/10000).toFixed(0)+'万' } },
        series: [
          { name: '基线', type: 'bar', stack: 'wf', itemStyle: { color: 'transparent' },
            data: base.slice(0, colLabels.length) },
          { name: '净流', type: 'bar', stack: 'wf',
            data: netByCol.map((v,i) => ({ value: Math.abs(v), itemStyle: { color: v>=0 ? '#16a34a' : '#dc2626' } })),
            label: { show: true, position: 'top', formatter: (p) => fmt(netByCol[p.dataIndex]) } },
        ],
      });
    }

    // 详细看板图表
    function renderDetailCharts(){
      if (!dashboard.value) return;
      const d = dashboard.value;
      if (trendRef.value) {
        const trend = window.echarts.init(trendRef.value);
        trend.setOption({
          tooltip: { trigger: 'axis' }, grid: { left: 60, right: 20, top: 30, bottom: 30 },
          xAxis: { type: 'category', data: d.trend.labels }, yAxis: { type: 'value' },
          series: [
            { name: '收入', type: 'line', smooth: true, data: d.trend.income, itemStyle: { color: '#16a34a' }, areaStyle: { color: 'rgba(22,163,74,.12)' } },
            { name: '支出', type: 'line', smooth: true, data: d.trend.expense, itemStyle: { color: '#dc2626' }, areaStyle: { color: 'rgba(220,38,38,.12)' } },
          ],
        });
        charts.push(trend);
      }
      if (pieRef.value) {
        const pie = window.echarts.init(pieRef.value);
        pie.setOption({
          tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
          legend: { bottom: 0, type: 'scroll' },
          series: [{ type: 'pie', radius: ['40%','68%'], center: ['50%','45%'], data: d.expense_breakdown,
            label: { formatter: '{b}\n{d}%', fontSize: 10 } }],
        });
        charts.push(pie);
      }
      const fmtY = v => '¥' + Number(v||0).toLocaleString('zh-CN',{maximumFractionDigits:0});
      if (arRef.value) {
        const ar = window.echarts.init(arRef.value);
        ar.setOption({ tooltip: { formatter: p => p.name + ': ¥' + Number(p.value||0).toLocaleString() }, grid: { left: 8, right: 55, bottom: 30 }, xAxis: { type: 'category', data: Object.keys(d.aging.ar), axisLabel:{interval:0} }, yAxis: { type: 'value', axisLabel:{formatter:fmtY} }, series: [{ type: 'bar', data: Object.values(d.aging.ar), itemStyle: { color: (p)=> ['#16a34a','#f59e0b','#f97316','#dc2626'][p.dataIndex] } }] });
        charts.push(ar);
      }
      if (apRef.value) {
        const ap = window.echarts.init(apRef.value);
        ap.setOption({ tooltip: { formatter: p => p.name + ': ¥' + Number(p.value||0).toLocaleString() }, grid: { left: 8, right: 55, bottom: 30 }, xAxis: { type: 'category', data: Object.keys(d.aging.ap), axisLabel:{interval:0} }, yAxis: { type: 'value', axisLabel:{formatter:fmtY} }, series: [{ type: 'bar', data: Object.values(d.aging.ap), itemStyle: { color: (p)=> ['#16a34a','#f59e0b','#f97316','#dc2626'][p.dataIndex] } }] });
        charts.push(ap);
      }
      if (compRef.value) {
        const comp = window.echarts.init(compRef.value);
        comp.setOption({ tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, legend: { bottom: 0 }, grid: { left: 60, right: 10, top: 20, bottom: 40 }, xAxis: { type: 'category', data: d.company_compare.map(c=>c.company) }, yAxis: { type: 'value' }, series: [
          { name: '收入', type: 'bar', data: d.company_compare.map(c=>c.income), itemStyle: { color: '#16a34a' } },
          { name: '支出', type: 'bar', data: d.company_compare.map(c=>c.expense), itemStyle: { color: '#dc2626' } },
          { name: '净额', type: 'bar', data: d.company_compare.map(c=>c.net), itemStyle: { color: '#2563eb' } },
        ] });
        charts.push(comp);
      }
    }
    function disposeCharts(){ while (charts.length) { const c = charts.pop(); try { c.dispose(); } catch {} } if (pivotChart) { try { pivotChart.dispose(); } catch {} pivotChart = null; } }
    const handleResize = () => { charts.forEach(c => { try { c.resize(); } catch {} }); if (pivotChart) { try { pivotChart.resize(); } catch {} } };

    function goVoucher(vid){
      // 跳到记账凭证tab（如果前端有对应路由/tab键）
      const want = 'vouchers';
      if (typeof window.__go === 'function') window.__go(want);
    }

    // 导出 Excel
    function doExport(){
      if (!matrix.value || !rows.value.length) return;
      const cols = columns.value;
      const headers = ['分组', '行项目', ...cols.map(c => colLabel(c)), totalCap.value];
      const aoa = [headers];
      const secMap = {A:'上期期末余额', B:'本期营收', C:'本期期末余额', D:'费用明细', E:'经营结果', F:'累计', F2:'勾稽校验'};
      let lastSec = '';
      for (const r of rows.value) {
        const secLabel = r.section !== lastSec ? secMap[r.section] || '' : '';
        lastSec = r.section;
        const row = [secLabel, rowTitle(r)];
        for (const c of cols) row.push(Number(r.cells[c.key].v||0));
        row.push(Number(r.total||0));
        aoa.push(row);
      }
      // 插入标题行
      aoa.unshift([`${winTitle.value}财务统计表 · ${granLabel.value} · ${companyLabel.value} · ${viewLabel.value}`]);
      aoa.splice(1, 0, []); // 空一行
      const ws = window.XLSX.utils.aoa_to_sheet(aoa);
      ws['!cols'] = [{wch:14},{wch:18}, ...cols.map(()=>({wch:12})),{wch:14}];
      // 合并 A1 跨标题
      ws['!merges'] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: headers.length - 1 } }];
      const wb = window.XLSX.utils.book_new();
      window.XLSX.utils.book_append_sheet(wb, ws, '财务统计表');
      window.XLSX.writeFile(wb, `财务统计表_${winTitle.value}_${Date.now()}.xlsx`);
    }

    onMounted(() => {
      _injectFinStyle();
      loadAll();
      window.addEventListener('resize', handleResize);
    });
    watch(detailFold, v => { if (v) nextTick(() => { disposeCharts(); renderDetailCharts(); }); else disposeCharts(); });
    watch(() => matrix.value, () => { if (viewMode.value === 'pivot') nextTick(() => renderPivot()); });
    onBeforeUnmount(() => { window.removeEventListener('resize', handleResize); disposeCharts(); });

    return {
      PERIODS, period, customRange, year, yearOptions, granularity, company, view, viewMode, detailFold,
      pivotDim, pivotType, pivotChartRef, renderPivot,
      sumLoading, matLoading, summary, matrix, columns, rows,
      granLabel, companyLabel, viewLabel, rowHeadCap, winTitle, totalCap,
      fmt, colLabel, sumPeriodText, rowTitle,
      focusedColIdx, expanded,
      sectionBreakBefore, firstInSection, rowClass, cellClass, hasFlow,
      onCellClick, toggleExpand, isExpanded, expDetail, singleAccRow, insightText, isBigFlow,
      drawer, drawerList, loadDrawerList, goVoucher,
      setPeriod, onCustomRange, onYearChange, granAllowed, toggleView, doExport,
      trendRef, pieRef, arRef, apRef, compRef,
    };
  }
};

/* ========== 财务看板样式 ========== */
const _FIN_STYLE_VER = '20260822-07';
function _injectFinStyle() {
  const STYLE_ID = 'fin-dash-style-' + _FIN_STYLE_VER;
  document.querySelectorAll('style[id^="fin-dash-style"]').forEach(el => el.remove());
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = `
  .fin-topbar{display:flex;gap:16px;align-items:flex-start;padding:8px 2px 10px;border-bottom:1px dashed #e2e8f0;margin-bottom:12px}
  .fin-tb-left{display:flex;gap:10px;align-items:center;flex:0 0 auto}
  .fin-tb-left .ph-icon{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#1d4ed8,#7c3aed);color:#fff;display:flex;align-items:center;justify-content:center}
  .fin-tb-ctl{flex:1}
  .ctl-row-1,.ctl-row-2{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:4px 0}
  .ctl-row-2{margin-top:6px}
  .ctl-item{display:flex;align-items:center;gap:6px}
  .ctl-label{font-size:12px;color:#475569;white-space:nowrap;font-weight:600}
  .ctl-spacer{flex:1}
  .fin-summary{background:linear-gradient(180deg,#f8fafc,#ffffff);border:1px solid #e2e8f0;border-radius:12px;padding:10px 14px;margin:6px 0 14px;position:sticky;top:0;z-index:10;box-shadow:0 2px 8px rgba(15,23,42,.06)}
  .sum-head{display:flex;justify-content:space-between;align-items:center;padding:2px 0 8px;border-bottom:1px dashed #cbd5e1;margin-bottom:8px}
  .sum-title{font-weight:800;color:#0f172a;font-size:13px}
  .sum-grid{display:grid;grid-template-columns:2fr 1.2fr 1.2fr 1.4fr 1.2fr;gap:12px}
  .sum-card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;position:relative;overflow:hidden}
  .sum-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:#cbd5e1}
  .sum-card.profit::before{background:linear-gradient(180deg,#16a34a,#059669)}
  .sum-card.cash::before{background:linear-gradient(180deg,#f59e0b,#d97706)}
  .sum-card.ar::before{background:linear-gradient(180deg,#dc2626,#b91c1c)}
  .sum-card.ap::before{background:linear-gradient(180deg,#ea580c,#c2410c)}
  .sc-lbl{font-size:11px;color:#475569;display:flex;align-items:baseline;gap:4px;font-weight:600}
  .sc-lbl .sc-sub{color:#64748b;font-size:10px}
  .sc-val{font-size:22px;font-weight:900;margin:2px 0;color:#0f172a;letter-spacing:-.5px}
  .sum-card.cash .sc-val{color:#92400e}
  .sc-foot{font-size:11px;color:#334155;margin-top:4px;font-weight:500}
  .up{color:#16a34a;font-weight:700}.down{color:#dc2626;font-weight:700}
  .ar-item{display:flex;justify-content:space-between;font-size:11px;color:#334155;padding:2px 0}
  .ar-item b{color:#dc2626;font-weight:800}
  /* ===== 现金告警（<3万）：红边+深红字+红底闪烁+震动 ===== */
  .sum-card.cash.low{border:3px solid #dc2626 !important;box-shadow:0 0 0 5px rgba(220,38,38,.18),0 0 20px rgba(220,38,38,.25) inset !important;animation:sum-cash-sos .9s ease-in-out infinite !important;background:linear-gradient(180deg,#fef2f2,#fee2e2) !important}
  .sum-card.cash.low::before{background:linear-gradient(180deg,#dc2626,#7f1d1d) !important;width:6px}
  .sum-card.cash.low .sc-val{color:#991b1b !important;animation:sos-text .8s ease-in-out infinite !important;font-weight:900;text-shadow:0 0 8px rgba(239,68,68,.4)}
  .sum-card.cash.low .sc-lbl,.sum-card.cash.low .sc-foot{color:#7f1d1d !important;font-weight:700}
  .acc-strip{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap;padding:8px 12px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0}
  .acc-chip{display:flex;flex-direction:column;align-items:center;min-width:100px;padding:6px 14px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;transition:all .2s}
  .acc-chip:hover{box-shadow:0 2px 8px rgba(0,0,0,.08)}
  .acc-chip.low{border-color:#fbbf24;background:linear-gradient(135deg,#fffbeb,#fef3c7)}
  .acc-chip .acc-name{font-size:11px;color:#64748b;font-weight:500;letter-spacing:.5px}
  .acc-chip .acc-bal{font-size:15px;color:#0f172a;margin-top:2px}
  @keyframes sum-cash-sos{0%,100%{transform:translateX(0);box-shadow:0 0 0 5px rgba(220,38,38,.18),0 0 20px rgba(220,38,38,.25) inset}25%{transform:translateX(-2px)}50%{transform:translateX(2px);box-shadow:0 0 0 8px rgba(220,38,38,.28),0 0 30px rgba(220,38,38,.4) inset}75%{transform:translateX(-2px)}}
  @keyframes sos-text{0%,100%{opacity:1}50%{opacity:.55}}

  .mat-panel{border-radius:10px}
  .mat-title{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:800;color:#0f172a}
  .mat-sub{font-size:12px;font-weight:500;color:#475569}
  .mat-spacer{flex:1}
  .mat-hint{color:#64748b;cursor:help}
  .mat-scroll{overflow:auto;max-height:70vh;position:relative}
  .mat-table{border-collapse:separate;border-spacing:0;width:100%;min-width:960px;font-size:12.5px}
  .mat-table thead th{position:sticky;top:0;background:#1e293b !important;color:#f8fafc !important;border-bottom:2px solid #0f172a;padding:9px 12px;font-weight:800;white-space:nowrap;z-index:3}
  .mat-table thead th:first-child{position:sticky;left:0;z-index:4;background:#1e293b !important}
  .mat-table thead th.col-rowhead{position:sticky;left:52px;z-index:4;background:#1e293b !important;border-right:1px solid #334155}
  .mat-table thead th.col-total{z-index:5;background:#4c1d95 !important;color:#faf5ff !important;border-left:2px solid #7c3aed}
  /* ===== 核心：默认 tbody td 明确深色字体 ===== */
  .mat-table tbody td{padding:6px 12px;border-bottom:1px solid #e2e8f0;white-space:nowrap;color:#1e293b !important;font-weight:500;font-family:Consolas,'Microsoft YaHei',monospace}
  /* ===== 关键修复：col-section/col-rowhead 不再设白背景，改为 transparent，让 tr 背景透出 ===== */
  .mat-table td.col-section{position:sticky;left:0;z-index:2;width:52px;text-align:center;border-right:2px solid #cbd5e1}
  .mat-table td.col-rowhead{position:sticky;left:52px;z-index:2;border-right:2px solid #cbd5e1;min-width:150px;font-weight:700 !important}
  .mat-table td.col-num{text-align:right;min-width:96px;font-variant-numeric:tabular-nums}
  .mat-table td.col-num.col-total{background:linear-gradient(180deg,#fef3c7,#fde68a) !important;color:#713f12 !important;font-weight:900 !important;position:sticky;right:0;z-index:2;border-left:2px solid #f59e0b}
  .sec-dot{font-size:9px;margin-right:6px}
  .sec-A{color:#0369a1}.sec-B{color:#15803d}.sec-C{color:#4338ca}.sec-D{color:#c2410c}.sec-E{color:#0f172a}.sec-F{color:#1d4ed8}.sec-F2{color:#6d28d9}
  .sec-break td{background:#1e293b !important;color:#f8fafc !important;font-weight:900;font-size:12.5px;padding:8px 14px !important;border-top:2px solid #0f172a;border-bottom:2px solid #0f172a !important;text-align:left !important;letter-spacing:1px}
  .row-plus{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:5px;background:#4f46e5;color:#fff;margin-right:6px;cursor:pointer;font-size:14px;font-weight:900;line-height:18px;user-select:none;box-shadow:0 1px 3px rgba(79,70,229,.3)}
  .row-plus:hover{background:#4338ca;transform:scale(1.1)}
  .row-plus.opened{background:#dc2626 !important;color:#fff !important;box-shadow:0 1px 3px rgba(220,38,38,.4)}
  .row-plus.ph{visibility:hidden}
  .row-title{font-weight:700;color:inherit !important}
  /* ===== A区：上期期末余额 —— 深天蓝色系 ===== */
  .mat-table tbody tr.row-A td{background:#bae6fd !important;color:#0c4a6e !important}
  .mat-table tbody tr.row-A td.col-section{background:#7dd3fc !important}
  .mat-table tbody tr.row-A td.col-rowhead{background:#7dd3fc !important;color:#075985 !important;font-weight:800 !important}
  /* ===== B区：本期营收 —— 深绿色系 ===== */
  .mat-table tbody tr.row-B td{background:#bbf7d0 !important;color:#14532d !important}
  .mat-table tbody tr.row-B td.col-section{background:#86efac !important}
  .mat-table tbody tr.row-B td.col-rowhead{background:#86efac !important;color:#166534 !important;font-weight:800 !important}
  /* ===== C区：本期期末余额 —— 深靛蓝色系 ===== */
  .mat-table tbody tr.row-C td{background:#c7d2fe !important;color:#312e81 !important}
  .mat-table tbody tr.row-C td.col-section{background:#a5b4fc !important}
  .mat-table tbody tr.row-C td.col-rowhead{background:#a5b4fc !important;color:#3730a3 !important;font-weight:800 !important}
  /* ===== D区：费用明细 —— 深橙色系 ===== */
  .mat-table tbody tr.row-D td{background:#fed7aa !important;color:#7c2d12 !important}
  .mat-table tbody tr.row-D td.col-section{background:#fdba74 !important}
  .mat-table tbody tr.row-D td.col-rowhead{background:#fdba74 !important;color:#9a3412 !important;font-weight:800 !important}
  /* ===== E区：经营结果 —— 深灰蓝 ===== */
  .mat-table tbody tr.row-E td{background:#cbd5e1 !important;color:#0f172a !important;font-weight:700 !important}
  .mat-table tbody tr.row-E td.col-section{background:#94a3b8 !important}
  .mat-table tbody tr.row-E td.col-rowhead{background:#94a3b8 !important;color:#0f172a !important;font-weight:800 !important}
  /* ===== F区：累计净增加 ===== */
  .mat-table tbody tr.row-F td{background:#67e8f9 !important;color:#164e63 !important;font-weight:800 !important}
  .mat-table tbody tr.row-F td.col-section{background:#22d3ee !important}
  .mat-table tbody tr.row-F td.col-rowhead{background:#22d3ee !important;color:#0e7490 !important;font-weight:900 !important}
  /* ===== 结果行（净额/累计）：强渐变+粗体 ===== */
  .mat-table tbody tr.row-result td{background:linear-gradient(180deg,#93c5fd,#60a5fa) !important;color:#1e3a8a !important;font-weight:900 !important;border-top:2px solid #2563eb;border-bottom:2px solid #2563eb}
  .mat-table tbody tr.row-result td.col-section,.mat-table tbody tr.row-result td.col-rowhead{background:linear-gradient(180deg,#60a5fa,#3b82f6) !important;color:#1e3a8a !important}
  /* ===== 非经营项净额（勾稽校验）：紫色系 ===== */
  .mat-table tbody tr.row-nonop td{background:linear-gradient(180deg,#ddd6fe,#c4b5fd) !important;color:#4c1d95 !important;font-weight:800 !important;border-top:1px dashed #7c3aed;border-bottom:1px dashed #7c3aed}
  .mat-table tbody tr.row-nonop td.col-section,.mat-table tbody tr.row-nonop td.col-rowhead{background:linear-gradient(180deg,#c4b5fd,#a78bfa) !important;color:#4c1d95 !important}
  /* ===== 现金行：金色渐变高亮 ===== */
  .mat-table tbody tr.row-cash td{background:linear-gradient(180deg,#fde68a,#fcd34d) !important;color:#78350f !important;font-weight:800 !important;border-top:1px solid #f59e0b;border-bottom:1px solid #f59e0b}
  .mat-table tbody tr.row-cash td.col-rowhead{background:linear-gradient(180deg,#fcd34d,#f59e0b) !important;color:#713f12 !important;font-weight:900 !important}
  .mat-table tbody tr.row-cash td.col-section{background:linear-gradient(180deg,#fde68a,#fcd34d) !important}
  /* ===== 现金告警(<3万)：血红色渐变+粗红边+高频闪烁 ===== */
  .mat-table tbody tr.row-cash.row-cash-low td,.mat-table tbody tr.row-cash.row-cash-low td.col-section,.mat-table tbody tr.row-cash.row-cash-low td.col-rowhead{background:linear-gradient(180deg,#fecaca,#fca5a5) !important;animation:row-sos .7s ease-in-out infinite !important;border-top:2px solid #dc2626 !important;border-bottom:2px solid #dc2626 !important}
  .mat-table tbody tr.row-cash.row-cash-low td.col-rowhead{color:#7f1d1d !important;font-weight:900 !important;animation:row-sos-text .6s ease-in-out infinite !important}
  @keyframes row-sos{0%,100%{box-shadow:inset 0 0 0 2px #dc2626,0 0 15px rgba(220,38,38,.3)}50%{box-shadow:inset 0 0 0 4px #b91c1c,0 0 25px rgba(220,38,38,.55);background:linear-gradient(180deg,#fca5a5,#f87171) !important}}
  @keyframes row-sos-text{0%,100%{opacity:1;text-shadow:0 0 5px rgba(220,38,38,.5)}50%{opacity:.6}}
  .cell-v{position:relative;display:inline-block;min-width:68px}
  .qv-tag{display:inline-block;font-size:10px;margin-left:5px;padding:1px 5px;border-radius:4px;font-weight:800}
  .qv-tag.↑{background:#dc2626 !important;color:#fff !important}
  .qv-tag.↓{background:#d97706 !important;color:#fff !important}
  .cell-warn-up{background:rgba(220,38,38,.22) !important}
  .cell-warn-down{background:rgba(217,119,6,.2) !important}
  .focus-col{box-shadow:inset 2px 0 0 #2563eb,inset -2px 0 0 #2563eb !important;background:rgba(37,99,235,.08) !important}
  .cell-rev{color:#15803d !important;font-weight:800 !important}
  .cell-exp{color:#c2410c !important;font-weight:700 !important}
  .cell-net{color:#1e3a8a !important;font-weight:900 !important}
  .cell-cash{color:#713f12 !important;font-weight:900 !important;background:rgba(251,191,36,.25) !important}
  .cell-expand{display:inline-block;margin-left:5px;opacity:.35;cursor:pointer;font-size:12px;color:#475569}
  .mat-table tbody td:hover .cell-expand{opacity:1;color:#2563eb;transform:scale(1.2)}
  .exp-row td{background:#f1f5f9 !important;border-bottom:1px dashed #94a3b8 !important;color:#334155 !important}
  .exp-sec{font-size:11px;color:#475569;font-weight:600}
  .exp-cell{font-size:11px;color:#334155;padding:6px 8px !important}
  .exp-items{display:flex;flex-direction:column;gap:3px}
  .exp-line{display:flex;justify-content:space-between;align-items:center;padding:1px 0}
  .exp-name{color:#475569;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:95px;font-weight:500}
  .exp-amt{color:#0f172a;font-weight:800;font-variant-numeric:tabular-nums}
  .exp-more{font-size:10px;color:#64748b;font-weight:600}
  .exp-line.total-row{border-top:1px dashed #94a3b8;margin-top:3px;padding-top:3px}
  .na{color:#94a3b8;font-weight:500}
  .exp-detail-row td{padding:0 !important;background:#f8fafc !important}
  .exp-panel{position:sticky;left:10px;width:min(1060px,calc(100vw - 230px));margin:8px 12px 12px 60px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;box-shadow:0 4px 16px rgba(15,23,42,.08);animation:expIn .18s ease;overflow-x:auto}
  @keyframes expIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
  .exp-sum{display:flex;gap:20px;align-items:center;padding-bottom:10px;border-bottom:1px dashed #e2e8f0;font-size:13px;color:#475569;flex-wrap:wrap}
  .exp-sum-col{font-weight:800;color:#1e293b;font-size:13.5px}
  .exp-sum .in{color:#047857;font-weight:700}
  .exp-sum .out{color:#b45309;font-weight:700}
  .exp-sum .op{color:#64748b}
  .exp-table{width:100%;border-collapse:collapse;margin-top:8px;font-size:12.5px}
  .exp-table th{text-align:left;color:#64748b;font-weight:600;padding:6px 8px;border-bottom:1px solid #e2e8f0;background:#f8fafc;position:sticky;top:0}
  .exp-table td{padding:5px 8px;border-bottom:1px solid #f1f5f9;color:#334155}
  .exp-table tbody tr:hover td{background:#f0f9ff}
  .exp-table .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .exp-table .in{color:#047857;font-weight:700}
  .exp-table .out{color:#b45309;font-weight:700}
  .exp-table .bal{color:#0f172a;font-weight:800}
  .exp-table td.acc{color:#818cf8;font-size:11.5px;white-space:nowrap}
  .exp-table td.sum{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#64748b}
  .exp-table tr.exp-big td{background:#fef9c3}
  .exp-table tr.exp-big td.num{font-weight:900}
  .vc-link{color:#4f46e5;cursor:pointer;text-decoration:underline;font-size:12px}
  .exp-insight{margin-top:10px;padding:8px 12px;background:#f0f9ff;border-left:3px solid #0ea5e9;border-radius:0 6px 6px 0;font-size:12.5px;color:#334155}
  .exp-empty{padding:16px;color:#94a3b8;font-size:13px;text-align:center}
  .exp-loading{margin:8px 12px 12px 60px;padding:16px;color:#94a3b8;font-size:13px}

  .detail-fold{border-radius:10px}
  .df-head{padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:8px;font-weight:800;color:#0f172a;border-bottom:1px solid #e2e8f0}
  .df-head:hover{background:#f8fafc}
  .df-icon{transition:transform .2s;color:#4f46e5}
  .df-icon.rot{transform:rotate(90deg)}
  .df-hint{flex:1;text-align:right;font-weight:500;color:#64748b;font-size:12px}
  .dr-filter{display:flex;align-items:center;gap:8px;padding:4px 0 10px;border-bottom:1px dashed #e2e8f0;margin-bottom:8px}
  .dr-total{font-size:12px;color:#0f172a;font-weight:800}
  .kpi-card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px}
  .k-label{font-size:11px;color:#475569;font-weight:600}
  .k-val{font-size:20px;font-weight:900;margin:2px 0;color:#0f172a}
  .k-sub{font-size:10px;color:#64748b;font-weight:500}
  .kpi-card.green{background:linear-gradient(180deg,#dcfce7,#bbf7d0);border-color:#86efac}
  .kpi-card.green .k-val{color:#166534}
  .kpi-card.red{background:linear-gradient(180deg,#fee2e2,#fecaca);border-color:#fca5a5}
  .kpi-card.red .k-val{color:#991b1b}
  .panel{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin-bottom:10px}
  .panel-title{font-size:13px;font-weight:800;color:#0f172a;margin:4px 0 8px}
  .cash-bad{color:#dc2626;font-weight:800;animation:shake .3s}
  @keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-2px)}75%{transform:translateX(2px)}}
  @media (max-width: 1200px){
    .sum-grid{grid-template-columns:1fr 1fr;gap:8px}
  }
  `;
  document.head.appendChild(s);
}
// 财务看板样式在组件挂载时注入
function exportToExcel(headers, rows, filename, sheetName='Sheet1') {
  if (!window.XLSX) { alert('Excel导出库未加载,请刷新页面'); return; }
  const data = [headers, ...rows];
  const ws = window.XLSX.utils.aoa_to_sheet(data);
  // 设置列宽
  const colWidths = headers.map(h => ({ wch: Math.max(12, String(h).length * 2) }));
  ws['!cols'] = colWidths;
  // 合并首行表头样式
  const range = window.XLSX.utils.decode_range(ws['!ref']);
  for (let C = range.s.c; C <= range.e.c; ++C) {
    const addr = window.XLSX.utils.encode_cell({ c: C, r: 0 });
    const cell = ws[addr];
    if (cell) { cell.s = { font: { bold: true, color: { rgb: 'FFFFFF' } }, fill: { fgColor: { rgb: '4472C4' } }, alignment: { horizontal: 'center', vertical: 'center' } }; }
  }
  const wb = window.XLSX.utils.book_new();
  window.XLSX.utils.book_append_sheet(wb, ws, sheetName);
  window.XLSX.writeFile(wb, filename + '.xlsx');
}
_injectFinStyle();  // 全局注入财务看板样式，确保任何时候进看板都已加载

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
    <div class="tabs-bar" v-if="tabs.length">
      <div 
        v-for="tab in tabs" 
        :key="tab.key" 
        :class="['tab-item', {active: active===tab.key}]" 
        @click="go(tab.key)"
      >
        <span v-html="Icon.icon(tab.icon,14)"></span>
        <span class="tab-label">{{tab.label}}</span>
        <span class="tab-close" v-if="tab.key !== 'dashboard'" @click.stop="closeTab(tab.key)">✕</span>
      </div>
      <div class="tabs-actions">
        <el-button size="small" text @click="closeOthers">关闭其他</el-button>
        <el-button size="small" text @click="closeAll">全部关闭</el-button>
      </div>
    </div>
    <div class="body">
      <div class="icon-rail">
        <div v-for="n in railNavDedup" :key="n.key" :class="['rail-item',{active:active===n.key}]" @click="go(n.key)" :title="n.label">
          <span v-html="Icon.icon(n.icon,20)"></span>
          <span class="rail-badge" v-if="badges[n.key]">{{badges[n.key]}}</span>
        </div>
        <div class="rail-spacer"></div>
      </div>
      <div class="content">
        <component :is="pageComp" :key="active"/>
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
    // 启动时从 localStorage 恢复上次的 active tab (刷新保活)
    function _loadInitialActive() {
      // 首页固定为工作台(不再恢复上次激活页, 避免刷一进就是财务看板)
      return 'dashboard';
    }
    const active = ref(_loadInitialActive());
    const badges = ref({});
    const isAdminOrGM = computed(() => ['ADMIN', 'GM'].includes(user.value?.role));
    const isAdmin = computed(() => user.value?.role === 'ADMIN');
    const roleLabel = computed(() => ({ADMIN:'管理员',GM:'总经理',SALES:'销售',FINANCE:'财务',MANAGER:'厂长',OPERATION:'运营',DEPARTMENT_HEAD:'部门主管'}[user.value?.role]||user.value?.role||'用户'));

    // 角色页面权限: 以 /api/admin/my-pages 返回为唯一真源。
    // ⚠️ 禁止前端本地 fallback 到角色大全集 — 后端永远返回保守默认值，前端失败时仅保留最小安全集。
    const SAFE_MIN_PAGES = ['dashboard', 'my-todos', 'my-done'];
    const rolePages = ref([]);
    async function loadRolePages() {
      if (!user.value?.role) { rolePages.value = SAFE_MIN_PAGES; window.__reloadQuickEntries && window.__reloadQuickEntries(); return; }
      if (user.value.role === 'ADMIN' || user.value.role === 'GM') { rolePages.value = '*'; window.__reloadQuickEntries && window.__reloadQuickEntries(); return; }
      try {
        const r = await api.get('/api/admin/my-pages');
        const pages = r.data;
        if (pages === '*' || (Array.isArray(pages) && pages.includes('*'))) {
          rolePages.value = '*';
        } else if (Array.isArray(pages) && pages.length) {
          rolePages.value = pages;
        } else {
          rolePages.value = SAFE_MIN_PAGES;
        }
      } catch(e) {
        rolePages.value = SAFE_MIN_PAGES;
      } finally {
        window.__reloadQuickEntries && window.__reloadQuickEntries();
      }
    }
    function _hasPage(key) {
      const rc = user.value?.role || '';
      if (rc === 'ADMIN' || rc === 'GM') return true;
      const p = rolePages.value;
      if (p === '*' || (Array.isArray(p) && p.includes('*'))) return true;
      return Array.isArray(p) && p.includes(key);
    }
    const ALL_NAV = [
      {key:'dashboard',label:'工作台',icon:'squares-2x2',group:'核心'},
      {key:'my-todos',label:'我的待办',icon:'bell',group:'核心'},
      {key:'my-done',label:'我的已办',icon:'clipboard-document-check',group:'核心'},
      {key:'workflow-list',label:'业务流程',icon:'list-bullet',group:'核心'},
      {key:'approvals',label:'审批中心',icon:'check-circle',group:'核心'},
      {key:'analysis',label:'经营分析',icon:'chart-bar',group:'核心'},
      {key:'opportunities',label:'商机管理',icon:'target',group:'销售'},
      {key:'customers',label:'客户档案',icon:'building-storefront',group:'销售'},
      {key:'orders',label:'销售订单',icon:'shopping-cart',group:'销售'},
      {key:'sample-request',label:'打样申请',icon:'beaker',group:'销售'},
      {key:'inventory',label:'库存查询',icon:'package',group:'仓储'},
      {key:'stock-check',label:'月度盘点',icon:'clipboard-document-check',group:'仓储'},
      {key:'stock-moves',label:'出入库流水',icon:'arrow-right-left',group:'仓储'},
      {key:'purchases',label:'采购订单',icon:'clipboard-document-list',group:'采购'},
      {key:'purchase-requests',label:'采购申请',icon:'document-text',group:'采购'},
      {key:'work-orders',label:'加工工单',icon:'wrench',group:'生产'},
      {key:'completions',label:'完工单',icon:'check-badge',group:'生产'},
      {key:'shipments',label:'出货单',icon:'truck',group:'生产'},
      {key:'requisitions',label:'领料出库',icon:'arrow-down-tray',group:'生产'},
      {key:'outsource',label:'外协单',icon:'arrow-path',group:'生产'},
      {key:'finance',label:'财务单据',icon:'banknotes',group:'财务'},
      {key:'finance-dashboard',label:'财务看板',icon:'document-chart-bar',group:'财务'},
      {key:'receivables',label:'应收管理',icon:'credit-card',group:'财务'},
      {key:'receivable-remind',label:'收款提醒',icon:'bell',group:'财务'},
      {key:'payroll',label:'工资管理',icon:'wallet',group:'财务'},
      {key:'expense',label:'费用报销',icon:'receipt-tax',group:'财务'},
      {key:'vouchers',label:'凭证管理',icon:'document',group:'财务'},
      {key:'reports',label:'财务报表',icon:'document-chart-bar',group:'财务'},
      {key:'accounts',label:'会计科目',icon:'book-open',group:'财务'},
      {key:'acceptances',label:'承兑汇票',icon:'ticket',group:'财务'},
      {key:'prepayments',label:'采购预付',icon:'arrow-up-circle',group:'财务'},
      {key:'loan-request',label:'借款申请',icon:'banknotes',group:'财务'},
      {key:'ai-finance',label:'财务AI助手',icon:'cpu-chip',group:'财务'},
      {key:'screen',label:'车间大屏',icon:'tv',group:'其他'},
      {key:'flow-design',label:'流程设计',icon:'paint-brush',group:'管理'},
      {key:'users',label:'用户管理',icon:'user-plus',group:'管理'},
      {key:'roles',label:'角色权限',icon:'shield-check',group:'管理'},
      {key:'number-rules',label:'编号规则',icon:'hashtag',group:'管理'},
      {key:'ai-analysis',label:'AI经营分析',icon:'cpu-chip',group:'分析'},
    ];
    const navItems = computed(() => {
      const rc = user.value?.role || '';
      let arr;
      if (rc === 'ADMIN' || rc === 'GM') arr = ALL_NAV.filter(n => n.group !== '管理' && n.group !== '分析');
      else arr = ALL_NAV.filter(n => n.group !== '管理' && n.group !== '分析' && _hasPage(n.key));
      // 按key去重 (Set + Map保证稳定顺序; 防御重复图标/重复key渲染)
      const seen = new Set();
      return arr.filter(n => seen.has(n.key) ? false : (seen.add(n.key), true));
    });
    const extraTabs = computed(() => {
      const rc = user.value?.role || '';
      let arr;
      if (rc === 'ADMIN' || rc === 'GM') arr = ALL_NAV.filter(n => n.group === '管理' || n.group === '分析');
      else arr = ALL_NAV.filter(n => (n.group === '管理' || n.group === '分析') && _hasPage(n.key));
      const seen = new Set();
      return arr.filter(n => seen.has(n.key) ? false : (seen.add(n.key), true));
    });
    const allTabs = computed(() => [...navItems.value, ...extraTabs.value]);
    // rail导航: 仅显示核心入口, 其余通过顶部Tabs访问
    const RAIL_KEYS = ['dashboard','my-todos','my-done','workflow-list','approvals','analysis','opportunities','customers','orders','sample-request','finance','finance-dashboard','ai-finance','flow-design','users'];
    const railNavDedup = computed(() => {
      const all = allTabs.value.filter(n => RAIL_KEYS.includes(n.key));
      const seen = new Set();
      return all.filter(n => seen.has(n.key) ? false : (seen.add(n.key), true));
    });
    const getTabInfo = (key) => (allTabs.value || []).find(t => t.key === key) || { key, label: key, icon: 'circle' };
    
    // Tab管理 - 持久化到localStorage,刷新后恢复 (类似钉钉/飞书行为)
    const TABS_STORAGE_KEY = 'erp_tabs_v1';
    function _loadTabs() {
      try {
        const raw = localStorage.getItem(TABS_STORAGE_KEY);
        if (!raw) return [{ key: 'dashboard', label: '工作台', icon: 'dashboard' }];
        const saved = JSON.parse(raw);
        if (!Array.isArray(saved.tabs) || !saved.tabs.length) return [{ key: 'dashboard', label: '工作台', icon: 'dashboard' }];
        // 用当前 navItems 校验每个key是否还有权限(否则丢弃)
        const allKeys = new Set(allTabs.value.map(n => n.key));
        const valid = saved.tabs.filter(t => allKeys.has(t.key) || t.key === 'dashboard');
        if (!valid.length) return [{ key: 'dashboard', label: '工作台', icon: 'dashboard' }];
        // 补全 label/icon (可能权限或菜单变更)
        return valid.map(t => {
          const info = getTabInfo(t.key);
          return { key: t.key, label: info.label || t.label, icon: info.icon || t.icon };
        });
      } catch (e) {
        return [{ key: 'dashboard', label: '工作台', icon: 'dashboard' }];
      }
    }
    function _loadActive() {
      // 首页固定为工作台(不再恢复上次激活页)
      return 'dashboard';
    }
    function _saveTabs() {
      try {
        localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify({ tabs: tabs.value, active: active.value }));
      } catch (e) {}
    }
    const tabs = ref(_loadTabs());

    function openTab(key) {
      // Tab打开前强制权限校验: 杜绝无权限代码调用打开Tab后才报错
      const rc = user.value?.role || '';
      const isAdminOrGM = rc === 'ADMIN' || rc === 'GM';
      const adminOnly = ['flow-design', 'approval-flows', 'users', 'roles', 'number-rules'];
      if (adminOnly.includes(key) && !isAdminOrGM) return;
      if (!isAdminOrGM && !_hasPage(key)) return;

      const info = getTabInfo(key);
      if (!tabs.value.find(t => t.key === key)) {
        tabs.value.push({ key: key, label: info.label, icon: info.icon });
      }
      active.value = key;
      window.location.hash = '#/' + key;
      _saveTabs();
    }

    function closeTab(key) {
      if (key === 'dashboard') return;
      const idx = tabs.value.findIndex(t => t.key === key);
      if (idx > -1) {
        tabs.value.splice(idx, 1);
        if (active.value === key) {
          active.value = tabs.value[Math.max(0, idx - 1)]?.key || 'dashboard';
          window.location.hash = '#/' + active.value;
        }
        _saveTabs();
      }
    }

    function closeOthers() {
      tabs.value = tabs.value.filter(t => t.key === active.value || t.key === 'dashboard');
      _saveTabs();
    }

    function closeAll() {
      tabs.value = tabs.value.filter(t => t.key === 'dashboard');
      active.value = 'dashboard';
      window.location.hash = '#/dashboard';
      _saveTabs();
    }
    
    const pageMap = {
      'dashboard': DashboardPage, 'my-todos': MyTodosPage, 'my-done': MyDonePage,
      'workflow-list': WorkflowListPage,
      'customers': CustomersPage, 'opportunities': OpportunitiesPage, 'orders': OrdersPage, 'work-orders': WorkOrdersPage,
      'completions': CompletionsPage, 'requisitions': RequisitionsPage,
      'inventory': InventoryPage, 'finance': FinancePage, 'purchases': PurchasesPage,
      'finance-dashboard': FinanceDashboardPage,
      'pr': PRPage, 'payroll': PayrollPage, 'approvals': ApprovalsPage,
      'approval-flows': FlowDesignPage, 'flow-design': FlowDesignPage,
      'users': UsersPage, 'roles': RolesPage,
      'sales-adjustments': SalesAdjustmentPage,
      'screen': ScreenPage,
      'analysis': AnalysisPage,
      'ai-finance': AnalysisPage,
      'sample-request': SampleRequestPage,
      'expense': ExpensePage,
      'purchase-requests': PurchaseRequestsPage,
      'receivables': ReceivablesPage,
      'stock-moves': StockMovesPage,
      'consign-log': ConsignLogPage,
      'vouchers': VouchersPage,
      'reports': ReportsPage,
      'accounts': AccountsPage,
      'acceptances': AcceptancesPage,
      'outsource': OutsourcePage,
      'prepayments': PrepaymentPage,
      'loan-request': LoanRequestPage,
      'shipments': ShipmentsPage,
      'stock-check': StockCheckPage,
      'receivable-remind': ReceivableRemindPage,
      'number-rules': NumberRulesPage,
    };
    const pageComp = computed(() => pageMap[active.value] || DashboardPage);
    function go(key) { openTab(key); }
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
      if (m && pageMap[m[1]]) {
        const page = m[1];
        const rc = user.value.role;
        const isAdminOrGM = rc === 'ADMIN' || rc === 'GM';
        // 管理员专属页面 - GM也可以访问
        const adminOnly = ['flow-design', 'approval-flows', 'users', 'roles', 'number-rules'];
        if (adminOnly.includes(page) && !isAdminOrGM) {
          go('dashboard'); return;
        }
        // 角色权限校验: 非管理员/GM只能访问已授权的页面
        if (!isAdminOrGM && !_hasPage(page)) {
          console.log('[PERMDEBUG] hash denied:', page, 'rolePages:', rolePages.value, 'rc:', rc);
          go('dashboard'); return;
        }
        // 如果不是当前激活的tab，打开它
        if (active.value !== page) {
          openTab(page);
        }
      }
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
        try { user.value = JSON.parse(localStorage.getItem(USER_KEY)); loadRolePages(); handleHash(); loadBadges(); } catch {}
      }
    });
    // 页面初始化时: 如果已有登录用户,立刻加载权限
    if (user.value?.role) loadRolePages();
    // 暴露给LoginPage登录成功后调用
    window.__onLoginOk = function(u) {
      user.value = u;
      active.value = 'dashboard';
      tabs.value = [{ key: 'dashboard', label: '工作台', icon: 'dashboard' }];
      location.hash = '#/dashboard';
      nextTick(async () => { await loadRolePages(); handleHash(); loadBadges(); });
    };
    // 401凭证失效时由api.req调用: 同步清空App的user.value, 让v-if="!user"切回LoginPage
    window.__forceLogout = function() {
      user.value = null;
      active.value = 'dashboard';
    };
    // 暴露go函数给子组件调用（用于打开新TAB）
    window.__go = function(key) { openTab(key); };
    // 全局权限校验 (供Dashboard等子组件/跨作用域使用; 唯一真源)
    window.__hasPage = _hasPage;
    window.__getRolePages = () => rolePages.value;
    return { user, active, pageComp, navItems, railNavDedup, tabs, badges, isAdmin, isAdminOrGM, roleLabel, go, closeTab, closeOthers, closeAll, logout, Icon };
  }
};

const app = createApp(App);

// 全局属性注入 Icon: 模板中所有 <... Icon> 都能从 globalProperties 解析(不依赖各组件setup是否返回)
app.config.globalProperties.Icon = Icon;

// 全局错误处理 - 防止蓝屏
app.config.errorHandler = function(err, vm, info) {
  const comp = (vm && (vm.$.type.name || vm.$.type.__name)) || '?';
  console.error('[Vue错误]', err, '组件:', comp, info);
  // 尝试显示友好提示而非蓝屏
  const el = document.getElementById('app');
  if (el && !el.querySelector('.vue-error-boundary')) {
    // 不强制修改DOM，避免更多问题
  }
};

// 全局警告处理
app.config.warnHandler = function(msg, vm, trace) {
  console.warn('[Vue警告]', msg, trace);
};

app.use(ElementPlus);
// 全局注册所有页面组件
app.component('LoginPage', LoginPage);
app.mount('#app');
