<script setup lang="ts">
import {
  ElButton,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTabPane,
  ElTabs,
  ElTag,
  ElUpload,
  type UploadFile
} from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-empty.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-option.css'
import 'element-plus/theme-chalk/el-select.css'
import 'element-plus/theme-chalk/el-tabs.css'
import 'element-plus/theme-chalk/el-tag.css'
import 'element-plus/theme-chalk/el-upload.css'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import { getApiErrorMessage } from '../api/client'
import {
  createKnowledgeDocument,
  deleteKnowledgeDocument,
  fetchIngestionTasks,
  fetchKnowledgeDocuments,
  reexecuteIngestionTask,
  retryIngestionTask,
  updateKnowledgeDocument,
  uploadKnowledgeFile,
  type KnowledgeDocument,
  type KnowledgeDocumentPayload,
  type KnowledgeIngestionTask
} from '../api/knowledge'

const loading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const taskLoading = ref(false)
const documents = ref<KnowledgeDocument[]>([])
const tasks = ref<KnowledgeIngestionTask[]>([])
const selectedId = ref<number | null>(null)
const uploadFile = ref<File | null>(null)
const query = ref('')
const mobileEditorOpen = ref(false)
let taskPollTimer: ReturnType<typeof window.setInterval> | undefined

const form = reactive<KnowledgeDocumentPayload>({ title: '', category: '', target_position: '', content: '', metadata: {} })
const uploadForm = reactive({ title: '', category: '', target_position: 'general' })
const knowledgeCategories = ['岗位能力', '面试计划', '评分标准', '面试题库', '常见追问', '优秀回答样例']
const editorSnapshot = ref('')

function serializeEditor() {
  return JSON.stringify({
    title: form.title,
    category: form.category,
    target_position: form.target_position,
    content: form.content,
    metadata: form.metadata || {}
  })
}

function markEditorSaved() {
  editorSnapshot.value = serializeEditor()
}

function restoreEditorSnapshot() {
  if (!editorSnapshot.value) return
  Object.assign(form, JSON.parse(editorSnapshot.value) as KnowledgeDocumentPayload)
}

const filteredDocuments = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return documents.value
  return documents.value.filter((doc) => [doc.title, doc.category, doc.target_position, doc.content].some((value) => value.toLowerCase().includes(keyword)))
})
const activeTasks = computed(() => tasks.value.filter((task) => ['pending', 'running'].includes(task.status)))
const isEditorDirty = computed(() => Boolean(editorSnapshot.value) && serializeEditor() !== editorSnapshot.value)

onMounted(async () => {
  await Promise.all([loadDocuments(), loadTasks()])
  taskPollTimer = window.setInterval(() => { if (activeTasks.value.length) loadTasks() }, 2500)
})
onUnmounted(() => { if (taskPollTimer) window.clearInterval(taskPollTimer) })

async function loadDocuments() {
  loading.value = true
  try {
    const { data } = await fetchKnowledgeDocuments()
    documents.value = data
    if (!selectedId.value && data.length) selectDocument(data[0], false)
  } catch (error) { ElMessage.error(getApiErrorMessage(error, '知识文档加载失败')) }
  finally { loading.value = false }
}

async function loadTasks() {
  taskLoading.value = true
  try { tasks.value = (await fetchIngestionTasks()).data }
  catch (error) { ElMessage.error(getApiErrorMessage(error, '入库任务加载失败')) }
  finally { taskLoading.value = false }
}

function selectDocument(document: KnowledgeDocument, openEditor = true) {
  selectedId.value = document.id
  form.title = document.title
  form.category = document.category
  form.target_position = document.target_position
  form.content = document.content
  form.metadata = JSON.parse(JSON.stringify(document.metadata || {})) as Record<string, unknown>
  markEditorSaved()
  if (openEditor) mobileEditorOpen.value = true
}

function newDocument() {
  selectedId.value = null
  form.title = ''; form.category = ''; form.target_position = ''; form.content = ''; form.metadata = { source: 'manual' }
  markEditorSaved()
  mobileEditorOpen.value = true
}

async function saveDocument() {
  if (!form.title.trim() || !form.category.trim() || !form.target_position.trim() || !form.content.trim()) {
    ElMessage.warning('请完整填写标题、分类、适用岗位和正文内容')
    return false
  }
  saving.value = true
  try {
    const payload = { ...form, metadata: form.metadata || {} }
    const { data } = selectedId.value ? await updateKnowledgeDocument(selectedId.value, payload) : await createKnowledgeDocument(payload)
    ElMessage.success(selectedId.value ? '文档已更新' : '文档已创建')
    await loadDocuments(); selectDocument(data)
    return true
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '保存失败'))
    return false
  }
  finally { saving.value = false }
}

async function returnToMobileList() {
  if (!isEditorDirty.value) {
    mobileEditorOpen.value = false
    return
  }

  try {
    await ElMessageBox.confirm('当前文档有未保存的修改。保存后返回，或放弃本次修改。', '未保存的修改', {
      confirmButtonText: '保存修改',
      cancelButtonText: '放弃修改',
      distinguishCancelAndClose: true,
      closeOnClickModal: false,
      type: 'warning'
    })
    if (await saveDocument()) mobileEditorOpen.value = false
  } catch (action) {
    if (action !== 'cancel') return
    restoreEditorSnapshot()
    mobileEditorOpen.value = false
  }
}

async function removeDocument(document: KnowledgeDocument) {
  await ElMessageBox.confirm(`确认删除“${document.title}”吗？`, '删除文档', { type: 'warning' })
  try { await deleteKnowledgeDocument(document.id); ElMessage.success('文档已删除'); if (selectedId.value === document.id) { newDocument(); mobileEditorOpen.value = false }; await loadDocuments() }
  catch (error) { ElMessage.error(getApiErrorMessage(error, '删除失败')) }
}

function isSupportedKnowledgeFile(file: File) { return ['.txt', '.md', '.pdf'].includes(file.name.slice(file.name.lastIndexOf('.')).toLowerCase()) }
function beforeUpload(file: File) { if (!isSupportedKnowledgeFile(file)) ElMessage.warning('仅支持 txt、md 和 pdf 文件'); return false }
function onUploadChange(file: UploadFile) {
  const rawFile = file.raw
  if (!rawFile) return
  if (!isSupportedKnowledgeFile(rawFile)) { uploadFile.value = null; ElMessage.warning('仅支持 txt、md 和 pdf 文件'); return }
  uploadFile.value = rawFile
  if (!uploadForm.title.trim()) uploadForm.title = rawFile.name.replace(/\.[^.]+$/, '')
}
function onUploadRemove() { uploadFile.value = null }

async function submitUpload() {
  if (!uploadFile.value) return ElMessage.warning('请先选择文件')
  if (!uploadForm.category.trim()) return ElMessage.warning('请填写文档分类')
  uploading.value = true
  try {
    const { data } = await uploadKnowledgeFile({ file: uploadFile.value, title: uploadForm.title, category: uploadForm.category, target_position: uploadForm.target_position })
    ElMessage.success(`已提交入库任务 #${data.id}`)
    uploadFile.value = null; uploadForm.title = ''
    await loadTasks()
  } catch (error) { ElMessage.error(getApiErrorMessage(error, '文件入库任务提交失败')) }
  finally { uploading.value = false }
}

async function retryTask(task: KnowledgeIngestionTask, full: boolean) {
  try {
    const { data } = full ? await reexecuteIngestionTask(task.id) : await retryIngestionTask(task.id)
    tasks.value = tasks.value.map((item) => item.id === data.id ? data : item)
    ElMessage.success(full ? '任务已重新执行' : '任务已重试')
  } catch (error) { ElMessage.error(getApiErrorMessage(error, '任务操作失败')) }
}

function statusType(status: string) { return status === 'succeeded' ? 'success' : ['failed', 'dead', 'publish_failed'].includes(status) ? 'danger' : 'warning' }
function stageLabel(stage: string) { return ({ queued: '排队中', parsing: '解析中', embedding: 'Embedding 中', indexing: '写入索引', completed: '已完成', failed: '失败', quality_failed: '质量失败', publish_failed: '发布失败' } as Record<string, string>)[stage] || stage }
</script>

<template>
  <main class="app-page">
    <section class="content knowledge-page">
      <header class="section-header">
        <div><span class="eyebrow">知识库管理</span><h1>面试知识库</h1><p>管理岗位资料、评分标准、追问策略与示范答案，为 AI 面试提供专业依据。</p></div>
        <el-button :loading="loading" @click="loadDocuments">刷新数据</el-button>
      </header>
      <div class="knowledge-layout" :class="{ 'mobile-editor-open': mobileEditorOpen }">
        <aside class="knowledge-sidebar">
          <div class="sidebar-actions"><el-input v-model="query" placeholder="搜索标题、分类或岗位" clearable /><el-button type="primary" @click="newDocument">新建</el-button></div>
          <el-empty v-if="filteredDocuments.length === 0" description="暂无知识文档" />
          <button v-for="document in filteredDocuments" :key="document.id" type="button" class="knowledge-item" :class="{ active: document.id === selectedId }" :aria-pressed="document.id === selectedId" @click="selectDocument(document)"><span>{{ document.title }}</span><small>{{ document.category }} / {{ document.target_position }}</small></button>
        </aside>
        <section class="knowledge-editor">
          <button class="mobile-editor-back" type="button" :aria-label="isEditorDirty ? '返回文档列表，有未保存的修改' : '返回文档列表'" @click="returnToMobileList">
            <span aria-hidden="true">←</span> 返回文档列表<span v-if="isEditorDirty" class="unsaved-indicator"> · 未保存</span>
          </button>
          <el-tabs>
            <el-tab-pane label="编辑文档">
              <el-form label-position="top" @submit.prevent="saveDocument"><div class="knowledge-form-grid"><el-form-item label="标题"><el-input v-model="form.title" /></el-form-item><el-form-item label="分类"><el-select v-model="form.category" placeholder="选择知识用途"><el-option v-if="form.category && !knowledgeCategories.includes(form.category)" :label="form.category" :value="form.category" /><el-option v-for="category in knowledgeCategories" :key="category" :label="category" :value="category" /></el-select></el-form-item><el-form-item label="适用岗位" class="span-2"><el-input v-model="form.target_position" /></el-form-item><el-form-item label="正文内容" class="span-2"><el-input v-model="form.content" type="textarea" :rows="18" resize="vertical" /></el-form-item></div><div class="editor-actions"><el-button type="danger" plain :disabled="!selectedId" @click="selectedId && removeDocument(documents.find((item) => item.id === selectedId)!)">删除文档</el-button><el-button type="primary" :loading="saving" native-type="submit">保存并重建索引</el-button></div></el-form>
            </el-tab-pane>
            <el-tab-pane label="文件导入">
              <div class="upload-panel"><el-form label-position="top"><div class="knowledge-form-grid"><el-form-item label="标题"><el-input v-model="uploadForm.title" placeholder="留空时使用文件名" /></el-form-item><el-form-item label="分类"><el-select v-model="uploadForm.category" placeholder="选择知识用途"><el-option v-for="category in knowledgeCategories" :key="category" :label="category" :value="category" /></el-select></el-form-item><el-form-item label="适用岗位" class="span-2"><el-input v-model="uploadForm.target_position" /></el-form-item></div></el-form><el-upload drag :auto-upload="false" :limit="1" :before-upload="beforeUpload" :on-change="onUploadChange" :on-remove="onUploadRemove" accept=".txt,.md,.pdf"><div class="el-upload__text">将文件拖到此处，或点击选择文件</div><template #tip><div class="el-upload__tip">支持 txt、md 和 pdf，文件大小不超过 10MB。</div></template></el-upload><div class="editor-actions"><el-button type="primary" :loading="uploading" @click="submitUpload">提交异步入库</el-button></div></div>
            </el-tab-pane>
            <el-tab-pane label="入库任务"><div class="ingestion-task-panel"><div class="task-panel-header"><strong>异步入库队列</strong><el-button text :loading="taskLoading" @click="loadTasks">刷新任务</el-button></div><el-empty v-if="!tasks.length" description="暂无入库任务" /><div v-for="task in tasks" :key="task.id" class="ingestion-task-row"><div class="task-main"><strong>#{{ task.id }} {{ task.title }}</strong><small>{{ task.file_name }} · {{ stageLabel(task.stage) }} · 尝试 {{ task.attempts }}/{{ task.max_attempts }}</small><span v-if="task.error" class="task-error">{{ task.error }}</span></div><el-tag :type="statusType(task.status)">{{ task.status }}</el-tag><div class="task-actions"><el-button v-if="['failed', 'dead', 'publish_failed'].includes(task.status)" size="small" @click="retryTask(task, false)">重试</el-button><el-button v-if="['failed', 'dead', 'publish_failed', 'succeeded'].includes(task.status)" size="small" type="primary" plain @click="retryTask(task, true)">重新执行</el-button></div></div></div></el-tab-pane>
          </el-tabs>
        </section>
      </div>
    </section>
  </main>
</template>
