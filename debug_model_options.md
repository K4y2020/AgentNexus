# Model Options Data Flow Analysis

## Issue Summary
When creating a policy with model parameters in AgentInfo, the model dropdown may be empty because `codexModelOptions` is not populated yet.

## Root Cause
The backend fetches model options asynchronously in the background after a session snapshot is requested. On the first request, if the background task hasn't completed, `model_options` returns an empty array.

From `agentnexus/server/routes/_sessions/orchestration.py:9615-9702`:
```python
async def _fetch_model_options(...):
    # ...
    cached = _model_options_cache.get(session_id)
    if runner_client is None:
        if cached:
            return cached
        # ... background task kicks off here
        return []  # Returns empty while loading
    if cached is not None and session_id not in _model_options_stale:
        return cached
    if session_id not in _model_options_inflight:
        # Starts background task
        task = asyncio.create_task(_load_model_options(...))
        # ...
    return cached or []  # Returns empty while task is in flight
```

## Solution Implemented

### Frontend Changes (web/src/components/AgentInfo.tsx)

1. **Detection**: Added `needsModelOptions` and `modelOptionsLoading` computed values to detect when a policy requires model options but they're not loaded yet.

2. **User Feedback**: Added a loading indicator above parameter fields when model options are being loaded:
   ```tsx
   {modelOptionsLoading && (
     <div role="status" className="...">
       Loading model options...
     </div>
   )}
   ```

3. **Button State**: Disabled the "Add" button while model options are loading to prevent incomplete submissions:
   ```tsx
   <Button disabled={!selected || modelOptionsLoading}>Add</Button>
   ```

4. **Fallback Models**: Always include `CLAUDE_NATIVE_MODELS` as baseline options, even when `codexModelOptions` is empty:
   ```tsx
   const modelIds = useMemo(() => {
     const ids: string[] = CLAUDE_NATIVE_MODELS.map((m) => m.id);
     for (const opt of codexModelOptions) {
       if (opt.id && !ids.includes(opt.id)) ids.push(opt.id);
     }
     return ids;
   }, [codexModelOptions]);
   ```

5. **Enrichment Logic**: Removed early-exit when `modelIds.length === 0` to ensure properties always get enriched:
   ```tsx
   const properties = useMemo(() => {
     // Always enrich, even with empty modelIds
     const enriched: typeof props = {};
     for (const [key, prop] of Object.entries(props)) {
       if (prop.items?.["x-enum-source"] === "models") {
         if (!prop.items.enum) {
           enriched[key] = { ...prop, items: { ...prop.items, enum: modelIds } };
         } else {
           enriched[key] = prop;
         }
       } else {
         enriched[key] = prop;
       }
     }
     return enriched;
   }, [rawSchema?.properties, modelIds]);
   ```

## Data Flow Summary

### Backend (Python)
1. **Runner endpoint**: `/v1/sessions/{session_id}/model-options`
   - Implemented in `agentnexus/runner/app.py:9619`
   - Calls `_codex_native_model_options(conv_id)` (line 4800)
   - Returns: `{"models": [...]}`

2. **Server caching**: 
   - Cache: `_model_options_cache` in `agentnexus/server/routes/_sessions/orchestration.py`
   - Fetcher: `_fetch_model_options()` (line 9615)
   - Background task spawned on first request
   - Returns `[]` while task is in flight
   
3. **Session response**:
   - Field: `model_options` in `SessionResponse`
   - Builder: `_build_session_response()` (line 975)

### Frontend (TypeScript)
1. **Wire mapping**: `web/src/lib/sessionsApi.ts:354`
   ```typescript
   codexModelOptions: wire.model_options ?? [],
   ```

2. **State management**: `web/src/store/chatStore.ts`
   - Stored as `codexModelOptions: NativeModelOption[]`

3. **Usage in AgentInfo**: `web/src/components/AgentInfo.tsx`
   - Reads `codexModelOptions` from store
   - Enriches policy parameters with `x-enum-source: "models"`
   - Shows loading state when needed

## Testing
To verify the fix:
1. Open a new codex-native session
2. Navigate to Agent Info (info icon in header)
3. Try to add a policy with model parameters (e.g., "Per-Session Cost Budget")
4. Observe: "Loading model options..." message appears if options aren't ready yet
5. Observe: "Add" button is disabled while loading
6. Once loaded, dropdown shows available models

## Files Modified
- `web/src/components/AgentInfo.tsx` - Added loading state and improved model option handling
