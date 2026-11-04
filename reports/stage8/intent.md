# Stage 8 - Intent prediction results (time-based test split, 3 seeds, mean ± std)

Baseline text encoder: TF-IDF(1-3)+SVD-64. Retrieval: top-10 earlier train sessions (no self, no future); bulk retrieval uses exact numpy cosine search, top-10 overlap with Qdrant = 0.946.

## Representation comparison and retrieval ablation (full prediction prefix)

| variant                 | accuracy      | macro_f1      | roc_auc       | pr_auc        | brier         |
|:------------------------|:--------------|:--------------|:--------------|:--------------|:--------------|
| A                       | 0.858 ± 0.003 | 0.846 ± 0.003 | 0.953 ± 0.001 | 0.863 ± 0.002 | 0.189 ± 0.001 |
| B                       | 0.877 ± 0.002 | 0.866 ± 0.002 | 0.962 ± 0.000 | 0.890 ± 0.001 | 0.167 ± 0.001 |
| B+R                     | 0.876 ± 0.003 | 0.864 ± 0.003 | 0.960 ± 0.001 | 0.881 ± 0.001 | 0.173 ± 0.002 |
| C_base                  | 0.843 ± 0.002 | 0.824 ± 0.002 | 0.948 ± 0.000 | 0.865 ± 0.001 | 0.213 ± 0.000 |
| C                       | 0.784 ± 0.002 | 0.759 ± 0.003 | 0.914 ± 0.001 | 0.797 ± 0.002 | 0.291 ± 0.002 |
| C+R                     | 0.791 ± 0.002 | 0.769 ± 0.002 | 0.917 ± 0.000 | 0.797 ± 0.000 | 0.287 ± 0.001 |
| C+R (k=5)               | 0.787 ± 0     | 0.763 ± 0     | 0.917 ± 0     | 0.799 ± 0     | 0.284 ± 0     |
| C+R (k=20)              | 0.792 ± 0     | 0.770 ± 0     | 0.919 ± 0     | 0.803 ± 0     | 0.283 ± 0     |
| C+R (k=50)              | 0.794 ± 0     | 0.772 ± 0     | 0.919 ± 0     | 0.807 ± 0     | 0.284 ± 0     |
| C-ft (Transformer head) | 0.857 ± 0     | 0.845 ± 0     | 0.953 ± 0     | 0.867 ± 0     | 0.184 ± 0     |

## C+R vs C, paired bootstrap (1,000 resamples)

- macro-F1 delta +0.0028 (95% CI -0.0157 to +0.0219), p = 0.379
- ROC-AUC delta +0.0035 (95% CI -0.0005 to +0.0075), p = 0.045
- C+R vs B: macro-F1 delta -0.1035 (p = 1.000), ROC-AUC delta -0.0447 (p = 1.000)
- B+R vs B (retrieval on top of hand-crafted features): macro-F1 delta -0.0004 (95% CI -0.0132 to +0.0119, p = 0.534), ROC-AUC delta -0.0019 (p = 0.838)

## Early-prediction curve (macro-F1 by events observed)

| variant   |     3 |     5 |    10 |    20 |   full |
|:----------|------:|------:|------:|------:|-------:|
| A         | 0.368 | 0.587 | 0.746 | 0.837 |  0.846 |
| B         | 0.364 | 0.589 | 0.745 | 0.835 |  0.866 |
| B+R       | 0.393 | 0.588 | 0.762 | 0.84  |  0.864 |
| C         | 0.388 | 0.578 | 0.693 | 0.75  |  0.759 |
| C+R       | 0.397 | 0.58  | 0.705 | 0.745 |  0.769 |
| C_base    | 0.364 | 0.582 | 0.738 | 0.81  |  0.824 |

Figures: early_curve.png, reliability.png. Raw runs: all_runs.csv.
