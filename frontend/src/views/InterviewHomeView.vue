<script setup lang="ts">
import { ArrowRight, Briefcase, CircleCheck, Clock, Document, MagicStick, Position, UploadFilled } from '@element-plus/icons-vue'
import { ElButton, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput, ElMessage, ElProgress, ElSegmented, ElTag } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-empty.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-progress.css'
import 'element-plus/theme-chalk/el-segmented.css'
import 'element-plus/theme-chalk/el-tag.css'
import '../styles/pages/interview-home.css'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { trackAnalyticsEvent } from '../api/analytics'
import { getApiErrorMessage } from '../api/client'
import { createInterview, fetchInterviews, rememberInterview, type InterviewCreateInput, type InterviewSession, type InterviewType, warmupInterview } from '../api/interview'
import { parseJobDescription } from '../api/jobDescription'
import { autoGenerateProfile, fetchProfile, updateProfile, type AutoProfileDraft, type Profile } from '../api/profile'
import { ResumeParsingFailedError, ResumeParsingTimeoutError, pasteResume, uploadResume, waitForResumeParsing, type ResumeVersion } from '../api/resume'

const router = useRouter()
const loading = ref(false)
const preparingProfile = ref(false)
const profilePreparationStage = ref<'submitting_resume' | 'parsing_resume' | 'parsing_job' | 'generating_profile' | null>(null)
const confirmingProfile = ref(false)
const sessions = ref<InterviewSession[]>([])
const form = reactive<InterviewCreateInput>({
  target_position: '',
  difficulty: 'medium',
  mode: 'training',
  interview_type: 'mixed'
})
const currentProfile = ref<Profile>({})
const resumeText = ref('')
const jobDescriptionText = ref('')
const selectedResumeFile = ref<File | null>(null)
const resumeFileInput = ref<HTMLInputElement | null>(null)
const resumeId = ref<number | null>(null)
const jobDescriptionId = ref<number | null>(null)
const profileDraft = ref<AutoProfileDraft | null>(null)
const profileConfirmed = ref(false)
type ResumeParsingNoticeStatus = 'idle' | 'waiting' | 'parsed' | 'failed' | 'timeout'
const resumeParsingNotice = reactive({
  status: 'idle' as ResumeParsingNoticeStatus,
  resumeId: null as number | null,
  startedAt: 0,
  elapsedSeconds: 0,
  message: ''
})
const modeOptions = [
  { label: '训练模式', value: 'training' },
  { label: '实战模式', value: 'mock' }
]
const interviewTypeOptions: Array<{ label: string; value: InterviewType }> = [
  { label: '综合', value: 'mixed' },
  { label: 'HR', value: 'hr' },
  { label: '项目深挖', value: 'project_deep_dive' },
  { label: '技术基础', value: 'technical_basics' },
  { label: '系统设计', value: 'system_design' }
]
let warmupTimer: ReturnType<typeof setTimeout> | null = null
let lastWarmedPosition = ''
let profileGenerationSequence = 0
let profileMaterialRevision = 0
let resumeParsingTimer: ReturnType<typeof setInterval> | null = null

const RESUME_PARSING_WAIT_SECONDS = 135

class StaleProfileGenerationError extends Error {}

const finishedCount = computed(() => sessions.value.filter((session) => session.status === 'finished').length)
const activeCount = computed(() => sessions.value.filter((session) => session.status !== 'finished').length)
const hasResume = computed(() => Boolean(selectedResumeFile.value || resumeText.value.trim() || resumeId.value))
const hasJobDescription = computed(() => Boolean(jobDescriptionText.value.trim() || jobDescriptionId.value))
const profileConfirmationRequired = computed(() => hasResume.value || hasJobDescription.value)
const trainingKind = computed(() => {
  if (hasResume.value && hasJobDescription.value) return { label: '岗位匹配训练', type: 'success' as const, hint: '将结合你的真实经历和目标岗位要求定制问题。' }
  if (hasResume.value) return { label: '简历定制训练', type: 'primary' as const, hint: '将围绕你的技能与项目经历进行深挖。' }
  if (hasJobDescription.value) return { label: 'JD 定制训练', type: 'warning' as const, hint: '将按目标岗位要求定制问题，补充简历后会更精准。' }
  return { label: '通用训练', type: 'info' as const, hint: '无需简历也可开始，问题将按目标岗位和难度生成。' }
})
const completeness = computed(() => Math.min(100, Math.max(0, profileDraft.value?.completeness || 0)))
const profileSummary = computed(() => {
  const draft = profileDraft.value
  if (!draft) return ''
  if (draft.auto_summary?.trim()) return draft.auto_summary.trim()
  const patch = draft.profile_patch
  const parts = [
    patch.target_position ? `目标 ${patch.target_position}` : '',
    patch.experience_years != null ? `${patch.experience_years} 年经验` : '',
    patch.skills ? `技能：${patch.skills}` : '',
    patch.self_evaluation || ''
  ].filter(Boolean)
  return parts.join('；') || '画像草稿已生成，请确认后开始训练。'
})
const modeHint = computed(() => form.mode === 'training'
  ? '每次作答后显示实时评分和改进建议，适合边练边学。'
  : '答题过程中不展示评分或即时提示，结束后统一查看复盘报告。')
const startButtonLabel = computed(() => form.mode === 'mock' ? '开始实战模拟' : '开始训练')
const preparingProfileLabel = computed(() => ({
  submitting_resume: '正在上传简历…',
  parsing_resume: 'AI 正在解析简历…',
  parsing_job: 'AI 正在解析 JD…',
  generating_profile: '正在生成自动画像…'
}[profilePreparationStage.value || 'generating_profile']))
const resumeParsingProgress = computed(() => Math.min(
  100,
  Math.round((resumeParsingNotice.elapsedSeconds / RESUME_PARSING_WAIT_SECONDS) * 100)
))
const resumeParsingNoticeTitle = computed(() => ({
  idle: '',
  waiting: 'AI 正在解析简历',
  parsed: 'AI 简历解析完成',
  failed: 'AI 简历解析失败',
  timeout: 'AI 简历解析等待超时'
}[resumeParsingNotice.status]))

function stopResumeParsingTimer() {
  if (resumeParsingTimer) window.clearInterval(resumeParsingTimer)
  resumeParsingTimer = null
}

function resetResumeParsingNotice() {
  stopResumeParsingTimer()
  resumeParsingNotice.status = 'idle'
  resumeParsingNotice.resumeId = null
  resumeParsingNotice.startedAt = 0
  resumeParsingNotice.elapsedSeconds = 0
  resumeParsingNotice.message = ''
}

function updateResumeParsingElapsed() {
  if (!resumeParsingNotice.startedAt) return
  resumeParsingNotice.elapsedSeconds = Math.min(
    RESUME_PARSING_WAIT_SECONDS,
    Math.floor((Date.now() - resumeParsingNotice.startedAt) / 1000)
  )
}

function beginResumeParsingNotice(id: number) {
  resetResumeParsingNotice()
  resumeParsingNotice.status = 'waiting'
  resumeParsingNotice.resumeId = id
  resumeParsingNotice.startedAt = Date.now()
  resumeParsingNotice.message = '解析任务已提交，通常会很快完成；最慢等待约 135 秒。'
  resumeParsingTimer = window.setInterval(updateResumeParsingElapsed, 500)
}

function finishResumeParsingNotice(status: Exclude<ResumeParsingNoticeStatus, 'idle' | 'waiting'>, message: string) {
  updateResumeParsingElapsed()
  stopResumeParsingTimer()
  resumeParsingNotice.status = status
  resumeParsingNotice.message = message
}

async function awaitSubmittedResume(
  submitted: ResumeVersion,
  generationSequence: number,
  materialRevision: number,
  materialKey: string
) {
  beginResumeParsingNotice(submitted.id)
  try {
    if (submitted.status === 'failed') {
      throw new ResumeParsingFailedError(submitted.error_message || 'AI 简历解析失败，请检查简历内容后重试')
    }
    const data = submitted.status === 'pending' ? await waitForResumeParsing(submitted.id) : submitted
    assertCurrentProfileGeneration(generationSequence, materialRevision, materialKey)
    finishResumeParsingNotice('parsed', '简历已解析完成，正在继续生成自动画像草稿。')
    return data
  } catch (error) {
    if (error instanceof StaleProfileGenerationError) {
      resetResumeParsingNotice()
    } else if (error instanceof ResumeParsingTimeoutError) {
      finishResumeParsingNotice('timeout', error.message)
    } else if (error instanceof ResumeParsingFailedError) {
      finishResumeParsingNotice('failed', error.message)
    } else {
      finishResumeParsingNotice('failed', getApiErrorMessage(error, '无法获取 AI 简历解析结果，请稍后重试'))
    }
    throw error
  }
}

async function load() {
  const [{ data: profile }, { data: history }] = await Promise.all([fetchProfile(), fetchInterviews()])
  currentProfile.value = profile
  form.target_position = profile.target_position || ''
  sessions.value = history
}

function scheduleWarmup(position: string) {
  const normalized = position.trim()
  if (warmupTimer) clearTimeout(warmupTimer)
  if (!normalized || normalized === lastWarmedPosition) return
  warmupTimer = setTimeout(() => {
    lastWarmedPosition = normalized
    void warmupInterview(normalized).catch(() => undefined)
  }, 500)
}

watch(() => form.target_position, scheduleWarmup)
onMounted(load)
onBeforeUnmount(() => {
  if (warmupTimer) clearTimeout(warmupTimer)
  stopResumeParsingTimer()
  profileGenerationSequence += 1
})

function profileMaterialKey() {
  const file = selectedResumeFile.value
  const fileKey = file
    ? `${file.name}:${file.size}:${file.lastModified}:${file.type}`
    : ''
  return JSON.stringify({
    resumeText: resumeText.value.trim(),
    fileKey,
    jobDescriptionText: jobDescriptionText.value.trim(),
    targetPosition: form.target_position.trim()
  })
}

function assertCurrentProfileGeneration(sequence: number, revision: number, materialKey: string) {
  if (
    sequence !== profileGenerationSequence
    || revision !== profileMaterialRevision
    || materialKey !== profileMaterialKey()
  ) {
    throw new StaleProfileGenerationError('画像生成期间材料已变化')
  }
}

function invalidateGeneratedProfile(source: 'resume' | 'job-description' | 'target-position') {
  profileMaterialRevision += 1
  if (source === 'resume') {
    resumeId.value = null
    resetResumeParsingNotice()
  }
  else if (source === 'job-description') jobDescriptionId.value = null
  profileDraft.value = null
  profileConfirmed.value = false
}

function onResumeTextInput() {
  invalidateGeneratedProfile('resume')
}

function onJobDescriptionInput() {
  invalidateGeneratedProfile('job-description')
}

function onTargetPositionInput() {
  invalidateGeneratedProfile('target-position')
}

function chooseResumeFile() {
  resumeFileInput.value?.click()
}

function onResumeFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] || null
  if (!file) return
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (!extension || !['pdf', 'txt', 'md'].includes(extension)) {
    ElMessage.warning('简历仅支持 PDF、TXT、MD 文件')
    input.value = ''
    return
  }
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.warning('简历文件不能超过 10MB')
    input.value = ''
    return
  }
  selectedResumeFile.value = file
  invalidateGeneratedProfile('resume')
}

function removeResumeFile() {
  selectedResumeFile.value = null
  if (resumeFileInput.value) resumeFileInput.value.value = ''
  invalidateGeneratedProfile('resume')
}

function getProfilePreparationErrorMessage(error: unknown) {
  if (error instanceof ResumeParsingTimeoutError) return error.message
  if (error instanceof ResumeParsingFailedError) return error.message
  if (profilePreparationStage.value === 'submitting_resume') {
    const detail = getApiErrorMessage(error, '')
    if (detail.includes('解析任务提交失败')) return detail
    if (selectedResumeFile.value) {
      return detail ? `简历文件上传失败：${detail}` : '简历文件上传失败，请检查文件后重试'
    }
    return detail ? `简历任务提交失败：${detail}` : '简历任务提交失败，请稍后重试'
  }
  if (profilePreparationStage.value === 'parsing_resume') {
    return getApiErrorMessage(error, error instanceof Error ? error.message : 'AI 简历解析失败，请稍后重试')
  }
  return getApiErrorMessage(error, error instanceof Error ? error.message : '画像生成失败，请检查输入后重试')
}

async function generateProfileDraft() {
  if (!profileConfirmationRequired.value) {
    ElMessage.info('请先粘贴或上传简历，或粘贴目标岗位 JD')
    return
  }
  const generationSequence = ++profileGenerationSequence
  const materialRevision = profileMaterialRevision
  const materialKey = profileMaterialKey()
  resetResumeParsingNotice()
  preparingProfile.value = true
  profileConfirmed.value = false
  try {
    let nextResumeId = resumeId.value
    let nextJobDescriptionId = jobDescriptionId.value
    let detectedPosition = form.target_position.trim()

    if (!nextResumeId && selectedResumeFile.value) {
      profilePreparationStage.value = 'submitting_resume'
      const { data: uploaded } = await uploadResume(selectedResumeFile.value)
      assertCurrentProfileGeneration(generationSequence, materialRevision, materialKey)
      profilePreparationStage.value = 'parsing_resume'
      const data = await awaitSubmittedResume(uploaded, generationSequence, materialRevision, materialKey)
      nextResumeId = data.id
      resumeId.value = data.id
      if (!detectedPosition && data.profile_patch?.target_position) detectedPosition = data.profile_patch.target_position
    } else if (!nextResumeId && resumeText.value.trim()) {
      profilePreparationStage.value = 'submitting_resume'
      const { data: submitted } = await pasteResume({
        title: `粘贴简历 ${new Date().toLocaleDateString('zh-CN')}`,
        content: resumeText.value.trim()
      })
      assertCurrentProfileGeneration(generationSequence, materialRevision, materialKey)
      profilePreparationStage.value = 'parsing_resume'
      const data = await awaitSubmittedResume(submitted, generationSequence, materialRevision, materialKey)
      nextResumeId = data.id
      resumeId.value = data.id
      if (!detectedPosition && data.profile_patch?.target_position) detectedPosition = data.profile_patch.target_position
    }

    if (!nextJobDescriptionId && jobDescriptionText.value.trim()) {
      profilePreparationStage.value = 'parsing_job'
      const { data } = await parseJobDescription({
        raw_text: jobDescriptionText.value.trim(),
        title: detectedPosition || '目标岗位 JD'
      })
      assertCurrentProfileGeneration(generationSequence, materialRevision, materialKey)
      nextJobDescriptionId = data.id
      jobDescriptionId.value = data.id
      const parsedPosition = data.target_position || data.parsed?.target_position || data.parsed_json?.target_position
      if (!detectedPosition && parsedPosition) detectedPosition = parsedPosition
    }

    profilePreparationStage.value = 'generating_profile'
    const { data: draft } = await autoGenerateProfile({
      ...(nextResumeId ? { resume_id: nextResumeId } : {}),
      ...(nextJobDescriptionId ? { job_description_id: nextJobDescriptionId } : {}),
      ...(detectedPosition ? { target_position: detectedPosition } : {})
    })
    assertCurrentProfileGeneration(generationSequence, materialRevision, materialKey)
    profileDraft.value = { ...draft, warnings: draft.warnings || [] }
    if (resumeParsingNotice.status === 'parsed') {
      resumeParsingNotice.message = '简历解析和自动画像草稿生成均已完成，请确认画像后再开始训练。'
    }
    const draftPosition = draft.profile_patch.target_position?.trim() || detectedPosition
    if (draftPosition) form.target_position = draftPosition
    ElMessage.success('自动画像草稿已生成')
  } catch (error) {
    if (error instanceof StaleProfileGenerationError) {
      ElMessage.info('材料已变化，旧画像结果已作废，请基于最新材料重新生成')
    } else {
      ElMessage.error(getProfilePreparationErrorMessage(error))
    }
  } finally {
    if (generationSequence === profileGenerationSequence) {
      preparingProfile.value = false
      profilePreparationStage.value = null
    }
  }
}

async function confirmGeneratedProfile() {
  if (!profileDraft.value) return
  confirmingProfile.value = true
  try {
    const { id: _id, user_id: _userId, ...profileFields } = currentProfile.value
    const payload: Profile = { ...profileFields, ...profileDraft.value.profile_patch }
    const { data } = await updateProfile(payload)
    void trackAnalyticsEvent({
      event_name: 'profile_applied',
      ...(resumeId.value ? { resume_id: resumeId.value } : {}),
      ...(jobDescriptionId.value ? { job_description_id: jobDescriptionId.value } : {})
    }).catch(() => undefined)
    currentProfile.value = data
    profileConfirmed.value = true
    if (data.target_position) form.target_position = data.target_position
    ElMessage.success('画像已确认，将用于本次训练')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '画像确认失败，请重试'))
  } finally {
    confirmingProfile.value = false
  }
}

async function startInterview() {
  if (!form.target_position.trim()) {
    ElMessage.warning('请先填写目标岗位')
    return
  }
  if (profileConfirmationRequired.value && !profileConfirmed.value) {
    ElMessage.warning(profileDraft.value ? '请先确认自动画像' : '请先生成并确认自动画像')
    return
  }
  loading.value = true
  try {
    const normalizedPosition = form.target_position.trim()
    void import('./InterviewChatView.vue')
    void warmupInterview(normalizedPosition).catch(() => undefined)
    const { data } = await createInterview({
      ...form,
      target_position: normalizedPosition,
      ...(resumeId.value ? { resume_id: resumeId.value } : {}),
      ...(jobDescriptionId.value ? { job_description_id: jobDescriptionId.value } : {})
    })
    rememberInterview(data)
    await router.push(`/interviews/${data.id}`)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '创建面试失败'))
  } finally {
    loading.value = false
  }
}

function statusLabel(status: InterviewSession['status']) {
  return status === 'preparing' ? '准备中' : status === 'active' ? '进行中' : '已完成'
}

function statusType(status: InterviewSession['status']) {
  return status === 'active' ? 'primary' : status === 'finished' ? 'success' : 'info'
}

function difficultyLabel(value: InterviewSession['difficulty']) {
  return { easy: '基础', medium: '标准', hard: '进阶' }[value]
}

function modeLabel(value: InterviewSession['mode']) {
  return value === 'mock' ? '实战' : '训练'
}

function interviewTypeLabel(value: InterviewSession['interview_type']) {
  return {
    mixed: '综合',
    hr: 'HR',
    project_deep_dive: '项目深挖',
    technical_basics: '技术基础',
    system_design: '系统设计'
  }[value]
}
</script>

<template>
  <main class="app-page home-page">
    <section class="content dashboard-page">
      <header class="page-heading home-heading">
        <div>
          <span class="eyebrow">岗位定制训练</span>
          <h1>开始岗位定制训练</h1>
          <p>根据目标技术岗位和难度生成专属训练流程，完成后获得多维复盘报告。</p>
        </div>
        <div class="overview-metrics" aria-label="训练概览">
          <div><span>累计训练</span><strong>{{ sessions.length }}</strong></div>
          <div><span>已完成</span><strong>{{ finishedCount }}</strong></div>
          <div><span>进行中</span><strong>{{ activeCount }}</strong></div>
        </div>
      </header>

      <div class="dashboard">
        <section class="create-panel primary-work-panel">
          <div class="panel-title-row">
            <span class="section-icon"><el-icon><MagicStick /></el-icon></span>
            <div>
              <h2>三分钟开始训练</h2>
              <p>补充简历与 JD，先确认 AI 生成的求职画像</p>
            </div>
          </div>
          <el-form label-position="top" class="interview-form" @submit.prevent="startInterview">
            <section class="quick-start-section" aria-labelledby="quick-start-heading">
              <div class="quick-start-heading">
                <div>
                  <span class="step-label">第 1 步</span>
                  <h3 id="quick-start-heading">简历 / JD / 自动画像</h3>
                </div>
                <el-tag :type="trainingKind.type" effect="light">{{ trainingKind.label }}</el-tag>
              </div>
              <p class="training-kind-hint">{{ trainingKind.hint }}</p>

              <div class="customization-grid">
                <article class="source-card">
                  <div class="source-card-heading">
                    <span class="source-icon"><el-icon><Document /></el-icon></span>
                    <div><h4>我的简历</h4><p>粘贴文本或上传文件，任选一种</p></div>
                  </div>
                  <el-input
                    v-model="resumeText"
                    type="textarea"
                    :rows="5"
                    maxlength="20000"
                    resize="vertical"
                    placeholder="粘贴教育背景、技能和项目经历…"
                    aria-label="粘贴简历文本"
                    :disabled="preparingProfile"
                    @input="onResumeTextInput"
                  />
                  <div class="file-picker-row">
                    <input
                      ref="resumeFileInput"
                      class="native-file-input"
                      type="file"
                      accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
                      aria-label="上传简历文件"
                      @change="onResumeFileChange"
                    />
                    <el-button :icon="UploadFilled" :disabled="preparingProfile" @click="chooseResumeFile">选择简历文件</el-button>
                    <span v-if="!selectedResumeFile" class="file-help">PDF / TXT / MD，最大 10MB</span>
                    <span v-else class="selected-file" :title="selectedResumeFile.name">
                      {{ selectedResumeFile.name }}
                      <button type="button" aria-label="移除已选简历文件" :disabled="preparingProfile" @click="removeResumeFile">移除</button>
                    </span>
                  </div>
                </article>

                <article class="source-card">
                  <div class="source-card-heading">
                    <span class="source-icon source-icon-job"><el-icon><Briefcase /></el-icon></span>
                    <div><h4>目标岗位 JD</h4><p>粘贴职责、任职要求和加分项</p></div>
                  </div>
                  <el-input
                    v-model="jobDescriptionText"
                    type="textarea"
                    :rows="5"
                    maxlength="20000"
                    resize="vertical"
                    placeholder="粘贴目标岗位的完整职位描述…"
                    aria-label="粘贴目标岗位 JD"
                    :disabled="preparingProfile"
                    @input="onJobDescriptionInput"
                  />
                  <p class="source-help">JD 会作为不可信数据解析，不会被当作系统指令执行。</p>
                </article>
              </div>

              <el-button
                type="primary"
                plain
                size="large"
                class="generate-profile-button"
                :loading="preparingProfile"
                :disabled="!profileConfirmationRequired || confirmingProfile"
                @click="generateProfileDraft"
              >
                {{ preparingProfile ? preparingProfileLabel : profileDraft ? '重新生成自动画像' : '生成自动画像' }}
              </el-button>

              <div
                v-if="resumeParsingNotice.status !== 'idle'"
                class="resume-parsing-status"
                :class="`resume-parsing-status--${resumeParsingNotice.status}`"
                aria-live="polite"
              >
                <div class="resume-parsing-status-heading">
                  <strong>{{ resumeParsingNoticeTitle }}</strong>
                  <span v-if="resumeParsingNotice.status === 'waiting'">
                    已等待 {{ resumeParsingNotice.elapsedSeconds }} 秒 / 最多约 135 秒
                  </span>
                  <span v-else-if="resumeParsingNotice.elapsedSeconds">
                    用时约 {{ resumeParsingNotice.elapsedSeconds }} 秒
                  </span>
                </div>
                <el-progress
                  v-if="resumeParsingNotice.status === 'waiting'"
                  :percentage="resumeParsingProgress"
                  :stroke-width="7"
                  :show-text="false"
                />
                <p>{{ resumeParsingNotice.message }}</p>
                <small v-if="resumeParsingNotice.resumeId">解析任务 #{{ resumeParsingNotice.resumeId }}</small>
              </div>

              <div v-if="profileDraft" class="profile-preview" aria-live="polite">
                <div class="profile-preview-heading">
                  <div><span class="step-label">第 2 步</span><h3>确认自动画像</h3></div>
                  <span class="completeness-label">完整度 {{ completeness }}%</span>
                </div>
                <el-progress :percentage="completeness" :stroke-width="8" :show-text="false" />
                <p class="profile-summary">{{ profileSummary }}</p>
                <ul v-if="profileDraft.warnings.length" class="profile-warnings">
                  <li v-for="warning in profileDraft.warnings" :key="warning">{{ warning }}</li>
                </ul>
                <div class="profile-confirm-row">
                  <span v-if="profileConfirmed" class="confirmed-copy"><el-icon><CircleCheck /></el-icon>画像已确认</span>
                  <span v-else>确认后才会写入求职画像并允许创建训练。</span>
                  <el-button
                    type="success"
                    :loading="confirmingProfile"
                    :disabled="profileConfirmed || preparingProfile"
                    @click="confirmGeneratedProfile"
                  >
                    {{ profileConfirmed ? '已确认' : '确认并应用画像' }}
                  </el-button>
                </div>
              </div>
            </section>

            <section class="training-settings" aria-labelledby="training-settings-heading">
              <div class="settings-heading">
                <span class="step-label">第 {{ profileConfirmationRequired ? 3 : 2 }} 步</span>
                <h3 id="training-settings-heading">训练设置</h3>
              </div>
            <el-form-item label="目标岗位">
              <el-input
                v-model="form.target_position"
                maxlength="160"
                placeholder="例如：前端开发工程师"
                :prefix-icon="Position"
                :disabled="preparingProfile"
                size="large"
                @input="onTargetPositionInput"
              />
            </el-form-item>
            <el-form-item label="面试难度">
              <el-segmented
                v-model="form.difficulty"
                :options="[{ label: '基础', value: 'easy' }, { label: '标准', value: 'medium' }, { label: '进阶', value: 'hard' }]"
                size="large"
              />
            </el-form-item>
            <el-form-item label="面试模式">
              <el-segmented
                v-model="form.mode"
                class="settings-selector mode-selector"
                :options="modeOptions"
                size="large"
              />
            </el-form-item>
            <p class="setting-hint" :class="{ 'mock-mode-hint': form.mode === 'mock' }">{{ modeHint }}</p>
            <el-form-item label="面试类型">
              <div class="interview-type-grid" role="radiogroup" aria-label="面试类型">
                <label
                  v-for="option in interviewTypeOptions"
                  :key="option.value"
                  class="interview-type-option"
                  :class="{ active: form.interview_type === option.value }"
                >
                  <input v-model="form.interview_type" type="radio" name="interview-type" :value="option.value" />
                  <span>{{ option.label }}</span>
                </label>
              </div>
            </el-form-item>
            <div class="difficulty-hint">
              <el-icon><Clock /></el-icon>
              <span>预计 20–30 分钟，共约 8 轮核心问答</span>
            </div>
            <p v-if="profileConfirmationRequired && !profileConfirmed" class="start-blocked-hint">
              {{ profileDraft ? '确认上方自动画像后即可开始训练。' : '生成并确认自动画像后即可开始训练。' }}
            </p>
            <el-button
              type="primary"
              native-type="submit"
              :loading="loading"
              :disabled="preparingProfile || confirmingProfile || (profileConfirmationRequired && !profileConfirmed)"
              size="large"
              class="start-button"
            >
              {{ startButtonLabel }}<el-icon class="el-icon--right"><ArrowRight /></el-icon>
            </el-button>
            </section>
          </el-form>
        </section>

        <aside class="history-panel">
          <div class="panel-heading">
            <div><h2>最近训练</h2><p>继续训练或查看历史复盘</p></div>
            <el-button text @click="router.push('/reports')">全部报告</el-button>
          </div>
          <el-empty v-if="sessions.length === 0" description="暂无训练记录" :image-size="72" />
          <div v-else class="session-list">
            <button v-for="session in sessions.slice(0, 6)" :key="session.id" type="button" class="session-item" :aria-label="`${statusLabel(session.status)}：${session.target_position}，${modeLabel(session.mode)}模式，${interviewTypeLabel(session.interview_type)}面试，${difficultyLabel(session.difficulty)}难度，第 ${session.current_question_index} 轮`" @click="router.push(`/interviews/${session.id}`)">
              <span class="session-icon">{{ session.target_position.slice(0, 1) }}</span>
              <span class="session-main">
                <strong>{{ session.target_position }}</strong>
                <small>{{ modeLabel(session.mode) }} · {{ interviewTypeLabel(session.interview_type) }} · {{ difficultyLabel(session.difficulty) }}难度 · 第 {{ session.current_question_index }} 轮</small>
              </span>
              <el-tag :type="statusType(session.status)" effect="light" size="small">{{ statusLabel(session.status) }}</el-tag>
            </button>
          </div>
        </aside>
      </div>
    </section>
  </main>
</template>
