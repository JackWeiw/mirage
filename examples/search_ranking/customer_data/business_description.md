# Search Ranking Service

## Architecture
High-concurrency RPC search service processing ranking requests.

## Business Workflow
1. Feature Extraction: Extract features from request data using async computation (folly futures)
2. Model Inference: Run model inference with parallel compute (taskflow)
3. Rank & Merge: Merge multiple ranking results, sort by score
4. Dedup & Filter: Remove duplicates with branch-heavy conditional logic
5. Result Assembly: Pack results into response format

## Key Observations
- Backend bound dominates (55%), especially memory bound (40%)
- Model inference and feature extraction are the two biggest stages
- Working set is ~256MB with significant L3 cache misses
