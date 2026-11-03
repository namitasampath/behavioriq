# Stage 6 - Representation sanity check

Baseline encoder: TF-IDF(1-3 gram) + SVD-14 (fallback)

| Embedding space | Neighbour outcome agreement @10 (test) |
| --- | --- |
| Custom contrastive Transformer | 0.829 |
| Baseline | 0.912 |
| Random (chance) | 0.820 |

Pretraining stopped after 15 epochs; best val loss 3.534.
