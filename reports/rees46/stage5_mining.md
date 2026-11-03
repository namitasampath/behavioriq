# Stage 5 - Behavioral mining report

Sessions analysed: 32,066

## Top 10 frequent sequential patterns (PrefixSpan, closed, length 3-5)

| pattern                                                                    |   support |   abandon_rate |   lift_abandon |
|:---------------------------------------------------------------------------|----------:|---------------:|---------------:|
| product_view -> add_to_cart -> purchase                                    |     0.032 |          0     |          0     |
| product_view -> add_to_cart -> product_view                                |     0.028 |          0.737 |         25.181 |
| product_view -> add_to_cart -> product_view -> add_to_cart                 |     0.011 |          0.457 |         15.622 |
| add_to_cart -> product_view -> purchase                                    |     0.007 |          0     |          0     |
| product_view -> add_to_cart -> product_view -> purchase                    |     0.007 |          0     |          0     |
| product_view -> add_to_cart -> product_view -> add_to_cart -> purchase     |     0.006 |          0     |          0     |
| add_to_cart -> product_view -> add_to_cart -> product_view                 |     0.005 |          0.688 |         23.528 |
| product_view -> add_to_cart -> product_view -> add_to_cart -> product_view |     0.005 |          0.692 |         23.667 |
| add_to_cart -> product_view -> add_to_cart -> product_view -> add_to_cart  |     0.002 |          0.378 |         12.935 |
| add_to_cart -> product_view -> add_to_cart -> product_view -> purchase     |     0.002 |          0     |          0     |

## Patterns most associated with abandonment

| pattern                                     |   support |   abandon_rate |   lift_abandon |
|:--------------------------------------------|----------:|---------------:|---------------:|
| product_view -> add_to_cart -> product_view |     0.028 |          0.737 |         25.181 |
| product_view -> add_to_cart -> purchase     |     0.032 |          0     |          0     |

## Top association rules (FP-Growth, lift > 2.0)

| antecedents                       | consequents   |   support |   confidence |   lift |
|:----------------------------------|:--------------|----------:|-------------:|-------:|
| has_add_to_cart, has_product_view | OUT_purchase  |     0.032 |        0.527 |  6.936 |
| has_add_to_cart                   | OUT_purchase  |     0.033 |        0.527 |  6.934 |

## Clustering quality

| method        |   clusters |   noise_share |   silhouette |   davies_bouldin |
|:--------------|-----------:|--------------:|-------------:|-----------------:|
| HDBSCAN       |         14 |         0.003 |        0.895 |            0.418 |
| K-Means (k=5) |          5 |         0     |        0.67  |            0.643 |

No ground-truth personas for this dataset (real logs have no hidden labels) - ARI is omitted.

## Behavioral personas discovered (HDBSCAN cluster profiles)

|   cluster |   sessions |   events |   compares |   reviews |   carts |   removes |   checkout |   conversion |   abandonment | label                |
|----------:|-----------:|---------:|-----------:|----------:|--------:|----------:|-----------:|-------------:|--------------:|:---------------------|
|         0 |      12223 |     1.05 |          0 |         0 |    0    |         0 |          0 |         0.05 |          0    | bouncers (c0)        |
|         1 |        548 |     9.17 |          0 |         0 |    2.53 |         0 |          0 |         0.52 |          0.48 | considerers (c1)     |
|         2 |       5268 |     2.05 |          0 |         0 |    0    |         0 |          0 |         0.05 |          0    | bouncers (c2)        |
|         3 |       3130 |     3.05 |          0 |         0 |    0    |         0 |          0 |         0.05 |          0    | bouncers (c3)        |
|         4 |        860 |     6.91 |          0 |         0 |    1    |         0 |          0 |         0.38 |          0.62 | cart abandoners (c4) |
|         5 |        539 |     2.78 |          0 |         0 |    1    |         0 |          0 |         0.78 |          0.22 | considerers (c5)     |
|         6 |       2009 |     4.05 |          0 |         0 |    0    |         0 |          0 |         0.05 |          0    | bouncers (c6)        |
|         7 |       1410 |     5.04 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c7)        |
|         8 |       1121 |     6.04 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c8)        |
|         9 |        847 |     7.04 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c9)        |
|        10 |        624 |     8.03 |          0 |         0 |    0    |         0 |          0 |         0.03 |          0    | browsers (c10)       |
|        11 |        518 |     9.04 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c11)       |
|        12 |        430 |    10.04 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c12)       |
|        13 |       2452 |    18.44 |          0 |         0 |    0    |         0 |          0 |         0.04 |          0    | browsers (c13)       |
