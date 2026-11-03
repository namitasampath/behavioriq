# Stage 8 - Contextual bandit (simulated, 30,000 decisions on test sessions)

|                                             |   total_reward |   mean_reward_per_decision |   mean_reward_last_10k |   regret_vs_oracle |
|:--------------------------------------------|---------------:|---------------------------:|-----------------------:|-------------------:|
| oracle (expected)                           |       11113.6  |                     0.3705 |                 0.3668 |                0   |
| LinUCB                                      |        9414.4  |                     0.3138 |                 0.3114 |             1699.2 |
| Thompson Sampling                           |        9180.43 |                     0.306  |                 0.3113 |             1933.1 |
| Random                                      |        8461.48 |                     0.282  |                 0.2775 |             2652.1 |
| Static rule (discount if predicted abandon) |        7917.32 |                     0.2639 |                 0.2574 |             3196.2 |
| Always none                                 |        7645    |                     0.2548 |                 0.2477 |             3468.6 |

Thompson Sampling permanently overtakes the static rule after **50 decisions** (warm-up window).

## Thompson Sampling action share per 3,000-decision window

|   window |   none |   discount_nudge |   compare_helper |   social_proof |
|---------:|-------:|-----------------:|-----------------:|---------------:|
|        0 |  0.169 |            0.345 |            0.23  |          0.256 |
|        1 |  0.133 |            0.319 |            0.341 |          0.207 |
|        2 |  0.149 |            0.218 |            0.479 |          0.154 |
|        3 |  0.098 |            0.215 |            0.552 |          0.135 |
|        4 |  0.08  |            0.167 |            0.563 |          0.19  |
|        5 |  0.079 |            0.132 |            0.6   |          0.189 |
|        6 |  0.093 |            0.107 |            0.623 |          0.177 |
|        7 |  0.087 |            0.094 |            0.611 |          0.207 |
|        8 |  0.058 |            0.105 |            0.616 |          0.221 |
|        9 |  0.061 |            0.121 |            0.592 |          0.227 |

Figures: bandit_reward.png, bandit_actions.png. Reward = purchase (0/1) minus action cost; response model in biq/bandit.py (UPLIFT, COST).
