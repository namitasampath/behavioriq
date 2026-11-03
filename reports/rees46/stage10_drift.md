# Stage 10 - Drift detection, real data (PSI + KS against the training-split reference)

Reference = train-split sessions; every scenario below is built from the SAME held-out test-split sessions (no `biq.simulator` traffic is used - comparing real logs against simulated traffic is always flagged as 'drifted', which tests nothing about the detector).

Detected 2/4 injected drifts; false alarm on control: False.

| scenario                                                                                                 | injected   |   drifted_features |   max_psi | top_feature    |   domain_auc | detected   |
|:---------------------------------------------------------------------------------------------------------|:-----------|-------------------:|----------:|:---------------|-------------:|:-----------|
| control (held-out test traffic, no shift)                                                                | False      |                  0 |     0.004 | n_add_to_cart  |        0.506 | False      |
| long-session-heavy resample (4x weight on above-median-length sessions; no device signal to resample on) | True       |                  3 |     0.355 | event_count    |        0.684 | True       |
| cart-heavy resample (4x weight on sessions with >=1 add_to_cart)                                         | True       |                  0 |     0.125 | n_add_to_cart  |        0.597 | False      |
| dwell/time-gaps x3 (duration_s, mean_gap_s scaled)                                                       | True       |                  1 |     0.322 | duration_s     |        0.752 | True       |
| category mix shift (4x weight on top category 'electronics')                                             | True       |                  0 |     0.014 | n_product_view |        0.565 | False      |

Rule: drift when any monitored feature has PSI > 0.2 (univariate) OR a domain classifier separates live from training traffic with ROC-AUC > 0.6 (multivariate; catches mixture shifts that move each feature only slightly).
