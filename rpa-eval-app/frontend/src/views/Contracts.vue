<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">合同台账</h1>
      <div class="filters">
        <el-form-item label="合同编号">
          <el-input v-model="filters.number" clearable data-testid="contract-number-filter" placeholder="CT-2026-RPA-001" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 150px">
            <el-option label="生效中" value="effective" />
            <el-option label="待复核" value="pending_review" />
          </el-select>
        </el-form-item>
        <el-form-item label="合同类型">
          <el-select v-model="filters.contract_type" clearable placeholder="全部类型" style="width: 190px">
            <el-option label="软件订阅" value="software_subscription" />
            <el-option label="合规服务" value="compliance_service" />
            <el-option label="实施服务" value="implementation" />
          </el-select>
        </el-form-item>
        <el-button type="primary" data-testid="contract-search" @click="loadContracts">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="contracts" border stripe empty-text="没有匹配结果">
        <el-table-column prop="number" label="合同编号" width="170" />
        <el-table-column prop="title" label="合同名称" min-width="260" show-overflow-tooltip />
        <el-table-column prop="supplier_name" label="供应商" min-width="210" show-overflow-tooltip />
        <el-table-column prop="contract_type" label="类型" width="150" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }"><el-tag>{{ statusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="amount" label="金额" width="140" align="right">
          <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :data-testid="`view-contract-${row.number}`" @click="$router.push(`/contracts/${row.number}`)">
              查看详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { apiClient, type Contract } from '@/api/client'

const loading = ref(false)
const contracts = ref<Contract[]>([])
const filters = reactive({ number: '', status: '', contract_type: '' })

function statusLabel(status: string) {
  return status === 'effective' ? '生效中' : status === 'pending_review' ? '待复核' : status
}

function formatMoney(value: number) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(value)
}

async function loadContracts() {
  loading.value = true
  try {
    const { data } = await apiClient.get<Contract[]>('/contracts', { params: { ...filters } })
    contracts.value = data
  } finally {
    loading.value = false
  }
}

function reset() {
  filters.number = ''
  filters.status = ''
  filters.contract_type = ''
  loadContracts()
}

onMounted(loadContracts)
</script>
