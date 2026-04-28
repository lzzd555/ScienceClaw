<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">审批待办</h1>
      <div class="filters">
        <el-form-item label="优先级">
          <el-select v-model="priority" clearable placeholder="全部" style="width: 140px" data-testid="approval-priority-filter">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务状态">
          <el-select v-model="status" clearable placeholder="全部" style="width: 150px">
            <el-option label="待审批" value="pending" />
            <el-option label="已通过" value="approved" />
          </el-select>
        </el-form-item>
      </div>
    </div>
    <div class="panel">
      <el-table v-loading="loading" :data="filteredApprovals" border stripe>
        <el-table-column prop="task_id" label="任务编号" width="180" />
        <el-table-column prop="purchase_order_number" label="采购订单" width="180" />
        <el-table-column prop="assignee" label="审批人" width="130" />
        <el-table-column label="优先级" width="110">
          <template #default="{ row }">{{ orderPriority(row.purchase_order_number) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="comment" label="审批意见" min-width="260" show-overflow-tooltip />
        <el-table-column prop="updated_at" label="更新时间" width="180" />
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :disabled="row.status === 'approved'"
              :data-testid="`approve-task-${row.task_id}`"
              @click="openApprove(row)"
            >
              审批
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dialogVisible" title="审批采购订单" width="500px">
      <el-form label-width="90px">
        <el-form-item label="任务编号">{{ currentTask?.task_id }}</el-form-item>
        <el-form-item label="采购订单">{{ currentTask?.purchase_order_number }}</el-form-item>
        <el-form-item label="审批意见">
          <el-input v-model="comment" type="textarea" :rows="4" data-testid="approval-comment" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="approving" data-testid="approval-submit" @click="approve">同意</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiClient, type ApprovalTask, type PurchaseOrder } from '@/api/client'

const loading = ref(false)
const approving = ref(false)
const priority = ref('')
const status = ref('pending')
const approvals = ref<ApprovalTask[]>([])
const orders = ref<PurchaseOrder[]>([])
const dialogVisible = ref(false)
const currentTask = ref<ApprovalTask | null>(null)
const comment = ref('同意采购订单进入执行，请按合同条款留痕归档。')

const filteredApprovals = computed(() =>
  approvals.value.filter((task) => {
    const matchesStatus = !status.value || task.status === status.value
    const matchesPriority = !priority.value || orderPriority(task.purchase_order_number) === priority.value
    return matchesStatus && matchesPriority
  })
)

function orderPriority(orderNumber: string) {
  return orders.value.find((order) => order.number === orderNumber)?.priority || 'high'
}

async function loadData() {
  loading.value = true
  try {
    const [approvalRes, orderRes] = await Promise.all([
      apiClient.get<ApprovalTask[]>('/approvals'),
      apiClient.get<PurchaseOrder[]>('/purchase-orders')
    ])
    approvals.value = approvalRes.data
    orders.value = orderRes.data
  } finally {
    loading.value = false
  }
}

function openApprove(task: ApprovalTask) {
  currentTask.value = task
  dialogVisible.value = true
}

async function approve() {
  if (!currentTask.value) return
  approving.value = true
  try {
    await apiClient.post(`/approvals/${currentTask.value.task_id}/approve`, { comment: comment.value })
    ElMessage.success('审批已通过')
    dialogVisible.value = false
    await loadData()
  } finally {
    approving.value = false
  }
}

onMounted(loadData)
</script>
