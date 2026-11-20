import { describe, expect, it } from 'vitest'
import {
  formatCell, formatCompact, formatHeadline, formatNumber, formatPercent, formatPeriod, humanize, toCsv,
} from './format.js'

describe('number formatting', () => {
  it('groups digits and caps decimals', () => {
    expect(formatNumber(1234.567)).toBe('1,234.57')
    expect(formatNumber(5)).toBe('5')
  })
  it('uses compact figures for axes from 1,000 up', () => {
    expect([0, 12, 999, 1000, 1500, 80693, 2_500_000].map(formatCompact)).toEqual(['0', '12', '999', '1K', '1.5K', '80.7K', '2.5M'])
  })
  it('keeps headline figures in full until a million', () => {
    expect([49362, 999_999, 2_500_000].map(formatHeadline)).toEqual(['49,362', '999,999', '2.5M'])
    expect(formatHeadline(null)).toBe('—')
  })
  it('formats cells', () => {
    expect([null, undefined, true, false, 1234.5, 'text'].map(formatCell)).toEqual(['—', '—', 'true', 'false', '1,234.5', 'text'])
  })
  it('formats shares', () => {
    expect(formatPercent(1, 3)).toBe('33%')
    expect(formatPercent(0.034, 1)).toBe('3.4%')
    expect(formatPercent(5, 0)).toBe('')
  })
})

describe('humanize', () => {
  it('turns column names into labels', () => {
    expect(humanize('total_revenue')).toBe('Total revenue')
    expect(humanize('Customer Name')).toBe('Customer name')
    expect(humanize('customer_id')).toBe('Customer ID')
    expect(humanize('')).toBe('')
    expect(humanize(null)).toBe('')
  })
})

describe('formatPeriod', () => {
  it('writes months and days readably and leaves other text alone', () => {
    expect(formatPeriod('2024-03')).toBe('Mar 2024')
    expect(formatPeriod('2024-12-05')).toBe('5 Dec 2024')
    expect(formatPeriod('2024-13')).toBe('2024-13')
    expect(formatPeriod('Q1')).toBe('Q1')
    expect(formatPeriod(7)).toBe(7)
  })
})

describe('toCsv', () => {
  it('quotes fields that contain commas, quotes or newlines', () => {
    const csv = toCsv(['name', 'note'], [['A, B', 'say "hi"'], ['line\nbreak', null], [1, 2.5]])
    expect(csv).toBe('name,note\r\n"A, B","say ""hi"""\r\n"line\nbreak",\r\n1,2.5')
  })
})
