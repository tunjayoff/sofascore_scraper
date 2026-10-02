// Generates src/api/v1/schema.ts from the committed API document (docs/api/openapi-v1.json).
//
//   npm run gen:api            writes the file
//   npm run gen:api -- --check exits with 1 when the committed file differs (tests/apiTypes.test.ts does the same)
//
// A small generator of our own instead of openapi-typescript: that package wants TypeScript 5 as a peer
// and the frontend is on 6, and the document uses only a handful of JSON Schema forms (pydantic's output):
// $ref, anyOf/oneOf, enum, const, the scalar types, arrays and objects. A form it does not know fails
// loudly, so a new kind of schema in the document cannot turn into `unknown` unnoticed.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const DOCUMENT = resolve(here, '../../docs/api/openapi-v1.json')
export const OUTPUT = resolve(here, '../src/api/v1/schema.ts')

const SCALARS = { string: 'string', integer: 'number', number: 'number', boolean: 'boolean', null: 'null' }
const METHODS = ['get', 'post', 'put', 'patch', 'delete']

function refName(ref) {
  const prefix = '#/components/schemas/'
  if (!ref.startsWith(prefix)) throw new Error(`unsupported $ref: ${ref}`)
  return ref.slice(prefix.length)
}

function union(parts) {
  const unique = [...new Set(parts)]
  return unique.length === 1 ? unique[0] : unique.join(' | ')
}

function key(name) {
  return /^[A-Za-z_$][\w$]*$/.test(name) ? name : JSON.stringify(name)
}

function comment(text, indent) {
  if (!text) return ''
  const lines = String(text).trim().split('\n')
  if (lines.length === 1) return `${indent}/** ${lines[0].replace(/\*\//g, '*\\/')} */\n`
  return `${indent}/**\n${lines.map((l) => `${indent} * ${l.replace(/\*\//g, '*\\/')}`.trimEnd()).join('\n')}\n${indent} */\n`
}

function objectType(schema, indent) {
  const props = schema.properties || {}
  const required = new Set(schema.required || [])
  const extra = schema.additionalProperties
  const names = Object.keys(props)
  if (!names.length) {
    if (extra === true || extra === undefined) return 'Record<string, unknown>'
    return `Record<string, ${typeOf(extra, indent)}>`
  }
  const inner = indent + '  '
  let out = '{\n'
  for (const name of names) {
    const prop = props[name]
    out += comment(prop.description, inner)
    out += `${inner}${key(name)}${required.has(name) ? '' : '?'}: ${typeOf(prop, inner)}\n`
  }
  if (extra === true) out += `${inner}[key: string]: unknown\n`
  else if (extra && typeof extra === 'object') out += `${inner}[key: string]: ${typeOf(extra, inner)}\n`
  return out + `${indent}}`
}

export function typeOf(schema, indent = '') {
  if (!schema || typeof schema !== 'object') throw new Error(`unsupported schema: ${JSON.stringify(schema)}`)
  if (schema.$ref) return refName(schema.$ref)
  if (schema.anyOf || schema.oneOf) return union((schema.anyOf || schema.oneOf).map((s) => typeOf(s, indent)))
  if ('const' in schema) return JSON.stringify(schema.const)
  if (schema.enum) return union(schema.enum.map((v) => JSON.stringify(v)))
  if (schema.type === 'array') {
    const item = typeOf(schema.items, indent)
    return /[|&]/.test(item) ? `(${item})[]` : `${item}[]`
  }
  if (schema.type === 'object' || schema.properties) return objectType(schema, indent)
  if (Array.isArray(schema.type)) return union(schema.type.map((t) => typeOf({ type: t }, indent)))
  if (schema.type in SCALARS) return SCALARS[schema.type]
  // No type at all (pydantic's `Any`): any JSON value
  const known = ['title', 'description', 'default', 'examples']
  if (Object.keys(schema).every((k) => known.includes(k))) return 'unknown'
  throw new Error(`unsupported schema: ${JSON.stringify(schema)}`)
}

function jsonBody(content) {
  const media = content?.['application/json']
  return media ? typeOf(media.schema) : null
}

function braces(fields) {
  return fields.length ? `{ ${fields.join('; ')} }` : '{}'
}

/** One entry per operation: its path and query parameters, the JSON body and the JSON answer of 2xx. */
function operations(doc) {
  let out = ''
  for (const [path, item] of Object.entries(doc.paths || {})) {
    for (const method of METHODS) {
      const op = item[method]
      if (!op) continue
      const params = { path: [], query: [] }
      for (const p of op.parameters || []) {
        if (p.in in params) params[p.in].push(`${key(p.name)}${p.required ? '' : '?'}: ${typeOf(p.schema)}`)
      }
      const ok = Object.entries(op.responses || {}).find(([status]) => /^2/.test(status))
      const answer = ok ? jsonBody(ok[1].content) : null
      const body = op.requestBody ? jsonBody(op.requestBody.content) : null
      out += comment(op.summary, '  ')
      out += `  ${JSON.stringify(op.operationId)}: {\n`
      out += `    method: ${JSON.stringify(method.toUpperCase())}\n`
      out += `    path: ${JSON.stringify(path)}\n`
      out += `    params: ${braces(params.path)}\n`
      out += `    query: ${braces(params.query)}\n`
      out += `    body: ${body ?? 'never'}\n`
      out += `    response: ${answer ?? 'string'}\n`
      out += '  }\n'
    }
  }
  return out
}

export function generate(doc) {
  let out =
    '// Generated from docs/api/openapi-v1.json by scripts/gen-api-types.mjs. Do not edit by hand:\n' +
    '// run `npm run gen:api` after the document changes (tests/apiTypes.test.ts fails until then).\n' +
    '/* eslint-disable */\n\n'
  out += `/** API version of the document: ${doc.info?.version ?? '?'} */\n`
  out += `export const API_DOCUMENT_VERSION = ${JSON.stringify(doc.info?.version ?? '')}\n\n`
  for (const [name, schema] of Object.entries(doc.components?.schemas || {})) {
    out += comment(schema.description, '')
    const body = typeOf(schema)
    out += body.startsWith('{\n') ? `export interface ${name} ${body}\n\n` : `export type ${name} = ${body}\n\n`
  }
  out += '/** Every operation of the document by its operationId. */\n'
  out += `export interface Operations {\n${operations(doc)}}\n`
  return out
}

function main() {
  const doc = JSON.parse(readFileSync(DOCUMENT, 'utf8'))
  const text = generate(doc)
  if (process.argv.includes('--check')) {
    let current = ''
    try {
      current = readFileSync(OUTPUT, 'utf8')
    } catch {
      /* missing: differs */
    }
    if (current !== text) {
      console.error('src/api/v1/schema.ts is out of date: run `npm run gen:api`')
      process.exit(1)
    }
    return
  }
  writeFileSync(OUTPUT, text)
  console.log(`wrote ${OUTPUT}`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
