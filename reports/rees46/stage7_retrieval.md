# Stage 7 - Retrieval evaluation (held-out test sessions, relevance = same outcome)

| collection                 |   precision@10 |   recall@10 |     MRR |   NDCG@10 |   p50_ms |   p95_ms |
|:---------------------------|---------------:|------------:|--------:|----------:|---------:|---------:|
| biq_rees46_custom          |          0.894 |       0.001 |   0.927 |     0.895 |   71.18  |   81.979 |
| biq_rees46_baseline        |          0.886 |       0.001 |   0.947 |     0.898 |   64.131 |   76.521 |
| numpy brute force (custom) |        nan     |     nan     | nan     |   nan     |    0.989 |    1.777 |

Share of abandoning neighbours for 50 abandoning test sessions: 0.30 (base rate in pool 0.03).
