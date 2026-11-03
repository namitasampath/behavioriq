# Stage 7 - Retrieval evaluation (held-out test sessions, relevance = same outcome)

| collection                 |   precision@10 |   recall@10 |     MRR |   NDCG@10 |   p50_ms |   p95_ms |
|:---------------------------|---------------:|------------:|--------:|----------:|---------:|---------:|
| biq_custom                 |          0.632 |       0.004 |   0.8   |     0.641 |    7.448 |   29.917 |
| biq_baseline               |          0.633 |       0.004 |   0.795 |     0.644 |   15.983 |   37.102 |
| numpy brute force (custom) |        nan     |     nan     | nan     |   nan     |    0.17  |    0.2   |

Share of abandoning neighbours for 50 abandoning test sessions: 0.51 (base rate in pool 0.36).
