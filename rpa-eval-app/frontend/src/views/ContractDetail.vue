<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">合同详情</h1>
      <el-button @click="$router.push('/contracts')">返回合同台账</el-button>
    </div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <template v-else-if="contract">
      <el-descriptions class="panel" title="基本信息" :column="3" border>
        <el-descriptions-item label="合同编号">{{ contract.number }}</el-descriptions-item>
        <el-descriptions-item label="合同名称">{{ contract.title }}</el-descriptions-item>
        <el-descriptions-item label="合同状态">{{ contract.status }}</el-descriptions-item>
        <el-descriptions-item label="合同类型">{{ contract.contract_type }}</el-descriptions-item>
        <el-descriptions-item label="归口部门">{{ contract.owner_department }}</el-descriptions-item>
        <el-descriptions-item label="有效期">{{ contract.start_date }} 至 {{ contract.end_date }}</el-descriptions-item>
      </el-descriptions>
      <el-descriptions class="panel detail-section" title="供应商信息" :column="3" border>
        <el-descriptions-item label="供应商编号">{{ contract.supplier_number }}</el-descriptions-item>
        <el-descriptions-item label="供应商名称">{{ contract.supplier_name }}</el-descriptions-item>
      </el-descriptions>
      <el-descriptions class="panel detail-section" title="金额与付款" :column="2" border>
        <el-descriptions-item label="合同金额">{{ formatMoney(contract.amount) }}</el-descriptions-item>
        <el-descriptions-item label="币种">{{ contract.currency }}</el-descriptions-item>
        <el-descriptions-item label="合规条款">{{ contract.compliance_clause }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { apiClient, type Contract } from '@/api/client'

const route = useRoute()
const loading = ref(false)
const contract = ref<Contract | null>(null)

function formatMoney(value: number) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(value)
}

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await apiClient.get<Contract[]>('/contracts', { params: { number: route.params.number } })
    contract.value = data[0] || null
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.detail-section {
  margin-top: 12px;
}
</style>
