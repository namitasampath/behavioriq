# Stage 6 - Representation sanity check

Baseline encoder: TF-IDF(1-3 gram) + SVD-64 (fallback)

| Embedding space | Neighbour outcome agreement @10 (test) |
| --- | --- |
| Custom contrastive Transformer | 0.585 |
| Baseline | 0.606 |
| Random (chance) | 0.346 |

Pretraining stopped after 15 epochs; best val loss 2.609.
