# Stage 8 - Intent prediction results (time-based test split, 3 seeds, mean ± std)

Baseline text encoder: TF-IDF(1-3)+SVD-64. Retrieval: top-10 earlier train sessions (no self, no future); bulk retrieval uses exact numpy cosine search, top-10 overlap with Qdrant = 0.952.

## Representation comparison and retrieval ablation (full prediction prefix)

| variant                 | accuracy      | macro_f1      | roc_auc       | pr_auc        | brier         |
|:------------------------|:--------------|:--------------|:--------------|:--------------|:--------------|
| A                       | 0.858 ± 0.003 | 0.846 ± 0.003 | 0.953 ± 0.001 | 0.863 ± 0.002 | 0.189 ± 0.001 |
| B                       | 0.877 ± 0.002 | 0.866 ± 0.002 | 0.962 ± 0.000 | 0.890 ± 0.001 | 0.167 ± 0.001 |
| B+R                     | 0.872 ± 0.003 | 0.861 ± 0.003 | 0.961 ± 0.000 | 0.887 ± 0.001 | 0.172 ± 0.001 |
| C_base                  | 0.843 ± 0.002 | 0.824 ± 0.002 | 0.948 ± 0.000 | 0.865 ± 0.001 | 0.213 ± 0.000 |
| C                       | 0.787 ± 0.002 | 0.760 ± 0.003 | 0.914 ± 0.001 | 0.801 ± 0.001 | 0.288 ± 0.001 |
| C+R                     | 0.792 ± 0.006 | 0.768 ± 0.006 | 0.914 ± 0.002 | 0.795 ± 0.004 | 0.290 ± 0.002 |
| C+R (k=5)               | 0.788 ± 0     | 0.762 ± 0     | 0.916 ± 0     | 0.804 ± 0     | 0.282 ± 0     |
| C+R (k=20)              | 0.782 ± 0     | 0.757 ± 0     | 0.919 ± 0     | 0.806 ± 0     | 0.282 ± 0     |
| C+R (k=50)              | 0.787 ± 0     | 0.763 ± 0     | 0.917 ± 0     | 0.805 ± 0     | 0.286 ± 0     |
| C-ft (Transformer head) | 0.854 ± 0     | 0.843 ± 0     | 0.953 ± 0     | 0.868 ± 0     | 0.184 ± 0     |

## C+R vs C, paired bootstrap (1,000 resamples)

- macro-F1 delta -0.0027 (95% CI -0.0208 to +0.0159), p = 0.620
- ROC-AUC delta -0.0012 (95% CI -0.0055 to +0.0031), p = 0.711
- C+R vs B: macro-F1 delta -0.1045 (p = 1.000), ROC-AUC delta -0.0480 (p = 1.000)
- B+R vs B (retrieval on top of hand-crafted features): macro-F1 delta +0.0004 (95% CI -0.0116 to +0.0119, p = 0.466), ROC-AUC delta -0.0010 (p = 0.715)

## Early-prediction curve (macro-F1 by events observed)

| variant   |     3 |     5 |    10 |    20 |   full |
|:----------|------:|------:|------:|------:|-------:|
| A         | 0.368 | 0.587 | 0.746 | 0.837 |  0.846 |
| B         | 0.364 | 0.589 | 0.745 | 0.835 |  0.866 |
| B+R       | 0.381 | 0.586 | 0.758 | 0.84  |  0.861 |
| C         | 0.39  | 0.579 | 0.681 | 0.745 |  0.76  |
| C+R       | 0.397 | 0.575 | 0.695 | 0.742 |  0.768 |
| C_base    | 0.364 | 0.582 | 0.738 | 0.81  |  0.824 |

Figures: early_curve.png, reliability.png. Raw runs: all_runs.csv.
