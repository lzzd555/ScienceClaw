<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Play, Save, CheckCircle, XCircle, Loader2, Terminal, Code, ArrowLeft, RotateCcw } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { getRpaVncUrl } from '@/utils/sandbox';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => route.query.sessionId as string);
const skillName = computed(() => (route.query.skillName as string) || '录制技能');
const skillDescription = computed(() => (route.query.skillDescription as string) || '');
const params = computed(() => {
  try {
    return JSON.parse((route.query.params as string) || '{}');
  } catch {
    return {};
  }
});

const vncUrl = computed(() => getRpaVncUrl());

const testing = ref(false);
const testDone = ref(false);
const testSuccess = ref(false);
const testOutput = ref('');
const testLogs = ref<string[]>([]);
const generatedScript = ref('');
const saving = ref(false);
const saved = ref(false);
const error = ref<string | null>(null);

const runTest = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId';
    return;
  }
  testing.value = true;
  testDone.value = false;
  testLogs.value = ['正在生成并执行 Playwright 脚本...'];

  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/test`, {
      params: params.value,
    }, {
      timeout: 120000, // Script execution can take a while
    });

    const result = resp.data.result || {};
    testOutput.value = result.output || '';
    testLogs.value = resp.data.logs || [];
    generatedScript.value = resp.data.script || '';
    testSuccess.value = result.success !== false;
    testDone.value = true;
  } catch (err: any) {
    testLogs.value.push(`错误: ${err.response?.data?.detail || err.message}`);
    testSuccess.value = false;
    testDone.value = true;
  } finally {
    testing.value = false;
  }
};

const goBackToConfigure = () => {
  router.push(`/rpa/configure?sessionId=${sessionId.value}`);
};

const goBackToRecorder = () => {
  router.push('/rpa/recorder');
};

const saveSkill = async () => {
  if (!sessionId.value) return;
  saving.value = true;
  error.value = null;

  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/save`, {
      skill_name: skillName.value,
      description: skillDescription.value,
      params: params.value,
    });

    if (resp.data.status === 'success') {
      saved.value = true;
      setTimeout(() => {
        router.push('/chat/skills');
      }, 2000);
    }
  } catch (err: any) {
    error.value = '保存失败: ' + (err.response?.data?.detail || err.message);
  } finally {
    saving.value = false;
  }
};

onMounted(() => {
  runTest();
});
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7]">
    <!-- Header -->
    <header class="h-16 bg-white border-b border-gray-200 flex items-center px-8 gap-4">
      <button
        @click="goBackToConfigure"
        class="flex items-center gap-1 text-gray-500 hover:text-gray-700 transition-colors"
      >
        <ArrowLeft :size="18" />
      </button>
      <Play class="text-[#831bd7]" :size="24" />
      <h1 class="text-gray-900 font-extrabold text-xl">测试技能</h1>
      <span class="text-sm text-gray-500">{{ skillName }}</span>
      <div class="flex-1"></div>

      <button
        v-if="testDone && !testSuccess && !saved"
        @click="goBackToRecorder"
        class="flex items-center gap-2 bg-white border border-gray-300 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
      >
        <RotateCcw :size="16" />
        重新录制
      </button>

      <button
        v-if="testDone && testSuccess && !saved"
        @click="saveSkill"
        :disabled="saving"
        class="flex items-center gap-2 bg-[#831bd7] text-white px-6 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8] transition-colors disabled:opacity-50"
      >
        <Save :size="16" />
        {{ saving ? '保存中...' : '保存技能' }}
      </button>

      <div v-if="saved" class="flex items-center gap-2 text-green-600 font-bold text-sm">
        <CheckCircle :size="18" />
        技能已保存，正在跳转...
      </div>
    </header>

    <div class="max-w-6xl mx-auto p-8">
      <div class="grid grid-cols-2 gap-8">
        <!-- Left: VNC viewport -->
        <div class="bg-[#1e1e1e] rounded-2xl shadow-2xl overflow-hidden border border-gray-800">
          <div class="h-8 bg-[#dadddf] flex items-center px-4 gap-2">
            <div class="flex gap-1.5">
              <div class="w-2 h-2 rounded-full bg-red-400"></div>
              <div class="w-2 h-2 rounded-full bg-yellow-400"></div>
              <div class="w-2 h-2 rounded-full bg-green-400"></div>
            </div>
            <span class="text-[10px] text-gray-500 ml-2">测试执行画面</span>
          </div>
          <div class="aspect-video bg-black">
            <iframe
              :src="vncUrl"
              class="w-full h-full border-0"
              allow="clipboard-read; clipboard-write"
            />
          </div>
        </div>

        <!-- Right: Logs & Status -->
        <div class="space-y-6">
          <!-- Status -->
          <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
            <div class="flex items-center gap-3 mb-4">
              <Loader2 v-if="testing" class="text-[#831bd7] animate-spin" :size="20" />
              <CheckCircle v-else-if="testDone && testSuccess" class="text-green-500" :size="20" />
              <XCircle v-else-if="testDone && !testSuccess" class="text-red-500" :size="20" />
              <h2 class="text-gray-900 font-bold text-lg">
                {{ testing ? '正在执行...' : testDone ? (testSuccess ? '执行成功' : '执行失败') : '准备测试' }}
              </h2>
            </div>

            <button
              v-if="testDone"
              @click="runTest"
              :disabled="testing"
              class="flex items-center gap-2 bg-gray-100 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 transition-colors"
            >
              <Play :size="14" />
              重新执行
            </button>
          </div>

          <!-- Logs -->
          <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
            <h3 class="text-gray-900 font-bold text-sm mb-3">
              <Terminal :size="14" class="inline mr-1" />
              执行日志
            </h3>
            <div class="bg-gray-900 rounded-lg p-4 max-h-48 overflow-y-auto">
              <div
                v-for="(log, idx) in testLogs"
                :key="idx"
                class="text-xs font-mono text-green-400 leading-relaxed"
              >
                <span class="text-gray-500">{{ String(idx + 1).padStart(2, '0') }}</span>
                {{ log }}
              </div>
              <div v-if="testOutput" class="text-xs font-mono text-gray-400 mt-2 border-t border-gray-700 pt-2">
                {{ testOutput }}
              </div>
            </div>
          </div>

          <!-- Generated Script -->
          <div v-if="generatedScript" class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
            <h3 class="text-gray-900 font-bold text-sm mb-3">
              <Code :size="14" class="inline mr-1" />
              执行的脚本
            </h3>
            <pre class="bg-gray-900 text-green-400 p-4 rounded-lg text-xs overflow-x-auto max-h-64 overflow-y-auto"><code>{{ generatedScript }}</code></pre>
          </div>

          <!-- Error -->
          <div v-if="error" class="bg-red-50 border border-red-200 rounded-xl p-4">
            <p class="text-red-600 text-sm">{{ error }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
