import { describe, expect, it } from 'vitest';
import { marked } from 'marked';
import { formatMarkdown } from './markdownFormatter';

describe('formatMarkdown', () => {
  it('keeps fenced code blocks with spaced language markers intact', () => {
    const input = [
      'Example call:',
      '``` python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      'Example call:',
      '```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n'));
  });

  it('does not strip a language code fence at the start of a message', () => {
    const input = [
      '``` python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      '```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n'));
  });

  it('moves an opening code fence onto its own line when it follows prose', () => {
    const input = [
      'Example call: ```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      'Example call:',
      '```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n'));
  });

  it('normalizes indented code fences so markdown parsers can recognize them', () => {
    const input = [
      'Example call:',
      '    ```python',
      'print("ok")',
      '    ```',
      '',
      'Test completed.',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      'Example call:',
      '```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n'));
  });

  it('normalizes unicode whitespace before code fences', () => {
    const input = [
      'Example call:',
      '\u3000```python',
      'print("ok")',
      '\u00a0```',
      '',
      'Test completed.',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      'Example call:',
      '```python',
      'print("ok")',
      '```',
      '',
      'Test completed.',
    ].join('\n'));
  });

  it('moves an opening code fence after unicode spacing onto its own line', () => {
    const input = [
      'Example call:\u3000```',
      'print("ok")',
      '```',
    ].join('\n');

    expect(formatMarkdown(input)).toBe([
      'Example call:',
      '```',
      'print("ok")',
      '```',
    ].join('\n'));
  });

  it('produces markdown that marked parses as a fenced code block', () => {
    const input = [
      '示例调用形式：',
      '\u3000``` python',
      'from person_physical_score import calculate_person_score',
      '',
      'result = calculate_person_score(height_cm=175, weight_kg=68, age=26)',
      'print(result)',
      '\u00a0```',
      '',
      '已执行测试，结果正常。',
    ].join('\n');

    const tokens = marked.lexer(formatMarkdown(input));
    const codeToken = tokens.find(token => token.type === 'code');

    expect(codeToken).toMatchObject({
      type: 'code',
      lang: 'python',
      text: [
        'from person_physical_score import calculate_person_score',
        '',
        'result = calculate_person_score(height_cm=175, weight_kg=68, age=26)',
        'print(result)',
      ].join('\n'),
    });
  });
});
