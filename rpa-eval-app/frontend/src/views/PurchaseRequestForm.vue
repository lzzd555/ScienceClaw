<template>
  <div class="page">
    <div class="toolbar">
      <h1 class="page-title">新建采购申请</h1>
      <el-button @click="$router.push('/purchase-requests')">返回列表</el-button>
    </div>
    <el-form ref="formRef" class="panel" :model="form" :rules="rules" label-width="110px">
      <el-form-item label="申请标题" prop="title">
        <el-input v-model="form.title" data-testid="pr-title" />
      </el-form-item>
      <el-form-item label="关联合同" prop="contract_number">
        <el-select v-model="form.contract_number" filterable placeholder="请选择合同" data-testid="pr-contract-number">
          <el-option v-for="contract in contracts" :key="contract.number" :label="`${contract.number} - ${contract.title}`" :value="contract.number" />
        </el-select>
      </el-form-item>
      <el-form-item label="申请部门" prop="department">
        <el-input v-model="form.department" data-testid="pr-department" />
      </el-form-item>
      <el-form-item label="申请人" prop="requester">
        <el-input v-model="form.requester" data-testid="pr-requester" />
      </el-form-item>
      <el-divider content-position="left">采购明细</el-divider>
      <el-table :data="form.items" border>
        <el-table-column label="物料/服务名称" min-width="220">
          <template #default="{ row, $index }">
            <el-input v-model="row.name" :data-testid="`pr-item-name-${$index}`" />
          </template>
        </el-table-column>
        <el-table-column label="数量" width="130">
          <template #default="{ row, $index }">
            <el-input-number v-model="row.quantity" :min="1" :data-testid="`pr-item-quantity-${$index}`" />
          </template>
        </el-table-column>
        <el-table-column label="单价" width="160">
          <template #default="{ row, $index }">
            <el-input-number v-model="row.unit_price" :min="1" :precision="2" :data-testid="`pr-item-price-${$index}`" />
          </template>
        </el-table-column>
        <el-table-column label="成本中心" width="180">
          <template #default="{ row, $index }">
            <el-input v-model="row.cost_center" :data-testid="`pr-item-cost-center-${$index}`" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ $index }">
            <el-button link type="danger" :disabled="form.items.length === 1" @click="removeItem($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="form-actions">
        <el-button data-testid="add-pr-item" @click="addItem">新增明细行</el-button>
        <el-button type="primary" :loading="submitting" data-testid="submit-purchase-request" @click="submit">
          提交采购申请
        </el-button>
      </div>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { apiClient, apiErrorMessage, type Contract, type PurchaseRequestItem } from '@/api/client'

const router = useRouter()
const formRef = ref<FormInstance>()
const contracts = ref<Contract[]>([])
const submitting = ref(false)
const form = reactive({
  title: '采购流程RPA评估新增申请',
  contract_number: '',
  department: '采购管理部',
  requester: '王敏',
  items: [{ name: 'RPA采购订单自动化执行授权', quantity: 5, unit_price: 6800, cost_center: 'PROC-RPA-2026' }] as PurchaseRequestItem[]
})
const rules: FormRules = {
  title: [{ required: true, message: '请输入申请标题', trigger: 'blur' }],
  contract_number: [{ required: true, message: '请选择关联合同', trigger: 'change' }],
  department: [{ required: true, message: '请输入申请部门', trigger: 'blur' }],
  requester: [{ required: true, message: '请输入申请人', trigger: 'blur' }]
}

function addItem() {
  form.items.push({ name: '', quantity: 1, unit_price: 1, cost_center: 'PROC-RPA-2026' })
}

function removeItem(index: number) {
  form.items.splice(index, 1)
}

async function submit() {
  await formRef.value?.validate()
  if (form.items.some((item) => !item.name || !item.cost_center || item.quantity <= 0 || item.unit_price <= 0)) {
    ElMessage.error('请完整填写采购明细')
    return
  }
  submitting.value = true
  try {
    await apiClient.post('/purchase-requests', form)
    ElMessage.success('采购申请已提交：PR-2026-RPA-NEW-001')
    await router.push('/purchase-requests')
  } catch (error) {
    const message = apiErrorMessage(error, '采购申请已存在或提交失败，请检查关联合同和明细数据')
    ElMessage.error(message.includes('already exists') ? '采购申请已存在：PR-2026-RPA-NEW-001' : message)
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  const { data } = await apiClient.get<Contract[]>('/contracts')
  contracts.value = data
  form.contract_number = data[0]?.number || ''
})
</script>

<style scoped>
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
</style>
