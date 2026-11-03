# Stage 8 - Intent prediction results (time-based test split, 3 seeds, mean ± std)

Baseline text encoder: TF-IDF(1-3)+SVD-14. Retrieval: top-10 earlier train sessions (no self, no future); bulk retrieval uses exact numpy cosine search, top-10 overlap with Qdrant = 0.384.

## Representation comparison and retrieval ablation (full prediction prefix)

| variant                 | accuracy      | macro_f1      | roc_auc       | pr_auc        | brier         |
|:------------------------|:--------------|:--------------|:--------------|:--------------|:--------------|
| A                       | 0.936 ± 0.000 | 0.669 ± 0.004 | 0.806 ± 0.000 | 0.654 ± 0.001 | 0.114 ± 0.000 |
| B                       | 0.940 ± 0.000 | 0.708 ± 0.002 | 0.802 ± 0.003 | 0.716 ± 0.001 | 0.109 ± 0.000 |
| B+R                     | 0.940 ± 0.000 | 0.708 ± 0.001 | 0.808 ± 0.003 | 0.712 ± 0.002 | 0.110 ± 0.000 |
| C_base                  | 0.940 ± 0.000 | 0.711 ± 0.000 | 0.797 ± 0.001 | 0.707 ± 0.001 | 0.109 ± 0.000 |
| C                       | 0.928 ± 0.000 | 0.588 ± 0.001 | 0.798 ± 0.001 | 0.596 ± 0.005 | 0.124 ± 0.000 |
| C+R                     | 0.927 ± 0.001 | 0.573 ± 0.009 | 0.795 ± 0.003 | 0.591 ± 0.005 | 0.125 ± 0.000 |
| C+R (k=5)               | 0.929 ± 0     | 0.606 ± 0     | 0.793 ± 0     | 0.607 ± 0     | 0.124 ± 0     |
| C+R (k=20)              | 0.927 ± 0     | 0.570 ± 0     | 0.792 ± 0     | 0.594 ± 0     | 0.125 ± 0     |
| C+R (k=50)              | 0.928 ± 0     | 0.586 ± 0     | 0.795 ± 0     | 0.603 ± 0     | 0.124 ± 0     |
| C-ft (Transformer head) | 0.939 ± 0     | 0.696 ± 0     | 0.806 ± 0     | 0.693 ± 0     | 0.112 ± 0     |

## C+R vs C, paired bootstrap (1,000 resamples)

- macro-F1 delta -0.0208 (95% CI -0.0486 to +0.0057), p = 0.929
- ROC-AUC delta -0.0031 (95% CI -0.0102 to +0.0040), p = 0.793
- C+R vs B: macro-F1 delta -0.1398 (p = 1.000), ROC-AUC delta -0.0064 (p = 0.765)
- B+R vs B (retrieval on top of hand-crafted features): macro-F1 delta -0.0008 (95% CI -0.0135 to +0.0109, p = 0.585), ROC-AUC delta +0.0063 (p = 0.129)

## Early-prediction curve (macro-F1 by events observed)

| variant   |     3 |     5 |    10 |    20 |   full |
|:----------|------:|------:|------:|------:|-------:|
| A         | 0.595 | 0.621 | 0.655 | 0.669 |  0.669 |
| B         | 0.585 | 0.644 | 0.676 | 0.699 |  0.708 |
| B+R       | 0.595 | 0.639 | 0.68  | 0.699 |  0.708 |
| C         | 0.594 | 0.594 | 0.585 | 0.586 |  0.588 |
| C+R       | 0.591 | 0.598 | 0.576 | 0.574 |  0.573 |
| C_base    | 0.538 | 0.644 | 0.681 | 0.7   |  0.711 |

Figures: early_curve.png, reliability.png. Raw runs: all_runs.csv.
