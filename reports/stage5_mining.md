# Stage 5 - Behavioral mining report

Sessions analysed: 6,000

## Top 10 frequent sequential patterns (PrefixSpan, closed, length 3-5)

| pattern                                       |   support |   abandon_rate |   lift_abandon |
|:----------------------------------------------|----------:|---------------:|---------------:|
| product_view -> product_view -> exit          |     0.446 |          0.626 |          1.709 |
| product_view -> add_to_cart -> checkout_start |     0.431 |          0.393 |          1.072 |
| product_view -> review_view -> exit           |     0.367 |          0.559 |          1.526 |
| product_view -> add_to_cart -> exit           |     0.36  |          1     |          2.731 |
| product_view -> review_view -> product_view   |     0.36  |          0.477 |          1.302 |
| product_view -> product_view -> add_to_cart   |     0.355 |          0.622 |          1.7   |
| product_view -> product_view -> product_view  |     0.349 |          0.549 |          1.499 |
| product_view -> compare -> product_view       |     0.339 |          0.487 |          1.331 |
| product_view -> compare -> exit               |     0.338 |          0.582 |          1.589 |
| product_view -> add_to_cart -> product_view   |     0.332 |          0.744 |          2.031 |

## Patterns most associated with abandonment

| pattern                                                     |   support |   abandon_rate |   lift_abandon |
|:------------------------------------------------------------|----------:|---------------:|---------------:|
| product_view -> add_to_cart -> exit                         |     0.36  |          1     |          2.731 |
| product_view -> add_to_cart -> product_view -> exit         |     0.242 |          1     |          2.731 |
| product_view -> product_view -> add_to_cart -> exit         |     0.215 |          1     |          2.731 |
| product_view -> add_to_cart -> product_view -> product_view |     0.192 |          0.771 |          2.106 |
| product_view -> product_view -> add_to_cart -> product_view |     0.193 |          0.764 |          2.087 |
| product_view -> add_to_cart -> product_view                 |     0.332 |          0.744 |          2.031 |
| product_view -> product_view -> product_view -> exit        |     0.269 |          0.691 |          1.888 |
| product_view -> product_view -> compare -> exit             |     0.202 |          0.683 |          1.865 |
| product_view -> product_view -> product_view -> add_to_cart |     0.212 |          0.664 |          1.812 |
| product_view -> add_to_cart -> product_view -> add_to_cart  |     0.223 |          0.647 |          1.768 |

## Top association rules (FP-Growth, lift > 2.0)

| antecedents                                                                                    | consequents   |   support |   confidence |   lift |
|:-----------------------------------------------------------------------------------------------|:--------------|----------:|-------------:|-------:|
| has_checkout_start, has_product_view                                                           | OUT_purchase  |     0.262 |        0.607 |  2.321 |
| has_add_to_cart, has_checkout_start, has_product_view                                          | OUT_purchase  |     0.262 |        0.607 |  2.321 |
| has_checkout_start                                                                             | OUT_purchase  |     0.262 |        0.607 |  2.321 |
| has_add_to_cart, has_checkout_start                                                            | OUT_purchase  |     0.262 |        0.607 |  2.321 |
| has_checkout_start, has_product_view, mobile                                                   | OUT_purchase  |     0.106 |        0.586 |  2.239 |
| has_add_to_cart, has_checkout_start, has_product_view, mobile                                  | OUT_purchase  |     0.106 |        0.586 |  2.239 |
| has_checkout_start, mobile                                                                     | OUT_purchase  |     0.106 |        0.586 |  2.239 |
| has_add_to_cart, has_checkout_start, mobile                                                    | OUT_purchase  |     0.106 |        0.586 |  2.239 |
| has_add_to_cart, has_compare, has_product_view, has_remove_from_cart, has_search, long_session | OUT_abandon   |     0.042 |        0.808 |  2.208 |
| has_compare, has_remove_from_cart, has_search, long_session                                    | OUT_abandon   |     0.042 |        0.808 |  2.208 |

## Clustering quality

| method        |   clusters |   noise_share |   silhouette |   davies_bouldin |   ari_vs_persona |
|:--------------|-----------:|--------------:|-------------:|-----------------:|-----------------:|
| HDBSCAN       |          6 |         0.185 |        0.391 |            0.979 |            0.096 |
| K-Means (k=5) |          5 |         0     |        0.455 |            0.952 |            0.11  |

ARI compares clusters with the simulator's hidden personas (evaluation only).

## Behavioral personas discovered (HDBSCAN cluster profiles)

|   cluster |   sessions |   events |   compares |   reviews |   carts |   removes |   checkout |   conversion |   abandonment | label                  |
|----------:|-----------:|---------:|-----------:|----------:|--------:|----------:|-----------:|-------------:|--------------:|:-----------------------|
|         0 |       1294 |    11.67 |       1.2  |      1.61 |    0    |      0    |          0 |         0    |          0    | researchers (c0)       |
|         1 |        419 |     6.53 |       0    |      0    |    0    |      0    |          0 |         0    |          0    | browsers (c1)          |
|         2 |        506 |     3.46 |       0    |      0    |    0    |      0    |          0 |         0    |          0    | bouncers (c2)          |
|         3 |       1005 |    13.27 |       1.19 |      1.35 |    1.28 |      0.28 |          0 |         0    |          1    | cart abandoners (c3)   |
|         4 |        758 |     6.61 |       0    |      0    |    1    |      0    |          1 |         0.73 |          0.27 | fast converters (c4)   |
|         5 |        907 |    10.97 |       1.07 |      1.11 |    1.14 |      0    |          1 |         0.62 |          0.38 | deliberate buyers (c5) |
