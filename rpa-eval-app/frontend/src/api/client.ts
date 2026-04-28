import axios from 'axios'

export interface User {
  username: string
  display_name: string
  role: string
  department: string
}

export interface Contract {
  number: string
  title: string
  contract_type: string
  status: string
  supplier_number: string
  supplier_name: string
  amount: number
  currency: string
  owner_department: string
  start_date: string
  end_date: string
  compliance_clause: string
}

export interface Supplier {
  number: string
  name: string
  status: string
  category: string
  region: string
  risk_level: string
  compliance_rating: string
  contact_person?: string | null
  contact_phone?: string | null
  contact_email?: string | null
}

export interface PurchaseRequestItem {
  name: string
  quantity: number
  unit_price: number
  cost_center: string
}

export interface PurchaseRequest {
  number: string
  title: string
  contract_number: string
  supplier_name: string
  department: string
  requester: string
  status: string
  total_amount: number
  created_at: string
  items: PurchaseRequestItem[]
}

export interface PurchaseOrder {
  number: string
  request_number: string
  supplier_number: string
  supplier_name: string
  status: string
  priority: string
  total_amount: number
  created_at: string
}

export interface ApprovalTask {
  task_id: string
  purchase_order_number: string
  assignee: string
  status: string
  decision?: string | null
  comment?: string | null
  updated_at: string
}

export interface ReportJob {
  job_id: string
  report_type: string
  status: string
  filename: string
  poll_count: number
  created_at: string
  completed_at?: string | null
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 15000
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('rpa_eval_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export function downloadFromResponse(data: BlobPart, filename: string) {
  const url = URL.createObjectURL(new Blob([data]))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function filenameFromDisposition(header?: string, fallback = 'export.xlsx') {
  const match = header?.match(/filename="?([^"]+)"?/)
  return match?.[1] || fallback
}

export function apiErrorMessage(error: unknown, fallback: string) {
  if (!axios.isAxiosError(error)) {
    return fallback
  }
  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => item?.msg || item?.message)
      .filter(Boolean)
      .join('；')
  }
  return fallback
}
