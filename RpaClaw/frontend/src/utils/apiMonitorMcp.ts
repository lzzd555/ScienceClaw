import type { JsonSchemaObject } from '@/api/rpaMcp';

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function cloneJsonValue<T>(value: T): T {
  try {
    return JSON.parse(JSON.stringify(value)) as T;
  } catch {
    return value;
  }
}

function getSchemaType(schema: JsonSchemaObject): string | undefined {
  if (Array.isArray(schema.type)) {
    return schema.type.find((item) => item && item !== 'null');
  }
  return typeof schema.type === 'string' ? schema.type : undefined;
}

function formatYamlScalar(value: unknown): string {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    const needsQuotes =
      trimmed === ''
      || /^\s|\s$/.test(value)
      || /[:#\n\r\t]/.test(value)
      || /^[-?:,\[\]{}&*!|>'"%@`]/.test(trimmed)
      || /^(true|false|null|~|yes|no|on|off|y|n|inf|-inf|nan)$/i.test(trimmed)
      || /^[-+]?\d+(\.\d+)?$/.test(trimmed);

    return needsQuotes ? JSON.stringify(value) : value;
  }

  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }

  if (value === null) {
    return 'null';
  }

  return prettyJson(value);
}

function getTopLevelIndent(lines: string[]): string {
  for (const line of lines) {
    if (!line.trim()) continue;
    const match = line.match(/^(\s*)/);
    return match?.[1] ?? '';
  }
  return '';
}

function findTopLevelFieldIndex(lines: string[], fieldName: string, indent: string): number {
  const fieldPattern = new RegExp(`^${escapeRegExp(indent)}${escapeRegExp(fieldName)}\\s*:`);
  return lines.findIndex((line) => fieldPattern.test(line));
}

function insertAt<T>(items: T[], index: number, item: T): T[] {
  const next = items.slice();
  next.splice(index, 0, item);
  return next;
}

export function syncYamlTopLevelField(yamlText: string, fieldName: string, value: unknown): string {
  const lines = yamlText.split(/\r?\n/);
  const indent = getTopLevelIndent(lines);
  const serializedValue = formatYamlScalar(value);
  const nextLine = `${indent}${fieldName}: ${serializedValue}`;
  const fieldIndex = findTopLevelFieldIndex(lines, fieldName, indent);

  if (fieldIndex >= 0) {
    const nextLines = lines.slice();
    nextLines[fieldIndex] = nextLine;
    return nextLines.join(yamlText.includes('\r\n') ? '\r\n' : '\n');
  }

  if (fieldName === 'description') {
    const nameIndex = findTopLevelFieldIndex(lines, 'name', indent);
    if (nameIndex >= 0) {
      return insertAt(lines, nameIndex + 1, nextLine).join(yamlText.includes('\r\n') ? '\r\n' : '\n');
    }
  }

  const appendIndex = lines.length > 0 && lines[lines.length - 1] === '' ? lines.length - 1 : lines.length;
  return insertAt(lines, appendIndex, nextLine).join(yamlText.includes('\r\n') ? '\r\n' : '\n');
}

export function buildSampleArguments(schema: JsonSchemaObject | null | undefined): unknown {
  if (!schema || typeof schema !== 'object') {
    return {};
  }

  if (schema.default !== undefined) {
    return cloneJsonValue(schema.default);
  }

  if (schema.const !== undefined) {
    return cloneJsonValue(schema.const);
  }

  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return cloneJsonValue(schema.enum[0]);
  }

  if (Array.isArray(schema.oneOf) && schema.oneOf.length > 0) {
    return buildSampleArguments(schema.oneOf[0] as JsonSchemaObject);
  }

  if (Array.isArray(schema.anyOf) && schema.anyOf.length > 0) {
    return buildSampleArguments(schema.anyOf[0] as JsonSchemaObject);
  }

  const schemaType = getSchemaType(schema);
  if (schemaType === 'object' || schema.properties) {
    const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
    const sample: Record<string, unknown> = {};
    for (const [key, propertySchema] of Object.entries(properties)) {
      const nextValue = buildSampleArguments(propertySchema as JsonSchemaObject);
      if (nextValue !== undefined) {
        sample[key] = nextValue;
      }
    }
    return sample;
  }

  if (schemaType === 'array') {
    const itemSchema = schema.items && typeof schema.items === 'object' ? schema.items : {};
    const sampleItem = buildSampleArguments(itemSchema as JsonSchemaObject);
    return sampleItem === undefined ? [] : [sampleItem];
  }

  if (schemaType === 'boolean') {
    return true;
  }

  if (schemaType === 'integer' || schemaType === 'number') {
    return 1;
  }

  if (schemaType === 'string') {
    return 'sample';
  }

  return {};
}

export function formatValidationStatus(status?: string | null): string {
  const normalized = (status || '').trim().toLowerCase();
  if (!normalized) {
    return 'Unknown';
  }
  if (normalized === 'valid') {
    return 'Valid';
  }
  if (normalized === 'invalid') {
    return 'Invalid';
  }
  return normalized
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function prettyJson(value: unknown): string {
  if (value === undefined) {
    return '';
  }

  try {
    return JSON.stringify(value, null, 2) ?? '';
  } catch {
    return String(value);
  }
}
