<template>
  <div class="page">
    <h1 class="page-title">报表中心</h1>
    <div class="report-grid">
      <el-card shadow="never">
        <template #header>同步导出</template>
        <div class="button-stack">
          <el-button data-testid="export-contracts" @click="exportFile('contracts')">导出合同台账</el-button>
          <el-button data-testid="export-purchase-orders" @click="exportFile('purchase-orders')">导出采购订单</el-button>
          <el-button type="primary" data-testid="export-merge" @click="exportFile('merge')">合并导出采购合规包</el-button>
        </div>
      </el-card>
      <el-card shadow="never">
        <template #header>异步报表任务</template>
        <el-descriptions v-if="job" :column="1" border>
          <el-descriptions-item label="任务编号">{{ job.job_id }}</el-descriptions-item>
          <el-descriptions-item label="报表类型">{{ job.report_type }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ job.status }}</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ job.filename }}</el-descriptions-item>
          <el-descriptions-item label="轮询次数">{{ job.poll_count }}</el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="暂无报表任务" />
        <div class="report-actions">
          <el-button type="primary" data-testid="generate-async-report" :loading="generating" @click="generateAsyncReport">
            生成供应商采购汇总
          </el-button>
          <el-button data-testid="refresh-report" @click="refreshReport">刷新状态</el-button>
          <el-button :disabled="job?.status !== 'completed'" data-testid="download-report" @click="downloadReport">下载报表</el-button>
        </div>
      </el-card>
    </div>
    <div class="panel table-panel">
      <h2 class="page-title">报表任务列表</h2>
      <el-table :data="jobs" border stripe>
        <el-table-column prop="job_id" label="任务编号" width="180" />
        <el-table-column prop="report_type" label="报表类型" min-width="220" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="filename" label="文件名" min-width="260" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column prop="completed_at" label="完成时间" width="180" />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiClient, downloadFromResponse, filenameFromDisposition, type ReportJob } from '@/api/client'

const jobs = ref<ReportJob[]>([])
const job = ref<ReportJob | null>(null)
const generating = ref(false)
const reportCode = 'RPT-2026-RPA-001'

async function loadJobs() {
  const { data } = await apiClient.get<ReportJob[]>('/reports')
  jobs.value = data
  job.value = data.find((item) => item.job_id === reportCode) || data[0] || null
}

async function exportFile(type: 'contracts' | 'purchase-orders' | 'merge') {
  const config = { responseType: 'blob' as const }
  const response =
    type === 'contracts'
      ? await apiClient.get('/reports/contracts/export', config)
      : type === 'purchase-orders'
        ? await apiClient.get('/reports/purchase-orders/export', config)
        : await apiClient.post('/reports/merge/export', undefined, config)
  downloadFromResponse(response.data, filenameFromDisposition(response.headers['content-disposition']))
}

async function generateAsyncReport() {
  generating.value = true
  try {
    const { data } = await apiClient.post<ReportJob>('/reports/async/generate')
    job.value = data
    ElMessage.success('异步报表任务已提交')
    await loadJobs()
  } finally {
    generating.value = false
  }
}

async function refreshReport() {
  const { data } = await apiClient.get<ReportJob>(`/reports/async/${reportCode}`)
  job.value = data
  await loadJobs()
}

async function downloadReport() {
  const response = await apiClient.get(`/reports/async/${reportCode}/download`, { responseType: 'blob' })
  downloadFromResponse(response.data, filenameFromDisposition(response.headers['content-disposition'], 'supplier_purchase_summary_2026.xlsx'))
}

onMounted(loadJobs)
</script>

<style scoped>
.report-grid {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 12px;
  margin-bottom: 12px;
}

.button-stack,
.report-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.button-stack {
  flex-direction: column;
  align-items: flex-start;
}

.table-panel {
  margin-top: 12px;
}
</style>
