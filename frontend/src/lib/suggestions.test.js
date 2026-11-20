import { describe, expect, it } from 'vitest'
import { suggestionsFor } from './suggestions.js'
import { sampleSource, uploadSource } from '../test/fixtures.js'

describe('suggestionsFor', () => {
  it('has curated questions for the sample database', () => {
    const q = suggestionsFor(sampleSource)
    expect(q).toHaveLength(4)
    expect(q[0]).toMatch(/customers/)
  })

  it('builds questions from an uploaded table\'s own columns', () => {
    const q = suggestionsFor(uploadSource)
    expect(q).toContain('How many rows are in sales?')
    expect(q).toContain('What is the total amount by region in sales?')
    expect(q.some((x) => /per month by sold on/.test(x))).toBe(true)
    expect(q.length).toBeLessThanOrEqual(4)
  })

  it('does not treat ids or emails as categories', () => {
    const q = suggestionsFor({
      kind: 'upload',
      tables: [{ name: 't', columns: [
        { name: 'user_id', type: 'integer' }, { name: 'email', type: 'text' }, { name: 'score', type: 'float' }] }],
    })
    expect(q.join(' ')).not.toMatch(/by email|by user/)
  })

  it('copes with empty or missing data', () => {
    expect(suggestionsFor(null)).toEqual([])
    expect(suggestionsFor({ kind: 'upload', tables: [] })).toEqual(['Show me a few rows.'])
  })
})
