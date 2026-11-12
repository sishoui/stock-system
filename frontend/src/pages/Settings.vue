<script setup>
// AI 接口配置（spec §4.4）：api_key / base_url / model_name / is_active 页面可编辑
import { onMounted, ref } from 'vue'
import { get, post } from '../api'

const cfg = ref({
  id: 0,
  api_key_masked: '',
  base_url: 'https://api.openai.com/v1',
  model_name: 'gpt-3.5-turbo',
  is_active: false,
})
// 编辑表单（api_key 为空时保持不覆盖）
const form = ref({
  api_key: '',
  base_url: '',
  model_name: '',
  is_active: false,
})
const msg = ref('')
const msgType = ref('info') // info | success | error
const saving = ref(false)
const testing = ref(false)
const showKey = ref(false)

async function load() {
  try {
    const c = await get('/ai-config')
    cfg.value = c
    form.value = {
      api_key: '',
      base_url: c.base_url,
      model_name: c.model_name,
      is_active: c.is_active,
    }
    msg.value = ''
  } catch (e) {
    msg.value = '加载配置失败：' + e
    msgType.value = 'error'
  }
}

function maskKey(key) {
  if (!key) return ''
  if (key.length <= 8) return '*'.repeat(key.length)
  return '*'.repeat(key.length - 4) + key.slice(-4)
}

async function save() {
  saving.value = true
  msg.value = ''
  try {
    const payload = {
      api_key: form.value.api_key, // 为空则后端保持原值不覆盖
      base_url: form.value.base_url,
      model_name: form.value.model_name,
      is_active: form.value.is_active,
    }
    const c = await post('/ai-config', payload)
    cfg.value = c
    form.value.api_key = '' // 不明文回显
    msg.value = '已保存'
    msgType.value = 'success'
  } catch (e) {
    msg.value = '保存失败：' + e
    msgType.value = 'error'
  } finally {
    saving.value = false
  }
}

async function testConn() {
  testing.value = true
  msg.value = ''
  try {
    const r = await post('/ai-config/test', {
      api_key: form.value.api_key,
      base_url: form.value.base_url,
      model_name: form.value.model_name,
      is_active: form.value.is_active,
    })
    if (r.ok) {
      msg.value = '连通正常'
      msgType.value = 'success'
    } else {
      msg.value = '连通失败：' + r.message
      msgType.value = 'error'
    }
  } catch (e) {
    msg.value = '测试异常：' + e
    msgType.value = 'error'
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="panel">
    <h3>AI 接口配置</h3>
    <div class="muted" style="margin-bottom:10px">
      可在本页面填写 API Key、Base URL 与模型名称，保存后即可用于 AI 三判断评估。
      API Key 仅在本机 SQLite 中存储，返回时脱敏显示。
    </div>

    <div style="margin-bottom:10px">
      <label class="muted" style="display:block;margin-bottom:4px">API Key</label>
      <input
        v-model="form.api_key"
        :type="showKey ? 'text' : 'password'"
        placeholder="sk-……"
        :disabled="saving || testing"
        style="width:100%;max-width:420px"
      />
      <button
        class="btn"
        :disabled="saving || testing"
        style="margin-left:8px;padding:6px 12px;font-size:12px"
        @click="showKey = !showKey"
      >
        {{ showKey ? '隐藏' : '显示' }}
      </button>
    </div>

    <div style="margin-bottom:10px">
      <label class="muted" style="display:block;margin-bottom:4px">Base URL</label>
      <input
        v-model="form.base_url"
        placeholder="https://api.openai.com/v1"
        :disabled="saving || testing"
        style="width:100%;max-width:420px"
      />
      <div class="muted" style="font-size:12px;margin-top:4px">
        兼容 OpenAI / 智谱 / 本地部署，例如 https://open.bigmodel.cn/api/paas/v4
      </div>
    </div>

    <div style="margin-bottom:10px">
      <label class="muted" style="display:block;margin-bottom:4px">模型名称</label>
      <input
        v-model="form.model_name"
        placeholder="gpt-3.5-turbo / glm-4-flash"
        :disabled="saving || testing"
        style="width:100%;max-width:420px"
      />
    </div>

    <div style="margin-bottom:10px">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" v-model="form.is_active" :disabled="saving || testing" />
        <span>启用 AI 评估</span>
      </label>
    </div>

    <div style="margin-bottom:10px">
      <button class="btn" :disabled="saving || testing" @click="testConn">
        {{ testing ? '测试中…' : '测试连接' }}
      </button>
      <button class="btn" :disabled="saving || testing" @click="save" style="margin-left:8px">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </div>

    <div v-if="msg" :class="['muted', msgType === 'success' ? 'up' : msgType === 'error' ? 'down' : '']">
      {{ msg }}
    </div>

    <div v-if="cfg.id" style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border)">
      <div class="muted" style="margin-bottom:6px">当前配置</div>
      <div style="margin-bottom:4px">
        <span class="muted" style="margin-right:8px">API Key：</span>
        <span style="font-family:monospace;font-size:12px">{{ cfg.api_key_masked || '（未配置）' }}</span>
      </div>
      <div style="margin-bottom:4px">
        <span class="muted" style="margin-right:8px">Base URL：</span>
        <span>{{ cfg.base_url }}</span>
      </div>
      <div style="margin-bottom:4px">
        <span class="muted" style="margin-right:8px">模型：</span>
        <span>{{ cfg.model_name }}</span>
      </div>
      <div style="margin-bottom:4px">
        <span class="muted" style="margin-right:8px">启用：</span>
        <span :class="cfg.is_active ? 'up' : 'down'">{{ cfg.is_active ? '是' : '否' }}</span>
      </div>
      <div class="muted" style="font-size:12px">更新时间：{{ cfg.updated_at }}</div>
    </div>
  </div>
</template>
