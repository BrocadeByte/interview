<script setup lang="ts">
import { Check, Document, User } from '@element-plus/icons-vue'
import { ElButton, ElForm, ElFormItem, ElIcon, ElInput, ElInputNumber, ElMessage, ElTag } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-input-number.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-tag.css'
import { onMounted, reactive, ref } from 'vue'

import { fetchProfile, updateProfile, type Profile } from '../api/profile'

const loading = ref(false)
const form = reactive<Profile>({
  age: null,
  education: '',
  major: '',
  experience_years: null,
  target_position: '',
  target_city: '',
  expected_salary: '',
  skills: '',
  projects: '',
  self_evaluation: ''
})

onMounted(async () => {
  const { data } = await fetchProfile()
  Object.assign(form, data)
})

async function submit() {
  loading.value = true
  try {
    await updateProfile(form)
    ElMessage.success('求职画像已保存')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="app-page profile-page">
    <section class="content profile-content">
      <header class="page-heading">
        <div>
          <span class="eyebrow">求职画像</span>
          <h1>求职画像</h1>
          <p>完善经历与目标，让每次面试的问题更贴合你的真实背景。</p>
        </div>
        <el-tag type="success" effect="plain" round class="privacy-tag"><el-icon><Check /></el-icon><span>资料仅用于面试定制</span></el-tag>
      </header>

      <el-form label-position="top" class="profile-form" @submit.prevent="submit">
        <section class="form-section">
          <div class="form-section-heading">
            <span class="section-icon"><el-icon><User /></el-icon></span>
            <div><h2>基本信息</h2><p>你的教育与求职目标</p></div>
          </div>
          <div class="profile-grid">
            <el-form-item label="年龄"><el-input-number v-model="form.age" :min="0" :max="100" controls-position="right" /></el-form-item>
            <el-form-item label="最高学历"><el-input v-model="form.education" placeholder="例如：本科" /></el-form-item>
            <el-form-item label="专业"><el-input v-model="form.major" placeholder="例如：计算机科学与技术" /></el-form-item>
            <el-form-item label="工作年限"><el-input-number v-model="form.experience_years" :min="0" :max="60" controls-position="right" /></el-form-item>
            <el-form-item label="目标岗位"><el-input v-model="form.target_position" placeholder="例如：前端开发工程师" /></el-form-item>
            <el-form-item label="目标城市"><el-input v-model="form.target_city" placeholder="例如：上海" /></el-form-item>
            <el-form-item label="期望薪资" class="span-2"><el-input v-model="form.expected_salary" placeholder="例如：20k–30k" /></el-form-item>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section-heading">
            <span class="section-icon section-icon-green"><el-icon><Document /></el-icon></span>
            <div><h2>能力与经历</h2><p>帮助 AI 生成更有针对性的专业问题</p></div>
          </div>
          <div class="profile-grid">
            <el-form-item label="技能标签" class="span-2"><el-input v-model="form.skills" type="textarea" :rows="3" placeholder="例如：Vue、TypeScript、工程化、性能优化" /></el-form-item>
            <el-form-item label="项目经历" class="span-2"><el-input v-model="form.projects" type="textarea" :rows="5" placeholder="简要描述代表项目、你的职责和关键成果" /></el-form-item>
            <el-form-item label="自我评价" class="span-2"><el-input v-model="form.self_evaluation" type="textarea" :rows="3" placeholder="总结你的优势、工作方式与职业方向" /></el-form-item>
          </div>
        </section>

        <div class="sticky-form-actions">
          <span>修改后记得保存，新的画像会应用到下一次面试。</span>
          <el-button type="primary" native-type="submit" :loading="loading" size="large">保存求职画像</el-button>
        </div>
      </el-form>
    </section>
  </main>
</template>
