API types are generated from the committed `docs/reference/openapi.v1.json`:

```sh
npm --prefix frontend ci
npm --prefix frontend run generate:api-types
npm --prefix frontend run check:api-types
```

Commit `frontend/src/api/generated/openapi.ts` after generation. The check renders
the schema again and compares the exact contents without rewriting the file.
It runs as part of `npm --prefix frontend test`, including in the existing frontend
CI job. Generator tests also cover missing output, output drift and schema drift.

This private frontend npm workspace shares the frontend lockfile. It keeps
`openapi-typescript` and its TypeScript 5 API together; the frontend uses the
TypeScript 7 compiler, whose package does not expose that API. The generator's
dependencies are development dependencies and do not enter the browser bundle.

The snapshot embeds `ExportMember` under the JSON request schema for
`/api/workspace/write-export`, but references it as `#/$defs/ExportMember`.
The generator resolves that one local definition in memory before OpenAPI
bundling. It preserves the member fields and leaves the committed JSON untouched.

Generation preserves schema optionality: a default does not make a property
required. Existing frontend types should derive from these declarations only
where their shapes match, including optionality and nullability. Keep deliberate
compatibility types when they differ from the schema, pending a contract decision.
