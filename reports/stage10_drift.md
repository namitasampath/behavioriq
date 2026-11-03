# Stage 10 - Drift detection (PSI + KS against the training reference)

Detected 5/5 injected drifts; false alarm on control: False.

| scenario                              | injected   |   drifted_features |   max_psi | top_feature        |   domain_auc | detected   | evidently_drift   |   evidently_share_drifted | agrees_with_our_rule   |
|:--------------------------------------|:-----------|-------------------:|----------:|:-------------------|-------------:|:-----------|:------------------|--------------------------:|:-----------------------|
| control (no drift, new traffic)       | False      |                  0 |     0.007 | n_product_view     |        0.486 | False      | False             |                     0     | True                   |
| mobile surge (80% mobile)             | True       |                  1 |     0.653 | is_mobile          |        0.676 | True       | False             |                     0.111 | False                  |
| comparer surge (85% heavy comparers)  | True       |                  1 |     0.304 | n_compare          |        0.731 | True       | True              |                     0.556 | True                   |
| deal-hunter influx (85% deal hunters) | True       |                  0 |     0.146 | is_mobile          |        0.629 | True       | False             |                     0.444 | False                  |
| slow sessions (dwell x3)              | True       |                  1 |     1.047 | duration_s         |        0.946 | True       | False             |                     0.111 | False                  |
| abandoner wave (85% abandoners)       | True       |                  0 |     0.153 | n_remove_from_cart |        0.625 | True       | False             |                     0.222 | False                  |

Rule: drift when any monitored feature has PSI > 0.2 (univariate) OR a domain classifier separates live from training traffic with ROC-AUC > 0.6 (multivariate; catches mixture shifts that move each feature only slightly).

Evidently AI agreement: 2/6 scenarios where Evidently's own dataset-drift verdict (share of drifted columns >= 0.5, its own preset default) matched our PSI/domain-classifier rule. Per-scenario HTML reports: `reports/evidently/`.
