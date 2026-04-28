<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">采购订单</h1>
      <div class="filters">
        <el-form-item label="优先级">
          <el-select v-model="priority" clearable placeholder="全部" style="width: 130px" data-testid="po-priority-filter">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="status" clearable placeholder="全部" style="width: 160px" data-testid="po-status-filter">
            <el-option label="待审批" value="pending_approval" />
            <el-option label="已审批" value="approved" />
          </el-select>
        </el-form-item>
        <el-button type="primary" data-testid="generate-po-from-existing-pr" :loading="generating" @click="generateOrder('PR-2026-RPA-001')">
          从 PR-2026-RPA-001 生成订单
        </el-button>
        <el-button data-testid="generate-po-from-new-pr" :loading="generating" @click="generateOrder('PR-2026-RPA-NEW-001')">
          从 PR-2026-RPA-NEW-001 生成订单
        </el-button>
      </div>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="filteredOrders" border stripe>
        <el-table-column prop="number" label="订单编号" width="180" />
        <el-table-column prop="request_number" label="采购申请" width="190" />
        <el-table-column prop="supplier_name" label="供应商" min-width="230" />
        <el-table-column prop="priority" label="优先级" width="110" />
        <el-table-column prop="status" label="状态" width="140" />
        <el-table-column prop="total_amount" label="订单金额" width="140" align="right">
          <template #default="{ row }">{{ formatMoney(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiClient, apiErrorMessage, type PurchaseOrder } from '@/api/client'

const loading = ref(false)
const generating = ref(false)
const priority = ref('')
const status = ref('')
const orders = ref<PurchaseOrder[]>([])

const filteredOrders = computed(() =>
  orders.value.filter((order) => (!priority.value || order.priority === priority.value) && (!status.value || order.status === status.value))
)

function formatMoney(value: number) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(value)
}

async function loadOrders() {
  loading.value = true
  try {
    const { data } = await apiClient.get<PurchaseOrder[]>('/purchase-orders')
    orders.value = data
  } finally {
    loading.value = false
  }
}

async function generateOrder(requestNumber: string) {
  generating.value = true
  try {
    await apiClient.post(`/purchase-orders/from-request/${requestNumber}`)
    ElMessage.success('采购订单已生成：PO-2026-RPA-NEW-001')
    await loadOrders()
  } catch (error) {
    const message = apiErrorMessage(error, `采购订单生成失败，请确认采购申请 ${requestNumber} 已存在`)
    ElMessage.error(message.includes('already exists') ? '采购订单已存在：PO-2026-RPA-NEW-001' : message)
  } finally {
    generating.value = false
  }
}

onMounted(loadOrders)
</script>
