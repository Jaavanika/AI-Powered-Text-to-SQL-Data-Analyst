import { vi } from 'vitest'

export const sampleSource = {
  id: 'sample', name: 'Sample business database', kind: 'sample', dialect: 'sqlite', read_only: true,
  created_at: '2026-11-19T10:00:00+00:00',
  tables: [
    { name: 'customers', original_name: 'customers', row_count: 40, columns: [
      { name: 'customer_id', original_name: 'customer_id', type: 'integer' },
      { name: 'customer_name', original_name: 'customer_name', type: 'text' } ] },
    { name: 'orders', original_name: 'orders', row_count: 400, columns: [
      { name: 'order_id', original_name: 'order_id', type: 'integer' },
      { name: 'order_date', original_name: 'order_date', type: 'date' } ] },
  ],
}

export const uploadSource = {
  id: 'up1', name: 'Shop data', kind: 'upload', dialect: 'sqlite', read_only: false,
  created_at: '2026-11-19T11:00:00+00:00',
  tables: [
    { name: 'sales', original_name: 'sales', row_count: 3, columns: [
      { name: 'region', original_name: 'Region', type: 'text' },
      { name: 'amount', original_name: 'Amount', type: 'float' },
      { name: 'sold_on', original_name: 'Sold On', type: 'date' } ] },
  ],
}

export const barResult = {
  columns: ['category', 'revenue'],
  rows: [['Electronics', 600], ['Furniture', 300.5], ['Apparel', 100]],
  row_count: 3, truncated: false, max_rows: 1000, elapsed_ms: 4, notes: [],
}

export const barAnalysis = {
  profile: {
    row_count: 3, truncated: false, facts: ['The result has 3 rows.'],
    columns: [
      { name: 'category', original_name: 'category', kind: 'categorical' },
      { name: 'revenue', original_name: 'revenue', kind: 'numeric' },
    ],
  },
  chart: {
    type: 'bar', title: 'Revenue by category', x: 'category', y: ['revenue'], x_kind: 'category', horizontal: false,
    data: [{ category: 'Electronics', revenue: 600 }, { category: 'Furniture', revenue: 300.5 }, { category: 'Apparel', revenue: 100 }],
    notes: [], alternatives: ['pie', 'table'],
  },
  summary: null, summary_source: null,
}

export function okResponse(overrides = {}) {
  return {
    status: 'ok', question: 'Revenue by category?', dialect: 'sqlite',
    linked: { tables: [], relationships: [], value_hints: [], fallback: false },
    sql: 'SELECT category, SUM(x) AS revenue FROM t GROUP BY category',
    explanation: 'Adds up revenue for each category.', tables_used: ['orders', 'products'],
    assumptions: ['Revenue = quantity x price.'], clarification: null, options: [],
    validation: { valid: true, errors: [], sql: 'SELECT 1', tables: ['orders'] },
    result: barResult, attempts: [], error: null,
    conversation_id: 'c1', turn_id: 't1', analysis: barAnalysis,
    ...overrides,
  }
}

export function historyItem(overrides = {}) {
  return {
    id: 'h1', seq: 1, question: 'Revenue by category?', status: 'ok',
    sql: 'SELECT category, SUM(x) AS revenue FROM t GROUP BY category', explanation: 'Adds up revenue.',
    tables_used: ['orders'], clarification: null, options: [], columns: ['category', 'revenue'], row_count: 3,
    error: null, summary: 'Electronics led with 600.', has_result: false, created_at: '2026-11-19T10:30:00+00:00',
    conversation_id: 'c1', conversation_title: 'Revenue by category?',
    ...overrides,
  }
}

export function historyPage(items, overrides = {}) {
  return { items, total: items.length, retention: { retain_rows: 0, retention_days: 90 }, ...overrides }
}

export function definition(overrides = {}) {
  return {
    id: 'd1', kind: 'metric', name: 'Revenue', aliases: ['sales'],
    description: 'Money from completed orders only.', expression: "SUM(CASE WHEN orders.status = 'completed' THEN 1 ELSE 0 END)",
    created_at: '2026-11-19T10:00:00+00:00', updated_at: '2026-11-19T10:00:00+00:00',
    ...overrides,
  }
}

export const clarificationResponse = {
  ...okResponse(), status: 'clarification', sql: null, result: null, analysis: null, validation: null,
  clarification: 'How should best-performing be measured?', options: ['Highest total revenue', 'Most units sold'],
  explanation: '', tables_used: [], assumptions: [],
}

/** A fresh mock of every api method. Individual tests override what they need. */
export function mockApi() {
  return {
    health: vi.fn().mockResolvedValue({ status: 'ok', llm_configured: true }),
    onUnauthorized: vi.fn().mockReturnValue(() => {}),
    authConfig: vi.fn().mockResolvedValue({ mode: 'none', registration_open: false }),
    register: vi.fn(),
    login: vi.fn(),
    logout: vi.fn().mockResolvedValue(null),
    me: vi.fn(),
    changePassword: vi.fn().mockResolvedValue(null),
    listDatasources: vi.fn().mockResolvedValue([sampleSource, uploadSource]),
    deleteDatasource: vi.fn().mockResolvedValue(null),
    refreshDatasource: vi.fn(),
    testConnection: vi.fn(),
    connectDatabase: vi.fn(),
    previewTable: vi.fn().mockResolvedValue({ table: 'customers', row_count: 40,
      columns: [{ name: 'customer_id', type: 'integer' }], rows: [{ customer_id: 1 }] }),
    previewUpload: vi.fn(),
    importUpload: vi.fn(),
    getSchema: vi.fn(),
    getBlockedColumns: vi.fn().mockResolvedValue({ columns: [], locked: [] }),
    setBlockedColumns: vi.fn().mockImplementation(async (_id, columns) => ({ columns, locked: [] })),
    suggestRelationships: vi.fn().mockResolvedValue([]),
    confirmRelationship: vi.fn().mockResolvedValue({}),
    deleteRelationship: vi.fn().mockResolvedValue(null),
    chat: vi.fn(),
    listConversations: vi.fn().mockResolvedValue([]),
    getConversation: vi.fn(),
    deleteConversation: vi.fn().mockResolvedValue(null),
    history: vi.fn().mockResolvedValue({ items: [], total: 0, retention: { retain_rows: 0, retention_days: 90 } }),
    clearHistory: vi.fn().mockResolvedValue(null),
    deleteTurn: vi.fn().mockResolvedValue(null),
    keptResult: vi.fn(),
    listContext: vi.fn().mockResolvedValue([]),
    createContext: vi.fn(),
    updateContext: vi.fn(),
    deleteContext: vi.fn().mockResolvedValue(null),
    previewContextImport: vi.fn(),
    importContext: vi.fn(),
    addContextExamples: vi.fn(),
    execute: vi.fn(),
    chart: vi.fn(),
    summary: vi.fn().mockResolvedValue({ summary: 'Electronics led with 600.', source: 'llm' }),
  }
}
