<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">供应商台账</h1>
      <div class="filters">
        <el-form-item label="供应商状态">
          <el-select v-model="status" clearable placeholder="全部状态" style="width: 170px" data-testid="supplier-status-filter">
            <el-option label="正常合作" value="active" />
            <el-option label="待补联系人" value="pending_contact" />
          </el-select>
        </el-form-item>
        <el-button type="primary" @click="loadSuppliers">查询</el-button>
      </div>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="suppliers" border stripe>
        <el-table-column prop="number" label="供应商编号" width="150" />
        <el-table-column prop="name" label="供应商名称" min-width="240" show-overflow-tooltip />
        <el-table-column prop="category" label="供应类别" width="160" />
        <el-table-column prop="region" label="区域" width="100" />
        <el-table-column prop="risk_level" label="风险等级" width="110" />
        <el-table-column prop="compliance_rating" label="合规评级" width="110" />
        <el-table-column prop="contact_person" label="联系人" width="120" />
        <el-table-column prop="contact_phone" label="联系电话" width="150" />
        <el-table-column prop="status" label="状态" width="130" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :data-testid="`edit-supplier-${row.number}`" @click="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dialogVisible" title="维护供应商联系人" width="460px">
      <el-form :model="editForm" label-width="96px">
        <el-form-item label="联系人">
          <el-input v-model="editForm.contact_person" data-testid="supplier-contact-person" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="editForm.contact_phone" data-testid="supplier-contact-phone" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editForm.contact_email" data-testid="supplier-contact-email" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" data-testid="supplier-save" @click="saveSupplier">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiClient, type Supplier } from '@/api/client'

const loading = ref(false)
const status = ref('')
const suppliers = ref<Supplier[]>([])
const dialogVisible = ref(false)
const currentNumber = ref('')
const editForm = reactive({ contact_person: '', contact_phone: '', contact_email: '' })

async function loadSuppliers() {
  loading.value = true
  try {
    const { data } = await apiClient.get<Supplier[]>('/suppliers', { params: { status: status.value || undefined } })
    suppliers.value = data
  } finally {
    loading.value = false
  }
}

function openEdit(row: Supplier) {
  currentNumber.value = row.number
  editForm.contact_person = row.contact_person || ''
  editForm.contact_phone = row.contact_phone || ''
  editForm.contact_email = row.contact_email || ''
  dialogVisible.value = true
}

async function saveSupplier() {
  await apiClient.patch(`/suppliers/${currentNumber.value}`, editForm)
  ElMessage.success('供应商联系人已保存')
  dialogVisible.value = false
  await loadSuppliers()
}

onMounted(loadSuppliers)
</script>
