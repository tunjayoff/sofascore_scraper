import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { DOCUMENT, OUTPUT, generate, typeOf } from '../scripts/gen-api-types.mjs'

/**
 * The frontend's types come from the committed API document (docs/api/openapi-v1.json). When the document
 * changes and `npm run gen:api` was not run, this test fails, so the types cannot drift from the API.
 */
describe('types generated from docs/api/openapi-v1.json', () => {
  it('the committed src/api/v1/schema.ts is what the generator makes of the document', () => {
    const doc = JSON.parse(readFileSync(DOCUMENT, 'utf8'))
    expect(readFileSync(OUTPUT, 'utf8')).toBe(generate(doc))
  })

  it('has every schema and every operation of the document', () => {
    const doc = JSON.parse(readFileSync(DOCUMENT, 'utf8'))
    const text = readFileSync(OUTPUT, 'utf8')
    for (const name of Object.keys(doc.components.schemas)) expect(text).toMatch(new RegExp(`export (interface|type) ${name}\\b`))
    for (const item of Object.values(doc.paths) as Record<string, { operationId: string }>[])
      for (const op of Object.values(item)) expect(text).toContain(`"${op.operationId}": {`)
  })

  it('translates the JSON Schema forms the document uses, and refuses others', () => {
    expect(typeOf({ anyOf: [{ type: 'string' }, { type: 'null' }] })).toBe('string | null')
    expect(typeOf({ type: 'array', items: { enum: ['a', 'b'] } })).toBe('("a" | "b")[]')
    expect(typeOf({ const: 'v1' })).toBe('"v1"')
    expect(typeOf({ type: 'object', additionalProperties: { type: 'number' } })).toBe('Record<string, number>')
    expect(typeOf({ $ref: '#/components/schemas/Job' })).toBe('Job')
    expect(typeOf({ title: 'Value' })).toBe('unknown')
    expect(() => typeOf({ not: { type: 'string' } })).toThrow(/unsupported/)
  })
})
