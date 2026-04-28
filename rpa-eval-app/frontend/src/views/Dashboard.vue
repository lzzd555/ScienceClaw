<template>
  <div class="page">
    <div class="metrics">
      <el-card v-for="item in metrics" :key="item.label" shadow="never" class="metric-card">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </el-card>
    </div>
    <div class="panel">
      <h2 class="page-title">今日RPA评估任务</h2>
      <el-table :data="tasks" border>
        <el-table-column prop="name" label="业务场景" min-width="220" />
        <el-table-column prop="owner" label="责任角色" width="140" />
        <el-table-column prop="target" label="验证目标" min-width="260" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag>{{ row.status }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
const metrics = [
  { label: '有效合同', value: '3' },
  { label: '供应商档案', value: '3' },
  { label: '待审批订单', value: '1' },
  { label: '报表任务', value: '1' }
]

const tasks = [
  { name: '合同台账查询与详情核对', owner: '采购专员', target: '按合同编号过滤，进入详情页核对金额、供应商与合规条款', status: '待执行' },
  { name: '供应商联系人维护', owner: '采购专员', target: '补全待联系供应商的联系人与电话，并保存状态', status: '待执行' },
  { name: '采购申请到订单生成', owner: '采购专员', target: '创建固定采购申请并生成采购订单', status: '待执行' },
  { name: '订单审批与报表导出', owner: '审批经理', target: '审批高优先级订单，生成并下载采购汇总报表', status: '待执行' }
]
</script>

<style scoped>
.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.metric-card span {
  color: #6b7280;
}

.metric-card strong {
  display: block;
  margin-top: 8px;
  font-size: 28px;
}
</style>
