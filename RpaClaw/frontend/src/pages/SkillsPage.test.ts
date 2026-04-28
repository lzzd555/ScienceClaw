// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SkillsPage from './SkillsPage.vue';
import en from '../locales/en';
import zh from '../locales/zh';

const push = vi.fn();
const getSkills = vi.fn();

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}));

vi.mock('../api/agent', () => ({
  getSkills: () => getSkills(),
  blockSkill: vi.fn(),
  deleteSkill: vi.fn(),
}));

async function flushAsyncUpdates() {
  await Promise.resolve();
  await nextTick();
}

async function mountSkillsPage(locale = 'zh') {
  const root = document.createElement('div');
  document.body.appendChild(root);

  const app = createApp(SkillsPage);
  app.use(createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'en',
    messages: { en, zh },
  }));
  app.mount(root);
  await flushAsyncUpdates();

  return { app, root };
}

describe('SkillsPage delete confirmation', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('renders the localized skill delete message with the selected skill name', async () => {
    getSkills.mockResolvedValue([
      {
        name: 'github-issues',
        description: 'GitHub issue helper',
        blocked: false,
        builtin: false,
        files: [],
      },
    ]);

    const { app, root } = await mountSkillsPage('zh');

    root.querySelector<HTMLButtonElement>('.skill-card button:last-child')?.click();
    await nextTick();

    const dialogText = document.body.textContent || '';
    expect(dialogText).toContain('确定要删除技能「github-issues」吗？');
    expect(dialogText).toContain('此操作无法撤销');
    expect(dialogText).not.toContain('{name}');
    expect(dialogText).not.toContain('Are you sure you want to delete');

    app.unmount();
  });
});

describe('SkillsPage skill summaries', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('shows the skill description instead of file names in cards', async () => {
    getSkills.mockResolvedValue([
      {
        name: 'brainstorming',
        description: 'Generate and refine feature ideas before implementation.',
        blocked: false,
        builtin: true,
        files: ['SKILL.md', 'spec-document-reviewer-prompt.md'],
      },
    ]);

    const { app, root } = await mountSkillsPage('en');
    const pageText = root.textContent || '';

    expect(pageText).toContain('Generate and refine feature ideas before implementation.');
    expect(pageText).not.toContain('SKILL.md');
    expect(pageText).not.toContain('spec-document-reviewer-prompt.md');

    app.unmount();
  });

  it('filters skills by description text', async () => {
    getSkills.mockResolvedValue([
      {
        name: 'brainstorming',
        description: 'Generate and refine feature ideas before implementation.',
        blocked: false,
        builtin: true,
        files: ['SKILL.md'],
      },
      {
        name: 'weather',
        description: 'Check the weather forecast for a city.',
        blocked: false,
        builtin: true,
        files: ['SKILL.md'],
      },
    ]);

    const { app, root } = await mountSkillsPage('en');
    const searchInput = root.querySelector<HTMLInputElement>('input');
    searchInput!.value = 'forecast';
    searchInput!.dispatchEvent(new Event('input'));
    await nextTick();

    const pageText = root.textContent || '';
    expect(pageText).toContain('weather');
    expect(pageText).not.toContain('brainstorming');

    app.unmount();
  });
});
