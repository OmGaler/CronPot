# CronPot HTTP API

Start the service locally:

```powershell
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

Base URL for the examples:

```text
http://127.0.0.1:8080
```

All API responses are JSON except dashboard and recipe HTML views. JSON request bodies must use `Content-Type: application/json`.

## Local Network Pairing

Start LAN mode:

```powershell
cronpot start --lan --vault docs
```

CronPot prints a six digit pairing code and detected mobile URLs such as:

```text
http://192.168.1.42:8080/mobile
```

When pairing is enabled, all application API endpoints require either a paired browser session cookie or the code as an API credential. `/mobile`, `/auth`, `/auth/status`, `/healthz`, and `/readyz` remain reachable so devices can pair and health checks can work.

For simple API clients, pass the code directly:

```http
Authorization: Bearer 123456
```

or:

```http
X-CronPot-Code: 123456
```

When CronPot runs in the local Kubernetes overlay, the API pod can read the same code from the `CRONPOT_AUTH_CODE` environment variable. The helper `scripts\k8s-start.cmd docs /lan` creates that value as a Kubernetes Secret and restarts the local API Deployment.

Unauthorised API response:

```json
{
  "error": "pairing code required",
  "mobile": "/mobile"
}
```

### `GET /mobile`

Returns the phone-oriented HTML UI. It includes pairing, URL ingest job queueing, job status, recipe search, shopping list generation, and copy-to-clipboard.

### `POST /auth`

Pairs the browser session when the code matches.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/auth -Method Post -ContentType "application/json" -Body '{"code":"123456"}'
```

Success response:

```json
{
  "authenticated": true
}
```

The response sets a `cronpot_session` cookie for the current browser.

### `GET /auth/status`

Returns whether the current request is paired.

```json
{
  "authenticated": true,
  "required": true
}
```

## Dashboard

### `GET /` and `GET /dashboard`

Returns the HTML dashboard. It shows service status, recipe metrics, recent recipes, recent ingest jobs, top tags, and top categories.

Open in a browser:

```text
http://127.0.0.1:8080/dashboard
```

## Health

### `GET /healthz`

Returns `200 OK` when the process is alive.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

Response:

```json
{
  "status": "ok"
}
```

### `GET /readyz`

Returns `200 OK` when the configured vault path exists and is a directory. Returns `503 Service Unavailable` when the vault is not available.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/readyz
```

Ready response:

```json
{
  "status": "ready"
}
```

Unavailable response:

```json
{
  "status": "vault unavailable"
}
```

## Analytics

### `GET /analytics`

Returns recipe counts, missing source count, tag counts, category counts, and ingredient counts.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/analytics
```

Response shape:

```json
{
  "recipe_count": 42,
  "recipes_missing_source": 3,
  "tags": {
    "parev": 20
  },
  "categories": {
    "Mains": 12
  },
  "ingredients": {
    "sugar": 8
  }
}
```

If `[llm].auto_normalise_ingredients = true`, this endpoint uses cached local LLM ingredient aliases for grouped ingredient counts. The cache lasts fifteen minutes per vault/model/limit combination.

## Recipes

### `GET /recipes`

Lists recipe summaries.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/recipes
```

Optional query parameters:

- `tag`: filter by exact tag. Can be repeated.
- `category`: filter by exact category. Can be repeated.
- `q`: case-insensitive search across title, ingredients, and method.

Examples:

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/recipes?tag=meaty"
Invoke-RestMethod "http://127.0.0.1:8080/recipes?category=Mains&q=chicken"
```

Response shape:

```json
{
  "count": 1,
  "recipes": [
    {
      "name": "Roast Chicken",
      "path": "docs/Roast Chicken.md",
      "categories": ["Mains"],
      "tags": ["main", "meaty"],
      "source": "https://example.com/roast-chicken"
    }
  ]
}
```

### `GET /recipes/{name}`

Returns one recipe. `{name}` can be a recipe stem or Markdown filename.

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/recipes/Roast%20Chicken"
```

Response shape:

```json
{
  "name": "Roast Chicken",
  "path": "docs/Roast Chicken.md",
  "categories": ["Mains"],
  "tags": ["main", "meaty"],
  "source": "https://example.com/roast-chicken",
  "ingredients": ["1 chicken"],
  "steps": ["Roast until cooked."],
  "prep_time": "",
  "cook_time": "",
  "total_time": "",
  "servings": "4",
  "yield": ""
}
```

If the client sends an HTML `Accept` header, CronPot returns the rendered recipe page instead of JSON.

Missing recipe response:

```json
{
  "error": "recipe not found"
}
```

## Shopping List

### `GET /shopping-list`

Builds a deduplicated shopping list from selected recipes.

Use one or more `recipe` query parameters:

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/shopping-list?recipe=Aglio%20e%20Olio&recipe=Roast%20Chicken"
```

Or use every recipe:

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/shopping-list?all=true"
```

Response shape:

```json
{
  "count": 2,
  "recipes": [
    {
      "name": "Roast Chicken",
      "path": "docs/Roast Chicken.md",
      "categories": ["Mains"],
      "tags": ["main", "meaty"],
      "source": "https://example.com/roast-chicken"
    }
  ],
  "items": ["1 chicken", "salt"]
}
```

If neither `all=true` nor recipe names are supplied, the endpoint returns `400 Bad Request`.

## Ingest

### `POST /ingest`

Fetches a URL, extracts a recipe, normalises it, optionally rewrites it with the configured LLM, and writes Markdown to the vault synchronously.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/ingest -Method Post -ContentType "application/json" -Body '{"url":"https://example.com/recipe"}'
```

Success response is `201 Created`:

```json
{
  "path": "docs/Example Recipe.md",
  "title": "Example Recipe"
}
```

Request body:

```json
{
  "url": "https://example.com/recipe",
  "background": false
}
```

If `background` is truthy, `/ingest` queues a background job and behaves like `/jobs/ingest`.

Common error responses:

- `400 Bad Request`: `url` is missing.
- `422 Unprocessable Entity`: extraction did not find core recipe content.
- `502 Bad Gateway`: fetch or configured LLM rewrite failed.

## Jobs

Jobs are durable JSON files under `.cronpot/jobs` in the vault. They are processed by `cronpot worker`, `cronpot jobs run`, `POST /jobs/run`, or the Kubernetes `cronpot-worker` Deployment.

Job status values:

- `pending`
- `running`
- `complete`
- `failed`

### `POST /jobs/ingest`

Queues a URL ingest job and returns `202 Accepted`.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/ingest -Method Post -ContentType "application/json" -Body '{"url":"https://example.com/recipe"}'
```

Response shape:

```json
{
  "id": "JOB_ID",
  "kind": "ingest_url",
  "url": "https://example.com/recipe",
  "status": "pending",
  "attempts": 0,
  "title": "",
  "recipe_path": "",
  "error": "",
  "created_at": 1760000000.0,
  "updated_at": 1760000000.0
}
```

### `GET /jobs`

Lists jobs.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs
```

Response:

```json
{
  "jobs": [
    {
      "id": "JOB_ID",
      "kind": "ingest_url",
      "url": "https://example.com/recipe",
      "status": "pending",
      "attempts": 0,
      "title": "",
      "recipe_path": "",
      "error": "",
      "created_at": 1760000000.0,
      "updated_at": 1760000000.0
    }
  ]
}
```

### `GET /jobs/{id}`

Returns one job.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/JOB_ID
```

Missing jobs return:

```json
{
  "error": "job not found"
}
```

### `POST /jobs/run`

Processes pending jobs once inside the API process. This is useful for local testing; production-style usage should run `cronpot worker` or the Kubernetes worker Deployment.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/run -Method Post
```

Response:

```json
{
  "jobs": [
    {
      "id": "JOB_ID",
      "status": "complete"
    }
  ]
}
```

The actual job objects include all fields shown in `/jobs`.

### `POST /jobs/{id}/retry`

Resets a failed or stale job to `pending` and clears its error.

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/JOB_ID/retry -Method Post
```

Response is the updated job object.

## HTTP Status Summary

Common statuses:

- `200 OK`: successful read or job run.
- `201 Created`: synchronous ingest wrote a recipe.
- `202 Accepted`: background job queued.
- `400 Bad Request`: required request data is missing.
- `404 Not Found`: recipe, job, or endpoint does not exist.
- `422 Unprocessable Entity`: recipe extraction was incomplete.
- `502 Bad Gateway`: upstream fetch or LLM call failed.
- `503 Service Unavailable`: readiness check failed because the vault is unavailable.
