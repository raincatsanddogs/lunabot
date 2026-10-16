<script setup lang="ts">
import { ref, watch } from 'vue';
import { useDashboardState } from '../composables/useDashboardState';
import { apiFetch } from '../utils/http';
import type { MemoryItem, MemoryCursorResponse, StickerItem, StickerCursorResponse } from '../types/api';
import { formatRelativeTime } from '../utils/format';

const { currentGroupId } = useDashboardState();

const tab = ref<'memories' | 'stickers'>('memories');
const memories = ref<MemoryItem[]>([]);
const nextMemCursor = ref<number | null>(null);
const loadingMemories = ref(false);
const memoryQuery = ref('');
const memoryStatus = ref('active');

const stickers = ref<StickerItem[]>([]);
const nextStickerCursor = ref<string | null>(null);
const loadingStickers = ref(false);
const stickerQuery = ref('');

// Selection for batch deletion
const selectedMemoryIds = ref<Set<string>>(new Set());

const loadMemories = async (reset = true) => {
  if (!currentGroupId.value) return;
  loadingMemories.value = true;
  try {
    const cur = reset ? '' : nextMemCursor.value ? `&cursor=${nextMemCursor.value}` : '';
    const q = memoryQuery.value ? `&q=${encodeURIComponent(memoryQuery.value)}` : '';
    const res = await apiFetch<MemoryCursorResponse>(
      `/api/v1/groups/${currentGroupId.value}/memories?limit=20&status=${memoryStatus.value}${cur}${q}`
    );
    if (reset) {
      memories.value = res.items;
      selectedMemoryIds.value.clear();
    } else {
      memories.value.push(...res.items);
    }
    nextMemCursor.value = res.next_cursor || null;
  } catch (err) {
    console.error('Failed to load memories:', err);
  } finally {
    loadingMemories.value = false;
  }
};

const loadStickers = async (reset = true) => {
  if (!currentGroupId.value) return;
  loadingStickers.value = true;
  try {
    const cur = reset ? '' : nextStickerCursor.value ? `&cursor=${nextStickerCursor.value}` : '';
    const q = stickerQuery.value ? `&q=${encodeURIComponent(stickerQuery.value)}` : '';
    const res = await apiFetch<StickerCursorResponse>(
      `/api/v1/groups/${currentGroupId.value}/stickers?limit=30${cur}${q}`
    );
    if (reset) {
      stickers.value = res.items;
    } else {
      stickers.value.push(...res.items);
    }
    nextStickerCursor.value = res.next_cursor || null;
  } catch (err) {
    console.error('Failed to load stickers:', err);
  } finally {
    loadingStickers.value = false;
  }
};

const toggleSelectMemory = (id: string) => {
  if (selectedMemoryIds.value.has(id)) {
    selectedMemoryIds.value.delete(id);
  } else {
    selectedMemoryIds.value.add(id);
  }
};

const deleteSelectedMemories = async () => {
  if (!currentGroupId.value || selectedMemoryIds.value.size === 0) return;
  if (!confirm(`确定删除选中的 ${selectedMemoryIds.value.size} 条记忆？系统将自动安装防护栏阻断再次学入。`)) {
    return;
  }
  try {
    await apiFetch(`/api/v1/groups/${currentGroupId.value}/memories`, {
      method: 'DELETE',
      body: JSON.stringify({ ids: Array.from(selectedMemoryIds.value) }),
    });
    await loadMemories(true);
  } catch (err: any) {
    alert(err.message || '删除失败');
  }
};

const deleteSingleMemory = async (mid: string) => {
  if (!currentGroupId.value) return;
  if (!confirm('确定删除该条记忆？系统将自动安装防护栏阻断再次学入。')) return;
  try {
    await apiFetch(`/api/v1/groups/${currentGroupId.value}/memories/${mid}`, {
      method: 'DELETE',
    });
    memories.value = memories.value.filter((m) => m.id !== mid);
    selectedMemoryIds.value.delete(mid);
  } catch (err: any) {
    alert(err.message || '删除失败');
  }
};

const deleteSingleSticker = async (sid: string) => {
  if (!currentGroupId.value) return;
  if (!confirm('确定废弃该表情包标注缓存？')) return;
  try {
    await apiFetch(`/api/v1/groups/${currentGroupId.value}/stickers/${sid}`, {
      method: 'DELETE',
    });
    stickers.value = stickers.value.filter((s) => s.id !== sid);
  } catch (err: any) {
    alert(err.message || '删除失败');
  }
};

watch(
  [currentGroupId, tab],
  () => {
    if (tab.value === 'memories') loadMemories(true);
    else loadStickers(true);
  },
  { immediate: true }
);
</script>

<template>
  <div class="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto space-y-4">
    <!-- Sub tabs -->
    <div class="flex items-center justify-between border-b border-slate-800 pb-3">
      <div class="flex gap-2">
        <button
          @click="tab = 'memories'"
          class="px-3 py-1.5 text-xs font-semibold rounded-lg transition"
          :class="tab === 'memories' ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30' : 'text-slate-400 hover:text-slate-200'"
        >
          🧠 长期记忆管理 (Memories)
        </button>
        <button
          @click="tab = 'stickers'"
          class="px-3 py-1.5 text-xs font-semibold rounded-lg transition"
          :class="tab === 'stickers' ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30' : 'text-slate-400 hover:text-slate-200'"
        >
          🖼️ 表情包与多模态描述 (Stickers)
        </button>
      </div>

      <!-- Action bar for Memories -->
      <div v-if="tab === 'memories'" class="flex items-center gap-2">
        <select v-model="memoryStatus" @change="loadMemories(true)" class="input-dark text-xs py-1">
          <option value="active">有效生效 (active)</option>
          <option value="candidate">待复核 (candidate)</option>
          <option value="all">全部状态</option>
        </select>
        <input
          v-model="memoryQuery"
          @keyup.enter="loadMemories(true)"
          placeholder="回车模糊搜索..."
          class="input-dark text-xs py-1 w-40"
        />
        <button
          v-if="selectedMemoryIds.size > 0"
          @click="deleteSelectedMemories"
          class="px-2.5 py-1 bg-rose-600 hover:bg-rose-500 text-white text-xs rounded font-medium"
        >
          批量清理 ({{ selectedMemoryIds.size }})
        </button>
      </div>

      <!-- Action bar for Stickers -->
      <div v-else class="flex items-center gap-2">
        <input
          v-model="stickerQuery"
          @keyup.enter="loadStickers(true)"
          placeholder="搜索表情描述..."
          class="input-dark text-xs py-1 w-48"
        />
      </div>
    </div>

    <!-- MEMORIES VIEW -->
    <div v-if="tab === 'memories'" class="space-y-3">
      <div
        v-for="item in memories"
        :key="item.id"
        @click="toggleSelectMemory(item.id)"
        class="card-panel transition hover:border-slate-600 flex items-start gap-3 cursor-pointer"
        :class="selectedMemoryIds.has(item.id) ? 'border-sky-500/80 bg-slate-800/90' : ''"
      >
        <input
          type="checkbox"
          :checked="selectedMemoryIds.has(item.id)"
          @click.stop="toggleSelectMemory(item.id)"
          class="mt-1 accent-sky-500 w-4 h-4 cursor-pointer"
        />
        <div class="flex-1 min-w-0 space-y-1">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span
                class="px-1.5 py-0.2 text-[10px] rounded font-mono uppercase"
                :class="item.kind === 'fact' ? 'bg-sky-500/10 text-sky-400 border border-sky-500/20' : 'bg-purple-500/10 text-purple-400 border border-purple-500/20'"
              >
                {{ item.kind }}
              </span>
              <span class="text-xs text-slate-400 font-mono">v{{ item.version }}</span>
              <span class="text-xs text-slate-400 font-mono">主体: {{ item.subject_ids.join(', ') || '未关联' }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-[11px] text-slate-500">{{ formatRelativeTime(item.time) }}</span>
              <button
                @click.stop="deleteSingleMemory(item.id)"
                class="text-[11px] text-slate-500 hover:text-rose-400 transition px-1 py-0.5 rounded hover:bg-rose-500/10"
                title="删除该条记忆"
              >
                🗑️
              </button>
            </div>
          </div>
          <div class="text-xs text-slate-200 leading-relaxed font-sans">
            {{ item.content }}
          </div>
          <!-- Evidence quotes -->
          <div v-if="item.evidence && item.evidence.length > 0" class="pt-1">
            <span class="text-[10px] text-slate-500 bg-slate-900/80 px-2 py-0.5 rounded font-mono inline-block">
              证据: "{{ item.evidence[0].quote }}"
            </span>
          </div>
        </div>
      </div>

      <div v-if="memories.length === 0 && !loadingMemories" class="text-center text-xs text-slate-500 py-12">
        暂无记忆条目
      </div>

      <div v-if="nextMemCursor" class="text-center pt-2">
        <button @click="loadMemories(false)" :disabled="loadingMemories" class="btn-secondary">
          {{ loadingMemories ? '正在加载...' : '加载更多记忆' }}
        </button>
      </div>
    </div>

    <!-- STICKERS VIEW -->
    <div v-if="tab === 'stickers'" class="space-y-3">
      <div v-for="item in stickers" :key="item.id" class="card-panel flex items-start justify-between gap-3">
        <div class="space-y-1.5 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold text-sky-300">
              🖼️ {{ item.description || '无画面描述' }}
            </span>
            <span v-if="item.text" class="text-[11px] text-amber-300 bg-amber-500/10 px-1.5 py-0.2 rounded border border-amber-500/20 font-mono">
              字: "{{ item.text }}"
            </span>
          </div>

          <!-- Intent & Tone Pills -->
          <div class="flex flex-wrap gap-1 text-[10px]">
            <span v-for="intent in item.intents" :key="intent" class="px-1.5 py-0.2 bg-slate-700/80 text-slate-300 rounded">
              #{{ intent }}
            </span>
            <span v-for="t in item.tone" :key="t" class="px-1.5 py-0.2 bg-indigo-500/10 text-indigo-300 rounded border border-indigo-500/20">
              语气: {{ t }}
            </span>
          </div>

          <div class="text-[10px] text-slate-500 font-mono">
            素材ID: {{ item.asset_id.slice(0, 16) }}... • {{ (item.size / 1024).toFixed(1) }} KB
          </div>
        </div>

        <button @click="deleteSingleSticker(item.id)" class="text-xs text-slate-400 hover:text-rose-400 shrink-0">
          废弃标注
        </button>
      </div>

      <div v-if="stickers.length === 0 && !loadingStickers" class="text-center text-xs text-slate-500 py-12">
        暂无表情包多模态描述缓存
      </div>

      <div v-if="nextStickerCursor" class="text-center pt-2">
        <button @click="loadStickers(false)" :disabled="loadingStickers" class="btn-secondary">
          {{ loadingStickers ? '正在加载...' : '加载更多表情包' }}
        </button>
      </div>
    </div>
  </div>
</template>
