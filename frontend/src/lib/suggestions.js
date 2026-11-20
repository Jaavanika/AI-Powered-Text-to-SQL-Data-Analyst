import { humanize } from './format.js'

const SAMPLE_QUESTIONS = [
  'How many customers are there?',
  'What is the total revenue by product category?',
  'Show the number of orders per month in 2024.',
  'List the products that have never been ordered.',
]

const LABEL_HINT = /(category|type|status|region|city|country|segment|group|department|channel|method)/i
const NOT_A_LABEL = /(id|email|phone|url|description|comment|note)$/i

const words = (name) => humanize(name).toLowerCase()

/**
 * A few starter questions for a data source. Uploaded data gets questions built from its own
 * tables and column types; the sample database has hand-written ones.
 */
export function suggestionsFor(datasource) {
  if (!datasource) return []
  if (datasource.kind === 'sample') return SAMPLE_QUESTIONS

  const tables = datasource.tables || []
  const questions = []
  for (const table of tables.slice(0, 2)) {
    const label = words(table.name)
    const columns = table.columns || []
    const measure = columns.find((c) => (c.type === 'float' || c.type === 'integer') && !/id$/i.test(c.name))
    const category = columns.find((c) => c.type === 'text' && LABEL_HINT.test(c.name))
      || columns.find((c) => c.type === 'text' && !NOT_A_LABEL.test(c.name))
    const date = columns.find((c) => c.type === 'date' || c.type === 'datetime')

    if (questions.length === 0) questions.push(`How many rows are in ${label}?`)
    if (measure && category) {
      questions.push(`What is the total ${words(measure.name)} by ${words(category.name)} in ${label}?`)
    }
    if (date) questions.push(`Show the number of rows in ${label} per month by ${words(date.name)}.`)
    if (measure) questions.push(`What is the average ${words(measure.name)} in ${label}?`)
  }
  if (!questions.length) questions.push('Show me a few rows.')
  return questions.slice(0, 4)
}
