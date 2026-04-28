<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">采购申请</h1>
      <el-button type="primary" data-testid="create-purchase-request" @click="$router.push('/purchase-requests/new')">
        新建采购申请
      </el-button>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="requests" border stripe>
        <el-table-column prop="number" label="申请编号" width="180" />
        <el-table-column prop="title" label="申请标题" min-width="260" show-overflow-tooltip />
        <el-table-column prop="contract_number" label="关联合同" width="170" />
        <el-table-column prop="supplier_name" label="供应商" min-width="210" />
        <el-table-column prop="department" label="申请部门" width="150" />
        <el-table-column prop="requester" label="申请人" width="110" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="total_amount" label="金额" width="140" align="right">
          <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiClient, type PurchaseRequest } from '@/api/client'

const loading = ref(false)
const requests = ref<PurchaseRequest[]>([])

function formatMoney(value: number) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(value)
}

async function loadRequests() {
  loading.value = true
  try {
    const { data } = await apiClient.get<PurchaseRequest[]>('/purchase-requests')
    requests.value = data
  } finally {
    loading.value = false
  }
}

onMounted(loadRequests)
</script>
