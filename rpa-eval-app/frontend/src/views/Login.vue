<template>
  <div class="login-page">
    <el-card class="login-card" shadow="never">
      <h1>采购合规RPA评估系统</h1>
      <p class="muted">请使用评估账号登录内部业务台账</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="handleLogin">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" data-testid="login-username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            data-testid="login-password"
            type="password"
            autocomplete="current-password"
            show-password
          />
        </el-form-item>
        <el-alert class="account-hint" type="info" :closable="false">
          可用账号：admin/admin123、buyer/buyer123、approver/approver123
        </el-alert>
        <el-button
          class="login-button"
          type="primary"
          native-type="submit"
          :loading="loading"
          data-testid="login-submit"
        >
          登录系统
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: 'admin', password: 'admin123' })
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

async function handleLogin() {
  await formRef.value?.validate()
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    await router.push((route.query.redirect as string) || '/dashboard')
  } catch {
    ElMessage.error('登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #e7edf5;
}

.login-card {
  width: 420px;
  border-radius: 8px;
}

h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.account-hint {
  margin-bottom: 14px;
}

.login-button {
  width: 100%;
}
</style>
