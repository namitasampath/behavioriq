# Stage 7 - Retrieval evaluation (held-out test sessions, relevance = same outcome)

| collection                 |   precision@10 |   recall@10 |     MRR |   NDCG@10 |   p50_ms |   p95_ms |
|:---------------------------|---------------:|------------:|--------:|----------:|---------:|---------:|
| biq_custom                 |          0.631 |       0.004 |   0.796 |     0.639 |   12.974 |   15.939 |
| biq_baseline               |          0.631 |       0.004 |   0.789 |     0.64  |   10.83  |   13.074 |
| numpy brute force (custom) |        nan     |     nan     | nan     |   nan     |    0.115 |    0.131 |

Share of abandoning neighbours for 50 abandoning test sessions: 0.51 (base rate in pool 0.36).
